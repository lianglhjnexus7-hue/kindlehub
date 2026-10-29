"""本轮优化的回归测试：状态机加固与布局紧凑化。

重点守住三条底线：
1. 状态判断必须基于真实生命周期事件，不能只看文件时间
2. 读取窗口不能漏掉靠前的边界事件
3. 无标题的窄条区域必须能渲染出文字
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kindle_mcp_dashboard import agents
from kindle_mcp_dashboard.defaults import DEFAULT_DASHBOARD
from kindle_mcp_dashboard.renderer import render_dashboard
from kindle_mcp_dashboard.sources import health_summary


def test_codex_reads_task_started_beyond_tail_window(tmp_path, monkeypatch):
    """task_started 位于文件前部时也要能读到（旧实现只读尾部 512KB 会漏）。"""
    sessions = tmp_path / "sessions" / "2026" / "10" / "23"
    sessions.mkdir(parents=True)
    log = sessions / "rollout.jsonl"
    # 先写 2MB 填充，再把 task_started 放在很前面
    filler = json.dumps({"type": "response_item", "payload": {"type": "message"},
                         "timestamp": "2026-10-23T02:00:00"})
    log.write_text(filler * 20000 + "\n", encoding="utf-8")
    now = time.time()
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "event_msg", "timestamp": "2026-10-23T02:00:30",
                            "payload": {"type": "task_started", "turn_id": "t",
                                        "started_at": now - 5}}) + "\n")
    # 把 task_started 挪到文件最前面
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text(lines[-1] + "\n" + "\n".join(lines[:-1]) + "\n", encoding="utf-8")

    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    monkeypatch.setattr(codex, "_active_workspace", lambda: "demo")
    state = codex.state()
    assert state.status == "running"
    assert state.confidence == "high"


def test_dsh_scans_full_session_for_turn_pairs(tmp_path, monkeypatch):
    """turn/start 在事件序列中后段时也要配对成功（旧实现只取尾部 N 条）。"""
    root = tmp_path / "sessions"
    proj = root / "--Users-alice-Projects-demo--"
    sess = proj / "s1"
    sess.mkdir(parents=True)
    events = [{"type": "session", "cwd": "<HOME>/alice/Projects/demo"}]
    for i in range(3000):
        events.append({"type": "tool/call", "cwd": "<HOME>/alice/Projects/demo"})
    events.append({"type": "turn/start", "cwd": "<HOME>/alice/Projects/demo"})
    payload = "\n".join(json.dumps(e) for e in events) + "\n"
    # 生产环境文件名是 session.v4.jsonl.zstd，glob 模式必须匹配
    f = sess / "session.v4.jsonl.zstd"
    f.write_text(payload, encoding="utf-8")
    # 假装是 zstd：monkeypatch subprocess 让它直接返回原文
    import subprocess
    class FakeRun:
        def __init__(self, out): self.stdout = out
    def fake_run(cmd, capture_output=None, timeout=None):
        return FakeRun(f.read_bytes())
    monkeypatch.setattr(subprocess, "run", fake_run)
    from kindle_mcp_dashboard.plugins import dsh
    monkeypatch.setattr(dsh, "DSH_SESSIONS", root)
    agents._dsh_cache.update({"key": None})

    state = dsh.state()
    assert state.status == "running"
    assert state.confidence == "high"
    assert "turn" in state.basis


def test_untitled_narrow_bar_renders(tmp_path, monkeypatch):
    """无标题 + border=False 的窄条必须画出文字（曾因 pad 吃光高度而空白）。"""
    from PIL import Image
    monkeypatch.setenv("KINDLE_DASHBOARD_STATE", str(tmp_path / "s.json"))
    data = {"version": 1, "refresh_seconds": 60,
            "settings": {"weather": {"city": ""}},
            "device": {"model": "KT2", "width": 600, "height": 800, "grayscale": 16},
            "active_screen": "overview",
            "screens": [{"id": "overview", "regions": [
                {"id": "bar", "kind": "text", "x": 20, "y": 1255, "w": 960, "h": 70,
                 "label": "", "border": False, "value": "更新 02:22 · 各源正常",
                 "size": 15, "align": "center"}]}]}
    im = render_dashboard(data, {})
    px = im.load()
    ink = sum(1 for y in range(735, 800) for x in range(0, 600, 2) if px[x, y] < 128)
    assert ink > 50, f"健康栏未渲染出文字（墨量 {ink}）"


def test_health_summary_flags_only_low_confidence():
    """健康摘要只对低置信度告警，不能把确证的也算成推测。"""
    rows = [
        {"agent": "A", "status": "running", "confidence": "high", "last_activity": time.time()},
        {"agent": "B", "status": "idle", "confidence": "low", "last_activity": time.time()},
    ]
    import kindle_mcp_dashboard.sources as src
    orig = src.agents.collect
    src.agents.collect = lambda settings=None: rows
    try:
        text = health_summary({"weather": {"city": ""}})
    finally:
        src.agents.collect = orig
    assert "B" in text
    assert "、A" not in text and "A、" not in text


def test_status_order_puts_waiting_first():
    rows = [
        {"agent": "idle1", "status": "idle", "last_activity": time.time()},
        {"agent": "run1", "status": "running", "last_activity": time.time()},
        {"agent": "wait1", "status": "waiting", "last_activity": time.time()},
    ]
    ordered = sorted(rows, key=lambda r: agents.STATUS_ORDER.index(r["status"]))
    assert [r["agent"] for r in ordered] == ["wait1", "run1", "idle1"]


def test_compact_row_for_idle_agent():
    rows = [{"agent": "Z", "status": "idle", "project": "demo", "task": None,
            "last_activity": time.time(), "confidence": "high", "basis": "已完成",
            "sessions": 1}]
    text = agents.format_rows(rows)
    assert text.count("\n") == 0, "空闲 Agent 应压缩为一行"
    assert "空闲" in text and "demo" in text


def test_active_row_shows_project_only():
    """活跃 Agent 显示两行：状态行 + 项目行。

    任务标题不上屏幕（太长、常换行），依据与置信度只留在 API 里。
    """
    rows = [{"agent": "D", "status": "running", "project": "demo", "task": "跑测试",
            "last_activity": time.time(), "confidence": "high",
            "basis": "turn 未配对", "sessions": 1}]
    text = agents.format_rows(rows)
    assert text.startswith("● D · 执行中")
    assert "demo" in text
    assert "跑测试" not in text, "任务标题不应上屏幕"
    assert "turn 未配对" not in text, "依据不应出现在屏幕上"