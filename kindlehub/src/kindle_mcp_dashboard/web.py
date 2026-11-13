"""HTTP 服务：只读显示页 + 本机管理后台。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
相对上游的改动：
- 页面不再依赖 object-fit（WebKit 533 不支持），改用 max-width:100% + height:auto
- 图片按设备原生像素输出，在 600px 视口下 1:1 呈现，无重采样
- 新增 /admin 管理后台与 /api/* 接口，仅允许本机回环访问
- /api/state 不再向局域网暴露完整配置
- 启用 HTTP/1.1 长连接
"""
from __future__ import annotations

import json
import shutil
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import agents, secrets, weather, workbuddy
from .defaults import DEVICE_PROFILES, OPTIONAL_REGIONS
from .renderer import render_png
from .schema import KINDS, DashboardError, validate_dashboard
from .sources import health_summary, snapshot
from .store import DashboardStore

WARM_INTERVAL = 60   # 后台采集间隔（秒）：Agent 状态分钟级就够，屏幕也 60 秒一刷
RENDER_TTL = 45       # 渲染结果缓存：多次请求复用同一张图，避免同一分钟内重复解码渲染

LOOPBACK = {"127.0.0.1", "::1"}

# 最近一次成功渲染的 PNG：渲染抛异常时回退到它，避免 Kindle 拿到 500 显示裂图
_last_png: dict[str, bytes | None] = {"data": None}
# 渲染缓存：Kindle 重载页面时会连发 /kindle 与 /screen.png 两个请求，
# 短时间内复用同一张图，既省 CPU 也减少无谓的整页重绘（对 e-ink 残影友好）。
_render_cache: dict[str, Any] = {"data": None, "at": 0.0}
# 显示页上报的设备能力（仅内存，最多留 20 条）
_device_reports: list[dict[str, Any]] = []
# 1x1 透明 GIF：给设备上报当探针用，避免 404 刷错误日志
_PIXEL_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
              b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00"
              b"\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
# Kindle 最后一次拉图的时间，用于判断设备是否还在线
_last_seen: dict[str, float | str | None] = {"at": None, "ip": None}
STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"admin.js", "admin.css"}
MAX_BODY = 1 << 20  # 1 MiB


def _is_private(ip: str) -> bool:
    """RFC1918 判定按八位组解析：startswith("172.2") 会误收 172.217.x 等公网段。"""
    if ip.startswith(("192.168.", "10.")):
        return True
    if ip.startswith("172."):
        parts = ip.split(".")
        return (len(parts) == 4 and parts[1].isascii() and parts[1].isdigit()
                and 16 <= int(parts[1]) <= 31)
    return False


def lan_ip() -> str | None:
    """当前局域网地址。换 Wi-Fi 后 DHCP 会分配新 IP，Kindle 上的旧书签就会失效，
    所以启动时把实际地址打印出来，后台也显示一份。

    不能靠 UDP connect 选路由：装了 Clash/Surge 这类 TUN 代理时，
    默认路由指向 utun，getsockname() 会返回 10.x 虚拟地址，
    这个地址 Kindle 根本访问不到。改为遍历物理接口的 IPv4，
    优先选 RFC1918 私有地址且跳过 utun/tun/lo/bridge 等虚拟网卡。
    """
    try:
        import psutil
        candidates: list[str] = []
        for name, addrs in psutil.net_if_addrs().items():
            if name.startswith(("utun", "tun", "lo", "bridge", "awdl", "llw")):
                continue
            if not name.startswith("en"):
                continue
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                ip = addr.address
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                # 私有地址优先（家里/公司/热点都是这类）
                if _is_private(ip):
                    candidates.append(ip)
        # 多个候选时优先 192.168.x（最常见），其次 10.x
        candidates.sort(key=lambda x: (0 if x.startswith("192.168.") else 1, x))
        return candidates[0] if candidates else None
    except Exception:
        # 兜底：仍用原来的方式，但明确排除虚拟网卡地址
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("8.8.8.8", 80))
                ip = probe.getsockname()[0]
                return ip if not ip.startswith("10.23.") else None
            finally:
                probe.close()
        except OSError:
            return None


