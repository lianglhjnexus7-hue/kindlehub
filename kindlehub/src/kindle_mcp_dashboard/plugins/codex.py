"""Codex 适配器：读取会话 JSONL 的 task_started / task_complete 生命周期。

数据来源：~/.codex/sessions/**/*.jsonl
判断依据：task_started 未配对 task_complete

只读事件类型与时间戳，不读对话正文。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _mtime, _project_name, _short, _tail_lines
from . import register

CODEX_DIR = Path.home() / ".codex"


def _active_workspace() -> str | None:
    """global-state 里的 active-workspace-roots 是当前打开的工作区。"""
    try:
        data = json.loads((CODEX_DIR / ".codex-global-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    roots = data.get("active-workspace-roots") or []
    return _project_name(str(roots[0])) if isinstance(roots, list) and roots else None


def state() -> AgentState:
    out = AgentState(agent="Codex", source="session_event")
    root = CODEX_DIR / "sessions"
    if not root.is_dir():
        out.status = "disconnected"
        return out

    files = list(root.rglob("*.jsonl"))
    if not files:
        out.status = "unknown"
        return out
    newest = max(files, key=_mtime)
    touched = _mtime(newest)
    age = __import__("time").time() - touched

    last_start = last_complete = None
    for event in _tail_lines(newest, limit=800):
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "task_started":
            last_start = payload
        elif kind == "task_complete":
            last_complete = payload

    out.last_activity = touched
    out.project = _active_workspace()

    if last_start and (not last_complete or
                       last_start.get("started_at", 0) > last_complete.get("started_at", 0)):
        # Codex 会话被中断时不会补写 task_complete，
        # 所以"没有结束事件"不能单独证明还在跑，还要看是否仍在写入。
        if age < RUNNING_WINDOW:
            out.status = "running"
            out.confidence = "high"
            out.basis = "任务开始事件未配对结束事件，且事件仍在写入"
        else:
            out.status = "idle"
            out.confidence = "medium"
            out.basis = "任务无结束事件，但已停止写入（可能被中断）"
    elif last_complete:
        out.status = "error" if last_complete.get("error") else "idle"
        out.confidence = "high"
        out.basis = "收到任务完成事件" + ("（带错误）" if last_complete.get("error") else "")
        out.task = _short(last_complete.get("last_agent_message")) or "上一轮已完成"
    else:
        out.status = "idle" if age < STALE_WINDOW else "unknown"
        out.confidence = "low"
        out.basis = "会话中未找到任务事件"
    return out


register("Codex", state)