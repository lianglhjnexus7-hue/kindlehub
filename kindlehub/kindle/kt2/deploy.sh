#!/usr/bin/env bash
# 把 KindleHub 直写方案部署到已越狱的 Kindle（通过 USB 挂载）。
#
# 前提：Kindle 已越狱并安装 KUAL，通过 USB 连接 Mac，挂载为 /Volumes/Kindle
# 用法：bash deploy.sh
# 不用 set -u：个别环境变量可能未定义，脚本内部已做兜底
set -eo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${KINDLE_MOUNT:-/Volumes/Kindle}"
DEST="$TARGET/extensions/kindlehub"

echo "==> 检查 Kindle 挂载"
if [ ! -d "$TARGET" ]; then
    echo "未找到 $TARGET" >&2
    echo "请用 USB 连接 Kindle，或设置 KINDLE_MOUNT 指向挂载点" >&2
    exit 1
fi
if [ ! -d "$TARGET/extensions" ]; then
    echo "$TARGET 下没有 extensions/ 目录，Kindle 可能未越狱" >&2
    exit 1
fi

echo "==> 自动识别 Mac 局域网地址"
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
if [ -z "$IP" ]; then
    echo "未能自动识别，使用占位地址，请手动编辑 kindlehub.conf" >&2
    IP="192.168.1.100"
fi
echo "    Mac IP: $IP"

echo "==> 拷贝文件到 $DEST"
mkdir -p "$DEST/bin"
cp "$SRC/extensions/kindlehub/bin/kindlehub.sh" "$DEST/bin/"
cp "$SRC/extensions/kindlehub/menu.json" "$DEST/"
cp "$SRC/extensions/kindlehub/config.xml" "$DEST/"

echo "==> 写入配置（DASHBOARD_URL=$IP）"
# sed 表达式用单引号，IP 经环境变量传入，避免被 shell 提前展开
DASH_IP="$IP" sed 's|^DASHBOARD_URL=.*|DASHBOARD_URL="http://'"$IP"':8787/screen.png"|' \
    "$SRC/extensions/kindlehub/kindlehub.conf" > "$DEST/kindlehub.conf"

echo "==> 设置可执行权限"
chmod 755 "$DEST/bin/kindlehub.sh" 2>/dev/null || true

# FAT32 不保存 Unix 权限，Kindle 靠扩展名+shebang 识别，
# 但 KUAL 要求 .sh 有执行位，尽量设置。
echo
echo "✓ 部署完成"
echo
echo "下一步（在 Kindle 上操作）："
echo "  1. 搜索框输入  ;kual  回车，打开 KUAL 菜单"
echo "  2. 找到 KindleHub → 启动 (Start)"
echo "  3. 屏幕应显示仪表盘，且不再锁屏"
echo
echo "如需改地址或间隔，编辑 $DEST/kindlehub.conf 后点 KUAL → KindleHub → 重启"