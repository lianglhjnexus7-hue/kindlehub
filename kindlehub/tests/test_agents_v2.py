"""本轮优化的回归测试：状态机加固与布局紧凑化。

重点守住三条底线：
1. 状态判断必须基于真实生命周期事件，不能只看文件时间
2. 读取窗口不能漏掉靠前的边界事件
3. 无标题的窄条区域必须能渲染出文字
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from kindle_mcp_dashboard import agents
from kindle_mcp_dashboard.defaults import DEFAULT_DASHBOARD
from kindle_mcp_dashboard.renderer import render_dashboard
from kindle_mcp_dashboard.sources import health_summary

ZSTD = shutil.which("zstd")
needs_zstd = pytest.mark.skipif(ZSTD is None, reason="未安装 zstd 命令行")


def _write_zst(path, docs):
    """按生产落盘格式写一份真正的 zstd 压缩会话。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs).encode("utf-8")
    subprocess.run([ZSTD, "-q", "-f", "-o", str(path)], input=raw, check=True)


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


@needs_zstd
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
    # 生产环境文件名是 session.v4.jsonl.zstd，glob 模式必须匹配
    f = sess / "session.v4.jsonl.zstd"
    _write_zst(f, events)

    from kindle_mcp_dashboard.plugins import dsh
    monkeypatch.setattr(dsh, "DSH_SESSIONS", root)
    dsh._dsh_cache.update({"key": None, "value": None})

    state = dsh.state()
    assert state.status == "running"
    assert state.confidence == "high"
    assert "turn" in state.basis


@needs_zstd
def test_dsh_caches_summary_not_whole_session(tmp_path, monkeypatch):
    """全量扫描必须保留，但不能把整个事件列表长期留在内存里。

    实测：本机最新会话解压后 94.6MB，旧实现把每条解析后的事件都塞进
    _dsh_cache，单次采集进程峰值 551MB——比其余六个适配器加起来还高 5 倍。
    """
    root = tmp_path / "sessions"
    sess = root / "demo" / "s1"
    sess.mkdir(parents=True)
    events = [{"type": "turn/start"}]
    events += [{"type": "tool/call"} for _ in range(5000)]
    events.append({"type": "turn/end"})
    f = sess / "session.v4.jsonl.zstd"
    _write_zst(f, events)

    from kindle_mcp_dashboard.plugins import dsh
    monkeypatch.setattr(dsh, "DSH_SESSIONS", root)
    dsh._dsh_cache.update({"key": None, "value": None})

    st = dsh.state()
    assert st.basis == "收到 turn 结束事件"
    cached = dsh._dsh_cache["value"]
    assert isinstance(cached, dict), "缓存应只留摘要，不能留全部事件"
    assert cached["last_type"] == "turn/end"
    assert cached["turn_open"] is False


