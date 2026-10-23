"""OpenCode 适配器：读取 session 表的最后写入时间。

数据来源：~/.local/share/opencode/opencode.db（session 表）

⚠️ 本适配器只能推测，不能确证：
OpenCode 的 HTTP API 需要认证，且其 event 表只有
message.updated / session.updated，没有 turn 边界事件。
因此这里只能看"最后写入时间"，一律标低置信度。

只读 directory/title/time_updated，message/part 表完全不查。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _ago, _mtime, _project_name, _short
from . import register

OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def state() -> AgentState:
    out = AgentState(agent="OpenCode", source="session_db")
    if not OPENCODE_DB.is_file():
        out.status = "disconnected"
        return out
    try:
        con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute(
                "select directory, title, time_updated from session "
                "order by time_updated desc limit 1"
            ).fetchone()
        finally:
            con.close()
    except Exception:
        out.status = "disconnected"
        return out

    if not row:
        out.status = "unknown"
        return out

    directory, title, updated_ms = row
    try:
        stamp = float(updated_ms) / 1000.0
    except (TypeError, ValueError):
        stamp = _mtime(OPENCODE_DB)
    out.last_activity = stamp
    out.project = _project_name(str(directory) if directory else None)
    # "New session - 2026-10-15T07:09:34.596Z" 这类默认标题没有信息量
    if title and not str(title).lower().startswith("new session"):
        out.task = _short(title)

    age = time.time() - stamp
    if age < RUNNING_WINDOW:
        out.status = "running"
        out.basis = "会话刚有写入，但无 turn 事件可确证（推测）"
    else:
        out.status = "idle"
        out.basis = f"距最后写入 {_ago(age)}，无 turn 事件可确证（推测）"
    out.confidence = "low"
    return out


register("OpenCode", state)