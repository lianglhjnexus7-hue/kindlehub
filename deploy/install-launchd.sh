#!/usr/bin/env bash
# 把 KindleHub 注册为 launchd 用户服务：开机自启 + 崩溃自动重启。
# 用法：bash deploy/install-launchd.sh
# 卸载：bash deploy/uninstall-launchd.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="ai.kindlehub.dashboard"
TEMPLATE="$ROOT/deploy/${LABEL}.plist.template"
INSTANCE="$ROOT/deploy/${LABEL}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -x "$ROOT/.venv/bin/kindlehub" ]]; then
    echo "找不到 $ROOT/.venv/bin/kindlehub，请先安装：" >&2
    echo "  .venv/bin/pip install -e kindlehub" >&2
    exit 1
fi

if [[ ! -f "$TEMPLATE" ]]; then
    echo "缺少模板 $TEMPLATE" >&2
    exit 1
fi

mkdir -p "$ROOT/logs" "$ROOT/.state" "$HOME/Library/LaunchAgents"

# 从模板生成本机实例：只替换 {{ROOT}}，不把绝对路径写进版本库
sed "s|{{ROOT}}|$ROOT|g" "$TEMPLATE" > "$INSTANCE"
chmod 600 "$INSTANCE"
echo "已生成本机配置 $INSTANCE"

# 已加载则先卸载，避免重复注册
if launchctl list | grep -q "$LABEL"; then
    echo "检测到已加载，先卸载旧服务..."
    launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# 停掉手动启动的实例，避免端口冲突
if lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "8787 端口被占用，尝试停止手动启动的实例..."
    lsof -nP -iTCP:8787 -sTCP:LISTEN -t | xargs -r kill 2>/dev/null || true
    sleep 1
fi

cp "$INSTANCE" "$PLIST_DST"
launchctl bootstrap "gui/$UID" "$PLIST_DST" 2>/dev/null || launchctl load "$PLIST_DST"

sleep 2
if curl -fsS -m 5 --noproxy "*" "http://127.0.0.1:8787/health" >/dev/null 2>&1; then
    IP=$(ipconfig getifaddr en0 2>/dev/null || echo "127.0.0.1")
    echo "✓ 服务已启动并注册开机自启"
    echo "  Kindle 地址: http://${IP}:8787/kindle"
    echo "  后台地址:    http://127.0.0.1:8787/admin"
    echo "  日志:        $ROOT/logs/kindlehub.log"
else
    echo "✗ 服务未响应，查看日志：tail -50 $ROOT/logs/kindlehub.err.log" >&2
    exit 1
fi