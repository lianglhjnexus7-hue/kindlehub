"""把仪表盘配置渲染成灰度 PNG。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
相对上游的改动：
- 字体链支持 macOS（Hiragino Sans GB / STHeiti / Songti），修复中文渲染为方框的问题
- 坐标改为等比映射（统一 scale = width/1000），消除非方形屏幕的纵向压缩
- 折行按真实像素宽度测量，支持中日韩全角字符，不再用 A-Z 平均宽度估算
- 16 级灰度量化关闭抖动，避免 e-ink 面板出现噪点
"""
from __future__ import annotations

import io
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .sources import resolve, snapshot

CANVAS_WIDTH = 1000

# (路径, ttc face 索引)。Hiragino Sans GB.ttc: 0=W3 常规, 2=W6 粗体。
REGULAR_FONTS: tuple[tuple[str, int], ...] = (
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 4),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", 0),
)
BOLD_FONTS: tuple[tuple[str, int], ...] = (
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 1),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 0),
)

# 拉丁词 / 空白 / 单字符（中文与标点逐字处理）
_TOKEN = re.compile(r"[A-Za-z0-9@._/:%+\-]+|\s+|.")

# 不宜出现在行首的标点
_NO_LINE_START = "，。、；：！？）》」』】%…,.;:!?)]}"


def render_dashboard(data: dict[str, Any], values: dict[str, Any] | None = None,
                     fetch: bool = True) -> Image.Image:
    width, height = data["device"]["width"], data["device"]["height"]
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    screen = next(item for item in data["screens"] if item["id"] == data["active_screen"])
    live = values or snapshot(data.get("settings"), fetch=fetch)
    scale = width / CANVAS_WIDTH
    for region in screen["regions"]:
        if region["kind"] == "image":
            _draw_image(image, region, width, height, scale)
        else:
            _draw_region(draw, region, live, width, height, scale)
    # 量化到面板的 16 级灰；关闭抖动，e-ink 上抖动会显脏。
    return image.quantize(colors=16, dither=Image.Dither.NONE).convert("L")


def render_png(data: dict[str, Any], values: dict[str, Any] | None = None,
               fetch: bool = True) -> bytes:
    buffer = io.BytesIO()
    render_dashboard(data, values, fetch=fetch).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _box(region: dict[str, Any], scale: float) -> tuple[int, int, int, int]:
    """等比映射：x/y/w/h 统一乘以 scale，保证像素长宽比与设备一致。"""
    return (
        round(region["x"] * scale),
        round(region["y"] * scale),
        round(region["w"] * scale),
        round(region["h"] * scale),
    )


