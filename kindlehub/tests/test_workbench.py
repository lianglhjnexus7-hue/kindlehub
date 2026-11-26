"""工作台摘要数据源（workbench）的回归测试。"""
from __future__ import annotations

import io
import json
from copy import deepcopy

import pytest
from PIL import Image

from kindle_mcp_dashboard import sources, workbench
from kindle_mcp_dashboard.defaults import DEFAULT_DASHBOARD
from kindle_mcp_dashboard.renderer import render_png
from kindle_mcp_dashboard.schema import validate_dashboard


def _reset():
    workbench._cache.update({"data": None, "at": -1e9, "error": ""})


def _stub(monkeypatch, payload, seen=None):
    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    body = json.dumps(payload).encode("utf-8")

    def open_url(req, timeout=None):
        if seen is not None:
            seen.append((req.full_url, timeout))
        return Resp(body)

    monkeypatch.setattr(workbench.urllib.request, "urlopen", open_url)


SAMPLE = {
    "schema_version": 1,
    "state": "partial",
    "updated_at": "2026-11-22 18:42:17",
    "pending_decisions": 3,
    "job_alerts": 2,
    "next_slot": "19:30",
    # 即使上游意外返回额外字段，也不得把任意内容带到设备画面。
    "errors": ["do not display"],
    "name": "do not display",
}


def test_cache_empty_says_not_collected():
    _reset()
    assert workbench.summary(fetch=False) == (
        "尚未采集\n待确认：未知 · 异常任务：未知\n更新时间：未知"
    )
    assert workbench.status()["has"] is False
    assert workbench._cache["at"] == -1e9


def test_render_path_never_goes_online(monkeypatch):
    _reset()

    def boom(*a, **k):
        raise AssertionError("fetch=False 时不该联网")

    monkeypatch.setattr(workbench.urllib.request, "urlopen", boom)
    assert workbench.summary(fetch=False).startswith("尚未采集")


def test_uses_kindle_summary_and_displays_only_approved_fields(monkeypatch):
    _reset()
    seen = []
    _stub(monkeypatch, SAMPLE, seen)
    text = workbench.summary(fetch=True, force=True)

    assert seen == [("http://127.0.0.1:8799/api/kindle-summary?v=2", workbench.TIMEOUT)]
    assert text.splitlines() == [
        "数据不完整",
        "待确认：3 · 异常任务：2",
        "下次排程：19:30",
        "更新时间：2026-11-22 18:42:17",
    ]
    assert "do not display" not in text


def test_ok_state_does_not_repeat_widget_heading(monkeypatch):
    _reset()
    _stub(monkeypatch, dict(SAMPLE, state="ok"))

    assert workbench.summary(fetch=True, force=True).splitlines() == [
        "待确认：3 · 异常任务：2",
        "下次排程：19:30",
        "更新时间：2026-11-22 18:42:17",
    ]


def test_unknown_counts_and_slot_are_not_reported_as_zero_or_none(monkeypatch):
    _reset()
    _stub(monkeypatch, {
        "schema_version": 1,
        "state": "stale",
        "updated_at": None,
        "pending_decisions": None,
        "job_alerts": None,
        "next_slot": None,
    })
    text = workbench.summary(fetch=True, force=True)

    assert text.splitlines() == [
        "数据已过期",
        "更新时间：未知",
    ]
    assert "下次排程" not in text


@pytest.mark.parametrize("state", ["stale", "unavailable"])
def test_stale_or_unavailable_source_suppresses_counts_and_slot(monkeypatch, state):
    _reset()
    payload = dict(SAMPLE, state=state)
    _stub(monkeypatch, payload)

    text = workbench.summary(fetch=True, force=True)
    assert text.splitlines() == [
        workbench._STATE_WARNINGS[state],
        "更新时间：2026-11-22 18:42:17",
    ]
    assert "待确认" not in text
    assert "异常任务" not in text
    assert "下次排程" not in text


def test_invalid_contract_is_unavailable_without_echoing_response(monkeypatch):
    _reset()
    _stub(monkeypatch, {"state": "ok\nprivate", "error": "private message"})
    text = workbench.summary(fetch=True, force=True)

    assert text.startswith("暂不可用")
    assert "private" not in text
    assert "错误" not in text
    assert workbench.status()["error"] == "ValueError"


@pytest.mark.parametrize("field,value", [
    ("pending_decisions", True),
    ("job_alerts", -1),
    ("updated_at", "2026-02-30 12:00:00"),
    ("next_slot", "99:99"),
])
def test_invalid_values_make_summary_unavailable(monkeypatch, field, value):
    _reset()
    payload = {
        "schema_version": 1,
        "state": "ok",
        "updated_at": "2026-11-22 18:42:17",
        "pending_decisions": 0,
        "job_alerts": 0,
        "next_slot": "19:30",
    }
    payload[field] = value
    _stub(monkeypatch, payload)

    assert workbench.summary(fetch=True, force=True).startswith("暂不可用")