def test_codex_finds_task_started_beyond_800_events(tmp_path, monkeypatch):
    """长 turn 把 task_started 挤出 800 条尾部窗口时，全量扫描仍要找到它。"""
    sessions = tmp_path / "sessions" / "2026" / "11" / "11"
    sessions.mkdir(parents=True)
    log = sessions / "rollout-long.jsonl"
    now = time.time()
    lines = [json.dumps({"type": "event_msg",
                         "payload": {"type": "task_started", "started_at": now - 30}})]
    lines += [json.dumps({"type": "response_item", "payload": {"type": "message"}})
              for _ in range(1200)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    monkeypatch.setattr(codex, "_active_workspace", lambda: "demo")
    codex._codex_cache.update({"key": None, "value": None})

    state = codex.state()
    assert state.status == "running"
    assert state.confidence == "high"


def test_codex_cache_keeps_no_conversation_content(tmp_path, monkeypatch):
    """折叠缓存里只允许有边界事件的标量，last_agent_message 绝不进缓存。"""
    sessions = tmp_path / "sessions" / "2026" / "11" / "11"
    sessions.mkdir(parents=True)
    log = sessions / "rollout-secret.jsonl"
    now = time.time()
    log.write_text("\n".join([
        json.dumps({"type": "event_msg",
                    "payload": {"type": "task_started", "started_at": now - 60}}),
        json.dumps({"type": "event_msg",
                    "payload": {"type": "task_complete", "started_at": now - 60,
                                "last_agent_message": "这段回复正文绝不能进缓存"}}),
    ]) + "\n", encoding="utf-8")

    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    codex._codex_cache.update({"key": None, "value": None})

    codex.state()
    assert "回复正文" not in json.dumps(codex._codex_cache["value"], ensure_ascii=False)


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


def test_health_summary_low_confidence_is_neutral_not_alert():
    """长期推测（如 OpenCode）不是故障，不该挂"异常"造成告警疲劳。"""
    import kindle_mcp_dashboard.sources as src
    rows = [{"agent": "OpenCode", "status": "idle", "confidence": "low",
             "last_activity": time.time()}]
    orig = src.weather.status
    src.weather.status = lambda: {"state": "ok", "age": 10, "city": ""}
    try:
        text = src.health_summary({}, agent_rows=rows)
    finally:
        src.weather.status = orig
    assert "异常" not in text
    assert "OpenCode" in text and "推测" in text


def test_health_summary_disconnected_still_alerts():
    """失联是真异常，必须保留"异常"字样。"""
    import kindle_mcp_dashboard.sources as src
    rows = [{"agent": "ZCode", "status": "disconnected", "confidence": "low",
             "last_activity": None}]
    text = src.health_summary({}, agent_rows=rows)
    assert "异常" in text and "ZCode" in text


def test_collect_caches_within_ttl():
    """15 秒内重复调用应命中缓存（相同 settings 返回同一列表对象）。"""
    agents._collect_cache.update({"key": None, "rows": None, "at": -1e9})
    first = agents.collect({"probe": "cache-test"})
    second = agents.collect({"probe": "cache-test"})
    assert first is second


def test_collect_force_bypasses_cache():
    agents._collect_cache.update({"key": None, "rows": None, "at": -1e9})
    first = agents.collect({"probe": "force-test"})
    second = agents.collect({"probe": "force-test"}, force=True)
    assert first is not second


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


def test_health_summary_includes_workbuddy_when_enabled():
    """启用的看板过期必须进健康摘要（此前只查天气，看板失联无人知晓）。"""
    import kindle_mcp_dashboard.sources as src
    orig = src.workbuddy.status
    src.workbuddy.status = lambda: {"state": "stale", "age": 999}
    try:
        text = src.health_summary({"workbuddy": {"enabled": True}}, agent_rows=[])
    finally:
        src.workbuddy.status = orig
    assert "看板" in text and "999" in text


def test_health_summary_ignores_disabled_workbuddy():
    """未启用的看板不告警：默认就是关闭，报"无数据"纯属噪音。"""
    import kindle_mcp_dashboard.sources as src
    orig = src.workbuddy.status
    src.workbuddy.status = lambda: {"state": "empty", "age": None}
    try:
        text = src.health_summary({"workbuddy": {"enabled": False}}, agent_rows=[])
    finally:
        src.workbuddy.status = orig
    assert "看板" not in text


def test_adapter_exception_reports_basis():
    """适配器炸了的那一行要带异常类型，后台能区分故障与空闲。"""
    from kindle_mcp_dashboard import plugins

    def boom():
        raise RuntimeError("x")

    plugins.register("Boom", boom)
    try:
        rows = plugins.collect_all()
        row = next(r for r in rows if r.agent == "Boom")
        assert "RuntimeError" in row.basis
    finally:
        plugins._adapters.pop("Boom", None)


def test_antigravity_prunes_node_modules(tmp_path, monkeypatch):
    """node_modules 里的同名目录不能被采进来（剪枝不改变结果集）。"""
    from kindle_mcp_dashboard.plugins import antigravity
    root = tmp_path / ".gemini" / "antigravity"
    (root / "conversations").mkdir(parents=True)
    (root / "conversations" / "a.db").write_bytes(b"x")
    junk = root / "node_modules" / "pkg" / "conversations"
    junk.mkdir(parents=True)
    (junk / "b.db").write_bytes(b"x")
    monkeypatch.setattr(antigravity, "GEMINI_ROOT", tmp_path / ".gemini")
    files = antigravity._candidate_files()
    assert [f.name for f in files] == ["a.db"]


def test_opencode_caches_query_by_db_mtime(tmp_path, monkeypatch):
    """db 没变化时第二次采集不再跑 group by 全表扫描。"""
    import sqlite3
    from kindle_mcp_dashboard.plugins import opencode
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("create table session_v2 (id text, directory text, title text, time_updated int)")
    con.execute("create table session_message (session_id text, time_updated int)")
    con.execute("insert into session_v2 values ('s', '/demo', 't', ?)",
                (int(time.time() * 1000),))
    con.commit()
    con.close()
    monkeypatch.setattr(opencode, "OPENCODE_DB", db)
    opencode._db_cache.update({"key": None, "row": None})
    calls = []
    orig = opencode._newest

    def counting(con):
        calls.append(1)
        return orig(con)

    monkeypatch.setattr(opencode, "_newest", counting)
    opencode.state()
    opencode.state()
    assert len(calls) == 1


def test_workbuddy_refuses_remote_http():
    """密码绝不经明文发往非回环地址；远程必须 https。"""
    from kindle_mcp_dashboard import workbuddy
    assert workbuddy._safe_base("http://127.0.0.1:8788")
    assert workbuddy._safe_base("https://board.example.com")
    assert not workbuddy._safe_base("http://board.example.com")
    settings = {"workbuddy": {"url": "http://board.example.com", "enabled": True}}
    assert "https" in workbuddy.current(settings, fetch=True)