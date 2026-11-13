#!/usr/bin/env bash
# 把工作目录的受控文件同步到发布仓库 .release，并推送到 GitHub。
#
# 用法：
#   bash scripts/publish-to-github.sh              # 同步 + 提交 + push
#   bash scripts/publish-to-github.sh --tag 1.3.0  # 额外打 tag + 创建 Release
#
# 详见 CONTRIBUTING-AGENTS.md。任何 Agent 改完代码都要跑这个。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REL="$ROOT/.release"
TAG=""
[ "${1:-}" = "--tag" ] && TAG="${2:-}"

cd "$ROOT"

echo "==> 0) 检查工作目录"
if [ ! -d "$REL/.git" ]; then
    echo "缺少 $REL（独立发布仓库）" >&2; exit 1
fi
if [ -n "$(git status --porcelain | grep -vE "^\?\?")" ]; then
    echo "!! 有未提交改动，先 commit 再发布："
    git status --short | head -10
    exit 1
fi
echo "    工作目录干净"

echo "==> 1) 跑测试"
export KINDLE_DASHBOARD_STATE="$ROOT/.state/kt2.json"
export KINDLEHUB_SECRETS="$ROOT/.state/secrets.json"
if [ -x "$ROOT/.venv/bin/pytest" ]; then
    "$ROOT/.venv/bin/pytest" kindlehub -q 2>&1 | tail -2
else
    echo "    (跳过：无 .venv)"
fi

echo "==> 2) 导出受控文件到发布仓库"
cd "$REL"
find . -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cd "$ROOT"
git archive HEAD | tar -x -C "$REL"
cd "$REL"
FILES=$(find . -path ./.git -prune -o -type f -print | wc -l | tr -d " ")
echo "    导出 $FILES 个文件"

echo "==> 3) 隐私扫描（命中即中止）"
# 只扫描可能携带真实值的文件；本脚本自身含模式字面量，排除以免自命中。
# 个人邮箱/真实网段等模式放在未跟踪的 .state/scan-patterns（每行一个 ERE）。
# 本脚本会被导出到公开仓库，所以绝不在这里写个人数据的字面量。
BAD=0
scan() {
    local label="$1" pattern="$2" hits
    # -print0/-0：文件名含空格（如 "KindleHub 停止.sh"）也不能漏扫
    hits=$(find . -path ./.git -prune -o -type f \
        \( -name "*.py" -o -name "*.sh" -o -name "*.conf" -o -name "*.json" \
           -o -name "*.toml" -o -name "*.yml" -o -name "*.yaml" \
           -o -name "*.example" -o -name "*.plist" \) \
        ! -path "./scripts/publish-to-github.sh" -print0 2>/dev/null \
        | xargs -0 grep -lE "$pattern" /dev/null 2>/dev/null | head -5 || true)
    if [ -n "$hits" ]; then
        echo "    !! $label:"; echo "$hits"; BAD=1
    else
        echo "    OK $label"
    fi
}
scan "绝对路径" "/Users/[a-z]"
scan "token"    "ghp_|sk-[A-Za-z0-9]{20}"
PATTERNS="$ROOT/.state/scan-patterns"
if [ -f "$PATTERNS" ]; then
    while IFS= read -r pat || [ -n "$pat" ]; do
        case "$pat" in ""|\#*) continue ;; esac
        scan "个人敏感模式" "$pat"
    done < "$PATTERNS"
else
    echo "    !! 缺少 .state/scan-patterns，个人邮箱/网段未校验（本机请补齐该文件）"
fi
if find . -name "relay.conf" -not -path "./.git/*" | grep -q .; then
    echo "    !! relay.conf 不该发布（含真实仓库 URL）"; BAD=1
else
    echo "    OK 无 relay.conf"
fi
if [ "$BAD" != "0" ]; then
    echo "隐私扫描未通过，已中止。修好后再跑。" >&2; exit 1
fi

echo "==> 4) 提交发布仓库"
cd "$REL"
# 发布仓库无本地 git 身份，会回落到全局个人身份；公开提交固定用项目身份。
export GIT_AUTHOR_NAME="KindleHub Dev" GIT_AUTHOR_EMAIL="kindlehub@localhost"
export GIT_COMMITTER_NAME="KindleHub Dev" GIT_COMMITTER_EMAIL="kindlehub@localhost"
git add -A
if git diff --cached --quiet; then
    echo "    无变化，跳过提交"
else
    MSG="release: 同步工作目录 $(cd "$ROOT" && git rev-parse --short HEAD)"
    [ -n "$TAG" ] && MSG="release: v$TAG"
    git commit -q -m "$MSG"
    echo "    已提交: $MSG"
fi

echo "==> 5) push 到 GitHub"
git push origin main 2>&1 | tail -3

if [ -n "$TAG" ]; then
    echo "==> 6) 打 tag v$TAG 并创建 Release"
    git tag -a "v$TAG" -m "v$TAG" 2>/dev/null || true
    git push origin "v$TAG" 2>&1 | tail -2
    gh release create "v$TAG" --title "v$TAG" --generate-notes 2>&1 | tail -2
fi

echo
echo "✓ 发布完成  https://github.com/lianglhjnexus7-hue/kindlehub"
[ -n "$TAG" ] && echo "  Release: https://github.com/lianglhjnexus7-hue/kindlehub/releases/tag/v$TAG"
echo "  main:     $(git rev-parse --short HEAD)"
