"""Antigravity / Gemini CLI 适配器：只读会话文件的写入时间。

数据来源：
- ~/.gemini/antigravity*/conversations/*.db 与 annotations/*.pbtxt
- ~/.t3/userdata/providers/antigravity/*/antigravity-acp/{brain,conversations,annotations}/...

这个产品没有对外暴露 turn 生命周期事件，会话内容放在加密/私有的 db 里。
所以这里**只按文件 mtime 推测**，一律标记 low 置信度，绝不声称确证。
不读取 db / transcript 正文、不读日志、不读凭据。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _mtime, _short
from . import register

GEMINI_ROOT = Path.home() / ".gemini"
T3_ANTIGRAVITY_ROOT = Path.home() / ".t3" / "userdata" / "providers" / "antigravity"

# 只看这些「会话状态」目录，避开 log / cache / node_modules / config
_CONVERSATION_PARTS = ("conversations", "annotations")
_SKIP_PARTS = ("node_modules", "cache", "crashes", "log", "logs", "updater")
# T3 Code 下会话转录位于 brain/<id>/.system_generated/logs/transcript.jsonl，
# 不能剪掉 logs 目录，只跳过无关缓存、内建技能与海量中间步骤。
_T3_CONVERSATION_PARTS = ("brain", "conversations", "annotations")
_T3_SKIP_PARTS = ("node_modules", "cache", "crashes", "builtin", "scratch", "updater",
                  "steps", "messages", "chunks", "mcp", "bin", "worktrees")


def _walk_candidates(root: Path, include_parts: tuple[str, ...],
                     skip_parts: tuple[str, ...], suffixes: set[str]) -> list[Path]:
    """按目录剪枝遍历会话文件，只收后缀匹配的候选路径。"""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_parts]
        rel = Path(dirpath).relative_to(root).parts
        if not any(s in rel for s in include_parts):
            continue
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in suffixes:
                out.append(p)
    return out


def _candidate_files() -> list[Path]:
    out: list[Path] = []
    # 产品有 antigravity / antigravity-cli / antigravity-ide 等多个变体，
    # 按 antigravity* 全扫，避免漏掉正在使用的 IDE 版（与文档承诺一致）
    if GEMINI_ROOT.is_dir():
        for root in sorted(GEMINI_ROOT.glob("antigravity*")):
            if root.is_dir():
                out.extend(_walk_candidates(root, _CONVERSATION_PARTS, _SKIP_PARTS,
                                            {".db", ".pbtxt", ".json"}))
    if T3_ANTIGRAVITY_ROOT.is_dir():
        out.extend(_walk_candidates(T3_ANTIGRAVITY_ROOT, _T3_CONVERSATION_PARTS,
                                    _T3_SKIP_PARTS, {".jsonl", ".json", ".db", ".pbtxt"}))
    return out


def state() -> AgentState:
    out = AgentState(agent="Antigravity", source="file_mtime")
    if not GEMINI_ROOT.is_dir() and not T3_ANTIGRAVITY_ROOT.is_dir():
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