def _draw_region(draw: ImageDraw.ImageDraw, region: dict[str, Any], values: dict[str, Any],
                 width: int, height: int, scale: float) -> None:
    x, y, w, h = _box(region, scale)
    has_label = bool(str(region.get("label", "")).strip())
    # 无标题的窄条（如底部健康栏）不该按卡片标准扣内边距，
    # 否则可用高度被 pad 吃光，一行都画不出来。
    pad = max(9, round(min(width, height) * 0.022)) if has_label else max(3, round(min(width, height) * 0.008))
    if region.get("border", True):
        radius = max(4, round(min(width, height) * 0.009))
        draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=30, width=2)
    if region.get("invert"):
        draw.rectangle((x, y, x + w, y + h), fill=20)
        fg, muted = 255, 205
    else:
        fg, muted = 15, 85
    label = str(region.get("label", ""))
    cursor_y = y + pad
    if has_label:
        font = _font(max(13, round(height * 0.017)), bold=True)
        draw.text((x + pad, cursor_y), label, font=font, fill=fg)
        cursor_y += _line_height(font) + max(4, round(height * 0.006))
    icon = region.get("icon")
    icon_space = 0
    if icon:
        icon_size = min(54, max(26, round(h * 0.30)))
        _draw_icon(draw, str(icon), x + pad, cursor_y + 3, icon_size, fg)
        icon_space = icon_size + pad
    kind = region["kind"]
    value = resolve(region, values)
    content_x = x + pad + icon_space
    content_w = max(10, w - pad * 2 - icon_space)
    if kind in {"text", "metric", "status"}:
        size = int(region.get("size", 40 if kind == "metric" else 26))
        head_size = int(region["head_size"]) if region.get("head_size") else None
        if kind == "status":
            status = str(region.get("status", value)).lower()
            dot = 18
            fill = 20 if status in {"ok", "online", "healthy", "running"} else 120
            draw.ellipse((content_x, cursor_y + 7, content_x + dot, cursor_y + 7 + dot), fill=fill)
            content_x += dot + pad
            content_w -= dot + pad
        _draw_wrapped(draw, str(value), content_x, cursor_y, content_w, y + h - pad, size, fg,
                      region.get("align", "left"), bold=kind == "metric", head_size=head_size)
        unit = str(region.get("unit", ""))
        if unit and kind == "metric":
            draw.text((x + w - pad, y + h - pad), unit, font=_font(18), fill=muted, anchor="rs")
    elif kind == "progress":
        percent = max(0, min(100, float(value or 0)))
        value_font = _font(int(region.get("size", 36)), bold=True)
        draw.text((content_x, cursor_y), f"{percent:.0f}%", font=value_font, fill=fg)
        bar_h = max(12, round(height * 0.022))
        bar_y = y + h - pad - bar_h
        draw.rectangle((x + pad, bar_y, x + w - pad, bar_y + bar_h), outline=fg, width=2)
        fill_w = round((w - pad * 2 - 6) * percent / 100)
        draw.rectangle((x + pad + 3, bar_y + 3, x + pad + 3 + fill_w, bar_y + bar_h - 3), fill=fg)
    elif kind == "list":
        items = region.get("items", value if isinstance(value, list) else [value])
        item_font = _font(int(region.get("size", 23)))
        line_h = _line_height(item_font) + max(7, round(height * 0.009))
        for index, item in enumerate(items):
            row_y = cursor_y + index * line_h
            if row_y + line_h > y + h - pad:
                break
            text = item.get("text", item.get("label", "")) if isinstance(item, dict) else str(item)
            status = item.get("status", "") if isinstance(item, dict) else ""
            marker_y = row_y + max(2, line_h // 2 - 6)
            marker_box = (x + pad, marker_y, x + pad + 12, marker_y + 12)
            if status in {"ok", "online", "healthy", "running"}:
                draw.ellipse(marker_box, fill=fg)
            else:
                draw.ellipse(marker_box, outline=fg, width=2)
            draw.text((x + pad + 24, row_y), text, font=item_font, fill=fg)


def _draw_image(image: Image.Image, region: dict[str, Any], width: int, height: int,
                scale: float) -> None:
    """绘制本地图片素材，等比缩放后居中放进 region。"""
    path = region.get("image") or region.get("path")
    if not isinstance(path, str) or not path:
        return
    try:
        source = Image.open(Path(path).expanduser()).convert("L")
    except (OSError, ValueError):
        return
    if region.get("trim"):
        bbox = ImageOps.invert(source).getbbox()
        if bbox:
            source = source.crop(bbox)
    x, y, w, h = _box(region, scale)
    pad = max(6, round(min(width, height) * 0.012))
    target_w, target_h = max(1, w - pad * 2), max(1, h - pad * 2)
    fitted = ImageOps.contain(source, (target_w, target_h))
    if region.get("invert"):
        fitted = ImageOps.invert(fitted)
    paste_x = x + (w - fitted.width) // 2
    paste_y = y + (h - fitted.height) // 2
    image.paste(fitted, (paste_x, paste_y))


def _fit_size(size: int, avail: int, bold: bool) -> int:
    """区域放不下一行时逐级缩小字号，避免内容整块消失。"""
    while size > 12:
        font = _font(size, bold)
        if _line_height(font) + max(3, round(size * 0.12)) <= avail:
            return size
        size = max(12, int(size * 0.85))
    return size


def _draw_wrapped(draw: ImageDraw.ImageDraw, value: str, x: int, y: int, width: int, bottom: int,
                  size: int, fill: int, align: str, bold: bool = False,
                  head_size: int | None = None) -> None:
    """head_size 存在时：第一行按 head_size 放大，其余行用 size。

    用于"28℃ 晴 / 湿度 70% · 风 9km/h"这种一张卡里两级字号的信息。
    """
    paragraphs = str(value).split("\n")
    anchor = "ra" if align == "right" else "ma" if align == "center" else "la"
    draw_x = x + width if align == "right" else x + width // 2 if align == "center" else x

    for index, paragraph in enumerate(paragraphs):
        use = head_size if (index == 0 and head_size) else size
        use = _fit_size(use, bottom - y, bold)
        font = _font(use, bold)
        line_h = _line_height(font) + max(3, round(use * 0.12))
        for line in _wrap(draw, paragraph, font, width):
            if y + line_h > bottom:
                return
            draw.text((draw_x, y), line, font=font, fill=fill, anchor=anchor)
            y += line_h
        y += max(2, round(use * 0.08))  # 段落间距


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    """按真实像素宽度折行。中文逐字可断，拉丁词尽量不拆。"""
    lines: list[str] = []
    for para in str(text).split("\n"):
        current = ""
        for token in _TOKEN.findall(para):
            trial = current + token
            if not current or draw.textlength(trial, font=font) <= max_w:
                current = trial
            else:
                lines.append(current.rstrip())
                current = "" if token.isspace() else token
        lines.append(current.rstrip())
    # 避免标点落到行首
    for index in range(1, len(lines)):
        while lines[index] and lines[index][0] in _NO_LINE_START:
            lines[index - 1] += lines[index][0]
            lines[index] = lines[index][1:]
    return lines or [""]


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path, index in (BOLD_FONTS if bold else REGULAR_FONTS):
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default(size=size)


def _line_height(font: ImageFont.ImageFont) -> int:
    box = font.getbbox("Ag中")
    return box[3] - box[1]


def _draw_icon(draw: ImageDraw.ImageDraw, name: str, x: int, y: int, size: int, fill: int) -> None:
    cx, cy = x + size // 2, y + size // 2
    if name in {"cloud", "weather"}:
        # 云：一条底边 + 三个半圆弧
        base_y = y + size - 4
        draw.line((x + 3, base_y, x + size - 3, base_y), fill=fill, width=3)
        r = size * 0.30
        draw.arc((x + 2, base_y - 2 * r, x + 2 + 2 * r, base_y), 180, 360, fill=fill, width=3)
        draw.arc((x + size * 0.24, base_y - 2.9 * r, x + size * 0.24 + 2.9 * r, base_y),
                 180, 360, fill=fill, width=3)
        draw.arc((x + size - 2 - 2 * r, base_y - 2 * r, x + size - 2, base_y), 180, 360, fill=fill, width=3)
    elif name in {"cpu", "memory"}:
        inset = size // 5
        draw.rectangle((x + inset, y + inset, x + size - inset, y + size - inset), outline=fill, width=3)
        for offset in range(inset, size - inset + 1, max(5, size // 5)):
            draw.line((x + offset, y, x + offset, y + inset), fill=fill, width=2)
            draw.line((x + offset, y + size - inset, x + offset, y + size), fill=fill, width=2)
    elif name == "disk":
        draw.rectangle((x + 4, y + 5, x + size - 4, y + size - 5), outline=fill, width=3)
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=fill, width=2)
    elif name == "agent":
        draw.ellipse((cx - 7, y + 2, cx + 7, y + 16), outline=fill, width=3)
        draw.line((cx, y + 16, cx, y + size - 4), fill=fill, width=3)
        draw.line((x + 5, cy, x + size - 5, cy), fill=fill, width=3)
        draw.line((cx, y + size - 4, x + 7, y + size), fill=fill, width=3)
        draw.line((cx, y + size - 4, x + size - 7, y + size), fill=fill, width=3)
    else:
        draw.ellipse((x + 4, y + 4, x + size - 4, y + size - 4), outline=fill, width=3)
