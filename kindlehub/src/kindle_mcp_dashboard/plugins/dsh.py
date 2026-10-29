"""DSH（DeepSeek）适配器：读取会话压缩日志的 turn/start 与 turn/end。

数据来源：~/.dsh/sessions/<项目>/<会话>/session.v4.jsonl.zstd
判断依据：turn/start 未配对 turn/end

为什么用 turn 而不是 step：step/start 会被 llm/retry 打断，
一个 turn 内可能出现多个 step，拿 step 当边界会误判。

只读事件类型与 cwd，不读消息正文。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..agents import (RUNNING_WINDOW, STALE_WINDOW, AgentState, _dsh_cache,
                     _mtime, _project_name, _short)
from . import register

DSH_SESSIONS = Path.home() / ".dsh" / "sessions"

# 这些事件表示"在等人"，比 running 更具体
WAITING_EVENTS = {"permission/request", "approval/request", "elicit/request",
                  "input/request", "permission/pending"}


def _read_events(path: Path, limit: int = 400) -> list[dict]:
    """解压并解析会话日志。

    必须全量扫描：turn/start 与 turn/end 可能相隔很远，
    只取尾部 N 条会把边界丢在窗口外，导致状态误判。
    """
    key = f"{path}:{_mtime(path)}:{os_size(path)}"
    if _dsh_cache.get("key") == key:
        return _dsh_cache.get("value") or []
    try:
        blob = subprocess.run(["zstd", "-dc", str(path)], capture_output=True,
                              timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    events = []
    for raw in blob.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    _dsh_cache.update({"key": key, "value": events})
    return events


def os_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def state() -> AgentState:
    out = AgentState(agent="DSH harness", source="session_event")
    if not DSH_SESSIONS.is_dir():
        out.status = "disconnected"
        return out

    # 多个会话可能同时写入（实测不同项目只差毫秒），按项目聚合而不是只取最新
    newest_by_project: dict[str, tuple[float, Path]] = {}
    for project_dir in DSH_SESSIONS.iterdir():
        if not project_dir.is_dir():
            continue
        for session_dir in project_dir.iterdir():
            if not session_dir.is_dir():
                continue
            for candidate in session_dir.glob("session.v4.jsonl*.zst*"):
                stamp = _mtime(candidate)
                current = newest_by_project.get(project_dir.name)
                if current is None or stamp > current[0]:
                    newest_by_project[project_dir.name] = (stamp, candidate)

    if not newest_by_project:
        out.status = "unknown"
        return out

    ranked = sorted(newest_by_project.items(), key=lambda kv: -kv[1][0])
    now = time.time()
    active = [(n, s, p) for n, (s, p) in ranked if now - s < RUNNING_WINDOW]

    project_dir, (newest_mtime, path) = ranked[0]
    out.last_activity = newest_mtime
    age = now - newest_mtime

    # 多个项目同时活跃时用 / 分隔（第一个是主项目，任务标题对应它）
    names: list[str] = []
    for name, _stamp, _p in active:
        label = _project_name(name) or name
        if label and label not in names:
            names.append(label)
    if not names:
        names = [_project_name(project_dir) or project_dir]
    out.project = "/".join(names)
    out.sessions = len(active) or 1

    events = _read_events(path)
    turn_open = waiting = False
    for event in events:
        kind = event.get("type")
        if kind == "turn/start":
            turn_open, waiting = True, False
        elif kind == "turn/end":
            turn_open, waiting = False, False
        elif kind in WAITING_EVENTS:
            waiting = True

    last_type = events[-1].get("type") if events else None
    if waiting and age < RUNNING_WINDOW:
        out.status, out.confidence = "waiting", "high"
        out.basis = "检测到等待授权或输入的事件"
    elif turn_open and age < RUNNING_WINDOW:
        out.status, out.confidence = "running", "high"
        out.basis = "turn 开始事件未配对结束事件，且事件仍在写入"
    elif turn_open:
        out.status, out.confidence = "idle", "medium"
        out.basis = "turn 无结束事件，但已停止写入（可能被中断）"
    elif last_type in {"turn/end", "step/end", "tool/result"}:
        out.status = "idle" if age < STALE_WINDOW else "unknown"
        out.confidence = "high"
        out.basis = "收到 turn 结束事件"
    else:
        out.status = "idle" if age < STALE_WINDOW else "unknown"
        out.confidence = "low"
        out.basis = "未找到 turn 边界事件"

    for event in reversed(events):
        if event.get("type") == "session/title":
            data = event.get("data")
            title = data.get("title") if isinstance(data, dict) else None
            # 有些会话的"标题"其实是用户输入的第一句 prompt，
            # 直接显示会把对话内容带上墙，这类一律不显示。
            text = str(title or "").strip()
            if text and not text.startswith(("你", "请", "帮我", "我们", "这是", "现在")):
                out.task = _short(title)
            break
    return out


register("DSH harness", state)