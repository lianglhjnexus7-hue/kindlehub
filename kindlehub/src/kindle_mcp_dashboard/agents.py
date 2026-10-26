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
import os
import re
import time
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

# zstd 会话无法随机读，适配器用它按 (路径, mtime, 大小) 缓存解析结果
_dsh_cache: dict[str, Any] = {"key": None, "value": None}


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


def _tail_lines(path: Path, limit: int = 400, chunk: int = 8 * 1024 * 1024) -> list[dict]:
    """读取会话事件。

    小于 8MB 的文件整份读——边界事件（task_started / turn/start）可能在文件中后段，
    只读尾部窗口会漏掉，导致状态误判。超过 8MB 才退回读尾部。
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(0 if size <= FULL_READ_LIMIT else max(0, size - chunk))
            blob = fh.read()
    except OSError:
        return []
    out = []
    for raw in blob.split(b"\n")[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return out


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
                max_idle: int = 3) -> str:
    """屏幕上显示成：活跃的 2 行（状态行 + 项目·任务），空闲的 1 行。

    时间放状态栏了，这里不再重复；依据与置信度留在后台和 API 里，
    屏幕只回答"谁在干活、在干什么"。

    Agent 超过 max_idle 个空闲时，多余的空闲/未知收成一行，
    避免行数溢出把内容截断。活跃的始终全部展开。
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
        task = row.get("task")
        if project and "、" in str(project):
            project = str(project).split("、")[0]
        lines.append(f"{mark} {row['agent']} · {state_cn}")
        detail = [x for x in (project, task) if x]
        if detail:
            lines.append("   " + " · ".join(detail))

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

def collect(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """调用所有已注册适配器，按状态优先级 + 最近活动排序。

    适配器通过 plugins 注册表加载；没装对应软件的机器上
    那一条直接缺席，其余照常工作。
    """
    from . import plugins
    plugins.load_builtin()
    rows = [state.as_dict() for state in plugins.collect_all()]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]) if r["status"] in STATUS_ORDER else 9,
                             -(r["last_activity"] or 0)))
    return rows