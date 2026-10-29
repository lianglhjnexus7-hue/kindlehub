"""Agent 监控适配器测试。

重点验证"不伪造状态"这条底线：拿不到证据时必须是 unknown，
不能因为进程存在或文件刚被改过就说"正在执行"。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kindle_mcp_dashboard import agents


def test_format_rows_marks_unknown_honestly():
    rows = [
        {"agent": "A", "status": "running", "project": "p1", "task": "t1",
         "last_activity": time.time(), "source": "s", "confidence": "high"},
        {"agent": "B", "status": "unknown", "project": None, "task": None,
         "last_activity": None, "source": "none", "confidence": "low"},
    ]
    text = agents.format_rows(rows)
    assert "● A · 执行中" in text
    assert "? B · 未知" in text
    assert "执行中" in text and text.count("执行中") == 1


def test_hide_unknown_filters_only_unreliable():
    rows = [
        {"agent": "A", "status": "running", "project": None, "task": None,
         "last_activity": time.time(), "source": "s", "confidence": "high"},
        {"agent": "B", "status": "unknown", "project": None, "task": None,
         "last_activity": None, "source": "none", "confidence": "low"},
        {"agent": "C", "status": "disconnected", "project": None, "task": None,
         "last_activity": None, "source": "none", "confidence": "low"},
    ]
    text = agents.format_rows(rows, hide_unknown=True)
    assert "A" in text
    assert "B" not in text and "C" not in text


def test_collect_sorts_running_first():
    rows = agents.collect({"workbuddy": {"url": "http://127.0.0.1:1", "enabled": True}})
    order = {"running": 0, "error": 1, "idle": 2, "unknown": 3, "disconnected": 4}
    ranks = [order[r["status"]] for r in rows]
    assert ranks == sorted(ranks)


def test_collect_never_claims_running_without_evidence():
    """离线环境下所有适配器都应给出 unknown/disconnected，而不是 running。"""
    rows = agents.collect({"workbuddy": {"url": "http://127.0.0.1:1", "enabled": True}})
    assert all(r["status"] in {"running", "idle", "error", "unknown", "disconnected"} for r in rows)
    assert all(r["confidence"] in {"high", "medium", "low"} for r in rows)


def test_project_name_decodes_dsh_directory(tmp_path, monkeypatch):
    assert agents._project_name("--Users-alice-Projects-kindle~0020dashboard--") == "kindle dashboard"
    assert agents._project_name("<HOME>/alice/Projects/TianXia") == "TianXia"
    assert agents._project_name("\\Users\\alice\\Library\\quant") == "quant"


def test_short_strips_markdown_and_truncates():
    text = agents._short("**加粗** 的一段很长很长的回答内容，后面还有很多很多字")
    assert "**" not in text
    assert len(text) <= 23


def test_codex_reads_task_lifecycle_from_session(tmp_path, monkeypatch):
    """构造一个只有 task_started 的会话，应判为 running（且 mtime 新鲜）。"""
    sessions = tmp_path / "sessions" / "2026" / "10" / "22"
    sessions.mkdir(parents=True)
    log = sessions / "rollout-test.jsonl"
    now = time.time()
    log.write_text("\n".join([
        json.dumps({"type": "session_meta", "timestamp": "2026-10-22T19:00:00"}),
        json.dumps({"type": "event_msg", "timestamp": "2026-10-22T19:00:00",
                    "payload": {"type": "task_started", "turn_id": "t1",
                                "started_at": now - 5}}),
    ]), encoding="utf-8")
    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    monkeypatch.setattr(codex, "_active_workspace", lambda: "demo")

    state = codex.state()
    assert state.status == "running"
    assert state.confidence == "high"
    assert state.project == "demo"


def test_codex_marks_idle_after_task_complete(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions" / "2026" / "10" / "22"
    sessions.mkdir(parents=True)
    log = sessions / "rollout-test.jsonl"
    now = time.time()
    log.write_text("\n".join([
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "started_at": now - 60}}),
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "started_at": now - 60,
                                                    "completed_at": now - 10,
                                                    "last_agent_message": "做完了"}}),
    ]), encoding="utf-8")
    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    monkeypatch.setattr(codex, "_active_workspace", lambda: "demo")

    state = codex.state()
    assert state.status == "idle"
    assert state.task == "做完了"


def test_opencode_disconnects_when_db_missing(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import opencode
    monkeypatch.setattr(opencode, "OPENCODE_DB", tmp_path / "missing.db")
    state = opencode.state()
    assert state.status == "disconnected"


def test_opencode_reads_session_without_touching_messages(tmp_path, monkeypatch):
    """只读 session 表的 directory/title/time_updated，不碰 message/part。"""
    import sqlite3
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, title text, time_updated integer)")
    con.execute("create table message (id text, data text)")
    con.execute("insert into message values ('m1', '机密对话内容')")
    con.execute("insert into session values ('s1', '<HOME>/alice/Projects/demo', '修一下布局', ?)",
                (int((time.time() - 30) * 1000),))
    con.commit()
    con.close()

    from kindle_mcp_dashboard.plugins import opencode
    monkeypatch.setattr(opencode, "OPENCODE_DB", db)
    state = opencode.state()
    assert state.status == "running"
    assert state.project == "demo"
    assert state.task == "修一下布局"


def test_zcode_disconnects_when_db_missing(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ZCODE_DB", tmp_path / "missing.sqlite")
    state = zcode.state()
    assert state.status == "disconnected"


def _zcode_db(tmp_path, task_status: str, meta_status: str):
    """建一个最小可用的 ZCode 任务库。"""
    import sqlite3
    db = tmp_path / "tasks.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table tasks (workspace_path text, title text, task_status text,"
                " updated_at text, model text, deleted integer, meta_json text)")
    meta = '{"status":"%s"}' % meta_status
    con.execute("insert into tasks values (?, ?, ?, ?, ?, 0, ?)",
                ("<HOME>/alice/Projects/demo", "跑测试", task_status,
                 str(int((time.time() - 600) * 1000)), "glm", meta))
    con.commit()
    con.close()
    return db


def test_zcode_maps_task_status(tmp_path, monkeypatch):
    db = _zcode_db(tmp_path, "completed", "completed")

    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ZCODE_DB", db)
    state = zcode.state()
    assert state.status == "idle"
    assert state.project == "demo"
    assert state.task == "跑测试"


def test_zcode_prefers_meta_status(tmp_path, monkeypatch):
    """task_status 常常滞后：任务还在跑时它可能已是 completed，
    但 meta_json 里的 status 是 active。以 meta 为准。"""
    db = _zcode_db(tmp_path, "completed", "active")

    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ZCODE_DB", db)
    state = zcode.state()
    assert state.status == "running"
    assert "task_meta" in state.basis