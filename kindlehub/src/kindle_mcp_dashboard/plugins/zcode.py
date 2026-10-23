"""ZCode 适配器：读取任务索引数据库的 task_status 字段。

数据来源：~/.zcode/v2/tasks-index.sqlite（tasks 表）
判断依据：task_status 是明确的状态接口，不是推测

只读连接，绝不写入。只取 workspace_path/title/task_status/updated_at，
searchable_text（含对话正文）一律不读。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, AgentState, _mtime, _project_name, _short
from . import register

ZCODE_DB = Path.home() / ".zcode" / "v2" / "tasks-index.sqlite"

STATUS_MAP = {
    "running": "running", "in_progress": "running", "streaming": "running",
    "completed": "idle", "done": "idle", "idle": "idle",
    "error": "error", "failed": "error", "cancelled": "idle",
}


def state() -> AgentState:
    out = AgentState(agent="ZCode", source="task_index")
    if not ZCODE_DB.is_file():
        out.status = "disconnected"
        return out
    try:
        con = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute(
                "select workspace_path, title, task_status, updated_at "
                "from tasks where deleted = 0 order by updated_at desc limit 1"
            ).fetchone()
        finally:
            con.close()
    except Exception:
        out.status = "disconnected"
        return out

    if not row:
        out.status = "unknown"
        return out

    workspace, title, status_text, updated_ms = row
    try:
        stamp = float(updated_ms) / 1000.0
    except (TypeError, ValueError):
        stamp = _mtime(ZCODE_DB)
    out.last_activity = stamp
    out.project = _project_name(str(workspace) if workspace else None)
    out.task = _short(title)

    lowered = str(status_text or "").lower()
    out.status = STATUS_MAP.get(lowered, "unknown")
    out.confidence = "high" if lowered in STATUS_MAP else "medium"
    out.basis = (f"任务表状态为 {status_text}" if lowered in STATUS_MAP
                 else f"未识别的状态值 {status_text}")
    if out.status == "idle" and time.time() - stamp < RUNNING_WINDOW:
        out.basis += "（刚有更新，可能正在收尾）"
    return out


register("ZCode", state)