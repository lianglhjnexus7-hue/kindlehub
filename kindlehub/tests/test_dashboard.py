"""KindleHub 测试。

基线来自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
改动：默认设备改为 KT2 600x800；新增中文渲染、等比映射、像素折行测试。
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from kindle_mcp_dashboard.defaults import DEFAULT_DASHBOARD
from kindle_mcp_dashboard.renderer import _box, _wrap, _font, render_dashboard, render_png
from kindle_mcp_dashboard.schema import DashboardError, canvas_height, validate_dashboard
from kindle_mcp_dashboard.store import DashboardStore

LIVE = {
    "system.time": "15:28",
    "system.date": "周四\n10月22日",
    "system.hostname": "mac",
    "system.cpu_percent": 22,
    "system.memory_percent": 62,
    "system.disk_percent": 58,
}


def test_default_dashboard_targets_kt2():
    data = validate_dashboard(DEFAULT_DASHBOARD)
    assert data["device"]["model"].endswith("(KT2)")
    assert (data["device"]["width"], data["device"]["height"]) == (600, 800)
    assert data["device"]["grayscale"] == 16


def test_default_layout_excludes_external_services():
    """默认布局只能含"开箱即用"的模块，依赖外部服务的必须走可选模块。

    同时 settings 里的 workbuddy 默认关闭——否则 clone 下来第一屏
    就是"模型消耗 · 看板未连接"。
    """
    sources = {r.get("source") for r in DEFAULT_DASHBOARD["screens"][0]["regions"]
               if r.get("source")}
    external = {"weather.current", "workbuddy.usage", "workbuddy.models",
                "workbuddy.models_detail"}
    assert not sources & external, f"默认布局含外部依赖模块: {sources & external}"
    assert DEFAULT_DASHBOARD["settings"]["workbuddy"]["enabled"] is False


def test_render_has_expected_panel_shape():
    image = render_dashboard(DEFAULT_DASHBOARD, LIVE)
    assert image.size == (600, 800)
    assert image.mode == "L"
    assert render_png(DEFAULT_DASHBOARD).startswith(b"\x89PNG")


def test_render_quantizes_to_16_levels():
    image = render_dashboard(DEFAULT_DASHBOARD, LIVE)
    assert len(set(image.get_flattened_data())) <= 16


def test_canvas_height_follows_device_aspect_ratio():
    # 600x800 设备：画布高应为 1000*800/600 ≈ 1333，而非固定的 1000
    assert round(canvas_height({"width": 600, "height": 800})) == 1333
    assert round(canvas_height({"width": 758, "height": 1024})) == 1351


def test_box_uses_uniform_scale():
    """x 与 y 必须使用同一个缩放系数，否则非方形屏幕会被纵向压缩。"""
    scale = 600 / 1000
    assert _box({"x": 0, "y": 0, "w": 1000, "h": 1000}, scale) == (0, 0, 600, 600)
    assert _box({"x": 500, "y": 500, "w": 100, "h": 100}, scale) == (300, 300, 60, 60)
    # 1000 单位高的区域落在 600px 处，而不是被拉伸到 800px
    assert _box({"x": 0, "y": 1000, "w": 100, "h": 100}, scale)[1] == 600


def test_region_at_canvas_bottom_lands_on_screen_bottom():
    """画布高 1333 对应设备高 800：y=1300 的 region 必须落在 780px 附近。"""
    scale = 600 / 1000
    assert _box({"x": 0, "y": 1300, "w": 100, "h": 33}, scale)[1] == 780


def test_render_supports_local_image_region(tmp_path):
    asset = tmp_path / "asset.png"
    from PIL import Image
    Image.new("L", (20, 20), 0).save(asset)
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [{
        "id": "logo",
        "kind": "image",
        "x": 100,
        "y": 100,
        "w": 800,
        "h": 800,
        "image": str(asset),
        "trim": True,
    }]
    data = validate_dashboard(data)
    image = render_dashboard(data)
    assert image.getpixel((300, 300)) == 0


def test_renders_cjk_without_tofu():
    """中文必须渲染出真实字形；字体缺失时会退化成空心方框，墨量接近 0。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "cn", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 400,
         "value": "中文渲染测试", "size": 60}
    ]
    image = render_dashboard(data, LIVE)
    dark = sum(1 for px in image.get_flattened_data() if px < 128)
    assert dark > 500, f"中文可能渲染为方框（墨量仅 {dark}）"