def test_request_failure_says_unavailable(monkeypatch):
    _reset()

    def fail(*a, **k):
        raise OSError("private network detail")

    monkeypatch.setattr(workbench.urllib.request, "urlopen", fail)
    text = workbench.summary(fetch=True, force=True)
    assert text.startswith("暂不可用")
    assert "private network detail" not in text
    assert workbench.status()["error"] == "OSError"


def test_cached_value_survives_within_ttl(monkeypatch):
    _reset()
    _stub(monkeypatch, SAMPLE)
    workbench.summary(fetch=True, force=True)
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        raise OSError("should not be called")

    monkeypatch.setattr(workbench.urllib.request, "urlopen", counting)
    text = workbench.summary(fetch=True)
    assert "待确认：3 · 异常任务：2" in text
    assert calls["n"] == 0


def test_stale_in_memory_cache_is_marked_without_fetching(monkeypatch):
    _reset()
    workbench._cache.update({
        "data": workbench._decode(SAMPLE),
        "at": 100.0,
        "error": "",
    })
    monkeypatch.setattr(workbench.time, "monotonic",
                        lambda: 100.0 + workbench.CACHE_STALE_AFTER + 1)

    def boom(*a, **k):
        raise AssertionError("fetch=False 时不该联网")

    monkeypatch.setattr(workbench.urllib.request, "urlopen", boom)
    text = workbench.summary(fetch=False)
    assert text.splitlines() == [
        "本机缓存已过期",
        "更新时间：2026-11-22 18:42:17",
    ]
    assert "待确认" not in text
    assert "异常任务" not in text
    assert "下次排程" not in text


def test_compact_summary_fits_kt2_canvas_without_network():
    dashboard = deepcopy(DEFAULT_DASHBOARD)
    dashboard["screens"][0]["regions"].append({
        "id": "workbench",
        "kind": "text",
        "x": 20,
        "y": 1040,
        "w": 960,
        "h": 210,
        "label": "WORKBENCH",
        "source": "workbench.status",
        "size": 17,
    })
    checked = validate_dashboard(dashboard)
    values = {"workbench.status": "\n".join([
        "数据不完整",
        "待确认：3 · 异常任务：2",
        "下次排程：19:30",
        "更新时间：2026-11-22 18:42:17",
    ])}

    png = render_png(checked, values=values, fetch=False)
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (600, 800)


def test_snapshot_exposes_workbench_status_without_fetching(monkeypatch):
    _reset()
    monkeypatch.setattr(sources.agents, "collect", lambda *a, **k: [])
    values = sources.snapshot(fetch=False)
    assert "workbench.status" in values
    assert "workbench.k3b" in values
    assert isinstance(values["workbench.status"], str)


SAMPLE_V2 = {
    "schema_version": 2,
    "state": "ok",
    "updated_at": "2026-11-26 14:45:00",
    "pending_decisions": 2,
    "job_alerts": 2,
    "next_slot": "14:55",
    "attention": {
        "pending_decisions": 2,
        "pending_handoffs": 5,
        "services_ok": 7,
        "services_total": 7,
        "services_down": [],
        "job_alerts": 2,
        "alerts": [],
    },
    "agents": [
        {"agent": "Antigravity", "status": "running", "confidence": "low", "project": "本机工作台", "ago_seconds": 8},
        {"agent": "Codex", "status": "waiting", "confidence": "high", "project": "墨水屏看板", "ago_seconds": 180},
        {"agent": "OpenCode", "status": "idle", "confidence": "low", "project": "知识库", "ago_seconds": 840},
        {"agent": "ZCode", "status": "idle", "confidence": "medium", "project": "量化交易系统", "ago_seconds": 2520},
    ],
    "projects": {"total": 14, "active": 6, "verify": 5, "done": 3},
    "channels": [
        {"name": "小浣熊", "online": True, "status_text": "2 账号在线"},
        {"name": "Loomy", "online": True, "status_text": "在线正常"},
        {"name": "Trae", "online": True, "status_text": "令牌剩 9.4 天"},
    ],
    "quota": {
        "available": True,
        "calls": 540,
        "total_tokens_human": "5934.4万",
        "top_models": [
            {"model": "kimi-k3.1-pro", "calls": 57, "tokens_human": "2195.5万", "share_pct": 37},
            {"model": "deepseek-v4.1-flash", "calls": 117, "tokens_human": "1450.6万", "share_pct": 24},
            {"model": "glm-5.3-flash", "calls": 123, "tokens_human": "1238.3万", "share_pct": 21},
        ],
        "accounts_ready": 7,
        "accounts_total": 9,
        "checkin_done": 12,
        "checkin_total": 12,
        "free_tokens_unclaimed_count": 15,
        "free_tokens_unclaimed_rmb": 1130,
    },
}


def test_v2_decode_and_path_rejection(monkeypatch):
    _reset()
    _stub(monkeypatch, SAMPLE_V2)
    snap = workbench.snapshot_data(fetch=True, force=True)
    assert snap["collected"] is True
    assert snap["cache_stale"] is False
    assert snap["data"]["projects"] == {"total": 14, "active": 6, "verify": 5, "done": 3}
    assert snap["data"]["quota"]["free_tokens_unclaimed_rmb"] == 1130

    bad = deepcopy(SAMPLE_V2)
    bad["agents"][0]["project"] = "/" + "Users/alice/secret"
    _stub(monkeypatch, bad)
    snap_bad = workbench.snapshot_data(fetch=True, force=True)
    assert snap_bad["data"]["state"] == "unavailable"
    assert snap_bad["data"]["agents"] is None


