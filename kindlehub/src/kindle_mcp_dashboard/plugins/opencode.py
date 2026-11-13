"""OpenCode 适配器：读 session_v2 与消息时间戳推测状态。

数据来源：~/.local/share/opencode/opencode.db
- session_v2 —— 当前版本 OpenCode 写的会话表（旧版是 session 表，仍在但不再更新）
- session_message —— 每条消息一行，time_updated 是真实的活动时间

⚠️ 本适配器只能推测，不能确证：
OpenCode 的 event 表没有 turn 边界事件，因此"还在写入"只能说明
最近有消息，一律标低置信度。只读 directory/title/时间戳，
message/session_message 的 data 列（对话正文）一律不查。

教训（2026-10 实机）：旧版适配器只读遗留 session 表，其 time_updated
停在最后一次元数据变更——当前版本会话活动全在 session_v2/session_message
里，结果正在使用的 OpenCode 被报成"15 天前"。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ..agents import RUNNING_WINDOW, AgentState, _ago, _project_name
from . import register

OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

# group by 全表扫描不便宜，db 没变就复用上次的查询结果。
# WAL 模式下新写入可能只落在 -wal 文件里，主文件 mtime 不动，
# 所以键必须把 -wal 的 mtime/大小也算进去。
_db_cache: dict[str, Any] = {"key": None, "row": None}


def _db_key() -> tuple | None:
    try:
        st = OPENCODE_DB.stat()
    except OSError:
        return None
    key: tuple = (st.st_mtime_ns, st.st_size)
    try:
        wst = Path(str(OPENCODE_DB) + "-wal").stat()
        key += (wst.st_mtime_ns, wst.st_size)
    except OSError:
        key += (0, 0)
    return key


def _newest(con: sqlite3.Connection) -> tuple[str, str, float] | None:
    """最新的会话：directory, title, 活动时间（秒）。

    先查 session_v2（当前版本），表不存在再退回遗留 session 表。
    活动时间取 max(会话时间, 消息时间)——会话行的 time_updated 在
    消息往来时未必跟着走。
    """
    try:
        row = con.execute(
            "select s.directory, s.title,"
            "       max(s.time_updated, coalesce(m.max_msg, 0)) as stamp"
            " from session_v2 s"
            " left join (select session_id, max(time_updated) as max_msg"
            "            from session_message group by session_id) m"
            "   on m.session_id = s.id"
            " order by stamp desc limit 1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = con.execute(
            "select directory, title, time_updated from session"
            " order by time_updated desc limit 1"
        ).fetchone()
    if not row:
        return None
    directory, title, updated = row
    try:
        stamp = float(updated) / 1000.0
    except (TypeError, ValueError):
        return None
    return str(directory or ""), title, stamp


def state() -> AgentState:
    out = AgentState(agent="OpenCode", source="session_db")
    if not OPENCODE_DB.is_file():
        out.status = "disconnected"
        return out
    key = _db_key()
    if key is not None and _db_cache["key"] == key:
        row = _db_cache["row"]
    else:
        try:
            con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=2.0)
            try:
                row = _newest(con)
            finally:
                con.close()
        except Exception:
            out.status = "disconnected"
            return out
        if key is not None:
            _db_cache.update({"key": key, "row": row})

    if not row:
        out.status = "unknown"
        return out

    directory, _title, stamp = row
    out.last_activity = stamp
    out.project = _project_name(directory) if directory else None
    # 会话标题是用户首句或对话摘要，可能含隐私，不上墙（与 Claude/DSH 一致）
    out.task = None

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
