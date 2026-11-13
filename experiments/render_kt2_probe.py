"""实验脚本 v2：验证 KT2 600x800 + macOS 中文字体 + 等比缩放。
不修改 vendor/ 源码。画布按设备长宽比归一化（1000 x 1333.33），彻底消除纵向压缩。
"""
from __future__ import annotations
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 600, 800
BASE = 1000.0
CANVAS_H = BASE * H / W          # 1333.33
PAD = max(8, round(min(W, H) * 0.022))

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]

def font(units: float, bold: bool = False):
    px = max(9, round(units * W / BASE))
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size=px, index=0)
        except Exception:
            continue
    return ImageFont.load_default(size=px)

def box(r):
    return (round(r["x"]*W/BASE), round(r["y"]*H/CANVAS_H),
            round(r["w"]*W/BASE), round(r["h"]*H/CANVAS_H))

TOKEN = re.compile(r"[A-Za-z0-9@._/:%+\-]+|\s+|.")

def wrap(d, text, f, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for tok in TOKEN.findall(para):
            if d.textlength(cur + tok, font=f) <= max_w or not cur:
                cur += tok
            else:
                lines.append(cur); cur = tok.lstrip() if tok.isspace() else tok
        lines.append(cur)
    return lines

def card(d, r, label, label_units=13):
    x, y, w, h = box(r)
    d.rounded_rectangle((x, y, x+w, y+h), radius=max(4, round(7*W/758)), outline=30, width=2)
    f = font(label_units, True)
    d.text((x+PAD, y+PAD), label, font=f, fill=15)
    return x+PAD, y+PAD+round(f.getbbox("Ag")[3]-f.getbbox("Ag")[1])+6, w-PAD*2, h

def body(d, r, text, units, bold=False, align="left"):
    x, y, w, h = box(r)
    f = font(units, bold)
    lh = round(f.getbbox("Ag")[3]-f.getbbox("Ag")[1]) + 4
    cy = y + PAD
    for line in wrap(d, str(text), f, w-PAD*2):
        if cy + lh > y + h - PAD:
            break
        if align == "right":
            d.text((x+w-PAD, cy), line, font=f, fill=15, anchor="ra")
        else:
            d.text((x+PAD, cy), line, font=f, fill=15)
        cy += lh
    return cy

def metric(d, r, label, pct):
    cx, cy, cw, ch = card(d, r, label)
    f = font(30, True)
    d.text((cx, cy), f"{pct:.0f}%", font=f, fill=15)
    x, y, w, h = box(r)
    bh = max(12, round(16*W/758))
    by = y + h - PAD - bh
    d.rectangle((x+PAD, by, x+w-PAD, by+bh), outline=15, width=2)
    d.rectangle((x+PAD+3, by+3, x+PAD+3+round((w-PAD*2-6)*pct/100), by+bh-3), fill=15)

def rows(d, r, items):
    cx, cy, cw, ch = card(d, r, "Agent 状态 · 实时")
    f = font(18)
    lh = round(f.getbbox("Ag")[3]-f.getbbox("Ag")[1]) + 11
    x, y, w, h = box(r)
    for name, status, ok in items:
        if cy + lh > y + h - PAD:
            break
        my = cy + max(1, lh//2 - 5)
        if ok:
            d.ellipse((x+PAD, my, x+PAD+10, my+10), fill=15)
        else:
            d.ellipse((x+PAD, my, x+PAD+10, my+10), outline=15, width=2)
        d.text((x+PAD+20, cy), f"{name} · {status}", font=f, fill=15)
        cy += lh

def main():
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    card(d, {"x":20,"y":20,"w":520,"h":160}, "本地时间")
    body(d, {"x":20,"y":60,"w":520,"h":120}, "15:28", 58, True)
    card(d, {"x":560,"y":20,"w":420,"h":160}, "今天")
    body(d, {"x":560,"y":60,"w":420,"h":120}, "周四 10月22日", 20)

    card(d, {"x":20,"y":200,"w":960,"h":180}, "天气 · 上海")
    body(d, {"x":20,"y":240,"w":960,"h":140}, "21°C 多云转晴，东南风 3 级", 32, True)

    metric(d, {"x":20,"y":400,"w":300,"h":200}, "CPU", 22)
    metric(d, {"x":350,"y":400,"w":300,"h":200}, "内存", 62)
    metric(d, {"x":680,"y":400,"w":300,"h":200}, "磁盘", 58)

    rows(d, {"x":20,"y":620,"w":960,"h":250}, [
        ("Codex", "未连接", False),
        ("OpenCode", "未连接", False),
    ])
    card(d, {"x":20,"y":890,"w":960,"h":230}, "今日待办")
    body(d, {"x":20,"y":930,"w":960,"h":190},
         "① 复核 Kindle 渲染管线\n② 接入 RSS 与天气源\n③ 管理后台最小可用版", 19)

    body(d, {"x":20,"y":1140,"w":960,"h":50}, "每 5 分钟刷新", 13, align="right")

    out = Path("artifacts/kt2-adapted-preview.png")
    img.quantize(colors=16, dither=Image.Dither.NONE).convert("L").save(out)
    im = Image.open(out)
    print(out.resolve(), im.size, "grays:", len(set(im.get_flattened_data() if hasattr(im,'get_flattened_data') else im.getdata())))

if __name__ == "__main__":
    main()
