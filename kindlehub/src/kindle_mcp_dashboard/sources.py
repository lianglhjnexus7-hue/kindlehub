"""实时数据源。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
改动：日期与星期改为中文；新增 agent/weather/todo 等外部源的占位约定，
未接入的来源一律返回"未知"，绝不伪造状态。
"""
from __future__ import annotations

import datetime as dt
import platform
import time
from pathlib import Path
from typing import Any

import psutil

from . import agents, weather, workbench, workbuddy

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# psutil.cpu_percent(interval=None) 依赖两次调用之间的时间差，
# 首次或调用过密时恒为 0。这里用带间隔的采样 + 1 秒缓存：
# 既保证读数真实，又避免管理后台拖拽预览时反复阻塞。
_CPU_TTL = 1.0
_cpu_cache = {"value": 0.0, "at": -1e9}


def _cpu_percent() -> int:
    now = time.monotonic()
    if now - _cpu_cache["at"] >= _CPU_TTL:
        _cpu_cache["value"] = psutil.cpu_percent(interval=0.1)
        _cpu_cache["at"] = now
    return round(_cpu_cache["value"])


def active_sources(data: dict[str, Any]) -> set[str]:
    """返回当前激活屏幕上实际引用的非空数据源集合。"""
    screens = data.get("screens") or []
    active_id = data.get("active_screen")
    screen = next((s for s in screens if isinstance(s, dict) and s.get("id") == active_id), None)
    if not screen:
        return set()
    return {
        str(region["source"])
        for region in (screen.get("regions") or [])
        if isinstance(region, dict) and isinstance(region.get("source"), str) and region.get("source")
    }


def _relay_status(settings: dict[str, Any]) -> str:
    relay_cfg = settings.get("relay") or {}
    if not relay_cfg.get("enabled"):
        return "本机直连"
    from . import web
    return web.relay_status_label(settings)


def snapshot(settings: dict[str, Any] | None = None, fetch: bool = True,
             needed: set[str] | None = None) -> dict[str, Any]:
    """采集实时数据源；传入 needed 时只采当前布局用到的源，避免重复扫盘与无谓联网。"""
    now = dt.datetime.now().astimezone()
    settings = settings or {}
    want_all = needed is None

    def want(*keys: str) -> bool:
        return want_all or bool(needed and needed.intersection(keys))

    values: dict[str, Any] = {
        "system.time": now.strftime("%H:%M"),
        "system.date": f"{_WEEKDAYS[now.weekday()]}\n{now.month}月{now.day}日",
        # 时间与日期合成一个源，供"首行大字 + 次行小字"的卡片使用
        "system.clock": f"{now.strftime('%H:%M')}\n{_WEEKDAYS[now.weekday()]} {now.month}月{now.day}日",
        "system.hostname": platform.node(),
    }
    if want("system.cpu_percent"):
        values["system.cpu_percent"] = _cpu_percent()
    if want("system.memory_percent"):
        values["system.memory_percent"] = round(psutil.virtual_memory().percent)
    if want("system.disk_percent"):
        values["system.disk_percent"] = round(psutil.disk_usage(Path.home()).percent)
    if want("system.load"):
        values["system.load"] = round(psutil.getloadavg()[0], 2)
    if want("system.uptime"):
        values["system.uptime"] = _duration(
            dt.timedelta(seconds=int(dt.datetime.now().timestamp() - psutil.boot_time())))

    if want("weather.current", "workbench.k3b"):
        values["weather.current"] = weather.current(settings.get("weather") or {}, fetch=fetch)
    if want("workbuddy.usage"):
        values["workbuddy.usage"] = workbuddy.current(settings, fetch=fetch)
    if want("workbuddy.models"):
        values["workbuddy.models"] = workbuddy.models(settings, fetch=fetch)
    if want("workbuddy.models_detail"):
        values["workbuddy.models_detail"] = workbuddy.models_detail(settings, fetch=fetch)
    if want("workbench.status"):
        values["workbench.status"] = workbench.summary(settings, fetch=fetch)
    if want("workbench.k3b"):
        wb_cfg = settings.get("workbuddy") or {}
        wb_credits = (
            workbuddy.credits(settings, fetch=fetch, include_today=False)
            if wb_cfg.get("enabled", False)
            else None
        )
        values["workbench.k3b"] = {
            "summary": workbench.snapshot_data(settings, fetch=fetch),
            "credits": wb_credits,
            "weather_city": str((settings.get("weather") or {}).get("city") or "").strip(),
            "relay_status": _relay_status(settings),
        }

    if want("agents.summary", "projects.summary", "system.health"):
        # 只采集一次，两个视图（按 Agent / 按项目）与健康摘要共用，
        # 避免同一轮渲染把会话目录扫两遍。collect 自身另有 15 秒缓存。
        agent_rows = agents.collect(settings)
        if want("agents.summary"):
            values["agents.summary"] = agents.format_rows(agent_rows)
        if want("projects.summary"):
            values["projects.summary"] = agents.format_by_project(agent_rows)
        if want("system.health"):
            values["system.health"] = health_summary(settings, agent_rows=agent_rows)

    return values