# 固定 URL 中继的发布状态。默认关闭，settings.relay.enabled=true 才生效。
_relay_state: dict = {"at": 0.0, "last": ""}
RELAY_INTERVAL = 300       # GitHub raw 的 CDN max-age=300，发更勤也没用


def _publish_relay(store: DashboardStore) -> None:
    """把当前这一帧推到固定 URL，供 Kindle 在跨网络时兜底拉取。

    只在启用时运行；失败只记一行日志，绝不影响主渲染链路。
    """
    try:
        from . import gist_relay
        data = store.read()
        cfg = dict(data.get("settings", {}).get("relay") or {})
        if not cfg.get("enabled"):
            return
        if not cfg.get("path") or gist_relay.is_guessable_path(cfg["path"]):
            # 首次启用或迁移遗留可猜路径：生成不可猜测的 capability 路径并持久化。
            # 固定 screen.png 等于把画面公开给任何知道仓库名的人。
            from secrets import token_hex
            cfg["path"] = f"relay/{token_hex(8)}.png"

            def mutation(d: dict) -> None:
                relay = dict(d.setdefault("settings", {}).get("relay") or {})
                relay["path"] = cfg["path"]
                d["settings"]["relay"] = relay

            store.update(mutation)
            print(f"[relay] 已生成随机路径 {cfg['path']}", flush=True)
        now = time.time()
        # 重启后也别立刻重复发：从 relay 分支最近一次 commit 取时间兜底
        if _relay_state["at"] <= 0:
            try:
                last = gist_relay.last_publish_time(cfg)
                if last and now - last < RELAY_INTERVAL:
                    _relay_state["at"] = last
            except Exception:
                pass
        if now - _relay_state["at"] < RELAY_INTERVAL:
            return
        _relay_state["at"] = now
        png = render_png(data, fetch=False)
        _relay_state["last"] = gist_relay.publish(png, cfg)
        print(f"[relay] {_relay_state['last']}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[relay] 跳过: {type(exc).__name__}: {exc}", flush=True)


def _warm_once(store: DashboardStore) -> None:
    """预热所有外部数据源。任何一个失败都不影响其余。

    每分钟采一次，与 Kindle 的刷新周期解耦——屏幕即便 10 分钟才重载，
    拿到的仍是 1 分钟内的真实状态。缓存自身的 TTL 不被这里的调用击穿。
    """
    try:
        settings = store.read().get("settings") or {}
    except Exception:
        return
    for job in (
        lambda: agents.collect(settings, force=True),
        lambda: weather.current(settings.get("weather") or {}, fetch=True, force=True),
        lambda: workbuddy.current(settings, fetch=True, force=True),
        lambda: workbuddy.models(settings, fetch=True, force=True),
    ):
        try:
            job()
        except Exception:
            pass
    _publish_relay(store)


def _warm_loop(store: DashboardStore) -> None:
    """后台预热外部数据源，让渲染路径永远只读缓存。

    e-ink 客户端对首字节延迟很敏感，渲染时同步等 DNS/TLS 会导致超时。
    先跑一轮再睡，保证刚启动时缓存尽快就位。
    """
    while True:
        _warm_once(store)
        time.sleep(WARM_INTERVAL)


