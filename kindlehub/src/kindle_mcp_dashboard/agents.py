"""Agent 活动监控引擎。

本文件只放与具体 Agent 无关的部分：
- AgentState 数据结构（agent / status / project / task / last_activity /
  source / confidence / basis / sessions）
- 状态枚举与排序
- 时间与文本工具
- 屏幕格式化（活跃详细、空闲紧凑）

具体 Agent 的采集逻辑在 plugins/ 下，通过 plugins/__init__.py 的注册表加载。
引擎不认识任何具体 Agent，因此换机器、删适配器都不会让监控整体失效。

设计原则：
- 只读，绝不修改任何 Agent 的数据库、配置或会话
- 优先使用明确的生命周期事件，其次才是"文件刚改过"这类弱证据
- 拿不到可靠证据时标 unknown，绝不把"进程存在"当成"正在执行"
- 每个状态都带 confidence 与 basis，让界面能区分"确证"与"推测"
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUS_ORDER = ["waiting", "running", "error", "idle", "unknown", "disconnected"]
ACTIVE = {"running", "waiting", "error"}

# 超过这个时间没有任何新事件，就不再说"正在执行"
RUNNING_WINDOW = 120.0
# 超过这个时间没动静，标记为 idle 而不是 running
STALE_WINDOW = 3600.0

# 小于这个大小的会话文件整份读取，确保不漏掉靠前的边界事件
FULL_READ_LIMIT = 8 * 1024 * 1024

# 有些 Agent 会把会话日志落成 zstd 压缩（Codex、DSH 都是），前缀还是 .jsonl
ZSTD_SUFFIXES = (".zst", ".zstd")

STATUS_CN = {
    "running": "执行中",
    "waiting": "等待输入",
    "idle": "空闲",
    "error": "出错",
    "unknown": "未知",
    "disconnected": "未连接",
}

STATUS_MARK = {
    "running": "●",
    "waiting": "◐",
    "idle": "○",
    "error": "✕",
    "unknown": "?",
    "disconnected": "⊘",
}

CONFIDENCE_CN = {"high": "确证", "medium": "半推测", "low": "推测"}


@dataclass
class AgentState:
    agent: str
    status: str = "unknown"          # waiting/running/error/idle/unknown/disconnected
    project: str | None = None
    task: str | None = None
    last_activity: float | None = None
    source: str = "none"
    confidence: str = "low"          # high(确证) / medium(半推测) / low(纯推测)
    basis: str = ""                  # 人话说明这个状态怎么来的
    sessions: int = 1                # 同时活跃的会话数

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "project": self.project,
            "task": self.task,
            "last_activity": self.last_activity,
            "source": self.source,
            "confidence": self.confidence,
            "basis": self.basis,
            "sessions": self.sessions,
        }


# ---------------------------------------------------------------- 工具

def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _iter_lines(path: Path):
    """逐条流式解析会话事件（普通 JSONL 或 zstd 压缩）。

    供全量扫描折叠用：边界事件（task_started / turn/start）可能在文件任意位置，
    尾部窗口会漏；但把整份事件列表留在内存里会随会话历史膨胀
    （实测单次采集峰值 551MB），所以逐条 yield，由调用方折叠成摘要。
    """
    if path.suffix.lower() in ZSTD_SUFFIXES:
        try:
            proc = subprocess.Popen(["zstd", "-dc", str(path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return
        try:
            with proc.stdout as stream:
                for raw in stream:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
        except OSError:
            return
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        return
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
    except OSError:
        return


def _tail_lines(path: Path, limit: int = 400, chunk: int = 8 * 1024 * 1024) -> list[dict]:
    """读取会话事件的尾部窗口。

    小于 8MB 的文件整份读——边界事件可能在文件中后段，只读尾部窗口会漏。
    超过 8MB 才退回尾部窗口。无论哪条路径都用定长环形缓冲，
    常驻内存只跟 limit 有关，不做整份 blob 的行切分。
    """
    if path.suffix.lower() in ZSTD_SUFFIXES:
        return _tail_zstd_lines(path, limit)
    tail: deque[dict] = deque(maxlen=max(1, limit))
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > FULL_READ_LIMIT:
                start = max(0, size - chunk)
                fh.seek(start)
                # start 正好落在行首时不能丢整行：仅当切入点在半行中间才丢弃残行
                if start > 0:
                    fh.seek(start - 1)
                    if fh.read(1) != b"\n":
                        fh.readline()
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    tail.append(json.loads(raw))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
    except OSError:
        return []
    return list(tail)


def _tail_zstd_lines(path: Path, limit: int = 400) -> list[dict]:
    """顺序解压压缩会话，只留尾部窗口。

    压缩流不能随机 seek，整份解压进内存会随会话历史一起涨
    （本机 ~/.codex/sessions 已 500MB+），所以边读边丢。
    """
    tail: deque[dict] = deque(maxlen=max(1, limit))
    for event in _iter_lines(path):
        tail.append(event)
    return list(tail)


def _project_name(path: str | None) -> str | None:
    """两种来源都要处理：
    - 普通路径 <HOME>/.../quant
    - 目录名编码 --Users-alice-Projects-kindle~0020dashboard--
    """
    if not path:
        return None
    cleaned = path.replace("~0020", " ").replace("\\", "/").strip("-")
    if "/" in cleaned:
        return cleaned.rsplit("/", 1)[-1] or None
    parts = [p for p in cleaned.split("-") if p]
    return parts[-1] if parts else None


_MD = re.compile(r"(\*\*|__|`|^#+\s*|^[-*]\s+)")


def _short(text: str | None, limit: int = 20) -> str | None:
    if not text:
        return None
    text = _MD.sub("", str(text))
    text = " ".join(text.split())
    # 取第一句，避免把整段回答塞到屏幕上
    for sep in ("。", "！", "？", ". ", "; ", "；"):
        idx = text.find(sep)
        if 0 < idx < limit:
            text = text[:idx]
            break
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ago(seconds: float) -> str:
    """把秒数说成人话：刚/12分钟前/3小时前/2天前。"""
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)}分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)}小时前"
    return f"{int(seconds // 86400)}天前"


def _clock(epoch: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(epoch)) if epoch else ""


# ---------------------------------------------------------------- 屏幕格式化

def format_rows(rows: list[dict[str, Any]], hide_unknown: bool = False,
                max_idle: int = 8) -> str:
    """屏幕上显示成：活跃的 2 行（状态行 + 项目），空闲的 1 行。

    时间放状态栏了，这里不再重复；依据留在后台和 API 里，屏幕只回答
    "谁在干活、在干什么"。

    max_idle 默认放宽到 8：本机装了 7+ 个 Agent 软件，过早折叠会让
    「显示不齐全」。只有真超过 8 个空闲才收成一行。区域放不下时由
    渲染器负责截断，而不是在数据层提前丢信息。

    低置信度的行会带一个 ~ 角标，明确告诉用户这是推测而非确证。
    """
    lines: list[str] = []

    active_rows = [r for r in rows if r["status"] in ACTIVE]
    idle_rows = [r for r in rows if r["status"] not in ACTIVE]
    if hide_unknown:
        idle_rows = [r for r in idle_rows
                     if r["status"] not in {"unknown", "disconnected"}]

    for row in active_rows:
        state_cn = STATUS_CN.get(row["status"], row["status"])
        mark = STATUS_MARK.get(row["status"], "?")
        project = row.get("project")
        # 推测的行加角标，避免把「刚有文件写入」当成确证在跑
        guess = "~" if row.get("confidence") == "low" else ""
        lines.append(f"{mark} {row['agent']} · {state_cn}{guess}")
        # 只显示项目，不显示任务标题——标题太长，且经常换行
        if project:
            lines.append("   " + str(project))

    for row in idle_rows[:max_idle]:
        state_cn = STATUS_CN.get(row["status"], row["status"])
        mark = STATUS_MARK.get(row["status"], "?")
        project = row.get("project") or "—"
        age = row.get("last_activity")
        when = f" · {_ago(time.time() - age)}" if age else ""
        lines.append(f"{mark} {row['agent']} · {state_cn} · {project}{when}")

    hidden = idle_rows[max_idle:]
    if hidden:
        names = "、".join(str(r["agent"]) for r in hidden[:3])
        suffix = f" 等 {len(hidden)} 个" if len(hidden) > 3 else ""
        lines.append("… 另有 " + names + suffix)

    return "\n".join(lines) if lines else "未发现 Agent"


def format_by_project(rows: list[dict[str, Any]],
                      max_projects: int = 6) -> str:
    """按项目聚合：一个项目一行，多个 Agent 在同一个项目时才展开明细。

    和 format_rows 的区别是视角——这里回答"哪个项目在推进"，
    而不是"哪个软件在跑"。同时推进多个项目时更省行数。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        project = str(row.get("project") or "—").split("、")[0]
        groups.setdefault(project, []).append(row)

    def sort_key(item):
        _name, members = item
        has_active = any(m["status"] in ACTIVE for m in members)
        newest = max((m.get("last_activity") or 0) for m in members)
        return (0 if has_active else 1, -newest)

    lines: list[str] = []
    for project, members in sorted(groups.items(), key=sort_key)[:max_projects]:
        active = [m for m in members if m["status"] in ACTIVE]
        idle = [m for m in members if m["status"] not in ACTIVE]

        if active:
            names = "、".join(str(m["agent"]) for m in active)
            if len(active) == 1:
                # 任务标题单独一行，否则「项目 + Agent + 任务」会超出屏幕宽度换行
                lines.append("● " + project + " · " + names + " 在跑")
                task = active[0].get("task")
                if task:
                    lines.append("   " + str(task))
            else:
                lines.append("● " + project + " · " + str(len(active)) + " 个在跑（" + names + "）")
                for m in active:
                    task = m.get("task")
                    detail = "   " + str(m["agent"])
                    if task:
                        detail += " · " + str(task)
                    lines.append(detail)
        else:
            newest = max(members, key=lambda x: x.get("last_activity") or 0)
            age = newest.get("last_activity")
            when = " · " + _ago(time.time() - age) if age else ""
            names = "、".join(str(m["agent"]) for m in idle)
            lines.append("○ " + project + " · " + names + " · 空闲" + when)

    return "\n".join(lines) if lines else "未发现项目"


