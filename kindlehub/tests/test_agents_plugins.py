"""新增 Agent 适配器的行为测试。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time

import pytest

from kindle_mcp_dashboard import agents

ZSTD = shutil.which("zstd")
needs_zstd = pytest.mark.skipif(ZSTD is None, reason="未安装 zstd 命令行")


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _write_zst(path, docs):
    """按 Codex 现在的落盘格式写一份压缩会话。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs).encode("utf-8")
    subprocess.run([ZSTD, "-q", "-f", "-o", str(path)], input=raw, check=True)


def _codex_event(kind, **extra):
    return {"timestamp": "2026-11-11T08:00:00Z", "payload": {"type": kind, **extra}}


def test_qoder_stage_running(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import qoder
    monkeypatch.setattr(qoder, "QODER_DIR", tmp_path / "tasks")
    _write(qoder.QODER_DIR / "s1" / "1.json", {
        "businessInfo": {"stage": "processing"},
        "subject": "修 A-1：点城显示资料",
    })
    st = qoder.state()
    assert st.status == "running"
    assert st.confidence == "high"
    assert st.task == "修 A-1：点城显示资料"
    # 绝不把 description（含任务正文）带上墙
    assert st.project is None


def test_qoder_processing_but_stale_is_idle(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import qoder
    monkeypatch.setattr(qoder, "QODER_DIR", tmp_path / "tasks")
    p = qoder.QODER_DIR / "s1" / "1.json"
    _write(p, {"businessInfo": {"stage": "processing"}, "subject": "旧任务"})
    old = time.time() - 10_000
    import os
    os.utime(p, (old, old))
    st = qoder.state()
    # processing 但文件早已不写，降级为空闲而不是一直挂着"执行中"
    assert st.status == "idle"
    assert st.confidence == "medium"


def test_antigravity_never_claims_certainty(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import antigravity
    root = tmp_path / ".gemini" / "antigravity"
    (root / "conversations").mkdir(parents=True)
    (root / "conversations" / "a.db").write_bytes(b"x")
    monkeypatch.setattr(antigravity, "GEMINI_ROOT", tmp_path / ".gemini")
    st = antigravity.state()
    assert st.status == "running"
    assert st.confidence == "low"      # 没有生命周期事件，永远只是推测
    assert st.task is None


def test_claude_code_reads_only_cwd(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import claude
    root = tmp_path / ".claude" / "projects" / "demo"
    root.mkdir(parents=True)
    (root / "s.jsonl").write_text(
        json.dumps({"type": "user", "cwd": "/home/alice/Projects/demo",
                    "message": {"content": "这段对话正文绝不能上墙"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude, "CLAUDE_DIR", tmp_path / ".claude" / "projects")
    st = claude.state()
    assert st.project == "demo"
    assert st.task is None
    assert st.confidence == "low"
    assert "对话正文" not in str(st.basis)


def test_format_rows_shows_all_seven(tmp_path):
    """7 个 Agent 时不能提前折叠，否则屏幕上少人。"""
    rows = [
        {"agent": f"A{i}", "status": "idle", "project": "p",
         "last_activity": time.time(), "confidence": "high", "basis": "", "sessions": 1}
        for i in range(7)
    ]
    text = agents.format_rows(rows)
    for i in range(7):
        assert f"A{i}" in text
    assert "另有" not in text


def test_low_confidence_marked():
    rows = [{"agent": "Guess", "status": "running", "project": "p",
             "last_activity": time.time(), "confidence": "low", "basis": "", "sessions": 1}]
    assert "执行中~" in agents.format_rows(rows)


# ---------------------------------------------------------------- Codex 压缩会话

@needs_zstd
def test_tail_lines_reads_zstd_sessions(tmp_path):
    """Codex 现在写 .jsonl.zst，尾部读取要能穿透压缩，且窗口上限不变。"""
    path = tmp_path / "rollout.jsonl.zst"
    _write_zst(path, [{"i": i} for i in range(100)])
    events = agents._tail_lines(path, limit=5)
    assert [e["i"] for e in events] == [95, 96, 97, 98, 99]


@needs_zstd
def test_codex_reads_compressed_sessions(tmp_path, monkeypatch):
    """只认 .jsonl 会让 Codex 那一格永远是 unknown（真实事故）。"""
    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    path = tmp_path / "sessions" / "2026" / "11" / "11" / "rollout-demo.jsonl.zst"
    _write_zst(path, [_codex_event("task_started", started_at=time.time())])
    st = codex.state()
    assert st.status == "running"
    assert st.confidence == "high"


@needs_zstd
def test_codex_ignores_non_session_files(tmp_path, monkeypatch):
    """半写状态的 .tmp/.bak 不能被当成最新会话读走。"""
    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    day = tmp_path / "sessions" / "2026" / "11" / "11"
    good = day / "rollout-good.jsonl.zst"
    _write_zst(good, [{"timestamp": "x", "payload": {"type": "noop"}}])
    junk = day / "rollout-good.jsonl.zst.tmp"
    junk.write_text(json.dumps(_codex_event("task_started", started_at=time.time())) + "\n",
                    encoding="utf-8")
    stamp = time.time() + 10
    os.utime(junk, (stamp, stamp))
    st = codex.state()
    assert st.last_activity is not None
    assert st.confidence == "low"        # 没被 .tmp 抢走
    assert st.status == "idle"


# ---------------------------------------------------------------- 隐私：标题不上墙

def test_opencode_title_never_leaks_to_wall(tmp_path, monkeypatch):
    """OpenCode 会话标题是对话派生内容，绝不能进 task 上墙（与 DSH 同类）。"""
    from kindle_mcp_dashboard.plugins import opencode
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("create table session_v2 (id text, directory text, title text, time_updated int)")
    con.execute("create table session_message (session_id text, time_updated int)")
    now_ms = int(time.time() * 1000)
    con.execute("insert into session_v2 values (?,?,?,?)",
                ("s1", "/home/alice/Projects/secret-proj", "用户的私密首句 prompt", now_ms))
    con.commit()
    con.close()
    monkeypatch.setattr(opencode, "OPENCODE_DB", db)
    monkeypatch.setattr(opencode, "_db_cache", {"key": None, "row": None})
    st = opencode.state()
    assert st.task is None
    assert st.project == "secret-proj"
    assert "私密" not in agents.format_by_project([st.as_dict()])


def test_zcode_generated_title_never_leaks_to_wall(tmp_path, monkeypatch):
    """title_source=generated/custom 的标题也是对话派生内容，一律不上墙。"""
    from kindle_mcp_dashboard.plugins import zcode
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, title text,"
                " title_source text, parent_id text, time_updated int)")
    now_ms = int(time.time() * 1000)
    con.execute("insert into session values (?,?,?,?,?,?)",
                ("sess_1", "/home/alice/Projects/secret-proj",
                 "模型生成的私密摘要", "generated", None, now_ms))
    con.commit()
    con.close()
    rollout = tmp_path / "rollout"
    rollout.mkdir()
    (rollout / "model-io-sess_1.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(zcode, "ZCODE_DB", db)
    monkeypatch.setattr(zcode, "ROLLOUT_DIR", rollout)
    st = zcode.state()
    assert st.status == "running"        # rollout 刚写入
    assert st.task is None
    assert "私密" not in agents.format_by_project([st.as_dict()])


def test_antigravity_scans_ide_variant(tmp_path, monkeypatch):
    """antigravity-ide 等变体也要扫到，不能只认 antigravity/antigravity-cli。"""
    from kindle_mcp_dashboard.plugins import antigravity
    root = tmp_path / ".gemini" / "antigravity-ide"
    (root / "conversations").mkdir(parents=True)
    (root / "conversations" / "a.db").write_bytes(b"x")
    monkeypatch.setattr(antigravity, "GEMINI_ROOT", tmp_path / ".gemini")
    files = antigravity._candidate_files()
    assert any("antigravity-ide" in str(f) for f in files)
    st = antigravity.state()
    assert st.status == "running"
    assert st.task is None


def test_tail_lines_boundary_does_not_drop_whole_line(tmp_path, monkeypatch):
    """>8MB 退回尾部窗口时，seek 正好落在行首不能丢整行（off-by-one）。"""
    path = tmp_path / "big.jsonl"
    line_len = len('{"i": "0000"}\n')
    lines = ['{"i": "%04d"}\n' % i for i in range(20)]
    path.write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr(agents, "FULL_READ_LIMIT", 10)   # 强制走尾部窗口分支
    chunk = 5 * line_len                                  # start 正好是第 15 行行首
    events = agents._tail_lines(path, limit=400, chunk=chunk)
    assert [e["i"] for e in events] == ["%04d" % i for i in range(15, 20)]