def make_server(host: str = "0.0.0.0", port: int = 8787, refresh: int = 60,
                store: DashboardStore | None = None) -> ThreadingHTTPServer:
    """构造（但不启动）HTTP 服务。与 serve() 分离，方便测试用随机端口。"""
    store = store or DashboardStore()
    store.ensure()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # ---------- 只读显示（局域网） ----------

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/kindle"}:
                if self.client_address[0] not in LOOPBACK:
                    _last_seen.update({"at": time.time(), "ip": self.client_address[0]})
                state = store.read()
                body = _page(state.get("refresh_seconds", refresh), state["device"]).encode()
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)
            elif path == "/screen.png":
                if self.client_address[0] not in LOOPBACK:
                    _last_seen.update({"at": time.time(), "ip": self.client_address[0]})
                # Kindle 端通过 batt/chg 传电量，用于自绘状态栏
                q = urllib.parse.parse_qs(urlparse(self.path).query)
                extra = {}
                batt = (q.get("batt") or [""])[0]
                # isdigit() 会放行 "²" 这类 Unicode 数字而 int() 拒收；
                # 超长数字串还会撞上 int 的位数上限，电量最多三位
                if batt.isascii() and batt.isdigit() and len(batt) <= 3:
                    extra["system.battery"] = int(batt)
                    extra["system.charging"] = (q.get("chg") or ["0"])[0] in {"1", "true"}
                # Kindle 上报自己脚本的版本，显示在页脚，便于确认自更新是否成功
                ver = (q.get("v") or [""])[0]
                if ver and len(ver) <= 16 and ver.isascii() and all(c.isdigit() or c == "." for c in ver):
                    extra["system.client_version"] = ver
                draw_bar = (q.get("bar") or ["1"])[0] not in {"0", "false"}
                # 局部刷新（JS）带 _= 序号参数，明确要求新图；
                # 整页重载不带，仍走 45 秒渲染缓存。
                if extra or not draw_bar or "cache=0" in urlparse(self.path).query:
                    self._send(HTTPStatus.OK, "image/png", self._render(store, extra, draw_bar))
                else:
                    self._send(HTTPStatus.OK, "image/png", self._render_cached(store))
            elif path == "/health":
                self._send(HTTPStatus.OK, "application/json", b'{"status":"ok"}')
            elif path == "/api/device":
                # 显示页上报设备能力（JS 可用时）：视口、像素比、支持的 API。
                # 只收 GET 查询串，不落盘不打印，用于替代人工猜测。
                query = urllib.parse.parse_qs(urlparse(self.path).query)
                _device_reports.append({
                    "at": time.time(),
                    "ip": self.client_address[0],
                    "viewport": (query.get("vp") or [""])[0],
                    "dpr": (query.get("dpr") or [""])[0],
                    "js": (query.get("js") or [""])[0],
                })
                del _device_reports[:-20]
                self._send(HTTPStatus.OK, "image/gif", _PIXEL_GIF)
            elif path in {"/admin", "/admin/"}:
                if not self._local():
                    return
                self._send_file("admin.html", "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                if not self._local():
                    return
                name = path[len("/static/"):]
                if name not in STATIC_FILES:
                    self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")
                    return
                ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
                self._send_file(name, ctype)
            elif path == "/api/state":
                if not self._local():
                    return
                self._send(HTTPStatus.OK, "application/json",
                           json.dumps(store.read(), ensure_ascii=False).encode())
            elif path == "/api/agents":
                # 采集调试端点：每行的状态/置信度/依据/最后活动时间。
                # 适配器"静默腐烂"（数据源搬家、格式变更）时，在这看一眼就知道，
                # 不用再等到屏幕上发现状态不对。带 ?force=1 可跳过缓存现采一次。
                if not self._local():
                    return
                force = "force=1" in urlparse(self.path).query
                settings = store.read().get("settings") or {}
                now = time.time()
                rows = []
                for row in agents.collect(settings, force=force):
                    item = dict(row)
                    if item.get("last_activity"):
                        item["age_seconds"] = round(now - item["last_activity"])
                    rows.append(item)
                self._json(HTTPStatus.OK, {
                    "agents": rows,
                    "cache_age_seconds": agents.cache_age_seconds(),
                    "collect_ttl": agents.COLLECT_TTL,
                })
            elif path == "/api/meta":
                if not self._local():
                    return
                relay_cfg = store.read().get("settings", {}).get("relay") or {}
                self._send(HTTPStatus.OK, "application/json", json.dumps({
                    "device_reports": _device_reports[-3:],
                    "lan_ip": lan_ip(),
                    "port": self.server.server_address[1],
                    "zstd_available": shutil.which("zstd") is not None,
                    "relay": {
                        "enabled": bool(relay_cfg.get("enabled")),
                        "path": str(relay_cfg.get("path") or ""),
                        "last": _relay_state["last"],
                    },
                    "devices": DEVICE_PROFILES,
                    "kinds": KINDS,
                    "kindle_seen": {
                        "seconds_ago": (round(time.time() - _last_seen["at"])
                                        if _last_seen["at"] else None),
                        "ip": _last_seen["ip"],
                    },
                    "health": {
                        "weather": weather.status(),
                        "workbuddy": workbuddy.status(),
                    },
                    "secrets_set": {
                        "workbuddy_password": bool(secrets.get("workbuddy_password")),
                    },
                    "sources": [
                        {"id": "system.time", "label": "本地时间"},
                        {"id": "system.date", "label": "日期"},
                        {"id": "system.hostname", "label": "主机名"},
                        {"id": "system.cpu_percent", "label": "CPU 占用"},
                        {"id": "system.memory_percent", "label": "内存占用"},
                        {"id": "system.disk_percent", "label": "磁盘占用"},
                        {"id": "system.load", "label": "负载"},
                        {"id": "system.uptime", "label": "运行时长"},
                        {"id": "weather.current", "label": "天气（Open-Meteo）"},
                        {"id": "workbuddy.usage", "label": "WorkBuddy 用量总量"},
                        {"id": "workbuddy.models", "label": "模型消耗（精简）"},
                        {"id": "workbuddy.models_detail", "label": "模型消耗（详细）"},
                        {"id": "agents.summary", "label": "Agent 监控"},
                        {"id": "system.health", "label": "数据健康摘要"},
                    ],
                    # 可选模块：依赖外部服务，供后台一键添加
                    "optional_regions": OPTIONAL_REGIONS,
                }, ensure_ascii=False).encode())
            else:
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")

        # ---------- 管理后台写操作（仅本机） ----------

        def do_PUT(self) -> None:  # noqa: N802
            if not self._local():
                return
            if urlparse(self.path).path != "/api/state":
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                checked = validate_dashboard(payload)
                store.write(checked)
            except DashboardError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"saved": True, "screens": len(checked["screens"])})

        def do_POST(self) -> None:  # noqa: N802
            if not self._local():
                return
            route = urlparse(self.path).path
            if route == "/api/secrets":
                payload = self._read_json()
                if payload is None:
                    return
                key = payload.get("key")
                if key != "workbuddy_password":
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "不支持的密钥名"})
                    return
                secrets.put(key, str(payload.get("value") or "").strip() or None)
                self._json(HTTPStatus.OK, {"saved": True})
                return
            if route != "/api/preview":
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                checked = validate_dashboard(payload)
            except DashboardError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send(HTTPStatus.OK, "image/png", render_png(checked, fetch=False))

        # ---------- 工具 ----------

        def _render_cached(self, store: DashboardStore) -> bytes:
            now = time.time()
            if _render_cache["data"] and now - _render_cache["at"] < RENDER_TTL:
                return _render_cache["data"]
            png = self._render(store)
            _render_cache.update({"data": png, "at": now})
            return png

        def _render(self, store: DashboardStore, extra: dict | None = None,
                    draw_bar: bool = True) -> bytes:
            """渲染失败时回退到上一张成功的图，绝不让设备拿到错误页。

            extra 用于注入设备侧传来的实时值（如电量），让自绘状态栏有数据。
            """
            try:
                data = store.read()
                if extra:
                    live = snapshot(data.get("settings"), fetch=False)
                    live.update(extra)
                    # 版本号拼在健康栏末尾，屏幕上直接显示设备跑的是哪个版本
                    ver = str(extra.get("system.client_version") or "")
                    if ver:
                        live["system.health"] = health_summary(
                            data.get("settings"), client_version=ver)
                    png = render_png(data, live, fetch=False, draw_bar=draw_bar)
                else:
                    png = render_png(data, fetch=False, draw_bar=draw_bar)
            except Exception as exc:  # noqa: BLE001
                print(f"渲染失败: {type(exc).__name__}: {exc}", flush=True)
                if _last_png["data"]:
                    return _last_png["data"]
                raise
            _last_png["data"] = png
            return png

        def _local(self) -> bool:
            if self.client_address[0] in LOOPBACK:
                # 防 DNS rebinding：恶意页面把域名解析到 127.0.0.1 后，
                # 请求同样来自回环；Host 不是本机名就一律拒绝
                host = urlparse("//" + (self.headers.get("Host") or "")).hostname
                if host in {"localhost", "127.0.0.1", "::1"}:
                    return True
            self._send(HTTPStatus.FORBIDDEN, "text/plain; charset=utf-8",
                       "管理后台仅允许本机访问\n".encode())
            return False

        def _read_json(self):
            # 跨站表单只能发 urlencoded/text/plain，强制 JSON 类型挡住 CSRF
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Content-Type 必须是 application/json"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "请求体为空或超过 1MiB"})
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "请求体不是合法 JSON"})
                return None

        def _send_file(self, name: str, ctype: str) -> None:
            target = STATIC_DIR / name
            if not target.is_file():
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found\n")
                return
            self._send(HTTPStatus.OK, ctype, target.read_bytes())

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            self._send(status, "application/json",
                       json.dumps(payload, ensure_ascii=False).encode())

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"kindle {self.client_address[0]}: {fmt % args}", flush=True)

    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "0.0.0.0", port: int = 8787, refresh: int = 60) -> None:
    store = DashboardStore()
    server = make_server(host, port, refresh, store)
    _warm_once(store)  # 启动时同步预热一次，保证首屏就有数据
    threading.Thread(target=_warm_loop, args=(store,), daemon=True).start()
    address = lan_ip() or host
    print(f"KindleHub 显示: http://{address}:{port}/kindle", flush=True)
    print(f"KindleHub 后台: http://127.0.0.1:{port}/admin （仅本机）", flush=True)
    server.serve_forever()


