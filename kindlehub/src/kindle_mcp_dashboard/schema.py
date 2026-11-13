"""仪表盘配置校验。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
改动：归一化画布由固定 1000x1000 改为按设备宽高比推导，
使 600x800 这类非方形屏幕不再被纵向压缩。
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

CANVAS_WIDTH = 1000
# 有序 list（不是 set）：渲染器按这个顺序处理，后台下拉框也按这个顺序展示
KINDS = ["text", "metric", "status", "progress", "list", "image"]


class DashboardError(ValueError):
    pass


def canvas_height(device: dict[str, Any]) -> float:
    """归一化画布高度：宽度固定 1000，高度按可用区域的长宽比推导。

    top_inset 是留给 Kindle 系统状态栏的像素高度（状态栏会覆盖屏幕顶部，
    且无法通过 lipc 关闭），内容从这个偏移之下开始排布。
    """
    usable = device["height"] - int(device.get("top_inset", 0) or 0)
    return CANVAS_WIDTH * usable / device["width"]


def validate_dashboard(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DashboardError("dashboard must be an object")
    data = deepcopy(value)
    device = data.get("device", {})
    if not isinstance(device, dict):
        raise DashboardError("device must be an object")
    for key in ("width", "height"):
        if not isinstance(device.get(key), int) or device[key] <= 0:
            raise DashboardError(f"device.{key} must be a positive integer")
    # top_inset 会被 canvas_height 做 int()；非数值真值会抛 ValueError/TypeError，
    # 而调用方只捕获 DashboardError，会直接丢连接。None 由 `or 0` 安全归零，放行。
    inset = device.get("top_inset")
    if inset is not None and (not isinstance(inset, (int, float)) or not math.isfinite(inset)):
        raise DashboardError("device.top_inset must be a finite number")
    limit_h = canvas_height(device)
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise DashboardError("settings must be an object")
    data["settings"] = settings
    refresh = data.get("refresh_seconds", 60)
    if not isinstance(refresh, int) or not 10 <= refresh <= 3600:
        raise DashboardError("refresh_seconds must be an integer between 10 and 3600")
    data["refresh_seconds"] = refresh
    screens = data.get("screens")
    if not isinstance(screens, list) or not screens:
        raise DashboardError("screens must be a non-empty list")
    screen_ids: set[str] = set()
    for screen in screens:
        if not isinstance(screen, dict):
            raise DashboardError("each screen must be an object")
        sid = _identifier(screen.get("id"), "screen.id")
        if sid in screen_ids:
            raise DashboardError(f"duplicate screen id: {sid}")
        screen_ids.add(sid)
        regions = screen.get("regions")
        if not isinstance(regions, list):
            raise DashboardError(f"screen {sid} regions must be a list")
        region_ids: set[str] = set()
        for region in regions:
            if not isinstance(region, dict):
                raise DashboardError("each region must be an object")
            rid = _identifier(region.get("id"), f"screen {sid} region.id")
            if rid in region_ids:
                raise DashboardError(f"duplicate region id in {sid}: {rid}")
            region_ids.add(rid)
            if region.get("kind") not in KINDS:
                raise DashboardError(f"region {rid} has unsupported kind")
            for key in ("x", "y", "w", "h"):
                number = region.get(key)
                if not isinstance(number, (int, float)) or not math.isfinite(number):
                    raise DashboardError(f"region {rid}.{key} must be a finite number")
            if region["w"] <= 0 or region["h"] <= 0:
                raise DashboardError(f"region {rid} must have positive size")
            if (region["x"] < 0 or region["y"] < 0
                    or region["x"] + region["w"] > CANVAS_WIDTH
                    or region["y"] + region["h"] > limit_h + 0.5):
                raise DashboardError(
                    f"region {rid} must fit inside the normalized "
                    f"{CANVAS_WIDTH}x{limit_h:.0f} canvas")
            # size/head_size 可选，但一旦给出必须是有限数值：渲染端 int() 先于
            # 字号夹取执行，字符串/null/容器会抛 ValueError/TypeError 崩帧，
            # 而 /api/preview 的 render_png 没有兜底，会直接丢掉连接。
            for key in ("size", "head_size"):
                if key in region:
                    number = region[key]
                    if not isinstance(number, (int, float)) or not math.isfinite(number):
                        raise DashboardError(f"region {rid}.{key} must be a finite number")
    # 先确认是字符串再做集合成员判断：list/dict 等不可哈希值会让 `in set`
    # 抛 TypeError 而非 DashboardError，调用方只捕获后者，会直接丢连接。
    active = data.get("active_screen")
    if not isinstance(active, str) or active not in screen_ids:
        raise DashboardError("active_screen must name an existing screen")
    return data


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or not value.replace("-", "").replace("_", "").isalnum():
        raise DashboardError(f"{field} must contain letters, digits, '_' or '-'")
    return value
