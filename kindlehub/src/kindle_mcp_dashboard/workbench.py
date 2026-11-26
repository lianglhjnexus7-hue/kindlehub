"""工作台摘要数据源（workbench，127.0.0.1:8799）。

只消费 Workbench 为 Kindle 提供的白名单摘要（v1/v2）；渲染路径只读内存缓存。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import urllib.request
from typing import Any

TIMEOUT = 2.0
TTL = 45.0
# Workbench 每 60 秒预热一次；两轮都未更新，再额外留一个 TTL 容错。
CACHE_STALE_AFTER = TTL + 2 * 60
DEFAULT_URL = "http://127.0.0.1:8799"
MAX_RESPONSE_BYTES = 64 * 1024
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z")
_SLOT = re.compile(r"\d{2}:\d{2}\Z")
_STATES = {"ok", "partial", "stale", "unavailable"}
_AGENT_STATUSES = {"running", "waiting", "error", "idle", "unknown", "disconnected"}
_CONFIDENCES = {"high", "medium", "low"}
_PATH_PREFIXES = ("/Users/", "/home/", "/private/")
_STATE_WARNINGS = {
    "partial": "数据不完整",
    "stale": "数据已过期",
    "unavailable": "暂不可用",
}

_cache: dict[str, Any] = {"data": None, "at": -1e9, "error": ""}


def _unavailable() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "state": "unavailable",
        "updated_at": None,
        "pending_decisions": None,
        "job_alerts": None,
        "next_slot": None,
        "attention": None,
        "agents": None,
        "projects": None,
        "channels": None,
        "quota": None,
    }


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return value


def _next_slot(value: Any) -> str | None:
    if not isinstance(value, str) or not _SLOT.fullmatch(value):
        return None
    try:
        dt.datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return value


def _count(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return value
    raise ValueError("invalid summary count")


def _nonneg_int(value: Any, *, rounded: bool = False) -> int | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid non-negative number")
    return round(float(value)) if rounded else int(value)


def _text(value: Any, limit: int = 24, *, allow_none: bool = False,
          non_empty: bool = False) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError("missing text")
    if not isinstance(value, str):
        raise ValueError("invalid text")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("control char in text")
    cleaned = value.strip()
    if non_empty and not cleaned:
        raise ValueError("empty text")
    if any(prefix in cleaned for prefix in _PATH_PREFIXES):
        raise ValueError("path leakage rejected")
    return cleaned[:limit]


def _text_list(value: Any, max_items: int, item_limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("invalid text list")
    return [_text(item, item_limit, non_empty=True) for item in value[:max_items]]


def _decode_attention(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid attention block")
    return {
        "pending_decisions": _count(raw.get("pending_decisions")),
        "pending_handoffs": _count(raw.get("pending_handoffs")),
        "services_ok": _count(raw.get("services_ok")),
        "services_total": _count(raw.get("services_total")),
        "services_down": _text_list(raw.get("services_down"), 5, 16),
        "job_alerts": _count(raw.get("job_alerts")),
        "alerts": _text_list(raw.get("alerts"), 5, 24),
    }


def _decode_agents(raw: Any) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("invalid agents block")
    items: list[dict[str, Any]] = []
    for entry in raw[:8]:
        if not isinstance(entry, dict):
            raise ValueError("invalid agent entry")
        status_val = _text(entry.get("status"), 16, non_empty=True)
        if status_val not in _AGENT_STATUSES:
            raise ValueError("invalid agent status")
        conf_raw = entry.get("confidence")
        conf_val = _text("low" if conf_raw is None else conf_raw, 10, non_empty=True)
        if conf_val not in _CONFIDENCES:
            raise ValueError("invalid agent confidence")
        items.append({
            "agent": _text(entry.get("agent"), 18, non_empty=True),
            "status": status_val,
            "confidence": conf_val,
            "project": _text(entry.get("project"), 18, allow_none=True),
            "ago_seconds": _nonneg_int(entry.get("ago_seconds")),
        })
    return items


def _decode_projects(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid projects block")
    return {
        "total": _count(raw.get("total")),
        "active": _count(raw.get("active")),
        "verify": _count(raw.get("verify")),
        "done": _count(raw.get("done")),
    }


def _decode_channels(raw: Any) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("invalid channels block")
    items: list[dict[str, Any]] = []
    for entry in raw[:3]:
        if not isinstance(entry, dict):
            raise ValueError("invalid channel entry")
        online = entry.get("online")
        if type(online) is not bool:
            raise ValueError("invalid channel online flag")
        items.append({
            "name": _text(entry.get("name"), 12, non_empty=True),
            "online": online,
            "status_text": _text(entry.get("status_text"), 18),
        })
    return items


def _decode_quota(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("invalid quota block")
    available = raw.get("available")
    if type(available) is not bool:
        raise ValueError("invalid quota available flag")
    models_raw = raw.get("top_models")
    if models_raw is None:
        models_raw = []
    elif not isinstance(models_raw, list):
        raise ValueError("invalid top_models")
    top_models: list[dict[str, Any]] = []
    for entry in models_raw[:3]:
        if not isinstance(entry, dict):
            raise ValueError("invalid model entry")
        top_models.append({
            "model": _text(entry.get("model"), 26, non_empty=True),
            "calls": _count(entry.get("calls")),
            "tokens_human": _text(entry.get("tokens_human"), 16, allow_none=True),
            "share_pct": _count(entry.get("share_pct")),
        })
    return {
        "available": available,
        "calls": _count(raw.get("calls")),
        "total_tokens_human": _text(raw.get("total_tokens_human"), 16, allow_none=True),
        "top_models": top_models,
        "accounts_ready": _count(raw.get("accounts_ready")),
        "accounts_total": _count(raw.get("accounts_total")),
        "checkin_done": _count(raw.get("checkin_done")),
        "checkin_total": _count(raw.get("checkin_total")),
        "free_tokens_unclaimed_count": _count(raw.get("free_tokens_unclaimed_count")),
        "free_tokens_unclaimed_rmb": _nonneg_int(
            raw.get("free_tokens_unclaimed_rmb"), rounded=True),
    }


def _decode(payload: Any) -> dict[str, Any]:
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload["schema_version"] not in {1, 2} or payload.get("state") not in _STATES):
        raise ValueError("invalid Kindle summary")

    updated_at = payload.get("updated_at")
    next_slot = payload.get("next_slot")
    if updated_at is not None and _timestamp(updated_at) is None:
        raise ValueError("invalid update time")
    if next_slot is not None and _next_slot(next_slot) is None:
        raise ValueError("invalid next slot")

    out: dict[str, Any] = {
        "schema_version": payload["schema_version"],
        "state": payload["state"],
        "updated_at": updated_at,
        "pending_decisions": _count(payload.get("pending_decisions")),
        "job_alerts": _count(payload.get("job_alerts")),
        "next_slot": next_slot,
        "attention": None,
        "agents": None,
        "projects": None,
        "channels": None,
        "quota": None,
    }
    if payload["schema_version"] == 2 and payload["state"] in {"ok", "partial"}:
        out["attention"] = _decode_attention(payload.get("attention"))
        out["agents"] = _decode_agents(payload.get("agents"))
        out["projects"] = _decode_projects(payload.get("projects"))
        out["channels"] = _decode_channels(payload.get("channels"))
        out["quota"] = _decode_quota(payload.get("quota"))
    return out


def _fetch(url: str) -> tuple[dict[str, Any], str]:
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/api/kindle-summary?v=2",
            headers={"User-Agent": "kindlehub"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                raise OSError("summary endpoint returned non-200")
            body = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("summary response too large")
        return _decode(json.loads(body.decode("utf-8"))), ""
    except Exception as exc:  # noqa: BLE001
        return _unavailable(), type(exc).__name__


def _ensure_cache(settings: dict[str, Any] | None, fetch: bool, force: bool) -> float:
    now = time.monotonic()
    if fetch and (force or now - _cache["at"] >= TTL):
        url = ((settings or {}).get("workbench") or {}).get("url") or DEFAULT_URL
        data, error = _fetch(str(url))
        _cache.update({"data": data, "at": now, "error": error})
    return now


def _format(data: dict[str, Any] | None, at: float, now: float) -> str:
    if data is None:
        return "尚未采集\n待确认：未知 · 异常任务：未知\n更新时间：未知"

    if now - at > CACHE_STALE_AFTER:
        return "本机缓存已过期\n更新时间：" + (data["updated_at"] or "未知")

    state = data["state"]
    lines = [_STATE_WARNINGS[state]] if state != "ok" else []
    if state in {"stale", "unavailable"}:
        lines.append("更新时间：" + (data["updated_at"] or "未知"))
        return "\n".join(lines)

    pending = data["pending_decisions"]
    alerts = data["job_alerts"]
    lines.append(
        f"待确认：{pending if pending is not None else '未知'} · "
        f"异常任务：{alerts if alerts is not None else '未知'}"
    )
    if data["next_slot"] is not None:
        lines.append("下次排程：" + data["next_slot"])
    lines.append("更新时间：" + (data["updated_at"] or "未知"))
    return "\n".join(lines)


def summary(settings: dict[str, Any] | None = None, fetch: bool = True,
            force: bool = False) -> str:
    """给屏幕的几行文字；fetch=False 绝不联网。"""
    now = _ensure_cache(settings, fetch, force)
    return _format(_cache["data"], _cache["at"], now)


def snapshot_data(settings: dict[str, Any] | None = None, fetch: bool = True,
                  force: bool = False) -> dict[str, Any]:
    """返回 K3-B 渲染所需的解码后白名单摘要与缓存新鲜度标记。"""
    now = _ensure_cache(settings, fetch, force)
    data = _cache["data"]
    return {
        "collected": data is not None,
        "cache_stale": data is not None and (now - _cache["at"] > CACHE_STALE_AFTER),
        "data": data,
    }


def status() -> dict[str, Any]:
    """返回最后一次采集结果与健康状态，兼顾健康检查和 /api/meta。"""
    data = _cache["data"]
    if data is None:
        return {
            "state": "empty",
            "age": None,
            "at": _cache["at"],
            "error": _cache["error"],
            "has": False,
        }
    age = round(time.monotonic() - _cache["at"])
    fresh_ok = (age <= CACHE_STALE_AFTER and not _cache["error"]
                and data.get("state") in {"ok", "partial"})
    return {
        "state": "ok" if fresh_ok else "stale",
        "age": age,
        "at": _cache["at"],
        "error": _cache["error"],
        "has": True,
    }
