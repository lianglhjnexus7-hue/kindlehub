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


def _short(text: str | None, limit: int = 22) -> str | None:
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

def format_rows(rows: list[dict[str, Any]], hide_unknown: bool = False) -> str:
    """活跃 Agent 显示完整信息（项目 + 任务 + 最近活动 + 依据），
    空闲/未知的压缩成一行。时间一律标注为"最近活动"，避免误读成任务开始时间。
    """
    lines: list[str] = []
    for row in rows:
        status = row["status"]
        if hide_unknown and status in {"unknown", "disconnected"}:
            continue
        mark = STATUS_MARK.get(status, "?")
        age = (time.time() - row["last_activity"]) if row.get("last_activity") else None
        stamp = f"最近活动 {_clock(row['last_activity'])}（{_ago(age)}）" if age is not None else "无活动记录"

        if status in ACTIVE:
            lines.append(f"{mark} {row['agent']} · {STATUS_CN.get(status, status)} · {stamp}")
            project = row.get("project")
            task = row.get("task")
            # 多项目同时活跃时，会话标题只属于其中一个，混写会误导
            if project and "、" in str(project):
                task = None
            detail = [x for x in (project, task) if x]
            if detail:
                lines.append("   项目 " + " · ".join(detail))
            if row.get("sessions", 1) > 1:
                lines.append(f"   {row['sessions']} 个会话同时活跃")
            basis = row.get("basis")
            if basis:
                conf = CONFIDENCE_CN.get(row.get("confidence"), "")
                lines.append(f"   [{conf}] {basis}")
        else:
            project = row.get("project") or "—"
            lines.append(f"{mark} {row['agent']} · {STATUS_CN.get(status, status)} · {project} · {stamp}")
    return "\n".join(lines) if lines else "未发现 Agent"


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