def test_k3b_render_eink_constraints_and_credits_branches():
    from kindle_mcp_dashboard.defaults import build_k3b_dashboard
    from kindle_mcp_dashboard.renderer import render_dashboard

    cfg = validate_dashboard(build_k3b_dashboard(DEFAULT_DASHBOARD))
    base_values = {
        "system.time": "14:08",
        "weather.current": "27℃ 多云",
        "system.battery": 88,
        "system.charging": True,
        "system.client_version": "1.13.1",
    }

    # 1) 带国内积分池分支
    values_with_credits = dict(base_values)
    values_with_credits["workbench.k3b"] = {
        "summary": {"collected": True, "cache_stale": False, "data": workbench._decode(SAMPLE_V2)},
        "credits": {"realms": {"cn": {"remain": 16890.0, "size": 22637.0, "accounts": 9}}},
        "weather_city": "湛江",
        "relay_status": "中继正常",
    }
    im = render_dashboard(cfg, values=values_with_credits, fetch=False)
    assert im.size == (600, 800)
    assert im.mode == "L"
    assert len(set(im.get_flattened_data())) <= 16

    # 右上角 210px 系统栏避让区（y < 38）必须 100% 纯白 255
    reserved = im.crop((390, 0, 600, 38))
    assert set(reserved.get_flattened_data()) == {255}

    # 抗吞色核验：顶部黑底横幅与面板标题条为纯黑 0；KPI 框内空白处为纯白 255；
    # 双层黑框内隙为 255，斜纹区仅由纯黑 0 与纯白 255 构成
    assert im.getpixel((300, 65)) == 0
    assert im.getpixel((570, 52)) == 0
    assert im.getpixel((300, 181)) == 0
    assert im.getpixel((50, 92)) == 255
    assert im.getpixel((24, 311)) == 255
    hatch_pixels = set(im.crop((280, 313, 390, 320)).get_flattened_data())
    assert hatch_pixels <= {0, 255} and len(hatch_pixels) == 2

    # 2) 无积分池密码时优雅回退 WB 账号池（7/9 就绪）
    values_fallback = dict(base_values)
    values_fallback["workbench.k3b"] = {
        "summary": {"collected": True, "cache_stale": False, "data": workbench._decode(SAMPLE_V2)},
        "credits": None,
        "weather_city": "湛江",
        "relay_status": "本机直连",
    }
    im2 = render_dashboard(cfg, values=values_fallback, fetch=False)
    assert im2.size == (600, 800)

    # 3) stale / unavailable 状态压制明细且不崩溃
    for bad_state in ("stale", "unavailable"):
        bad_payload = dict(SAMPLE_V2, state=bad_state, attention=None, agents=None,
                           projects=None, channels=None, quota=None)
        values_bad = dict(base_values)
        values_bad["workbench.k3b"] = {
            "summary": {"collected": True, "cache_stale": False, "data": workbench._decode(bad_payload)},
            "credits": None,
            "weather_city": "湛江",
            "relay_status": "本机直连",
        }
        im_bad = render_dashboard(cfg, values=values_bad, fetch=False)
        assert im_bad.size == (600, 800)


def test_k3b_preset_skips_duplicate_agents_and_analytics_collection(tmp_path, monkeypatch):
    """启用 K3-B 副屏预设时，预热与渲染链路不得再重复触发 agents.collect 与 140KB analytics。"""
    from kindle_mcp_dashboard import web, workbuddy
    from kindle_mcp_dashboard.defaults import build_k3b_dashboard
    from kindle_mcp_dashboard.store import DashboardStore

    _reset()
    cfg = build_k3b_dashboard(DEFAULT_DASHBOARD)
    cfg["settings"]["workbuddy"] = {"url": "http://127.0.0.1:8788", "enabled": True}
    store = DashboardStore(tmp_path / "kt2.json")
    store.write(cfg)

    def boom_agents(*a, **k):
        raise AssertionError("K3-B 预设下不应调用本地 agents.collect")

    def boom_analytics(*a, **k):
        raise AssertionError("K3-B 预设下不应拉取 140KB workbuddy._load")

    monkeypatch.setattr(sources.agents, "collect", boom_agents)
    monkeypatch.setattr(web.agents, "collect", boom_agents)
    monkeypatch.setattr(workbuddy, "_load", boom_analytics)
    monkeypatch.setattr(web.weather, "current", lambda *a, **k: "27℃ 晴")
    monkeypatch.setattr(workbuddy, "credits", lambda *a, **k: None)
    _stub(monkeypatch, SAMPLE_V2)

    web._warm_once(store)
    png = render_png(store.read(), fetch=False)
    assert png.startswith(b"\x89PNG")
