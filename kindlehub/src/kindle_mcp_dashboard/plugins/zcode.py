"""ZCode 适配器：用会话 rollout 日志的实时写入判断状态。

数据来源：
- ~/.zcode/cli/rollout/model-io-sess_*.jsonl —— 每次模型调用完成后追加一行，
  会话在跑时文件持续增长。mtime 在刷新窗口内 = 有真实活动，证据最硬。
- ~/.zcode/cli/db/db.sqlite —— session 表，取 directory/title/父子关系等元数据。

为什么不再读 tasks-index.sqlite：它的 task_status 与 meta_json.status 是
索引字段的滞后值，实测任务跑到一半时两个字段都可能停在上一轮的
completed——2026-10 实机观察，正在运行的会话被报成 completed/idle。

只读文件名与 mtime、session 表的元数据列；rollout 正文、searchable_text、
消息 data 一律不读。会话标题（first_input/generated/custom）都是对话派生
内容，可能含隐私，一律不上墙（task 恒为 None，与 Claude/DSH/OpenCode 一致）。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _ago, _mtime, _project_name
from . import register

ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"
ZCODE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"

ROLLOUT_PREFIX = "model-io-"
ROLLOUT_SUFFIX = ".jsonl"


def _sessions_meta() -> dict[str, dict]:
    """session 表元数据：id -> {directory, title, title_source, parent_id}。

    title_source / parent_id 是后加的列，老版本没有；缺列时退回最小查询。
    """
    if not ZCODE_DB.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "select id, directory, title, title_source, parent_id from session"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = [(r[0], r[1], r[2], "", None) for r in
                    con.execute("select id, directory, title from session").fetchall()]
        finally:
            con.close()
    except Exception:
        return {}
    return {r[0]: {"directory": r[1], "title": r[2],
                   "title_source": r[3] or "", "parent_id": r[4]} for r in rows}


def state() -> AgentState:
    out = AgentState(agent="ZCode", source="session_event")
    meta = _sessions_meta()
    now = time.time()

    newest: tuple[float, str] | None = None   # 全体 rollout 里最新的一份
    fresh: list[tuple[float, str]] = []       # 刷新窗口内有写入的（活跃）
    if ROLLOUT_DIR.is_dir():
        for path in ROLLOUT_DIR.glob(ROLLOUT_PREFIX + "sess_*" + ROLLOUT_SUFFIX):
            stamp = _mtime(path)
            if stamp <= 0:
                continue
            sid = path.name[len(ROLLOUT_PREFIX):-len(ROLLOUT_SUFFIX)]
            if newest is None or stamp > newest[0]:
                newest = (stamp, sid)
            if now - stamp < RUNNING_WINDOW:
                fresh.append((stamp, sid))

    if newest is None:
        # 没有 rollout（旧版本没这目录，或已被清理）：退回 session 表时间戳，
        # 只能推测，与 OpenCode 同一置信档。
        if not meta:
            out.status = "disconnected"
            return out
        try:
            con = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=2.0)
            try:
                row = con.execute(
                    "select id, time_updated from session order by time_updated desc limit 1"
                ).fetchone()
            finally:
                con.close()
        except Exception:
            out.status = "disconnected"
            return out
        if not row:
            out.status = "unknown"
            return out
        sid, updated_ms = row
        try:
            stamp = float(updated_ms) / 1000.0
        except (TypeError, ValueError):
            out.status = "unknown"
            return out
        info = meta.get(sid) or {}
        out.last_activity = stamp
        out.project = _project_name(str(info.get("directory")) if info.get("directory") else None)
        age = now - stamp
        if age < RUNNING_WINDOW:
            out.status = "running"
            out.basis = "会话刚有更新，但无 rollout 证据（推测）"
        else:
            out.status = "idle"
            out.basis = f"距最后写入 {_ago(age)}，无 rollout 证据（推测）"
        out.confidence = "low"
        return out

    if fresh:
        # 活跃：rollout 仍在写入。多个项目同时跑时用 / 分隔（与 DSH 一致）
        fresh.sort(reverse=True)
        out.status = "running"
        out.confidence = "high"
        out.basis = "会话 rollout 日志仍在写入"
        out.sessions = len(fresh)
        out.last_activity = fresh[0][0]
        names: list[str] = []
        for _stamp, sid in fresh:
            directory = (meta.get(sid) or {}).get("directory")
            label = _project_name(str(directory)) if directory else None
            if label and label not in names:
                names.append(label)
        out.project = "/".join(names) if names else None
        return out

    # 全部安静：给出最近一次活动，medium 置信（mtime 证据确凿但不代表此刻状态）
    stamp, sid = newest
    out.last_activity = stamp
    info = meta.get(sid) or {}
    directory = info.get("directory")
    out.project = _project_name(str(directory)) if directory else None
    age = now - stamp
    if age < STALE_WINDOW:
        out.status = "idle"
        out.confidence = "medium"
        out.basis = f"距最后写入 {_ago(age)}，无实时写入"
    else:
        out.status = "unknown"
        out.confidence = "low"
        out.basis = f"距最后写入 {_ago(age)}，太久无活动"
    return out


register("ZCode", state)
