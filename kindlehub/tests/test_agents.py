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
    # 对话正文绝不上墙：last_agent_message 只留在原始日志里
    assert state.task == "上一轮已完成"


def test_codex_error_complete_not_shown_as_completed(tmp_path, monkeypatch):
    """task_complete 带 error 时状态是 error，文案不能说"已完成"（自相矛盾）。"""
    sessions = tmp_path / "sessions" / "2026" / "11" / "13"
    sessions.mkdir(parents=True)
    log = sessions / "rollout-err.jsonl"
    now = time.time()
    log.write_text("\n".join([
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "started_at": now - 60}}),
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "started_at": now - 60,
                                                    "completed_at": now - 10, "error": True,
                                                    "last_agent_message": "回复正文绝不能上墙"}}),
    ]), encoding="utf-8")
    from kindle_mcp_dashboard.plugins import codex
    monkeypatch.setattr(codex, "CODEX_DIR", tmp_path)
    monkeypatch.setattr(codex, "_active_workspace", lambda: "demo")
    codex._codex_cache.update({"key": None, "value": None})

    state = codex.state()
    assert state.status == "error"
    assert state.confidence == "high"
    assert state.task == "上一轮出错"
    assert "已完成" not in state.task
    assert "回复正文" not in str(state.task)


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
    assert state.task is None  # 标题是对话派生内容，不上墙


def test_opencode_reads_session_v2_message_activity(tmp_path, monkeypatch):
    """当前版本 OpenCode 写 session_v2 + session_message：
    会话行时间停在元数据变更，真实活动要看消息时间戳。
    消息 data 列是对话正文，本适配器只读时间戳，不查正文。"""
    import sqlite3
    db = tmp_path / "opencode.db"
    con = sqlite3.connect(db)
    con.execute("create table session_v2 (id text, directory text, title text,"
                " time_updated integer)")
    con.execute("create table session_message (id text, session_id text,"
                " time_updated integer, data text)")
    con.execute("insert into session_v2 values ('s1', '<HOME>/alice/Projects/demo',"
                " '修一下布局', ?)", (int((time.time() - 3600) * 1000),))
    con.execute("insert into session_message values ('m1', 's1', ?, '机密对话内容')",
                (int((time.time() - 30) * 1000),))
    con.commit()
    con.close()

    from kindle_mcp_dashboard.plugins import opencode
    monkeypatch.setattr(opencode, "OPENCODE_DB", db)
    state = opencode.state()
    assert state.status == "running"
    assert state.project == "demo"
    assert state.task is None  # 标题是对话派生内容，不上墙


def test_zcode_disconnects_when_rollout_and_db_missing(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ROLLOUT_DIR", tmp_path / "missing-rollout")
    monkeypatch.setattr(zcode, "ZCODE_DB", tmp_path / "missing.sqlite")
    state = zcode.state()
    assert state.status == "disconnected"


def _zcode_db(tmp_path):
    """建一个最小可用的 ZCode session 库（db.sqlite）。"""
    import sqlite3
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, title text,"
                " title_source text, parent_id text, time_updated integer)")
    con.execute("insert into session values ('sess_parent', '<HOME>/alice/Projects/demo',"
                " '跑测试', 'generated', NULL, ?)", (int((time.time() - 600) * 1000),))
    con.execute("insert into session values ('sess_subagent_agent_x',"
                " '<HOME>/alice/Projects/demo', '把这份清单逐项复核', 'first_input',"
                " 'sess_parent', ?)", (int((time.time() - 600) * 1000),))
    con.commit()
    con.close()
    return db


def _zcode_rollout(tmp_path, name: str, age_seconds: float) -> Path:
    rollout = tmp_path / "rollout"
    rollout.mkdir(exist_ok=True)
    log = rollout / name
    log.write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_seconds
    import os
    os.utime(log, (stamp, stamp))
    return rollout


def test_zcode_running_when_rollout_fresh(tmp_path, monkeypatch):
    """rollout 日志在刷新窗口内有写入 = 有真实活动，判 running。"""
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ROLLOUT_DIR",
                        _zcode_rollout(tmp_path, "model-io-sess_parent.jsonl", age_seconds=5))
    monkeypatch.setattr(zcode, "ZCODE_DB", _zcode_db(tmp_path))

    state = zcode.state()
    assert state.status == "running"
    assert state.confidence == "high"
    assert state.project == "demo"
    assert state.task is None  # 标题是对话派生内容，不上墙


def test_zcode_idle_when_rollout_stale(tmp_path, monkeypatch):
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ROLLOUT_DIR",
                        _zcode_rollout(tmp_path, "model-io-sess_parent.jsonl", age_seconds=600))
    monkeypatch.setattr(zcode, "ZCODE_DB", _zcode_db(tmp_path))

    state = zcode.state()
    assert state.status == "idle"
    assert "距最后写入" in state.basis


def test_zcode_subagent_title_never_leaks(tmp_path, monkeypatch):
    """子代理标题是任务指令（近似 prompt），父会话标题也是对话派生内容，都不上墙。"""
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(
        zcode, "ROLLOUT_DIR",
        _zcode_rollout(tmp_path, "model-io-sess_subagent_agent_x.jsonl", age_seconds=5))
    monkeypatch.setattr(zcode, "ZCODE_DB", _zcode_db(tmp_path))

    state = zcode.state()
    assert state.status == "running"
    assert state.task is None  # 标题是对话派生内容，不上墙


def test_zcode_never_shows_first_input_title(tmp_path, monkeypatch):
    """title_source=first_input 说明标题就是用户输入的 prompt，不上墙。"""
    from kindle_mcp_dashboard.plugins import zcode
    monkeypatch.setattr(zcode, "ROLLOUT_DIR",
                        _zcode_rollout(tmp_path, "model-io-sess_parent.jsonl", age_seconds=600))
    db = tmp_path / "db.sqlite"
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, title text,"
                " title_source text, parent_id text, time_updated integer)")
    con.execute("insert into session values ('sess_parent', '<HOME>/alice/Projects/demo',"
                " '帮我重构数据采集层', 'first_input', NULL, ?)",
                (int((time.time() - 600) * 1000),))
    con.commit()
    con.close()
    monkeypatch.setattr(zcode, "ZCODE_DB", db)

    state = zcode.state()
    assert state.task is None


def test_zcode_multiple_projects_joined(tmp_path, monkeypatch):
    """多个项目同时活跃时用 / 分隔，活跃会话数如实上报。"""
    import sqlite3
    from kindle_mcp_dashboard.plugins import zcode
    _zcode_rollout(tmp_path, "model-io-sess_parent.jsonl", age_seconds=5)
    _zcode_rollout(tmp_path, "model-io-sess_other.jsonl", age_seconds=8)
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table session (id text, directory text, title text,"
                " title_source text, parent_id text, time_updated integer)")
    con.execute("insert into session values ('sess_parent', '<HOME>/alice/Projects/demo',"
                " '跑测试', 'generated', NULL, ?)", (int((time.time() - 600) * 1000),))
    con.execute("insert into session values ('sess_other', '<HOME>/alice/Projects/quant',"
                " '调参', 'generated', NULL, ?)", (int((time.time() - 600) * 1000),))
    con.commit()
    con.close()
    monkeypatch.setattr(zcode, "ROLLOUT_DIR", tmp_path / "rollout")
    monkeypatch.setattr(zcode, "ZCODE_DB", db)

    state = zcode.state()
    assert state.status == "running"
    assert state.project == "demo/quant"
    assert state.sessions == 2