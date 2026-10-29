"""WorkBuddy 用量看板数据源。

看板本身是 950KB 的单页应用，靠 JS 渲染，Kindle 无法显示。
这里改为在服务端调用它的 JSON 接口，把聚合数字重绘到墨水屏上。

接口：
- POST /panel/login              {password} -> {token}
- GET  /usage/analytics?range=…  header X-Panel-Token: <token>
      返回 summary.window（总量）与 models[]（按模型明细）

只读取聚合字段。账号 UUID、昵称、credential_file、日志路径一律不碰、不显示。
密码存放于 secrets.json（0600），不进入 dashboard.json。

摘要与明细共用同一次 analytics 请求，避免重复拉取 ~140KB。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from . import secrets

DEFAULT_URL = "http://127.0.0.1:8788"
DEFAULT_RANGE = "today"
TIMEOUT = 5.0
TTL = 120.0        # 成功结果缓存 2 分钟
FAIL_TTL = 30.0    # 失败只缓存 30 秒，看板恢复后能尽快跟上
TOKEN_TTL = 1800.0

_payload: dict[str, Any] = {"key": None, "at": -1e9, "data": None, "ttl": 0.0}
_token: dict[str, Any] = {"value": None, "at": -1e9, "base": None}


def _request(url: str, payload: dict | None = None, token: str | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "KindleHub/0.2"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Panel-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _login(base: str, password: str, force: bool = False) -> str | None:
    now = time.monotonic()
    if (not force and _token["value"] and _token["base"] == base
            and now - _token["at"] < TOKEN_TTL):
        return _token["value"]
    try:
        result = _request(base.rstrip("/") + "/panel/login", {"password": password})
    except Exception:
        return None
    token = result.get("token")
    if not token:
        return None
    _token.update({"value": token, "at": now, "base": base})
    return token


def status() -> dict[str, Any]:
    """供后台展示的数据源健康度。"""
    if _payload["data"] is None:
        return {"state": "empty", "age": None}
    age = round(time.monotonic() - _payload["at"])
    return {"state": "ok" if age < TTL else "stale", "age": age}


_credits_cache: dict[str, Any] = {"key": None, "at": -1e9, "data": None}

REALM_CN = {"cn": "国内", "intl": "国际", "all": "全部", "unknown": "未知"}

RANGE_CN = {
    "today": "今天",
    "d3": "近 3 日",
    "week": "本周",
    "month": "本月",
    "d30": "近 30 天",
    "all": "全部累计",
}


def credits(settings: dict[str, Any], fetch: bool = True, force: bool = False) -> dict | None:
    """汇总账号池的积分余额。

    /accounts 的每条记录里 credits.remain / credits.size 是积分余额与总额。
    只取这两个数字求和，账号 uid、昵称、凭证文件名一律不读。
    """
    config = settings.get("workbuddy") or {}
    base = str(config.get("url") or DEFAULT_URL)
    span = str(config.get("range") or DEFAULT_RANGE)
    now = time.monotonic()
    if (_credits_cache["key"] == base and _credits_cache["data"] is not None
            and now - _credits_cache["at"] < TTL and not force):
        return _credits_cache["data"]
    if not fetch:
        return _credits_cache["data"] if _credits_cache["key"] == base else None

    password = secrets.get("workbuddy_password")
    if not password:
        return None
    token = _login(base, password)
    if not token:
        return None
    try:
        payload = _request(f"{base.rstrip('/')}/accounts?realm=all", token=token)
    except Exception:
        return _credits_cache["data"] if _credits_cache["key"] == base else None

    # 国际版与国内版是两套账号池，积分不能混算
    by_realm: dict[str, dict[str, float]] = {}
    for account in payload.get("accounts") or []:
        block = account.get("credits")
        if not isinstance(block, dict):
            continue
        realm = str(account.get("realm") or "unknown")
        slot = by_realm.setdefault(realm, {"remain": 0.0, "size": 0.0, "accounts": 0})
        r, s = block.get("remain"), block.get("size")
        if isinstance(r, (int, float)):
            slot["remain"] += float(r)
        if isinstance(s, (int, float)):
            slot["size"] += float(s)
        slot["accounts"] += 1
    if not by_realm:
        return None

    # 各区域的今日消耗。/usage/analytics 支持 realm 参数，
    # 但返回体 ~140KB，所以只对实际存在的区域各取一次，并跟随 2 分钟缓存。
    for realm in list(by_realm):
        try:
            payload = _request(
                f"{base.rstrip('/')}/usage/analytics?range={span}&realm={realm}", token=token)
            window = (payload.get("summary") or {}).get("window") or {}
            by_realm[realm]["today_credit"] = float(window.get("credit") or 0.0)
            by_realm[realm]["today_cost"] = float(window.get("cost_cny") or 0.0)
        except Exception:
            by_realm[realm]["today_credit"] = None
            by_realm[realm]["today_cost"] = None

    result = {"realms": by_realm}
    _credits_cache.update({"key": base, "at": now, "data": result})
    return result


def _short(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{value / 1e6:.1f}M"
    if value >= 1e3:
        return f"{value / 1e3:.1f}K"
    return f"{value:.0f}"


def _load(settings: dict[str, Any], fetch: bool, force: bool) -> tuple[dict | None, str]:
    """取 analytics 载荷。返回 (payload, 错误文案)。"""
    config = settings.get("workbuddy") or {}
    base = str(config.get("url") or DEFAULT_URL)
    span = str(config.get("range") or DEFAULT_RANGE)
    key = f"{base}|{span}"

    now = time.monotonic()
    if (_payload["key"] == key and _payload["data"] is not None
            and now - _payload["at"] < _payload["ttl"] and not force):
        return _payload["data"], ""
    if not fetch:
        return _payload["data"] if _payload["key"] == key else None, ""

    def stale() -> tuple[dict | None, str]:
        """已有旧数据时优先返回它，只推迟重试。
        否则看板一次重启就会把整块明细刷成"看板未连接"。"""
        if _payload["data"] is not None and _payload["key"] == key:
            _payload.update({"at": now, "ttl": FAIL_TTL})
            return _payload["data"], ""
        return None, "看板未连接"

    password = secrets.get("workbuddy_password")
    if not password:
        return None, "未配置看板密码"

    token = _login(base, password)
    if not token:
        return stale()
    try:
        data = _request(f"{base.rstrip('/')}/usage/analytics?range={span}", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            token = _login(base, password, force=True)
            if not token:
                return None, "看板密码错误"
            try:
                data = _request(f"{base.rstrip('/')}/usage/analytics?range={span}", token=token)
            except Exception:
                return stale()
        else:
            return stale()
    except Exception:
        return stale()

    _payload.update({"key": key, "at": now, "data": data, "ttl": TTL})
    return data, ""


def _fail(settings: dict[str, Any], text: str) -> str:
    """失败结果短暂缓存，避免每次渲染都重试。"""
    config = settings.get("workbuddy") or {}
    base = str(config.get("url") or DEFAULT_URL)
    span = str(config.get("range") or DEFAULT_RANGE)
    _payload.update({"key": f"{base}|{span}", "at": time.monotonic(), "data": None, "ttl": FAIL_TTL})
    return text


def current(settings: dict[str, Any], fetch: bool = True, force: bool = False) -> str:
    """3 行总量摘要。fetch=False 时只读缓存，绝不联网。"""
    config = settings.get("workbuddy") or {}
    if not config.get("enabled", True):
        return ""
    data, err = _load(settings, fetch, force)
    if err:
        return _fail(settings, err) if fetch else err
    if data is None:
        return "获取中…" if not fetch else "看板未连接"

    window = (data.get("summary") or {}).get("window") or {}
    requests = int(window.get("requests") or 0)
    if not requests:
        return "今日暂无请求"
    errors = int(window.get("errors") or 0)
    success = (1 - errors / requests) * 100
    tokens = window.get("total_tokens") or 0
    hit = window.get("cache_hit_pct")
    if hit is None:
        prompt = window.get("prompt_tokens") or 0
        cached = window.get("cached_tokens") or 0
        hit = (cached / prompt * 100) if prompt else 0.0
    cost = window.get("cost_cny")

    lines = [f"请求 {requests} · 成功率 {success:.0f}%",
             f"Token {_short(tokens)} · 缓存 {hit:.0f}%"]
    if isinstance(cost, (int, float)) and cost > 0:
        lines.append(f"花费 ¥{cost:.2f}")
    return "\n".join(lines)


def models(settings: dict[str, Any], fetch: bool = True, force: bool = False,
           limit: int = 3, detail: bool = False) -> str:
    """按模型用量。

    首页（detail=False）：统计周期 + 模型名 + 次数/Token/费用，外加积分余额。
    详细（detail=True）：附加速度、缓存命中率、思考/缓存 Token、消耗积分。
    """
    config = settings.get("workbuddy") or {}
    if not config.get("enabled", True):
        return ""
    data, err = _load(settings, fetch, force)
    if err:
        return _fail(settings, err) if fetch else err
    if data is None:
        return "获取中…" if not fetch else "看板未连接"

    rows = []
    for item in data.get("models") or []:
        window = item.get("window") or {}
        tokens = window.get("total_tokens") or 0
        if not tokens:
            continue
        rows.append((tokens, item.get("model") or "?", window))
    rows.sort(reverse=True)
    if not rows:
        return "今日暂无模型消耗"

    span = str((settings.get("workbuddy") or {}).get("range") or DEFAULT_RANGE)
    period = RANGE_CN.get(span, span)
    lines = [f"统计周期：{period}", ""]
    for _tokens, name, window in rows[:limit]:
        requests = int(window.get("requests") or 0)
        total = window.get("total_tokens") or 0
        speed = window.get("speed_avg")
        hit = window.get("cache_hit_pct")
        reasoning = window.get("reasoning_tokens") or 0
        cached = window.get("cached_tokens") or 0
        cost = window.get("cost_cny")

        # 首页只保留三个决策指标：调用次数、总 Token、费用。
        # 速度/命中率/思考/缓存属于细节，放到 models_detail()。
        parts = [f"{requests} 次", _short(total)]
        if isinstance(cost, (int, float)) and cost > 0:
            parts.append(f"¥{cost:.2f}")
        else:
            parts.append("费用未计价")
        # 该模型消耗的积分（上游给的 credit 字段），0 也显示，保留两位小数
        credit = window.get("credit")
        if isinstance(credit, (int, float)):
            parts.append(f"{credit:.2f} 积分")

        lines.append(name)
        lines.append(" · ".join(parts))

        if detail:
            extra = []
            if isinstance(speed, (int, float)) and speed > 0:
                extra.append(f"速度 {speed:.0f}tok/s")
            if isinstance(hit, (int, float)):
                extra.append(f"缓存命中率 {hit:.1f}%")
            if reasoning:
                extra.append(f"思考 Token {_short(reasoning)}")
            if cached:
                extra.append(f"缓存 Token {_short(cached)}")
            credit = window.get("credit")
            if isinstance(credit, (int, float)) and credit > 0:
                extra.append(f"消耗 {credit:.1f} 积分")
            if extra:
                lines.append("   " + " · ".join(extra))

    balance = credits(settings, fetch=fetch, force=force)
    if balance:
        realms = balance.get("realms") or {}
        if realms:
            lines.append("")
        for realm, slot in sorted(realms.items(), key=lambda kv: -kv[1]["size"]):
            size = slot.get("size") or 0
            remain = slot.get("remain") or 0
            if size <= 0:
                continue
            label = REALM_CN.get(realm, realm)
            pct = remain / size * 100
            line = f"{label}积分 {remain:,.0f} / {size:,.0f}（剩 {pct:.0f}%）"
            today = slot.get("today_credit")
            if isinstance(today, (int, float)):
                line += f" · 今日 {today:,.0f}"
            lines.append(line)
    return "\n".join(lines)


def models_detail(settings: dict[str, Any], fetch: bool = True, force: bool = False) -> str:
    """详细版：在首页三指标之外，附加速度、命中率、思考/缓存 Token、积分。"""
    return models(settings, fetch=fetch, force=force, detail=True)
