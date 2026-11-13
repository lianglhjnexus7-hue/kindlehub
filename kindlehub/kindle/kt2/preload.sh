#!/usr/bin/env bash
# 离线预载：把渲染好的 PNG 写进 Kindle 的 U 盘分区，供断网时循环显示。
#
# 背景：Kindle 插 USB 进入大容量存储模式时 framework 暂停、/mnt/us 被
# Mac 独占，Kindle 端脚本跑不了。所以这里不是「实时传输」，而是
# 「趁插线时塞一批帧进去，拔线后从本地读」。
#
# 用法：bash preload.sh [帧数，默认 6]
set -eo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KINDLE="${KINDLE_MOUNT:-/Volumes/Kindle}"
DEST="$KINDLE/kindlehub-offline"
FRAMES="${1:-6}"

if [ ! -d "$KINDLE" ]; then
    echo "未找到 $KINDLE，请用 USB 连接 Kindle 并进入 U 盘模式" >&2
    exit 1
fi

URL="http://127.0.0.1:8787/screen.png?bar=1"
echo "==> 生成 $FRAMES 帧到 $DEST"
mkdir -p "$DEST"

ok=0
i=1
while [ "$i" -le "$FRAMES" ]; do
    if curl -sS -m 15 --noproxy '*' -o "$DEST/frame-$i.png" "$URL" 2>/dev/null; then
        sz=$(wc -c < "$DEST/frame-$i.png" | tr -d ' ')
        if [ "$sz" -gt 1000 ]; then
            echo "    帧 $i: $sz 字节"
            ok=$((ok + 1))
        else
            rm -f "$DEST/frame-$i.png"
        fi
    fi
    i=$((i + 1))
done

if [ "$ok" -eq 0 ]; then
    echo "!! 一帧都没生成（Mac 服务没跑？），未改动 Kindle" >&2
    rmdir "$DEST" 2>/dev/null || true
    exit 1
fi

# 写清单：Kindle 端离线循环脚本按这个文件逐帧播放
printf '%s\n' "$ok" > "$DEST/count.txt"
date '+%Y-%m-%d %H:%M' > "$DEST/generated.txt"
sync

echo
echo "✓ 已写入 $ok 帧"
echo
echo "拔掉 USB 后，Kindle 端的离线循环脚本会从"
echo "  /mnt/us/kindlehub-offline/frame-N.png"
echo "读图显示（不依赖网络）。"
