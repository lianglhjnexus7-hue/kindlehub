"""HTTP 层测试。

覆盖只读显示、本机管理接口、路径穿越防护与写入校验。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from kindle_mcp_dashboard import agents
from kindle_mcp_dashboard.defaults import DEFAULT_DASHBOARD


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("KINDLE_DASHBOARD_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("KINDLEHUB_SECRETS", str(tmp_path / "secrets.json"))
    from kindle_mcp_dashboard.web import make_server
    httpd = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(port: int, path: str) -> tuple[int, bytes, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def send(port: int, path: str, method: str, payload) -> tuple[int, bytes]:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_health(server):
    status, body, _ = get(server, "/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_display_page_refreshes_and_is_webkit533_safe(server):
    """页面必须：有 meta refresh 兜底 + JS 增强 + 不用 object-fit。"""
    status, body, headers = get(server, "/kindle")
    html = body.decode()
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert 'http-equiv="refresh"' in html
    assert "object-fit" not in html                # WebKit 533 不支持
    # 浏览器模式要请求不带自绘状态栏的图（Kindle 自己有系统状态栏，重复不好看）
    assert 'src="/screen.png?bar=0"' in html


def test_page_js_is_es3_safe_for_webkit533(server):
    """JS 只能用 Safari 5.0 时代的 API，不能出现现代语法。"""
    _, body, _ = get(server, "/kindle")
    html = body.decode()
    assert "<script>" in html
    # addEventListener 允许出现，但必须先 typeof 探测再调用
    if "addEventListener(" in html:
        assert 'typeof window.addEventListener === "function"' in html, \
            "addEventListener 未做能力探测就直接调用"
    for banned in ("classList", "=>", "fetch(", "const ", "let "):
        assert banned not in html, f"JS 用了 WebKit 533 不支持的 {banned}"
    # 必须做能力探测并在失败时回退
    assert "attachEvent" in html
    assert "getElementsByTagName" in html
    # 局部刷新要绕过渲染缓存，否则拿到的还是同一张图
    assert "cache=0" in html


def test_device_report_endpoint_accepts_probe(server):
    """设备能力上报：返回 1x1 GIF，不落错误。"""
    status, body, headers = get(server, "/api/device?js=1&vp=600x800&dpr=1")
    assert status == 200
    assert headers["Content-Type"] == "image/gif"
    assert body.startswith(b"GIF89a")


def test_meta_exposes_device_reports(server):
    _, body, _ = get(server, "/api/meta")
    assert "device_reports" in json.loads(body)


def test_screen_png_is_native_panel_size(server):
    from io import BytesIO
    from PIL import Image
    status, body, headers = get(server, "/screen.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    image = Image.open(BytesIO(body))
    assert image.size == (DEFAULT_DASHBOARD["device"]["width"],
                          DEFAULT_DASHBOARD["device"]["height"])
    assert image.mode == "L"


def test_state_is_readable_locally(server):
    status, body, _ = get(server, "/api/state")
    assert status == 200
    assert json.loads(body)["device"]["width"] == 600


def test_meta_exposes_health_and_lan_ip(server):
    status, body, _ = get(server, "/api/meta")
    data = json.loads(body)
    assert status == 200
    assert "lan_ip" in data and "port" in data
    assert set(data["health"]) == {"weather", "workbuddy"}
    assert data["secrets_set"]["workbuddy_password"] is False


def test_agents_endpoint_exposes_rows_and_evidence(server):
    """采集调试端点：每行带状态/置信度/依据，供后台「Agent 采集状态」区显示。"""
    status, body, _ = get(server, "/api/agents")
    assert status == 200
    data = json.loads(body)
    assert isinstance(data["agents"], list) and data["agents"]
    for row in data["agents"]:
        assert {"agent", "status", "confidence", "basis"} <= set(row)
    assert "cache_age_seconds" in data and "collect_ttl" in data


def test_agents_endpoint_force_bypasses_cache(server):
    status, body, _ = get(server, "/api/agents?force=1")
    assert status == 200
    data = json.loads(body)
    assert data["cache_age_seconds"] is not None
    assert data["cache_age_seconds"] < agents.COLLECT_TTL * 2


def test_static_assets_are_whitelisted(server):
    assert get(server, "/static/admin.js")[0] == 200
    assert get(server, "/static/admin.css")[0] == 200
    # 白名单外一律 404，包括路径穿越
    assert get(server, "/static/../web.py")[0] == 404
    assert get(server, "/static/secrets.json")[0] == 404


def test_unknown_path_404(server):
    assert get(server, "/nope")[0] == 404


def test_put_rejects_invalid_json(server):
    status, _ = send(server, "/api/state", "PUT", b"{not json")
    assert status == 400


def test_put_rejects_out_of_bounds_region(server):
    bad = json.loads(json.dumps(DEFAULT_DASHBOARD))
    bad["screens"][0]["regions"][0]["w"] = 5000
    status, body = send(server, "/api/state", "PUT", bad)
    assert status == 400
    assert "canvas" in json.loads(body)["error"]


def test_put_saves_valid_state(server):
    good = json.loads(json.dumps(DEFAULT_DASHBOARD))
    good["refresh_seconds"] = 120
    status, _ = send(server, "/api/state", "PUT", good)
    assert status == 200
    _, body, _ = get(server, "/api/state")
    assert json.loads(body)["refresh_seconds"] == 120


def test_secrets_rejects_unknown_key(server):
    status, _ = send(server, "/api/secrets", "POST", {"key": "aws_key", "value": "x"})
    assert status == 400


def test_preview_returns_png_for_draft(server):
    draft = json.loads(json.dumps(DEFAULT_DASHBOARD))
    status, body = send(server, "/api/preview", "POST", draft)
    assert status == 200
    assert body.startswith(b"\x89PNG")


def test_screen_png_survives_hostile_battery_param(server):
    """batt=²（isdigit 放行但 int 拒收）与超长数字串都不能把渲染打崩。"""
    import urllib.parse
    for batt in ("²", "９９", "9" * 5000):
        status, body, _ = get(server, "/screen.png?batt=" + urllib.parse.quote(batt))
        assert status == 200, f"batt={batt[:10]!r} 导致 {status}"
        assert body.startswith(b"\x89PNG")


def test_admin_rejects_foreign_host_header(server):
    """DNS rebinding 防护：来源虽是回环，Host 不是本机名也要拒绝。"""
    request = urllib.request.Request(f"http://127.0.0.1:{server}/api/state",
                                     headers={"Host": "evil.example"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 403


def test_write_rejects_form_content_type(server):
    """跨站表单只能发 urlencoded/text/plain，必须被 Content-Type 校验挡住。"""
    body = json.dumps(DEFAULT_DASHBOARD).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server}/api/state", data=body, method="PUT",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 400


def test_meta_exposes_zstd_and_relay(server):
    _, body, _ = get(server, "/api/meta")
    data = json.loads(body)
    assert "zstd_available" in data
    assert {"enabled", "path", "last"} <= set(data["relay"])


def test_relay_generates_random_path_on_first_enable(tmp_path, monkeypatch):
    """首次启用中继必须自动生成不可猜测的 capability 路径并持久化。"""
    from kindle_mcp_dashboard import gist_relay, web
    from kindle_mcp_dashboard.store import DashboardStore
    store = DashboardStore(tmp_path / "state.json")
    data = store.read()
    data["settings"]["relay"] = {"enabled": True, "repo": "owner/repo"}
    store.write(data)
    monkeypatch.setattr(gist_relay, "publish", lambda png, cfg: "ok " + cfg["path"])
    monkeypatch.setattr(gist_relay, "last_publish_time", lambda cfg: None)
    monkeypatch.setattr(web, "render_png", lambda data, fetch=False: b"\x89PNG fake")
    web._relay_state.update({"at": 0.0, "last": ""})
    web._publish_relay(store)
    saved = store.read()["settings"]["relay"]
    assert saved["path"].startswith("relay/") and saved["path"].endswith(".png")
    assert len(saved["path"]) >= len("relay/") + 16 + len(".png")
    assert saved["path"] != "relay/screen.png"
    # 再次发布不能重新生成路径（Kindle 侧的 URL 必须稳定）
    web._relay_state.update({"at": 0.0, "last": ""})
    web._publish_relay(store)
    assert store.read()["settings"]["relay"]["path"] == saved["path"]


def test_gist_relay_refuses_guessable_path(monkeypatch):
    """没配置随机路径时拒绝发布，而不是回退到可猜的 screen.png。"""
    from kindle_mcp_dashboard import gist_relay
    monkeypatch.setattr(gist_relay, "gh_token", lambda: "token")
    assert gist_relay.raw_url({"repo": "owner/repo"}) == ""
    msg = gist_relay.publish(b"png", {"enabled": True, "repo": "owner/repo"})
    assert "path" in msg


def test_is_guessable_path():
    """常见可猜名字判为不安全；随机 capability 路径与空路径不误判。"""
    from kindle_mcp_dashboard import gist_relay
    for bad in ("screen.png", "relay/screen.png", "Dashboard.PNG", "frame.png"):
        assert gist_relay.is_guessable_path(bad), bad
    for ok in ("relay/0123456789abcdef.png", "", "relay/custom-name.png"):
        assert not gist_relay.is_guessable_path(ok), ok


def test_relay_migrates_legacy_guessable_path(tmp_path, monkeypatch):
    """遗留的可猜路径（screen.png）必须被迁移成随机 capability 路径并持久化。"""
    from kindle_mcp_dashboard import gist_relay, web
    from kindle_mcp_dashboard.store import DashboardStore
    store = DashboardStore(tmp_path / "state.json")
    data = store.read()
    data["settings"]["relay"] = {"enabled": True, "repo": "owner/repo", "path": "screen.png"}
    store.write(data)
    monkeypatch.setattr(gist_relay, "publish", lambda png, cfg: "ok " + cfg["path"])
    monkeypatch.setattr(gist_relay, "last_publish_time", lambda cfg: None)
    monkeypatch.setattr(web, "render_png", lambda data, fetch=False: b"\x89PNG fake")
    web._relay_state.update({"at": 0.0, "last": ""})
    web._publish_relay(store)
    saved = store.read()["settings"]["relay"]
    assert saved["path"].startswith("relay/") and saved["path"].endswith(".png")
    assert not gist_relay.is_guessable_path(saved["path"])
    assert len(saved["path"]) >= len("relay/") + 16 + len(".png")
    # 迁移后 URL 必须稳定，不能每次刷新都换（Kindle 侧要能持续拉到）
    web._relay_state.update({"at": 0.0, "last": ""})
    web._publish_relay(store)
    assert store.read()["settings"]["relay"]["path"] == saved["path"]
