"""Qoder 适配器：读取任务索引里的 stage 字段。

数据来源：~/.qoder/tasks/*/<n>.json
判断依据：businessInfo.stage 是明确的生命周期状态，不是推测。

只读 businessInfo（stage / begin_at）、subject 与文件 mtime。
不读 description（含任务正文）、不读任何凭据文件。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..agents import RUNNING_WINDOW, STALE_WINDOW, AgentState, _mtime, _project_name, _short
from . import register

QODER_DIR = Path.home() / ".qoder" / "tasks"

# Qoder 的 stage 取值 → 统一状态
STAGE_MAP = {
    "processing": "running",
    "running": "running",
    "in_progress": "running",
    "waiting": "waiting",
    "pending": "waiting",
    "completed": "idle",
    "done": "idle",
    "success": "idle",
    "failed": "error",
    "error": "error",
    "cancelled": "idle",
    "canceled": "idle",
}


def _read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError, TypeError):
        return None


def state() -> AgentState:
    out = AgentState(agent="Qoder", source="task_index")
    if not QODER_DIR.is_dir():
        out.status = "disconnected"
        return out

    files = [p for p in QODER_DIR.glob("*/*.json") if p.is_file()]
    if not files:
        out.status = "unknown"
        return out

    newest = max(files, key=_mtime)
    stamp = _mtime(newest)
    age = time.time() - stamp
    out.last_activity = stamp

    doc = _read(newest) or {}
    info = doc.get("businessInfo") or {}
    if not isinstance(info, dict):
        info = {}
    stage = str(info.get("stage") or "").lower()
    subject = doc.get("subject")

    # 项目名：Qoder 的任务文件没有工作区字段，从 description 里
    # 拿绝对路径会泄露本机目录结构，所以这里不猜，留空让屏幕显示 —
    out.project = None
    out.task = _short(subject) if subject else None

    if stage in STAGE_MAP:
        out.status = STAGE_MAP[stage]
        out.confidence = "high"
        out.basis = f"任务表 stage 为 {stage}"
        # processing 但文件早已不写了：多半是被中断，降级为空闲
        if out.status in {"running", "waiting"} and age >= RUNNING_WINDOW:
            out.status = "idle"
            out.confidence = "medium"
            out.basis += "，但已停止写入（可能被中断）"
    elif age < STALE_WINDOW:
        out.status = "unknown"
        out.confidence = "low"
        out.basis = f"未识别的 stage={stage or '空'}，仅按文件时间判断"
    else:
        out.status = "unknown"
        out.confidence = "low"
        out.basis = "任务文件久未更新且无有效状态字段"
    return out


register("Qoder", state)
