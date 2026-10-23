#!/usr/bin/env bash
set -euo pipefail
LABEL="ai.kindlehub.dashboard"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "已卸载 ${LABEL}（日志与配置保留）"
