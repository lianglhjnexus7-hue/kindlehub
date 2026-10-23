"""天气数据源：Open-Meteo（免费、无需 API Key、无需注册）。

不使用 macOS 天气 App 的缓存：其数据位于受 TCC 保护的容器内，格式私有且不稳定。
不使用 IP 定位：实测移动网络出口 IP 在城市间漂移（ipinfo 报江门、wttr.in 报广州），不可信。

城市名通过设置项配置，经 Open-Meteo Geocoding 转经纬度后缓存。
任何失败都返回"未知"，绝不编造天气。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 6.0
TTL = 600.0        # 成功结果缓存 10 分钟
FAIL_TTL = 60.0    # 失败结果只缓存 1 分钟，避免网络抖动导致长时间空白

# WMO Weather interpretation codes -> 中文
WMO = {
    0: "晴", 1: "少云", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}

_cache: dict[str, Any] = {"key": None, "at": -1e9, "data": None, "ttl": 0.0}
_geo_cache: dict[str, Any] = {}


def _get_json(url: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "KindleHub/0.2"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _geocode(city: str) -> tuple[float, float, str] | None:
    """地名解析。

    GeoNames 的中文城市常以"市"结尾存储（湛江查不到、湛江市可以），
    因此按 原名 → 原名+市 → 原名+区 依次尝试。
    同名地点遍布全球（上海市会命中美国伊利诺伊州的小镇），
    所以取候选里人口最多的一个，而不是无脑取第一条。
    """
    if city in _geo_cache:
        return _geo_cache[city]
    for candidate in (city, city + "市", city + "区"):
        try:
            payload = _get_json(GEOCODE_URL, {
                "name": candidate, "count": 5, "language": "zh", "format": "json",
            })
        except Exception:
            return None  # 网络异常不写缓存，下次还能重试
        results = payload.get("results") or []
        if results:
            top = max(results, key=lambda r: (r.get("population") or 0))
            value = (top["latitude"], top["longitude"], top.get("name") or candidate)
            _geo_cache[city] = value
            return value
    _geo_cache[city] = None
    return None


def current(settings: dict[str, Any], fetch: bool = True, force: bool = False) -> str:
    """返回两行文本：温度+天气 / 湿度+风。

    fetch=False 时只读缓存，绝不发起网络请求——渲染路径必须用它，
    否则 e-ink 客户端会卡在 DNS/TLS 握手上等到超时。
    """
    city = str(settings.get("city") or "").strip()
    if not city:
        return "未设置城市"

    now = time.monotonic()
    fresh = _cache["key"] == city and now - _cache["at"] < _cache["ttl"] and _cache["data"]
    if fresh and not force:
        return _cache["data"]
    if not fetch:
        # 有过期数据就先用着（stale-while-revalidate），没有就显示获取中
        if _cache["key"] == city and _cache["data"]:
            return _cache["data"]
        return "获取中…"

    def remember(text: str, ok: bool) -> str:
        if not ok and _cache["key"] == city and _cache["data"]:
            # 网络抖动时保留上一次的好数据，只推迟重试，
            # 否则屏幕会从"30℃ 少云"闪成"未知"。
            _cache.update({"at": now, "ttl": FAIL_TTL})
            return _cache["data"]
        _cache.update({"key": city, "at": now, "data": text, "ttl": TTL if ok else FAIL_TTL})
        return text

    location = _geocode(city)
    if not location:
        return remember("未知", False)
    lat, lon, _label = location
    try:
        payload = _get_json(FORECAST_URL, {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "timezone": "auto",
        })
    except Exception:
        return remember("未知", False)

    cur = payload.get("current") or {}
    temp = cur.get("temperature_2m")
    code = cur.get("weather_code")
    humidity = cur.get("relative_humidity_2m")
    wind = cur.get("wind_speed_10m")
    if temp is None:
        return remember("未知", False)

    desc = WMO.get(int(code), "未知") if code is not None else "未知"
    # 用全角 ℃ 而非 °C：Hiragino 的 ° 字形带额外字距，中间会裂开
    line1 = f"{temp:.0f}℃ {desc}"
    parts = []
    if humidity is not None:
        parts.append(f"湿度 {humidity:.0f}%")
    if wind is not None:
        parts.append(f"风 {wind:.0f}km/h")
    line2 = " · ".join(parts) if parts else ""
    return remember(f"{line1}\n{line2}" if line2 else line1, True)


def status() -> dict[str, Any]:
    """供后台展示的数据源健康度。"""
    if not _cache["data"]:
        return {"state": "empty", "age": None, "city": _cache["key"]}
    age = round(time.monotonic() - _cache["at"])
    state = "ok" if age < TTL else "stale"
    return {"state": state, "age": age, "city": _cache["key"]}


def describe(settings: dict[str, Any]) -> dict[str, Any]:
    """供调试或后台使用的状态信息（不含敏感数据）。"""
    city = str(settings.get("city") or "").strip()
    return {"city": city or None, "cached": _cache["key"] == city and _cache["data"] is not None}