# ---------------------------------------------------------------- 汇总

# 采集结果短缓存：适配器要遍历会话目录、解压 zstd、开 SQLite，
# 渲染路径每分钟可能调用多次，没必要每次都全量扫盘。
# 15 秒与 web.py 的预热周期（60 秒）错开半拍，屏幕看到的最多旧约 75 秒，
# 对几分钟一刷的 e-ink 来说没有可感知的差别。
COLLECT_TTL = 15.0
_collect_cache: dict[str, Any] = {"key": None, "at": -1e9, "rows": None}


def collect(settings: dict[str, Any] | None = None, force: bool = False) -> list[dict[str, Any]]:
    """调用所有已注册适配器，按状态优先级 + 最近活动排序。

    适配器通过 plugins 注册表加载；没装对应软件的机器上
    那一条直接缺席，其余照常工作。结果带 15 秒缓存，
    force=True 可跳过（预热循环用它保证每次都真采）。
    """
    key = json.dumps(settings or {}, sort_keys=True, ensure_ascii=False)
    now = time.monotonic()
    if (not force and _collect_cache["key"] == key and _collect_cache["rows"] is not None
            and now - _collect_cache["at"] < COLLECT_TTL):
        return _collect_cache["rows"]
    from . import plugins
    plugins.load_builtin()
    rows = [state.as_dict() for state in plugins.collect_all()]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]) if r["status"] in STATUS_ORDER else 9,
                             -(r["last_activity"] or 0)))
    _collect_cache.update({"key": key, "at": now, "rows": rows})
    return rows


def cache_age_seconds() -> float | None:
    """最近一次真实采集距今多少秒（供后台调试显示）。从未采过则 None。"""
    if _collect_cache["rows"] is None:
        return None
    return round(time.monotonic() - _collect_cache["at"])