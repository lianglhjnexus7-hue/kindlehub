"""KindleHub 测试。

基线来自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
改动：默认设备改为 KT2 600x800；新增中文渲染、等比映射、像素折行测试。
"""
from copy import deepcopy
import json
from pathlib import Path

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