"""Claude Code 适配器：读取会话 JSONL 的 user / assistant 事件。

数据来源：~/.claude/projects/<slug>/*.jsonl

只读每条记录的 type、timestamp、cwd 与 sessionId，不读 message 正文、
不读 tool 参数（可能含对话内容或绝对路径）。
Claude Code 没有明确的 task_started / task_complete 生命周期事件，
所以「最近有写入」只能作为低置信度的推测，绝不声称确证。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _mtime, _project_name, _short
from . import register

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def state() -> AgentState:
    out = AgentState(agent="Claude Code", source="session_event")
    if not CLAUDE_DIR.is_dir():
        out.status = "disconnected"
        return out

    files = [p for p in CLAUDE_DIR.glob("*/*.jsonl") if p.is_file()]
    if not files:
        out.status = "unknown"
        return out

    newest = max(files, key=_mtime)
    stamp = _mtime(newest)
    age = time.time() - stamp
    out.last_activity = stamp
    out.confidence = "low"      # 无生命周期事件，永远是推测

    # cwd 是项目根目录，取最后一段作为项目名；不显示完整绝对路径
    cwd = None
    try:
        with newest.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("cwd"):
                    cwd = str(rec["cwd"])
                    break
    except OSError:
        pass
    out.project = _project_name(cwd) if cwd else None
    out.task = None             # 会话标题就是用户首句 prompt，不上墙

    if age < RUNNING_WINDOW:
        out.status = "running"
        out.basis = "会话刚有写入，但无 turn 事件可确证（推测）"
    elif age < STALE_WINDOW:
        out.status = "idle"
        out.basis = f"会话 {int(age / 60)} 分钟前停写，无生命周期事件（推测）"
    else:
        out.status = "unknown"
        out.basis = "会话文件久未更新（推测）"
    return out


register("Claude Code", state)
