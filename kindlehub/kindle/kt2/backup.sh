#!/usr/bin/env bash
# 备份 Kindle 的 documents 与 extensions。
# 越狱前务必先跑这个。
set -eo pipefail

KINDLE="${KINDLE_MOUNT:-/Volumes/Kindle}"
DEST="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.backup/kindle-$(date +%Y%m%d-%H%M)}"

if [ ! -d "$KINDLE" ]; then
    echo "未找到 $KINDLE，请用 USB 连接 Kindle" >&2
    exit 1
fi

echo "==> 备份到 $DEST"
mkdir -p "$DEST"
cp -R "$KINDLE/documents" "$DEST/"
cp -R "$KINDLE/extensions" "$DEST/"

echo
echo "✓ 备份完成"
du -sh "$DEST"
ls "$DEST"