def test_wrap_respects_pixel_width():
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("L", (600, 200), 255))
    font = _font(28)
    text = "这是一段需要自动换行的中文长文本，用来验证折行逻辑是否按真实像素宽度计算"
    lines = _wrap(draw, text, font, 300)
    assert len(lines) > 1
    for line in lines:
        assert draw.textlength(line, font=font) <= 300


def test_store_updates_region_atomically(tmp_path):
    store = DashboardStore(tmp_path / "state.json")
    store.write(DEFAULT_DASHBOARD)
    store.update_region("overview", "agents", {"label": "现在谁在忙"})
    agents_region = next(r for r in store.active_screen()["regions"] if r["id"] == "agents")
    assert agents_region["label"] == "现在谁在忙"


def test_rejects_out_of_bounds_region():
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"][0]["w"] = 2000
    try:
        validate_dashboard(data)
    except DashboardError:
        return
    raise AssertionError("invalid region was accepted")


def test_rejects_region_below_canvas():
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"][0]["y"] = 1300
    data["screens"][0]["regions"][0]["h"] = 200
    try:
        validate_dashboard(data)
    except DashboardError:
        return
    raise AssertionError("region below canvas was accepted")


def test_refresh_seconds_is_validated():
    import pytest
    data = deepcopy(DEFAULT_DASHBOARD)
    data["refresh_seconds"] = 5
    with pytest.raises(DashboardError):
        validate_dashboard(data)
    data["refresh_seconds"] = 300
    assert validate_dashboard(data)["refresh_seconds"] == 300
    del data["refresh_seconds"]
    assert validate_dashboard(data)["refresh_seconds"] == 60


def test_default_dashboard_has_all_system_sources():
    """默认布局引用的 source 必须都能被 snapshot() 提供，否则会显示"未知"。"""
    from kindle_mcp_dashboard.sources import snapshot
    live = snapshot()
    for region in DEFAULT_DASHBOARD["screens"][0]["regions"]:
        src = region.get("source")
        if src:
            assert src in live, f"{region['id']} 引用了不存在的源 {src}"


def test_accepts_hyphens_and_underscores_in_identifiers():
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["id"] = "operations-overview"
    data["screens"][0]["regions"][0]["id"] = "local_time"
    data["active_screen"] = "operations-overview"
    checked = validate_dashboard(data)
    assert checked["active_screen"] == "operations-overview"
    assert checked["screens"][0]["regions"][0]["id"] == "local_time"


def test_ey21_package_matches_confirmed_firmware():
    root = Path(__file__).parents[1]
    profile = json.loads((root / "kindle/ey21-5.6.1.1/device.json").read_text())
    assert profile["model"] == "EY21"
    assert profile["variant"] == "Wi-Fi"
    assert profile["serial_prefix"] == "B024"
    assert profile["firmware"] == "5.6.1.1"
    assert profile["build"] == "2689890035"
    menu = json.loads((root / "kindle/ey21-5.6.1.1/extensions/kindle-dashboard/menu.json").read_text())
    for item in menu["items"]:
        assert (root / "kindle/ey21-5.6.1.1/extensions/kindle-dashboard" / item["action"]).exists()


def test_rejects_nan_coordinate():
    """NaN 的所有比较均为 False，会绕过 <=0 / >canvas 校验；渲染时
    round(nan*scale) 抛错且坏配置被持久化，每帧必败。必须在校验层拒收。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"][0]["w"] = float("nan")
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_rejects_infinite_coordinate():
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"][0]["x"] = float("inf")
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_rejects_non_dict_payloads():
    """非 dict 载荷必须抛 DashboardError（→400），而非 AttributeError（→500+traceback）。"""
    for bad in ([], "x", 5, None):
        with pytest.raises(DashboardError):
            validate_dashboard(bad)
    data = deepcopy(DEFAULT_DASHBOARD)
    data["device"] = []
    with pytest.raises(DashboardError):
        validate_dashboard(data)
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"] = [None]
    with pytest.raises(DashboardError):
        validate_dashboard(data)
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [5]
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_progress_unknown_source_renders_zero():
    """progress 的 source 解析为"未知"时不能 float() 抛错毁掉整帧。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "p", "kind": "progress", "x": 0, "y": 0, "w": 1000, "h": 200,
         "source": "nonexistent.source"}
    ]
    data = validate_dashboard(data)
    assert render_png(data, fetch=False).startswith(b"\x89PNG")