def _page(refresh: int, device: dict) -> str:
    """生成显示页。

    JS 关闭时（Kindle 实验浏览器默认）靠 meta refresh 整页重载，这是基线路径。
    JS 可用时升级为「局部刷新」：只替换 img.src，边框与标题不重绘，
    显著减轻 e-ink 残影；同时监听窗口聚焦，锁屏唤醒后立刻拉新图。

    刻意用 ES3 写法：Kindle 实验浏览器是 WebKit 531/533（约 Safari 5.0），
    没有 addEventListener / classList / fetch。探测缺任何一样就退回整页刷新。
    """
    width, height = device["width"], device["height"]
    seconds = max(10, int(refresh))
    js = """
    (function () {
      var img = document.getElementsByTagName("img")[0];
      if (!img) return;
      var timer = null, seq = 0, broken = 0;
      function stamp() {
        seq += 1;
        return "/screen.png?cache=0&bar=0&_=" + seq + "&" + (new Date()).getTime();
      }
      function load() {
        broken = 0;
        img.src = stamp();
      }
      function schedule() {
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(load, SECONDS * 1000);
      }
      img.onerror = function () {
        broken += 1;
        if (broken < 4) {
          window.setTimeout(load, 2000);
        } else {
          // 局部刷新连续失败（服务重启/断网恢复中）时回退整页重载，
          // 重新拿一份带 meta refresh 的基线页面，避免画面永久停格。
          window.setTimeout(function () { window.location.reload(); }, 5000);
        }
      };
      img.onload = schedule;
      // 局部刷新接管后必须停掉整页 meta refresh，否则两种机制叠加，
      // 既浪费带宽又让边框反复重绘，残影反而更重。
      try {
        var metas = document.getElementsByTagName("meta");
        for (var i = 0; i < metas.length; i++) {
          if (metas[i].httpEquiv && String(metas[i].httpEquiv).toLowerCase() === "refresh") {
            metas[i].parentNode.removeChild(metas[i]);
          }
        }
      } catch (e) {}
      if (typeof window.attachEvent === "function") {
        window.attachEvent("onfocus", load);
      } else if (typeof window.addEventListener === "function") {
        window.addEventListener("focus", load, false);
      }
      load();
      // 上报设备能力：视口、像素比、以及支持到哪一级 API。
      // 用 1x1 GIF 做探针，JS 不通也不会刷错误日志。
      try {
        var canAdd = typeof window.addEventListener === "function";
        var canAttach = typeof window.attachEvent === "function";
        var probe = new Image();
        probe.src = "/api/device?js=1&vp=" + encodeURIComponent(
            (window.innerWidth || 0) + "x" + (window.innerHeight || 0)
        ) + "&dpr=" + encodeURIComponent(String(window.devicePixelRatio || 1))
          + "&api=" + encodeURIComponent((canAdd ? "a" : "") + (canAttach ? "b" : ""));
      } catch (e) {}
    })();
    """.replace("SECONDS", str(seconds))
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{seconds}">
<title>KindleHub</title>
<style>html,body{{margin:0;padding:0;background:#fff;overflow:hidden}}
img{{display:block;max-width:100%;height:auto}}</style>
</head><body><img src="/screen.png?bar=0" width="{width}" height="{height}" alt="Dashboard">
<script>{js}</script></body></html>"""
