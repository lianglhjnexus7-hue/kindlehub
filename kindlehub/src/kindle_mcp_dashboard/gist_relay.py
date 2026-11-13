"""把渲染好的 PNG 发布到 GitHub 仓库的固定路径，供 Kindle 跨网络拉取。

## 为什么是这个方案

现有链路要 Mac 与 Kindle 在同一局域网、且 Mac 的 IP 不变。
这里改成：Mac 把 PNG 推到一个**永不变化的固定 URL**，Kindle 只管去那个
URL 取。Mac 换家里/公司/手机热点都没有影响，Kindle 也不需要知道 Mac 在哪。

## 为什么用「仓库二进制文件」而不是 gist + 加密文本

- gist 只支持文本，PNG 必须 base64；Kindle 的 busybox 没有 base64 解码，
  更没有 AES 解密工具 —— 加密方案在设备端跑不起来。
- 仓库 raw 支持二进制：**Kindle 端一行 wget 直接拿到 PNG，零解码、零新增依赖**，
  现有 eips 直写链路一行都不用改。
- 隐私靠随机长路径 + 只放渲染后的图片；画面不含会话正文 / 原始日志
  （服务端已有会话标题过滤）。

## URL

    https://raw.githubusercontent.com/<owner>/<repo>/<branch>/relay/<name>.png

路径里的 name 是随机串，等价于一个不可猜测的 capability URL。
加 ?t=<epoch> 是为了破 raw 的 CDN 缓存。

## 访问

上传用 GitHub Contents API + gh 的 token（从 keyring 取，不落盘）；
读取完全公开、无需认证，所以 Kindle 侧只需要 wget。
"""
from __future__ import annotations

import base64
import json
import subprocess
import urllib.request
from typing import Any

# 默认值留空/占位：真实仓库名从 settings.relay.repo 读，绝不硬编码进源码
DEFAULT_REPO = ""                 # 形如 "<owner>/<repo>"，由配置提供
# 专用分支：每 5 分钟一帧会淹没 main 的发布历史，所以隔离到 relay 分支
DEFAULT_BRANCH = "relay"
# 无默认路径：capability 路径由 web._publish_relay 首次启用时随机生成并持久化。
# 固定可猜的路径等于把画面公开，宁可拒绝发布也不回退到 screen.png。
DEFAULT_PATH = ""
RAW_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

# 可猜测的遗留路径：仓库名公开时，这些常见名字等于把画面公开。
# 首次启用或迁移时一律换成 relay/<随机hex>.png（capability URL）。
_GUESSABLE_NAMES = {"screen.png", "dashboard.png", "frame.png", "relay.png"}


def is_guessable_path(path: str) -> bool:
    """basename 是常见可猜名字即视为不安全；空路径由调用方单独处理。"""
    name = str(path or "").rsplit("/", 1)[-1].lower()
    return name in _GUESSABLE_NAMES


def gh_token() -> str:
    """从 gh 的 keyring 取 token，绝不写进配置或仓库。"""
    try:
        return subprocess.check_output(["gh", "auth", "token"], timeout=8).decode().strip()
    except Exception:
        return ""


def _api(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kindlehub-relay",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, json.loads(resp.read())


def raw_url(cfg: dict[str, Any]) -> str:
    """Kindle 侧使用的固定 URL。未配置随机路径时返回空串，绝不落到可猜路径。"""
    path = str(cfg.get("path") or DEFAULT_PATH)
    if not path:
        return ""
    return RAW_TMPL.format(
        repo=str(cfg.get("repo") or DEFAULT_REPO),
        branch=str(cfg.get("branch") or DEFAULT_BRANCH),
        path=path,
    )


def publish(png: bytes, cfg: dict[str, Any]) -> str:
    """发布一帧，返回状态文案。默认关闭，不开就完全不影响现有链路。"""
    if not cfg.get("enabled"):
        return "relay: 未启用"
    token = gh_token()
    if not token:
        return "relay: 无法获取 gh token（先跑 gh auth login）"
    repo = str(cfg.get("repo") or DEFAULT_REPO)
    if not repo:
        return "relay: 未配置 repo（形如 <owner>/<repo>）"
    path = str(cfg.get("path") or DEFAULT_PATH)
    if not path:
        return "relay: 未配置 path（首次启用时自动生成随机路径）"
    branch = str(cfg.get("branch") or DEFAULT_BRANCH)
    api = "https://api.github.com/repos/" + repo + "/contents/" + path
    try:
        # 已存在要带 sha 才能更新
        sha = None
        try:
            _status, existing = _api("GET", api + "?ref=" + branch, token)
            sha = existing.get("sha")
        except Exception:
            pass
        body: dict[str, Any] = {
            "message": "kindlehub relay frame",
            "content": base64.b64encode(png).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        _api("PUT", api, token, body)
        return f"relay: 已发布 {len(png)}B -> {raw_url(cfg)}"
    except Exception as exc:  # noqa: BLE001
        return f"relay: 失败 {type(exc).__name__}: {str(exc)[:80]}"


def last_publish_time(cfg: dict[str, Any]) -> float | None:
    """从 relay 分支最近一次 commit 取发布时间，供重启后节流兜底。"""
    try:
        token = gh_token()
        repo = str(cfg.get("repo") or "")
        branch = str(cfg.get("branch") or DEFAULT_BRANCH)
        if not token or not repo:
            return None
        url = f"https://api.github.com/repos/{repo}/commits?sha={branch}&per_page=1"
        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "kindlehub-relay"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            arr = json.loads(resp.read())
        if not arr:
            return None
        from datetime import datetime, timezone
        text = arr[0]["commit"]["author"]["date"]
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def fetch(cfg: dict[str, Any], timeout: int = 15) -> bytes | None:
    """从固定 URL 取回 PNG（模拟 Kindle 的 wget）；失败返回 None。"""
    try:
        import time
        url = raw_url(cfg) + "?t=" + str(int(time.time()))
        req = urllib.request.Request(
            url, headers={"Cache-Control": "no-cache", "User-Agent": "kindlehub-relay"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        # PNG 魔数校验，避免把错误页当成图片
        return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None
    except Exception:
        return None
