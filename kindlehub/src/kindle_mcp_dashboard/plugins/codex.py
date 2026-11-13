"""Codex 适配器：读取会话 JSONL 的 task_started / task_complete 生命周期。

数据来源：~/.codex/sessions/**/*.jsonl 以及当前落盘格式 *.jsonl.zst
判断依据：task_started 未配对 task_complete

只读事件类型与时间戳，不读对话正文。
全量扫描折叠 + (路径, mtime, 大小) 缓存：长 turn 里边界事件可能被挤到
800 条窗口之外，必须从头扫；流式解析让常驻内存不随会话历史增长。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..agents import (RUNNING_WINDOW, STALE_WINDOW, AgentState, _iter_lines,
                      _mtime, _project_name)
from . import register

CODEX_DIR = Path.home() / ".codex"

# 真正的会话文件名；.jsonl 之外还有 .jsonl.zst（当前落盘格式）
SESSION_SUFFIXES = (".jsonl", ".jsonl.zst", ".jsonl.zstd")

# 全量扫描结果按 (路径, mtime, 大小) 缓存；只留边界事件的标量，不留事件本体
_codex_cache: dict[str, Any] = {"key": None, "value": None}


def _is_session(path: Path) -> bool:
    """排除 .tmp / .bak 这类半写或备份文件，否则会被当成最新会话读歪。"""
    return path.name.lower().endswith(SESSION_SUFFIXES)


def _active_workspace() -> str | None:
    """global-state 里的 active-workspace-roots 是当前打开的工作区。"""
    try:
        data = json.loads((CODEX_DIR / ".codex-global-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    roots = data.get("active-workspace-roots") or []
    return _project_name(str(roots[0])) if isinstance(roots, list) and roots else None


def _read_summary(path: Path) -> dict:
    """全量扫描折叠：只留最近一对边界事件的时间戳与错误标志。

    流式逐条解析，缓存里绝不放 last_agent_message 这类对话正文。
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    key = (str(path), _mtime(path), size)
    cached = _codex_cache.get("value")
    if _codex_cache.get("key") == key and isinstance(cached, dict):
        return cached
    summary: dict[str, Any] = {"last_start": None, "last_complete": None}
    for event in _iter_lines(path):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "task_started":
            summary["last_start"] = {"started_at": payload.get("started_at", 0)}
        elif kind == "task_complete":
            summary["last_complete"] = {"started_at": payload.get("started_at", 0),
                                        "error": bool(payload.get("error"))}
    # 没解析到任何事件（如 zstd 缺失）不缓存，装上工具后下次采集能自愈
    if summary["last_start"] or summary["last_complete"]:
        _codex_cache.update({"key": key, "value": summary})
    return summary


def state() -> AgentState:
    out = AgentState(agent="Codex", source="session_event")
    root = CODEX_DIR / "sessions"
    if not root.is_dir():
        out.status = "disconnected"
        return out

    files = [p for p in root.rglob("*.jsonl*") if _is_session(p)]
    if not files:
        out.status = "unknown"
        return out
    newest = max(files, key=_mtime)
    touched = _mtime(newest)
    age = time.time() - touched

    summary = _read_summary(newest)
    last_start = summary["last_start"]
    last_complete = summary["last_complete"]

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
        errored = bool(last_complete.get("error"))
        out.status = "error" if errored else "idle"
        out.confidence = "high"
        out.basis = "收到任务完成事件" + ("（带错误）" if errored else "")
        # 对话正文绝不上墙：last_agent_message 是模型回复，只用静态文案。
        # 出错时不能说"已完成"，否则与 error 状态自相矛盾。
        out.task = "上一轮出错" if errored else "上一轮已完成"
    else:
        out.status = "idle" if age < STALE_WINDOW else "unknown"
        out.confidence = "low"
        out.basis = "会话中未找到任务事件"
    return out


register("Codex", state)