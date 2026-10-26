#!/usr/bin/env bash
# KindleHub 直写方案部署（不依赖 KUAL）。
#
# Kindle 通过 USB 连接 Mac，挂载为 /Volumes/Kindle。
# 部署后在 Kindle 主页「文档」里点「KindleHub 启动」即可。
#
# 用法：bash deploy-nokual.sh
set -eo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${KINDLE_MOUNT:-/Volumes/Kindle}"
EXT="$TARGET/extensions/kindlehub"
DOC="$TARGET/documents"

echo "==> 检查 Kindle 挂载"
if [ ! -d "$TARGET" ]; then
    echo "未找到 $TARGET，请用 USB 连接 Kindle" >&2; exit 1
fi
if [ ! -d "$TARGET/extensions" ]; then
    echo "$TARGET 下没有 extensions/，Kindle 可能未越狱" >&2; exit 1
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
[ -z "$IP" ] && IP="192.168.1.100"
echo "==> Mac IP: $IP"

echo "==> 安装扩展到 $EXT"
mkdir -p "$EXT/bin"
cp "$SRC/extensions/kindlehub/bin/kindlehub.sh" "$EXT/bin/"
cp "$SRC/extensions/kindlehub/menu.json" "$SRC/extensions/kindlehub/config.xml" "$EXT/"
sed 's|^DASHBOARD_URL=.*|DASHBOARD_URL="http://'"$IP"':8787/screen.png"|' \
    "$SRC/extensions/kindlehub/kindlehub.conf" > "$EXT/kindlehub.conf"
chmod 755 "$EXT/bin/kindlehub.sh" 2>/dev/null || true

echo "==> 安装文档入口到 $DOC（KUAL 不可用时的备选方式）"
mkdir -p "$DOC"
cp "$SRC/documents/KindleHub 启动.sh" "$DOC/"
cp "$SRC/documents/KindleHub 停止.sh" "$DOC/"
cp "$SRC/documents/KindleHub 刷新.sh" "$DOC/"

echo
echo "✓ 部署完成"
echo
echo "在 Kindle 上操作（主页 → 文档）："
echo "  点「KindleHub 启动」  → 开始刷新并禁止屏保"
echo "  点「KindleHub 停止」  → 停止并恢复屏保"
echo "  点「KindleHub 刷新」  → 手动刷新一帧"