def test_list_items_null_renders():
    """items 显式为 null 时 region.get 返回 None，enumerate(None) 会 TypeError。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "l", "kind": "list", "x": 0, "y": 0, "w": 1000, "h": 400,
         "items": None}
    ]
    data = validate_dashboard(data)
    assert render_png(data, fetch=False).startswith(b"\x89PNG")


def test_weather_malformed_response_degrades_to_unknown(monkeypatch):
    """Open-Meteo 返回畸形数据时 current() 必须降级为"未知"，绝不崩溃。

    守住模块契约「任何失败都返回未知、不编造天气」。覆盖三类畸形：
    地理编码返回非 dict、weather_code 非数字、温度是字符串。
    每例前重置缓存，否则上一例的 FAIL_TTL 缓存会让下一例直接短路。
    """
    from kindle_mcp_dashboard import weather

    geo = {"results": [{"latitude": 21.2, "longitude": 110.3,
                        "name": "湛江", "population": 1}]}

    def reset():
        monkeypatch.setattr(weather, "_cache",
                            {"key": None, "at": -1e9, "data": None, "ttl": 0.0})
        monkeypatch.setattr(weather, "_geo_cache", {})

    reset()
    monkeypatch.setattr(weather, "_get_json", lambda url, params: ["unexpected"])
    assert weather.current({"city": "湛江"}, fetch=True) == "未知"

    reset()

    def bad_code(url, params):
        if "geocoding" in url:
            return geo
        return {"current": {"temperature_2m": 20, "weather_code": "oops"}}
    monkeypatch.setattr(weather, "_get_json", bad_code)
    assert weather.current({"city": "湛江"}, fetch=True) == "未知"

    reset()

    def bad_temp(url, params):
        if "geocoding" in url:
            return geo
        return {"current": {"temperature_2m": "warm", "weather_code": 0}}
    monkeypatch.setattr(weather, "_get_json", bad_temp)
    assert weather.current({"city": "湛江"}, fetch=True) == "未知"


def test_weather_valid_response_renders(monkeypatch):
    """正常响应仍要渲染出温度+天气/湿度+风两行，不能被防御逻辑误伤。"""
    from kindle_mcp_dashboard import weather

    monkeypatch.setattr(weather, "_cache",
                        {"key": None, "at": -1e9, "data": None, "ttl": 0.0})
    monkeypatch.setattr(weather, "_geo_cache", {})

    def good(url, params):
        if "geocoding" in url:
            return {"results": [{"latitude": 21.2, "longitude": 110.3,
                                 "name": "湛江", "population": 1}]}
        return {"current": {"temperature_2m": 28.6, "weather_code": 1,
                            "relative_humidity_2m": 70, "wind_speed_10m": 12}}
    monkeypatch.setattr(weather, "_get_json", good)
    assert weather.current({"city": "湛江"}, fetch=True) == "29℃ 少云\n湿度 70% · 风 12km/h"


def test_list_null_text_and_unhashable_status_render():
    """list 项 text/label 为 null、status 为 list 时不能崩溃毁掉整帧。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "l", "kind": "list", "x": 0, "y": 0, "w": 1000, "h": 600,
         "items": [{"text": None}, {"label": None}, {"text": "hi", "status": ["ok"]}]}
    ]
    data = validate_dashboard(data)
    assert render_png(data, fetch=False).startswith(b"\x89PNG")


def test_font_size_out_of_range_does_not_crash_or_hang():
    """size/head_size 为 0、负数或超大时只缩字，不崩帧也不空转。
    旧实现 _font(0) 抛 ValueError、_font(100000) 抛 OSError；
    且 _draw_wrapped 逐号递减会让超大 size 空转数十万次（挂死）。"""
    for size, head in [(0, None), (-5, None), (100000, None),
                       (26, -3), (26, 100000)]:
        data = deepcopy(DEFAULT_DASHBOARD)
        region = {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 600,
                  "value": "hello 世界", "size": size}
        if head is not None:
            region["head_size"] = head
        data["screens"][0]["regions"] = [region]
        data = validate_dashboard(data)
        assert render_png(data, fetch=False).startswith(b"\x89PNG")


@pytest.mark.parametrize("key,value", [
    ("size", "abc"), ("size", None), ("size", [1]), ("size", {"a": 1}),
    ("size", float("nan")), ("size", float("inf")),
    ("head_size", "abc"), ("head_size", None), ("head_size", [1]), ("head_size", {"a": 1}),
])
def test_schema_rejects_non_numeric_font_size(key, value):
    """size/head_size 非数值必须在边界被拒：渲染端 int() 先于字号夹取执行，
    字符串/null/容器会崩帧，而 /api/preview 的 render_png 无兜底会丢连接。
    admin.js 输入非数字曾产出 NaN→JSON null，正是这条路径。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 600,
         "value": "hello", key: value}]
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_schema_accepts_numeric_font_size():
    """正常整数/浮点字号必须放行，不能被类型校验误伤。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 600,
         "value": "hello", "size": 26, "head_size": 40.0}]
    validate_dashboard(data)


