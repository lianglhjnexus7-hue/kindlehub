"""Antigravity / Gemini CLI 适配器：只读会话文件的写入时间。

数据来源：~/.gemini/antigravity*/conversations/*.db 与 annotations/*.pbtxt

这个产品没有对外暴露 turn 生命周期事件，会话内容放在加密/私有的 db 里。
所以这里**只按文件 mtime 推测**，一律标记 low 置信度，绝不声称确证。
不读取 db 内容（可能含对话正文）、不读日志、不读凭据。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _mtime, _short
from . import register

GEMINI_ROOT = Path.home() / ".gemini"

# 只看这些「会话状态」目录，避开 log / cache / node_modules / config
_CONVERSATION_PARTS = ("conversations", "annotations")
_SKIP_PARTS = ("node_modules", "cache", "crashes", "log", "logs", "updater")


def _candidate_files() -> list[Path]:
    out: list[Path] = []
    for sub in ("antigravity", "antigravity-cli"):
        root = GEMINI_ROOT / sub
        if not root.is_dir():
            continue
        # os.walk 边遍历边剪枝：rglob("*") 会先把 node_modules 整棵走完
        # 再按路径过滤，几万个小文件纯属浪费
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
            rel = Path(dirpath).relative_to(root).parts
            if not any(s in rel for s in _CONVERSATION_PARTS):
                continue
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix not in {".db", ".pbtxt", ".json"}:
                    continue
                out.append(p)
    return out


def state() -> AgentState:
    out = AgentState(agent="Antigravity", source="file_mtime")
    if not GEMINI_ROOT.is_dir():
        out.status = "disconnected"
        return out

    files = _candidate_files()
    if not files:
        out.status = "unknown"
        return out

    newest = max(files, key=_mtime)
    stamp = _mtime(newest)
    age = time.time() - stamp
    out.last_activity = stamp
    out.confidence = "low"          # 无事件接口，永远是推测
    out.project = None
    out.task = None

    if age < RUNNING_WINDOW:
        out.status = "running"
        out.basis = "会话文件刚有写入，但无 turn 事件可确证（推测）"
    elif age < STALE_WINDOW:
        out.status = "idle"
        out.basis = f"会话文件 {int(age / 60)} 分钟前停写，无生命周期事件（推测）"
    else:
        out.status = "unknown"
        out.basis = "会话文件久未更新（推测）"
    return out


register("Antigravity", state)
