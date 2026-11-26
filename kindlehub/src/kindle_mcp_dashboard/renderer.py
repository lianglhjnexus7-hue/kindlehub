"""把仪表盘配置渲染成灰度 PNG。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
相对上游的改动：
- 字体链支持 macOS（Hiragino Sans GB / STHeiti / Songti），修复中文渲染为方框的问题
- 坐标改为等比映射（统一 scale = width/1000），消除非方形屏幕的纵向压缩
- 折行按真实像素宽度测量，支持中日韩全角字符，不再用 A-Z 平均宽度估算
- 16 级灰度量化关闭抖动，避免 e-ink 面板出现噪点
"""
from __future__ import annotations

import datetime as dt
import io
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .sources import active_sources, resolve, snapshot

CANVAS_WIDTH = 1000

# 字号安全上限：FreeType 对超大像素抛 OSError，且适配循环逐号递减，
# 不夹住会让恶意/误配的超大 size 空转数十万次。512 远超任何正常排版需求。
MAX_FONT_SIZE = 512

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
                     fetch: bool = True, draw_bar: bool = True) -> Image.Image:
    width, height = data["device"]["width"], data["device"]["height"]
    # top_inset 是顶部状态栏高度。图片输出整屏（600x800），状态栏由我们自己画，
    # 内容整体下移 inset，实现完全接管屏幕（Kindle 系统栏不再参与）。
    inset = int(data["device"].get("top_inset", 0) or 0)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    screen = next(item for item in data["screens"] if item["id"] == data["active_screen"])
    if values:
        live = values
    else:
        needed = active_sources(data)
        if inset > 0 and draw_bar:
            needed.add("weather.current")
        live = snapshot(data.get("settings"), fetch=fetch, needed=needed)
    scale = width / CANVAS_WIDTH
    if inset > 0 and draw_bar:
        _draw_status_bar(draw, width, inset, live)
    for region in screen["regions"]:
        if region["kind"] == "image":
            _draw_image(image, region, width, height, scale, inset)
        elif region["kind"] == "k3b":
            _draw_k3b(image, draw, region, live, scale, inset)
        else:
            _draw_region(draw, region, live, width, height, scale, inset)
    # 确定性量化至面板 16 级硬件灰阶（0, 17, 34, ..., 255）；关闭抖动并确保 0/34/255 零漂移。
    return image.point(lambda p: ((p + 8) // 17) * 17)


def render_png(data: dict[str, Any], values: dict[str, Any] | None = None,
               fetch: bool = True, draw_bar: bool = True) -> bytes:
    buffer = io.BytesIO()
    render_dashboard(data, values, fetch=fetch, draw_bar=draw_bar).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _text_center_y(font: ImageFont.ImageFont, y0: int, y1: int) -> int:
    """按字形实际边界框垂直居中，避免标题条内文字偏上或偏下。"""
    box = font.getbbox("Ag中")
    return y0 + ((y1 - y0) - (box[1] + box[3])) // 2


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    """按真实像素宽度单行截断，防止面板单元格文字越过黑框分割线。"""
    s = str(text or "")
    if max_w <= 0:
        return ""
    if draw.textlength(s, font=font) <= max_w:
        return s
    ell = "…"
    while len(s) > 1 and draw.textlength(s + ell, font=font) > max_w:
        s = s[:-1]
    return (s + ell) if draw.textlength(s + ell, font=font) <= max_w else s


def _draw_status_bar(draw: ImageDraw.ImageDraw, width: int, height_inset: int,
                     values: dict[str, Any]) -> None:
    """自绘顶部 40px 状态栏：左侧时间/天气/10 格黑框电量，右侧留白 210px 避让系统重绘区。"""
    draw.rectangle((0, 0, width, height_inset), fill=255)
    # 底部 2px 实心黑分割线落在 y = height_inset - 2 .. height_inset - 1，
    # 保证右上角 (390..width, 0..height_inset - 2) 100% 纯白无侵入。
    bar_bottom = max(2, height_inset - 2)
    draw.rectangle((0, bar_bottom, width, height_inset - 1), fill=0)

    # 右侧按比例保留 210/600 纯白避让区（KT2 上即 x=390..600）
    reserved_right = round(width * 210 / 600)
    safe_right = max(80, width - reserved_right - 4)
    cursor = max(12, round(width * 14 / 600))

    clock = str(values.get("system.time", "")).strip()
    weather_line = (str(values.get("weather.current") or "").splitlines() or [""])[0].strip()
    city = str((values.get("workbench.k3b") or {}).get("weather_city") or "").strip() if isinstance(values.get("workbench.k3b"), dict) else ""
    if city and weather_line and weather_line not in {"未设置城市", "获取中…", "未知"} and not weather_line.startswith(city):
        weather_text = f"{city} {weather_line}"
    else:
        weather_text = weather_line
    left_label = f"{clock} · {weather_text}" if (clock and weather_text) else (clock or weather_text)

    battery = values.get("system.battery")
    level: int | None = None
    if battery is not None:
        try:
            level = max(0, min(100, int(battery)))
        except (TypeError, ValueError):
            level = None
    charging = bool(values.get("system.charging"))

    font = _font(14, bold=True)
    y_text = _text_center_y(font, 0, bar_bottom)

    batt_reserve = 0
    pct_text = ""
    if level is not None:
        pct_text = f"{level}%"
        pct_w = round(draw.textlength(pct_text, font=font))
        # 10 格黑框电池（57px）+ 间隔（6px）+ 百分比 + 可选闪电（14px）+ 前置留空（10px）
        batt_reserve = 10 + 57 + 6 + pct_w + (14 if charging else 0)

    if left_label:
        avail_text = max(40, safe_right - cursor - batt_reserve)
        clipped = _fit_text(draw, left_label, font, avail_text)
        draw.text((cursor, y_text), clipped, font=font, fill=0)
        cursor += round(draw.textlength(clipped, font=font)) + 10

    if level is None or cursor + 57 > safe_right:
        return

    cells, cell_w, cell_h, gap = 10, 3, 10, 2
    box_w = cells * cell_w + (cells - 1) * gap + 8  # 边框 2px + 内隙 2px
    box_h = cell_h + 8
    y0 = max(2, (bar_bottom - box_h) // 2)
    if cursor + box_w + 6 > safe_right:
        return
    draw.rectangle((cursor, y0, cursor + box_w, y0 + box_h), outline=0, fill=255, width=2)
    # 电池正极凸头
    nub_y0 = y0 + max(3, (box_h - 6) // 2)
    draw.rectangle((cursor + box_w + 1, nub_y0, cursor + box_w + 3, nub_y0 + 6), fill=0)

    filled = max(1, int(round(level / 10.0))) if level > 0 else 0
    cell_y0 = y0 + 4
    for i in range(cells):
        cx = cursor + 4 + i * (cell_w + gap)
        cbox = (cx, cell_y0, cx + cell_w, cell_y0 + cell_h)
        if i < filled:
            draw.rectangle(cbox, fill=0)
        else:
            draw.rectangle(cbox, fill=255, outline=0, width=1)
    cursor += box_w + 7

    pct_clipped = _fit_text(draw, pct_text, font, safe_right - cursor)
    if pct_clipped:
        draw.text((cursor, y_text), pct_clipped, font=font, fill=0)
        cursor += round(draw.textlength(pct_clipped, font=font))

    if charging and cursor + 12 <= safe_right:
        cursor += 6
        ly = y0 + 2
        draw.line((cursor + 5, ly, cursor, ly + 7), fill=0, width=2)
        draw.line((cursor, ly + 7, cursor + 6, ly + 7), fill=0, width=2)
        draw.line((cursor + 6, ly + 7, cursor + 1, ly + 14), fill=0, width=2)


def _box(region: dict[str, Any], scale: float,
         inset: int = 0) -> tuple[int, int, int, int]:
    """等比映射：x/y/w/h 统一乘以 scale，保证像素长宽比与设备一致。

    inset 是顶部状态栏占用的像素高度，所有内容整体下移这么多。
    """
    return (
        round(region["x"] * scale),
        inset + round(region["y"] * scale),
        round(region["w"] * scale),
        round(region["h"] * scale),
    )


def _draw_region(draw: ImageDraw.ImageDraw, region: dict[str, Any], values: dict[str, Any],
                 width: int, height: int, scale: float, inset: int = 0) -> None:
    x, y, w, h = _box(region, scale, inset)
    has_label = bool(str(region.get("label", "")).strip())
    # 无标题的窄条（如底部健康栏）不该按卡片标准扣内边距，
    # 否则可用高度被 pad 吃光，一行都画不出来。
    pad = max(9, round(min(width, height) * 0.022)) if has_label else max(3, round(min(width, height) * 0.008))
    if region.get("border", True):
        radius = max(4, round(min(width, height) * 0.009))
        draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, outline=0, width=2)
    if region.get("invert"):
        draw.rectangle((x, y, x + w, y + h), fill=20)
        fg, muted = 255, 205
    else:
        fg, muted = 0, 60
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
        try:
            percent = max(0, min(100, float(value or 0)))
        except (TypeError, ValueError):
            percent = 0
        value_font = _font(int(region.get("size", 36)), bold=True)
        draw.text((content_x, cursor_y), f"{percent:.0f}%", font=value_font, fill=fg)
        bar_h = max(12, round(height * 0.022))
        bar_y = y + h - pad - bar_h
        draw.rectangle((x + pad, bar_y, x + w - pad, bar_y + bar_h), outline=0, width=2)
        fill_w = round((w - pad * 2 - 6) * percent / 100)
        draw.rectangle((x + pad + 3, bar_y + 3, x + pad + 3 + fill_w, bar_y + bar_h - 3), fill=0)
    elif kind == "list":
        items = region.get("items")
        if items is None:
            items = value if isinstance(value, list) else [value]
        elif not isinstance(items, list):
            items = [items]
        item_font = _font(int(region.get("size", 23)))
        line_h = _line_height(item_font) + max(7, round(height * 0.009))
        for index, item in enumerate(items):
            row_y = cursor_y + index * line_h
            if row_y + line_h > y + h - pad:
                break
            if isinstance(item, dict):
                # text/label 可能是 null，status 可能是 list 等不可哈希值；
                # 统一强制成 str，避免 draw.text(None) 与 set 成员判断崩溃
                text = str(item.get("text") or item.get("label") or "")
                status = str(item.get("status") or "")
            else:
                text, status = str(item), ""
            marker_y = row_y + max(2, line_h // 2 - 6)
            marker_box = (x + pad, marker_y, x + pad + 12, marker_y + 12)
            if status in {"ok", "online", "healthy", "running"}:
                draw.ellipse(marker_box, fill=fg)
            else:
                draw.ellipse(marker_box, outline=0, width=2)
            draw.text((x + pad + 24, row_y), text, font=item_font, fill=fg)


def _draw_image(image: Image.Image, region: dict[str, Any], width: int, height: int,
                scale: float, inset: int = 0) -> None:
    """绘制本地图片素材，等比缩放后居中放进 region。"""
    path = region.get("image") or region.get("path")
    if not isinstance(path, str) or not path:
        return
    try:
        source = Image.open(Path(path).expanduser()).convert("L")
    except Exception:
        # 图片解码失败模式很多（OSError/ValueError/DecompressionBombError 等），
        # 坏素材只跳过这张图，绝不能拖垮整帧渲染
        return
    if region.get("trim"):
        bbox = ImageOps.invert(source).getbbox()
        if bbox:
            source = source.crop(bbox)
    x, y, w, h = _box(region, scale, inset)
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
    # 先夹住字号再进下面的逐号递减适配循环，否则超大 size 会空转数十万次
    size = max(1, min(int(size), MAX_FONT_SIZE))
    if head_size:
        head_size = max(1, min(int(head_size), MAX_FONT_SIZE))
    paragraphs = str(value).split(chr(10))
    anchor = "ra" if align == "right" else "ma" if align == "center" else "la"
    draw_x = x + width if align == "right" else x + width // 2 if align == "center" else x

    # 预计算总高度；放不下就整体缩字号，宁可字小一点也不裁掉末尾行。
    # Agent 数量随本机装了哪些软件变化，硬裁会让最后几个凭空消失。
    def _total_h(font_size, first):
        total = 0
        for index, para in enumerate(paragraphs):
            u = first if (index == 0 and first) else font_size
            fnt = _font(u, bold)
            lh = _line_height(fnt) + max(3, round(u * 0.12))
            total += len(_wrap(draw, para, fnt, width)) * lh
            total += max(2, round(u * 0.08))
        return total

    available = bottom - y
    while size > 11 and _total_h(size, head_size) > available:
        size -= 1
        if head_size and head_size > size + 4:
            head_size = size + 4

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
    # 夹到安全区间：Pillow 对 <=0 抛 ValueError、超大像素抛 OSError，坏 size 只缩字不崩帧
    size = max(1, min(int(size), MAX_FONT_SIZE))
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


_K3B_STATUS_TEXT = {
    "running": "执行中",
    "waiting": "等待确认",
    "error": "出错",
    "idle": "空闲",
    "unknown": "未知",
    "disconnected": "离线",
}

_K3B_REALM_CN = {"cn": "国内", "intl": "国际", "all": "全部", "unknown": "账号"}


def _fill_hatch_45(image: Image.Image, x0: int, y0: int, x1: int, y1: int) -> None:
    """在半开区间 [x0, x1) x [y0, y1) 填充纯黑白 45° 斜纹，抗墨水屏刷新吞色。"""
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    tile = Image.new("L", (w, h), 255)
    td = ImageDraw.Draw(tile)
    for offset in range(-h, w + h, 5):
        td.line((offset, h, offset + h, 0), fill=0, width=2)
    image.paste(tile, (x0, y0))


def _draw_double_slot(image: Image.Image, draw: ImageDraw.ImageDraw,
                      x0: int, y0: int, x1: int, y1: int,
                      segments: list[tuple[float, str]]) -> None:
    """K3-B 双层黑框进度槽：2px 外黑框 + 2px 纯白内隙 + 内部纯黑/45°斜纹填充。"""
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    draw.rectangle((x0, y0, x1, y1), fill=255, outline=0, width=2)
    ix0, iy0, ix1, iy1 = x0 + 4, y0 + 4, x1 - 4, y1 - 4
    span = ix1 - ix0 + 1
    if span <= 0 or iy1 < iy0:
        return

    active_segs = [(ratio, mode) for ratio, mode in segments if ratio > 0 and mode != "empty"]
    if not active_segs:
        return

    cur = ix0
    gap = 2 if len(active_segs) > 1 else 0
    for idx, (ratio, mode) in enumerate(active_segs):
        if cur > ix1:
            break
        remaining = ix1 - cur + 1
        seg_w = max(2, min(remaining, round(span * ratio)))
        seg_x1 = min(ix1, cur + seg_w - 1)
        if mode == "hatch":
            _fill_hatch_45(image, cur, iy0, seg_x1 + 1, iy1 + 1)
        else:
            draw.rectangle((cur, iy0, seg_x1, iy1), fill=0)
        cur = seg_x1 + 1 + (gap if idx < len(active_segs) - 1 else 0)


def _k3b_ago(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60}分前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    return f"{seconds // 86400}天前"


def _k3b_panel_bar(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int,
                   bar_h: int, left_text: str, right_text: str,
                   font_l: ImageFont.ImageFont, font_r: ImageFont.ImageFont,
                   pad_x: int = 8) -> None:
    y1 = y0 + bar_h - 1
    draw.rectangle((x0, y0, x1, y1), fill=0)
    lt = _fit_text(draw, left_text, font_l, max(20, (x1 - x0) - pad_x * 2))
    draw.text((x0 + pad_x, _text_center_y(font_l, y0, y1)), lt, font=font_l, fill=255)
    if right_text:
        max_rw = max(16, (x1 - x0) - pad_x * 2 - round(draw.textlength(lt, font=font_l)) - 8)
        rt = _fit_text(draw, right_text, font_r, max_rw)
        rx = x1 - pad_x - round(draw.textlength(rt, font=font_r))
        draw.text((rx, _text_center_y(font_r, y0, y1)), rt, font=font_r, fill=255)


def _k3b_banner_state(summary_wrap: dict[str, Any]) -> tuple[str, str, bool]:
    collected = bool(summary_wrap.get("collected"))
    cache_stale = bool(summary_wrap.get("cache_stale"))
    data = summary_wrap.get("data") if isinstance(summary_wrap.get("data"), dict) else None
    updated_at = (data or {}).get("updated_at")
    hhmm = updated_at[11:16] if isinstance(updated_at, str) and len(updated_at) >= 16 else "--:--"
    if not collected or data is None:
        return "工作台 / 尚未采集", f"快照 {hhmm}", False
    if cache_stale:
        return "工作台 / 缓存已过期", f"快照 {hhmm}", False
    state = data.get("state")
    if state == "stale":
        return "工作台 / 数据已过期", f"快照 {hhmm}", False
    if state == "unavailable":
        return "工作台 / 暂不可用", f"快照 {hhmm}", False
    if state == "partial":
        return "工作台 / 数据不完整", f"快照 {hhmm}", True
    return "工作台 / 重墨双层框版", f"快照 {hhmm}", True


def _pick_credits_pool(credits_data: Any) -> tuple[str, float, float] | None:
    if not isinstance(credits_data, dict):
        return None
    realms = credits_data.get("realms")
    if not isinstance(realms, dict):
        return None
    valid = [
        (name, float(slot.get("remain") or 0.0), float(slot.get("size") or 0.0))
        for name, slot in realms.items()
        if isinstance(slot, dict) and float(slot.get("size") or 0.0) > 0
    ]
    if not valid:
        return None
    for item in valid:
        if item[0] == "cn":
            return item
    return max(valid, key=lambda item: item[2])


def _draw_k3b_kpis(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
                   data: dict[str, Any] | None, usable: bool, scale: float,
                   font_lbl: ImageFont.ImageFont, font_val: ImageFont.ImageFont,
                   font_sub: ImageFont.ImageFont) -> None:
    draw.rectangle((x0, y0, x1, y1), fill=255, outline=0, width=2)
    body_w = x1 - x0
    if usable and data:
        att = data.get("attention") if isinstance(data.get("attention"), dict) else {}
        dec = att["pending_decisions"] if att.get("pending_decisions") is not None else data.get("pending_decisions")
        hnd = att.get("pending_handoffs")
        svc_ok, svc_tot = att.get("services_ok"), att.get("services_total")
        svc_down = att.get("services_down") or []
        jobs = att["job_alerts"] if att.get("job_alerts") is not None else data.get("job_alerts")
        nxt = data.get("next_slot")
        alerts = att.get("alerts") or []
        cells = [
            ("待拍板决策", f"{dec:02d}" if dec is not None else "--",
             "需拍板" if (dec or 0) > 0 else ("已清零" if dec == 0 else "未知")),
            ("待验收交接", f"{hnd:02d}" if hnd is not None else "--",
             "需验收" if (hnd or 0) > 0 else ("无积压" if hnd == 0 else "未知")),
            ("常驻服务", f"{svc_ok}/{svc_tot}" if (svc_ok is not None and svc_tot is not None) else "--",
             f"掉线:{svc_down[0]}" if svc_down else (f"异常任务 {jobs}" if jobs is not None else "状态未知")),
            ("下次排程", nxt if nxt else "--:--",
             alerts[0] if alerts else ("今日日程" if nxt else "今日已结")),
        ]
    else:
        sub = "暂不可用" if not data or data.get("state") == "unavailable" else "已过期"
        cells = [
            ("待拍板决策", "--", sub),
            ("待验收交接", "--", sub),
            ("常驻服务", "--", sub),
            ("下次排程", "--:--", sub),
        ]

    pad_x = max(6, round(9 * scale))
    for i, (lbl, val, sub) in enumerate(cells):
        cx0 = x0 + round(body_w * i / 4)
        cx1 = x0 + round(body_w * (i + 1) / 4)
        if i > 0:
            draw.line((cx0, y0, cx0, y1), fill=0, width=2)
        cw = (cx1 - cx0) - pad_x * 2
        draw.text((cx0 + pad_x, y0 + round(6 * scale)), _fit_text(draw, lbl, font_lbl, cw), font=font_lbl, fill=34)
        draw.text((cx0 + pad_x, y0 + round(22 * scale)), _fit_text(draw, val, font_val, cw), font=font_val, fill=0)
        draw.text((cx0 + pad_x, y0 + round(53 * scale)), _fit_text(draw, sub, font_sub, cw), font=font_sub, fill=34)


def _draw_k3b_agent_badge(draw: ImageDraw.ImageDraw, bx0: int, by0: int, bx1: int, by1: int,
                          status: str, font: ImageFont.ImageFont) -> None:
    label = _K3B_STATUS_TEXT.get(status, "未知")
    if status == "running":
        draw.rectangle((bx0, by0, bx1, by1), fill=0)
        fg = 255
    elif status in {"waiting", "error"}:
        draw.rectangle((bx0, by0, bx1, by1), fill=255, outline=0, width=1)
        draw.rectangle((bx0 + 2, by0 + 2, bx1 - 2, by1 - 2), outline=0, width=1)
        fg = 0
    else:
        draw.rectangle((bx0, by0, bx1, by1), fill=255, outline=0, width=1)
        fg = 0
    tw = round(draw.textlength(label, font=font))
    tx = bx0 + max(1, (bx1 - bx0 - tw) // 2)
    draw.text((tx, _text_center_y(font, by0, by1)), label, font=font, fill=fg)


def _draw_k3b_agents_panel(image: Image.Image, draw: ImageDraw.ImageDraw,
                           x0: int, y0: int, x1: int, y1: int,
                           data: dict[str, Any] | None, usable: bool,
                           sx: float, sy: float) -> None:
    smin = min(sx, sy)
    f_bar = _font(max(10, round(12 * smin)), bold=True)
    f_bar_r = _font(max(9, round(11 * smin)), bold=True)
    f_row = _font(max(10, round(12 * smin)), bold=True)
    f_badge = _font(max(9, round(11 * smin)), bold=True)
    f_leg = _font(max(9, round(11 * smin)), bold=True)

    bar_h = round(25 * sy)
    row_h = round(27 * sy)
    proj = (data.get("projects") if (usable and data and isinstance(data.get("projects"), dict)) else None) or {}
    proj_total = proj.get("total")
    agent_list = (data.get("agents") if (usable and data and isinstance(data.get("agents"), list)) else None) or []

    draw.rectangle((x0, y0, x1, y1), fill=255, outline=0, width=2)
    title_l = f"AI 施工与 {proj_total if proj_total is not None else 14} 项目脉冲"
    title_r = f"{len(agent_list)} 会话" if usable else "状态未知"
    _k3b_panel_bar(draw, x0, y0, x1, bar_h, title_l, title_r, f_bar, f_bar_r)

    tbl_y = y0 + bar_h
    pad_x = round(8 * sx)
    col_badge_x = x0 + round(112 * sx)
    badge_w = round(68 * sx)
    badge_h = round(19 * sy)
    col_proj_x = col_badge_x + badge_w + round(14 * sx)
    ago_w = round(62 * sx)

    for idx in range(4):
        ry0 = tbl_y + idx * row_h
        ry1 = ry0 + row_h - 1
        if idx > 0:
            draw.line((x0 + 2, ry0, x1 - 2, ry0), fill=0, width=1)
        if not usable:
            if idx == 1:
                msg = "工作台摘要暂不可用" if not data or data.get("state") == "unavailable" else "工作台摘要已过期"
                mw = round(draw.textlength(msg, font=f_row))
                draw.text((x0 + max(pad_x, (x1 - x0 - mw) // 2), _text_center_y(f_row, ry0, ry1)),
                          msg, font=f_row, fill=34)
            continue
        if not agent_list:
            if idx == 1:
                msg = "暂无活跃或近期会话"
                mw = round(draw.textlength(msg, font=f_row))
                draw.text((x0 + max(pad_x, (x1 - x0 - mw) // 2), _text_center_y(f_row, ry0, ry1)),
                          msg, font=f_row, fill=34)
            continue
        if idx >= len(agent_list):
            continue

        entry = agent_list[idx]
        ty = _text_center_y(f_row, ry0, ry1)
        name = _fit_text(draw, entry.get("agent") or "Agent", f_row, col_badge_x - (x0 + pad_x) - 6)
        draw.text((x0 + pad_x, ty), name, font=f_row, fill=0)

        by0 = ry0 + max(2, (row_h - badge_h) // 2)
        _draw_k3b_agent_badge(draw, col_badge_x, by0, col_badge_x + badge_w, by0 + badge_h,
                              str(entry.get("status") or "unknown"), f_badge)

        proj_name = _fit_text(draw, entry.get("project") or "—", f_row, (x1 - pad_x - ago_w) - col_proj_x - 6)
        draw.text((col_proj_x, ty), proj_name, font=f_row, fill=0)

        ago_txt = _fit_text(draw, _k3b_ago(entry.get("ago_seconds")), f_badge, ago_w)
        aw = round(draw.textlength(ago_txt, font=f_badge))
        draw.text((x1 - pad_x - aw, _text_center_y(f_badge, ry0, ry1)), ago_txt, font=f_badge, fill=0)

    # 项目脉冲双层黑框进度槽 + 图例
    p_y = tbl_y + 4 * row_h
    draw.line((x0, p_y, x1, p_y), fill=0, width=2)
    bar_x0, bar_x1 = x0 + pad_x, x1 - pad_x
    slot_y0 = p_y + round(7 * sy)
    slot_y1 = slot_y0 + round(14 * sy)
    tot = proj.get("total") or 0
    act = proj.get("active") or 0
    ver = proj.get("verify") or 0
    done = proj.get("done") or 0
    segs = [(act / tot, "solid"), (ver / tot, "hatch"), (done / tot, "empty")] if usable and tot > 0 else []
    _draw_double_slot(image, draw, bar_x0, slot_y0, bar_x1, slot_y1, segs)

    leg_y0 = slot_y1 + round(5 * sy)
    sw_sz = max(8, round(10 * smin))
    leg_cy = _text_center_y(f_leg, leg_y0, y1 - 2)
    sw_y0 = leg_y0 + max(1, (y1 - 2 - leg_y0 - sw_sz) // 2)
    cur_x = bar_x0
    for label_text, sw_mode in (
        (f"推进中 {act if usable and proj else '--'}", "solid"),
        (f"待验收 {ver if usable and proj else '--'}", "hatch"),
        (f"已收口 {done if usable and proj else '--'}", "empty"),
    ):
        if sw_mode == "solid":
            draw.rectangle((cur_x, sw_y0, cur_x + sw_sz, sw_y0 + sw_sz), fill=0)
        elif sw_mode == "hatch":
            draw.rectangle((cur_x, sw_y0, cur_x + sw_sz, sw_y0 + sw_sz), fill=255, outline=0, width=1)
            _fill_hatch_45(image, cur_x + 1, sw_y0 + 1, cur_x + sw_sz, sw_y0 + sw_sz)
        else:
            draw.rectangle((cur_x, sw_y0, cur_x + sw_sz, sw_y0 + sw_sz), fill=255, outline=0, width=1)
        cur_x += sw_sz + round(5 * sx)
        draw.text((cur_x, leg_cy), label_text, font=f_leg, fill=0)
        cur_x += round(draw.textlength(label_text, font=f_leg)) + round(18 * sx)

    total_lbl = f"共 {tot} 项" if usable and tot > 0 else "共 -- 项"
    tw = round(draw.textlength(total_lbl, font=f_leg))
    draw.text((bar_x1 - tw, leg_cy), total_lbl, font=f_leg, fill=0)


def _draw_k3b_channels_panel(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
                             data: dict[str, Any] | None, usable: bool,
                             sx: float, sy: float) -> None:
    smin = min(sx, sy)
    f_bar = _font(max(10, round(12 * smin)), bold=True)
    f_bar_r = _font(max(9, round(11 * smin)), bold=True)
    f_name = _font(max(10, round(12 * smin)), bold=True)
    f_sub = _font(max(9, round(11 * smin)), bold=True)
    bar_h = round(25 * sy)

    channels = (data.get("channels") if (usable and data and isinstance(data.get("channels"), list)) else None) or []
    if usable and channels:
        online_cnt = sum(1 for c in channels if c.get("online"))
        right_lbl = "全渠道可用" if online_cnt == len(channels) else f"{online_cnt}/{len(channels)} 在线"
        slots = channels[:3]
    else:
        right_lbl = "暂不可用" if not usable else "无数据"
        slots = [{"name": n, "online": False, "status_text": right_lbl} for n in ("小浣熊", "Loomy", "Trae")]

    draw.rectangle((x0, y0, x1, y1), fill=255, outline=0, width=2)
    _k3b_panel_bar(draw, x0, y0, x1, bar_h, "扩展渠道在线状态", right_lbl, f_bar, f_bar_r)

    grid_y = y0 + bar_h
    cols = max(1, len(slots))
    body_w = x1 - x0
    pad_x = round(8 * sx)
    for i, ch in enumerate(slots):
        cx0 = x0 + round(body_w * i / cols)
        cx1 = x0 + round(body_w * (i + 1) / cols)
        if i > 0:
            draw.line((cx0, grid_y, cx0, y1), fill=0, width=2)
        cw = (cx1 - cx0) - pad_x * 2
        cname = str(ch.get("name") or "")
        if usable and channels and not ch.get("online"):
            cname += " [离线]"
        st_text = str(ch.get("status_text") or ("在线正常" if ch.get("online") else "离线"))
        draw.text((cx0 + pad_x, grid_y + round(7 * sy)), _fit_text(draw, cname, f_name, cw), font=f_name, fill=0)
        draw.text((cx0 + pad_x, grid_y + round(25 * sy)), _fit_text(draw, st_text, f_sub, cw), font=f_sub, fill=34)


def _draw_k3b_usage_panel(image: Image.Image, draw: ImageDraw.ImageDraw,
                          x0: int, y0: int, x1: int, y1: int,
                          data: dict[str, Any] | None, usable: bool,
                          credits_data: Any, sx: float, sy: float) -> None:
    smin = min(sx, sy)
    f_bar = _font(max(10, round(12 * smin)), bold=True)
    f_bar_r = _font(max(9, round(11 * smin)), bold=True)
    f_tot = _font(max(16, round(23 * smin)), bold=True)
    f_unit = _font(max(10, round(12 * smin)), bold=True)
    f_mname = _font(max(10, round(12 * smin)), bold=True)
    f_msub = _font(max(9, round(11 * smin)), bold=True)
    f_wlbl = _font(max(9, round(12 * smin)), bold=True)
    f_wval = _font(max(11, round(16 * smin)), bold=True)

    bar_h = round(25 * sy)
    quota = (data.get("quota") if (usable and data and isinstance(data.get("quota"), dict)) else None) or {}
    q_ok = bool(usable and quota.get("available"))
    calls = quota.get("calls")

    draw.rectangle((x0, y0, x1, y1), fill=255, outline=0, width=2)
    right_hdr = f"{calls} 次调用" if (q_ok and calls is not None) else "-- 次调用"
    _k3b_panel_bar(draw, x0, y0, x1, bar_h, "今日消耗与每日福利", right_hdr, f_bar, f_bar_r)

    body_y = y0 + bar_h
    body_w = x1 - x0
    split_x = x0 + round(body_w * 331 / 572)
    draw.line((split_x, body_y, split_x, y1), fill=0, width=2)

    # 左栏：总 Token + Top 3 模型（双层黑框进度槽）
    lx0, lx1 = x0 + round(10 * sx), split_x - round(10 * sx)
    lw = lx1 - lx0
    tot_tok = quota.get("total_tokens_human") if q_ok else None
    tot_str = tot_tok if tot_tok else "--"
    draw.text((lx0, body_y + round(12 * sy)), _fit_text(draw, tot_str, f_tot, lw - round(45 * sx)), font=f_tot, fill=0)
    tot_w = round(draw.textlength(tot_str, font=f_tot))
    draw.text((lx0 + tot_w + round(6 * sx), body_y + round(21 * sy)),
              "Token" if q_ok else "暂无用量", font=f_unit, fill=0)

    models_list = (quota.get("top_models") or [])[:3] if q_ok else []
    model_y0 = body_y + round(54 * sy)
    row_step = max(round(56 * sy), round((y1 - model_y0 - round(8 * sy)) / 3))
    if not models_list:
        msg = "工作台用量暂不可用" if not usable else "今日暂无模型调用"
        draw.text((lx0, model_y0 + round(24 * sy)), _fit_text(draw, msg, f_mname, lw), font=f_mname, fill=34)
    for idx, item in enumerate(models_list):
        my = model_y0 + idx * row_step
        if my + round(48 * sy) > y1 - 4:
            break
        pct = max(0, min(100, int(item.get("share_pct") or 0)))
        pct_str = f"{pct}%"
        pw = round(draw.textlength(pct_str, font=f_mname))
        m_name = _fit_text(draw, item.get("model") or "?", f_mname, lw - pw - 8)
        draw.text((lx0, my), m_name, font=f_mname, fill=0)
        draw.text((lx1 - pw, my), pct_str, font=f_mname, fill=0)

        mc = item.get("calls")
        mtok = item.get("tokens_human") or "0"
        sub_txt = f"{mc if mc is not None else 0}次 · {mtok}"
        draw.text((lx0, my + round(18 * sy)), _fit_text(draw, sub_txt, f_msub, lw), font=f_msub, fill=34)
        _draw_double_slot(image, draw, lx0, my + round(36 * sy), lx1, my + round(48 * sy),
                          [(pct / 100.0, "solid")])

    # 右栏：国内积分（或 WB 账号池回退） + 每日签到黑框 + 待领免费福利实心黑块
    rx0, rx1 = split_x + round(10 * sx), x1 - round(10 * sx)
    rw = rx1 - rx0
    pad_v = round(12 * sy)
    gap_v = round(12 * sy)
    ry_cur = body_y + pad_v
    avail_r = max(120, (y1 - body_y) - pad_v * 2 - gap_v * 2)
    top_h = round(avail_r * 0.30)
    mid_h = round(avail_r * 0.33)

    # 1) 国内积分（有密码且拉到余额）或回退 WB 账号池
    pool = _pick_credits_pool(credits_data)
    if pool is not None:
        realm, remain, size = pool
        pct = max(0.0, min(100.0, remain / size * 100.0))
        pool_lbl = f"{_K3B_REALM_CN.get(realm, realm)}积分（剩 {pct:.0f}%）"
        pool_val = f"{remain:,.0f} / {size:,.0f}"
        pool_ratio = pct / 100.0
    else:
        pool_lbl = "WB 账号池"
        ready, total = quota.get("accounts_ready"), quota.get("accounts_total")
        if usable and ready is not None and total is not None and total > 0:
            pool_val = f"{ready}/{total} 就绪"
            pool_ratio = max(0.0, min(1.0, ready / total))
        else:
            pool_val = "未接入" if usable else "暂不可用"
            pool_ratio = 0.0

    draw.text((rx0, ry_cur), _fit_text(draw, pool_lbl, f_wlbl, rw), font=f_wlbl, fill=34)
    draw.text((rx0, ry_cur + round(20 * sy)), _fit_text(draw, pool_val, f_wval, rw), font=f_wval, fill=0)
    slot_y0 = ry_cur + round(46 * sy)
    _draw_double_slot(image, draw, rx0, slot_y0, rx1, min(ry_cur + top_h, slot_y0 + round(13 * sy)),
                      [(pool_ratio, "solid")])

    # 2) 每日签到黑框
    box2_y0 = ry_cur + top_h + gap_v
    box2_y1 = box2_y0 + mid_h
    draw.rectangle((rx0, box2_y0, rx1, box2_y1), fill=255, outline=0, width=2)
    ck_done, ck_tot = quota.get("checkin_done"), quota.get("checkin_total")
    if usable and ck_done is not None and ck_tot is not None:
        ck_val = f"{ck_done}/{ck_tot} " + ("已完成" if ck_tot > 0 and ck_done >= ck_tot else "进行中")
    else:
        ck_val = "暂无数据"
    draw.text((rx0 + round(9 * sx), box2_y0 + round(12 * sy)), "每日签到", font=f_wlbl, fill=34)
    draw.text((rx0 + round(9 * sx), box2_y0 + round(36 * sy)),
              _fit_text(draw, ck_val, f_wval, rw - round(18 * sx)), font=f_wval, fill=0)

    # 3) 待领免费福利（实心黑底白字）
    box3_y0 = box2_y1 + gap_v
    box3_y1 = y1 - pad_v
    draw.rectangle((rx0, box3_y0, rx1, box3_y1), fill=0)
    ft_cnt = quota.get("free_tokens_unclaimed_count") if usable else None
    ft_rmb = quota.get("free_tokens_unclaimed_rmb") if usable else None
    if ft_cnt is None:
        ft_val = "暂无数据"
    elif ft_cnt == 0:
        ft_val = "今日已领完"
    elif ft_rmb is not None:
        ft_val = f"{ft_cnt} 张 · 约 ¥{ft_rmb}"
    else:
        ft_val = f"{ft_cnt} 张"
    draw.text((rx0 + round(9 * sx), box3_y0 + round(14 * sy)), "待领免费福利", font=f_wlbl, fill=255)
    draw.text((rx0 + round(9 * sx), box3_y0 + round(39 * sy)),
              _fit_text(draw, ft_val, f_wval, rw - round(18 * sx)), font=f_wval, fill=255)


def _draw_k3b_footer(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
                     values: dict[str, Any], summary_wrap: dict[str, Any],
                     relay_status: str, usable: bool, smin: float) -> None:
    f_foot = _font(max(9, round(11 * smin)), bold=True)
    data = summary_wrap.get("data") if isinstance(summary_wrap.get("data"), dict) else None
    updated_at = (data or {}).get("updated_at")
    now_hhmm = str(values.get("system.time") or dt.datetime.now().strftime("%H:%M"))
    wb_hhmm = updated_at[11:16] if isinstance(updated_at, str) and len(updated_at) >= 16 else "--:--"
    left_txt = f"更新 {now_hhmm} · 工作台 {wb_hhmm}"

    if not summary_wrap.get("collected"):
        health_txt = "未采集"
    elif summary_wrap.get("cache_stale"):
        health_txt = "缓存过期"
    elif not usable:
        health_txt = "工作台异常"
    elif (data or {}).get("state") == "partial":
        health_txt = f"{relay_status} · 不完整"
    else:
        health_txt = relay_status
    ver = str(values.get("system.client_version") or "").strip()
    right_txt = f"{health_txt} · v{ver}" if ver else health_txt
    _k3b_panel_bar(draw, x0, y0, x1, y1 - y0 + 1, left_txt, right_txt, f_foot, f_foot, pad_x=10)


def _draw_k3b(image: Image.Image, draw: ImageDraw.ImageDraw, region: dict[str, Any],
              values: dict[str, Any], scale: float, inset: int = 0) -> None:
    """绘制 K3-B（黑条双层框重墨版）全屏仪表盘，严禁浅灰底色（>40）吞色。"""
    rx, ry, rw, rh = _box(region, scale, inset)
    sx, sy = rw / 600.0, rh / 760.0
    smin = min(sx, sy)

    payload = resolve(region, values)
    if not isinstance(payload, dict):
        payload = {}
    summary_wrap = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    data = summary_wrap.get("data") if isinstance(summary_wrap.get("data"), dict) else None
    credits_data = payload.get("credits")
    relay_status = str(payload.get("relay_status") or "本机直连")

    x0 = rx + round(14 * sx)
    x1 = rx + rw - round(14 * sx)
    hdr_txt, snap_txt, usable = _k3b_banner_state(summary_wrap)

    # 1) 顶部 32px 实心黑底白字横幅
    hdr_y0 = ry + round(9 * sy)
    _k3b_panel_bar(draw, x0, hdr_y0, x1, round(32 * sy), hdr_txt, snap_txt,
                   _font(max(12, round(16 * smin)), bold=True),
                   _font(max(10, round(12 * smin)), bold=True), pad_x=10)

    # 2) 4 格 2px 黑框 KPI
    kpi_y0 = ry + round(48 * sy)
    _draw_k3b_kpis(draw, x0, kpi_y0, x1, ry + round(122 * sy), data, usable, smin,
                   _font(max(9, round(11 * smin)), bold=True),
                   _font(max(18, round(25 * smin)), bold=True),
                   _font(max(9, round(11 * smin)), bold=True))

    # 3) 面板 1：AI 施工与 14 项目脉冲
    _draw_k3b_agents_panel(image, draw, x0, ry + round(129 * sy), x1, ry + round(308 * sy),
                           data, usable, sx, sy)

    # 4) 面板 2：扩展渠道在线状态
    _draw_k3b_channels_panel(draw, x0, ry + round(315 * sy), x1, ry + round(386 * sy),
                             data, usable, sx, sy)

    # 5) 面板 3：今日消耗与每日福利
    _draw_k3b_usage_panel(image, draw, x0, ry + round(393 * sy), x1, ry + round(719 * sy),
                          data, usable, credits_data, sx, sy)

    # 6) 底部 26px 实心黑底白字健康栏
    _draw_k3b_footer(draw, x0, ry + round(726 * sy), x1, ry + round(752 * sy),
                     values, summary_wrap, relay_status, usable, smin)

