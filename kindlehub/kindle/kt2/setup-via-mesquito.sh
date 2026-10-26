#!/bin/sh
# KindleHub 一键安装（通过 Mesquito 的 request_upload 以 root 运行）
# 调用方式：curl -L http://<mac>:8792/setup.sh | sh
# 关键：用 upstart 托管刷新循环，避免安装脚本退出时进程被杀。

BASE="http://192.168.1.100:8792"
EXT="/mnt/us/extensions/kindlehub"
LOG="/mnt/us/kindlehub-setup.log"
JOB="/etc/init/kindlehub.conf"

exec > "$LOG" 2>&1

echo "=== KindleHub setup: $(date) ==="
echo "uid=$(id -u)"

mkdir -p "$EXT/bin"
if curl -sL "$BASE/kindlehub.sh" -o "$EXT/bin/kindlehub.sh"; then
    chmod 755 "$EXT/bin/kindlehub.sh"
    echo "OK 脚本: $(wc -c < "$EXT/bin/kindlehub.sh") 字节"
else
    echo "FAIL 下载 kindlehub.sh"; exit 1
fi

cat > "$EXT/kindlehub.conf" <<CONF
DASHBOARD_URL="http://192.168.1.100:8787/screen.png"
REFRESH_SECONDS=60
SCREEN_WIDTH=600
SCREEN_HEIGHT=800
CONF
echo "OK 配置"

if curl -sL "http://192.168.1.100:8787/screen.png" -o /tmp/kh-test.png; then
    echo "OK 能拉到图: $(wc -c < /tmp/kh-test.png) 字节"
else
    echo "WARN 拉图失败"
fi

if [ -d /etc/init ]; then
    mntroot rw 2>/dev/null || mount -o remount,rw / 2>/dev/null
    cat > "$JOB" <<UPSTART
# KindleHub 电子墨水屏刷新服务
start on started framework
stop on stopping framework
respawn
respawn limit 20 60
exec /bin/sh /mnt/us/extensions/kindlehub/bin/kindlehub.sh loop
UPSTART
    mntroot ro 2>/dev/null || mount -o remount,ro / 2>/dev/null
    echo "OK upstart job 已写入"
    cat "$JOB"
else
    echo "WARN 无 /etc/init"
fi

if [ -f "$EXT/daemon.pid" ]; then
    kill "$(cat "$EXT/daemon.pid")" 2>/dev/null
    rm -f "$EXT/daemon.pid"
fi

if command -v initctl >/dev/null 2>&1; then
    initctl stop kindlehub 2>/dev/null
    if initctl start kindlehub; then
        echo "OK upstart 已启动"
    else
        echo "FAIL upstart 失败，回退 setsid"
        setsid /bin/sh "$EXT/bin/kindlehub.sh" loop >/dev/null 2>&1 &
    fi
elif command -v start >/dev/null 2>&1; then
    start kindlehub && echo "OK start 已启动"
else
    echo "WARN 无 initctl，setsid 直接跑"
    setsid /bin/sh "$EXT/bin/kindlehub.sh" loop >/dev/null 2>&1 &
fi

sleep 3
echo "--- 进程数 ---"
ps | grep -c kindlehub
echo "--- 完成 ---"
