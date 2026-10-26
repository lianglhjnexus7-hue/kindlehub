#!/usr/bin/env bash
# 部署 WinterBreak（Mesquito 方案）到 Kindle —— 适用于 5.12.2.2
#
# 原理：把 mesquito/ 与 apps/ 拷到 Kindle 根目录，并覆盖
# .active_content_sandbox/store，让打开 Kindle Store 时加载 Mesquito，
# 从而能运行 WinterBreak 越狱应用。
#
# 用法：bash deploy-winterbreak.sh
set -eo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../vendor/WinterBreak" && pwd)"
KINDLE="${KINDLE_MOUNT:-/Volumes/Kindle}"

if [ ! -d "$KINDLE" ]; then
    echo "未找到 $KINDLE，请用 USB 连接 Kindle" >&2; exit 1
fi
if [ ! -d "$SRC/mesquito" ]; then
    echo "缺少 $SRC/mesquito，请先下载 WinterBreak.tar.gz 并解压到 vendor/WinterBreak" >&2
    exit 1
fi

echo "==> 检查越狱是否已生效（避免重复）"
if [ -f "$KINDLE/extensions/MRInstaller/bin/install.sh" ]; then
    echo "    检测到 MRInstaller，可能已越狱。仍继续部署（可重复执行）。"
fi

echo "==> 备份 Kindle 现有 .active_content_sandbox"
if [ -d "$KINDLE/.active_content_sandbox" ]; then
    BK="$KINDLE/.active_content_sandbox.bak-$(date +%H%M%S)"
    cp -R "$KINDLE/.active_content_sandbox" "$BK" 2>/dev/null || true
    echo "    已备份到 $(basename "$BK")"
fi

echo "==> 拷贝 mesquito/"
cp -R "$SRC/mesquito" "$KINDLE/"

echo "==> 拷贝 apps/"
mkdir -p "$KINDLE/apps"
cp -R "$SRC/apps/tech.hackerdude.winterbreak" "$KINDLE/apps/"

echo "==> 合并 .active_content_sandbox（含隐藏 store 目录）"
mkdir -p "$KINDLE/.active_content_sandbox"
cp -R "$SRC/.active_content_sandbox/." "$KINDLE/.active_content_sandbox/"

echo
echo "✓ 文件已部署"
echo
echo "接下来在 Kindle 上操作："
echo "  1. 拔掉 USB"
echo "  2. 打开飞行模式（设置里），再关掉——让 Kindle 重新连网"
echo "  3. 回到主页，点右上角购物车图标，打开 Kindle Store"
echo "  4. 提示关闭飞行模式时点 Yes"
echo "  5. Mesquito 加载后，点 WinterBreak 图标"
echo "  6. 等约 30 秒，屏幕刷出文字并重启 → 越狱完成"
echo
echo "验证：搜索框输入 ;un 回车，有反应即成功"