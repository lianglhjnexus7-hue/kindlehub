#!/usr/bin/env bash
# 同步 Kindle 分发文件到 kindlehub-dist/ 并 bump version.txt。
# Kindle 端每分钟查一次 version.txt，发现更高版本自动下载 kindlehub.sh，
# 校验 sha256 清单 + sh -n 后热替换。用法：bash deploy/publish-kindle.sh [版本号]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/kindlehub-dist"
SRC="$ROOT/kindlehub/kindle/kt2/extensions/kindlehub/bin/kindlehub.sh"

version="${1:-}"
if [ -z "$version" ]; then
    version="$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "$SRC" | head -1)"
fi
if [ -z "$version" ]; then
    echo "无法从脚本里读出版本号，请显式传入：publish-kindle.sh 1.12.0" >&2
    exit 1
fi

mkdir -p "$DIST"
cp "$SRC" "$DIST/kindlehub.sh"
cp "$ROOT/kindlehub/kindle/kt2/setup-via-mesquito.sh" "$DIST/setup.sh"
printf '%s\n' "$version" > "$DIST/version.txt"

sh -n "$DIST/kindlehub.sh"   # 发布前最后校验，坏了宁可不发
# sha256 清单：Kindle 自更新时校验完整性。HTTP 明文传输，没有清单的话
# 同网段任何主机都能冒充分发服务投递恶意脚本。
# macOS 没有 sha256sum，用 shasum -a 256，输出格式与 busybox sha256sum 一致。
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$DIST" && sha256sum kindlehub.sh > kindlehub.sh.sha256)
else
    (cd "$DIST" && shasum -a 256 kindlehub.sh > kindlehub.sh.sha256)
fi

# relay.conf 由 settings.relay 推导，单独保存在 .state/relay.conf（0600），
# 绝不放进对 0.0.0.0:8792 开放的分发目录以免同网段设备读取 capability URL。
# 同时主动清理历史上遗留的 $DIST/relay.conf。
rm -f "$DIST/relay.conf"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
STATE="${KINDLE_DASHBOARD_STATE:-$ROOT/.state/kt2.json}"
RELAY_URL="$("$PY" - "$ROOT" "$STATE" <<'PYEOF'
import json, sys
root, state = sys.argv[1], sys.argv[2]
sys.path.insert(0, root + "/kindlehub/src")
try:
    from kindle_mcp_dashboard import gist_relay
    cfg = json.load(open(state)).get("settings", {}).get("relay", {})
    if cfg.get("enabled") and cfg.get("path") and not gist_relay.is_guessable_path(cfg["path"]):
        print(gist_relay.raw_url(cfg))
except Exception:
    pass
PYEOF
)"
if [ -n "$RELAY_URL" ]; then
    mkdir -p "$ROOT/.state"
    printf '# KindleHub 跨网络中继配置\n# 本机安全留档（0600）；跨网使用请手动写入 Kindle 本地 kindlehub.conf\nRELAY_URL="%s"\n' "$RELAY_URL" > "$ROOT/.state/relay.conf"
    chmod 600 "$ROOT/.state/relay.conf"
else
    rm -f "$ROOT/.state/relay.conf"
fi
echo "已发布 ${version}："
ls -l "$DIST"