def health_summary(settings: dict[str, Any] | None = None,
                   agent_rows: list[dict[str, Any]] | None = None,
                   client_version: str = "") -> str:
    """汇总各外部源的采集状态，返回一行文字。

    用途：
    - 屏幕底部显示"数据更新于 02:10"，让人知道画面是不是新鲜的
    - 某个源失联或过期时直接写出来，不靠猜

    agent_rows 可传入 snapshot 里已采集的结果，避免重复扫盘；
    不传时自己采（供独立调用）。
    """
    settings = settings or {}
    problems: list[str] = []

    w = weather.status()
    if w["state"] == "empty":
        problems.append("天气无数据")
    elif w["state"] == "stale":
        problems.append(f"天气数据 {w['age']} 秒未更新")

    # 看板只在显式启用时才考核：默认配置 enabled=False，
    # 没启用的源报"无数据"只会造成告警疲劳
    wb_cfg = settings.get("workbuddy") or {}
    if wb_cfg.get("enabled", False):
        wb = workbuddy.status()
        if wb["state"] == "empty":
            problems.append("看板无数据")
        elif wb["state"] == "stale":
            problems.append(f"看板数据 {wb['age']} 秒未更新")

    rows = agent_rows if agent_rows is not None else agents.collect(settings)
    lost = [r["agent"] for r in rows if r["status"] == "disconnected"]
    if lost:
        problems.append("、".join(lost) + " 采集失败")
    # 长期推测（如 OpenCode 无 turn 事件）是已知事实而非故障，
    # 挂"异常"会造成告警疲劳；降级为中性提示，单独一句话说。
    inferred = [r["agent"] for r in rows
                if r["confidence"] == "low" and r["status"] != "disconnected"]

    stamp = dt.datetime.now().strftime("%H:%M")
    head = "；".join(problems)
    tail = ("推测：" + "、".join(inferred)) if inferred else ""
    parts = [p for p in (head, tail) if p]
    if parts:
        base_text = f"更新 {stamp} · {'异常：' + head if head else ''}{'；' if head and tail else ''}{tail}"
    else:
        base_text = f"更新 {stamp} · 各源正常"
    # 设备脚本版本放最后，一眼能确认自更新是否成功
    if client_version:
        base_text += f" · v{client_version}"
    return base_text


def resolve(region: dict[str, Any], values: dict[str, Any]) -> Any:
    source = region.get("source")
    if not source:
        return region.get("value", "")
    # 未接入的数据源不伪造，明确显示未知
    return values.get(source, region.get("value", "未知"))


def _duration(value: dt.timedelta) -> str:
    days = value.days
    hours, remainder = divmod(value.seconds, 3600)
    minutes = remainder // 60
    return f"{days}天 {hours}小时" if days else f"{hours}小时 {minutes}分"
