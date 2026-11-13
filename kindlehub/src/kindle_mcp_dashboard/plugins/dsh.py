"""DSH（DeepSeek）适配器：读取会话压缩日志的 turn/start 与 turn/end。

数据来源：~/.dsh/sessions/<项目>/<会话>/session.v4.jsonl.zstd
判断依据：turn/start 未配对 turn/end

为什么用 turn 而不是 step：step/start 会被 llm/retry 打断，
一个 turn 内可能出现多个 step，拿 step 当边界会误判。

只读事件类型与 cwd，不读消息正文。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..agents import (RUNNING_WINDOW, STALE_WINDOW, AgentState, _iter_lines,
                      _mtime, _project_name, _short)
from . import register

DSH_SESSIONS = Path.home() / ".dsh" / "sessions"

# 这些事件表示"在等人"，比 running 更具体
WAITING_EVENTS = {"permission/request", "approval/request", "elicit/request",
                  "input/request", "permission/pending"}

# 采集摘要的初始值；缓存里只放这些标量，不放事件本体
EMPTY_SUMMARY = {"turn_open": False, "waiting": False, "last_type": None, "title": ""}

# 摘要缓存按 (路径, mtime, 大小) 失效；zstd 无法随机读，重扫只发生在文件变化时
_dsh_cache: dict[str, Any] = {"key": None, "value": None}


def _read_summary(path: Path) -> dict:
    """流式解压并全量扫描会话日志，但只留下来判定用的摘要。

    turn/start 与 turn/end 可能相隔很远，只取尾部 N 条会把边界丢在窗口外，
    所以必须从头扫到尾。可是把每条解析后的事件都留在缓存里，
    常驻内存会跟着日志长度一起涨（本机最新会话解压后 94.6MB，
    实测单次采集峰值 551MB）。因此边扫边折叠，缓存里只放几个标量；
    解压也走流式管道，不再把整份解压结果缓冲进内存。
    """
    key = f"{path}:{_mtime(path)}:{os_size(path)}"
    cached = _dsh_cache.get("value")
    if _dsh_cache.get("key") == key and isinstance(cached, dict):
        return cached
    summary = dict(EMPTY_SUMMARY)
    for event in _iter_lines(path):
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        summary["last_type"] = kind
        if kind == "turn/start":
            summary["turn_open"], summary["waiting"] = True, False
        elif kind == "turn/end":
            summary["turn_open"], summary["waiting"] = False, False
        elif kind in WAITING_EVENTS:
            summary["waiting"] = True
        elif kind == "session/title":
            data = event.get("data")
            title = data.get("title") if isinstance(data, dict) else None
            summary["title"] = str(title or "").strip()
    # 一条事件都没解析到（如 zstd 缺失）不缓存，装上工具后下次采集能自愈
    if summary["last_type"] is not None:
        _dsh_cache.update({"key": key, "value": summary})
    return summary


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

    summary = _read_summary(path)
    turn_open, waiting = summary["turn_open"], summary["waiting"]
    last_type = summary["last_type"]

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
        # 已确证 turn 结束就是 idle，与距今多久无关；
        # 只有"无边界事件"那一支才按时间降级 unknown（与 Codex 一致）
        out.status = "idle"
        out.confidence = "high"
        out.basis = "收到 turn 结束事件"
    else:
        out.status = "idle" if age < STALE_WINDOW else "unknown"
        out.confidence = "low"
        out.basis = "未找到 turn 边界事件"

    # 会话"标题"常常就是用户输入的第一句 prompt，无法可靠区分标题与正文。
    # 画面会经中继推到公开 URL，绝不能带会话内容，故一律不把标题上墙。
    return out


register("DSH harness", state)