@pytest.mark.parametrize("value", ["abc", [1], {"a": 1}, float("nan"), float("inf")])
def test_schema_rejects_non_numeric_top_inset(value):
    """device.top_inset 非数值会让 canvas_height 的 int() 抛 ValueError/TypeError，
    而 PUT /api/state 与 POST /api/preview 只捕获 DashboardError → 丢连接。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["device"]["top_inset"] = value
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_schema_accepts_null_and_zero_top_inset():
    """null 由 `or 0` 安全归零，0 与正常像素值都必须放行，不能误伤。"""
    for value in (None, 0, 40):
        data = deepcopy(DEFAULT_DASHBOARD)
        data["device"]["top_inset"] = value
        validate_dashboard(data)


@pytest.mark.parametrize("value", [[], {}, ["home"], None, 5])
def test_schema_rejects_non_string_active_screen(value):
    """active_screen 为不可哈希值时 `in screen_ids`（set）抛 TypeError 而非
    DashboardError，PUT/preview 只捕获后者会丢连接。必须先校验字符串。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["active_screen"] = value
    with pytest.raises(DashboardError):
        validate_dashboard(data)


@pytest.mark.parametrize("value", [[1], {"a": 1}, ["system.time"], 5, True, None])
def test_schema_rejects_non_string_source(value):
    """region.source 非字符串时 resolve() 的 values.get(source) 对不可哈希真值
    抛 TypeError；validate 放行后 /api/preview 的 render_png 无兜底会丢连接。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 600,
         "value": "hello", "source": value}]
    with pytest.raises(DashboardError):
        validate_dashboard(data)


def test_schema_accepts_string_empty_and_absent_source():
    """正常字符串源、空串与缺省（未绑定）都必须放行，且能正常渲染。"""
    for source in ("system.time", "", None):
        data = deepcopy(DEFAULT_DASHBOARD)
        region = {"id": "t", "kind": "text", "x": 0, "y": 0, "w": 1000, "h": 600,
                  "value": "hello"}
        if source is not None:
            region["source"] = source
        data["screens"][0]["regions"] = [region]
        checked = validate_dashboard(data)
        assert render_png(checked, fetch=False).startswith(b"\x89PNG")


@pytest.mark.parametrize("key", ["weather", "workbuddy", "relay"])
@pytest.mark.parametrize("value", ["x", [1], 5, True])
def test_schema_rejects_non_object_settings_subconfig(key, value):
    """weather/workbuddy/relay 子配置被消费端当对象 .get；非 dict 真值会抛
    AttributeError，render_png 与 /api/meta 无兜底会丢连接。必须在边界拒绝。"""
    data = deepcopy(DEFAULT_DASHBOARD)
    data.setdefault("settings", {})[key] = value
    with pytest.raises(DashboardError):
        validate_dashboard(data)


@pytest.mark.parametrize("key", ["weather", "workbuddy", "relay"])
def test_schema_accepts_object_and_null_settings_subconfig(key):
    """正常对象与 null（消费端 `or {}` 安全归空）都必须放行，且能正常渲染。"""
    for value in ({"enabled": True}, None):
        data = deepcopy(DEFAULT_DASHBOARD)
        data.setdefault("settings", {})[key] = value
        checked = validate_dashboard(data)
        assert render_png(checked, fetch=False).startswith(b"\x89PNG")


def test_image_decompression_bomb_is_skipped(monkeypatch):
    """超大图片触发 DecompressionBombError 时只跳过该图，不拖垮整帧。"""
    from PIL import Image
    from kindle_mcp_dashboard import renderer

    def boom(*args, **kwargs):
        raise Image.DecompressionBombError("image too large")
    monkeypatch.setattr(renderer.Image, "open", boom)
    data = deepcopy(DEFAULT_DASHBOARD)
    data["screens"][0]["regions"] = [
        {"id": "img", "kind": "image", "x": 0, "y": 0, "w": 500, "h": 400,
         "image": "/nonexistent/bomb.png"}
    ]
    data = validate_dashboard(data)
    assert render_png(data, fetch=False).startswith(b"\x89PNG")