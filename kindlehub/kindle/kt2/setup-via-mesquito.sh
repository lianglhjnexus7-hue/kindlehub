#!/bin/sh
# KindleHub 一键安装（通过 Mesquito 的 request_upload 以 root 运行）
# 调用方式：curl -L http://<mac>:8792/setup.sh | sh
# 关键：用 upstart 托管刷新循环，避免安装脚本退出时进程被杀。
#
# 不含任何固定 IP：Mac 地址自动发现（扫同网段 8787/health），
# 与 kindlehub.sh 的多候选 + 兜底扫描是同一套思路，换网络不用改脚本。
# 自动发现失败时也可显式指定：
#   curl -sL http://<mac>:8792/setup.sh | sh -s -- <mac-ip>

EXT="/mnt/us/extensions/kindlehub"
LOG="/mnt/us/kindlehub-setup.log"
JOB="/etc/init/kindlehub.conf"

exec > "$LOG" 2>&1

echo "=== KindleHub setup: $(date) ==="
echo "uid=$(id -u)"

# ---------- 自动发现 Mac（Dashboard 服务在 8787） ----------
# 能执行本脚本说明 Wi-Fi 已连上 Mac 所在网络，直接扫同网段即可。
# busybox wget 不一定支持 -T，沿用 kindlehub.sh 的后台 kill 超时兜底。
probe_port() {
    _url="$1"
    _flag=$(mktemp /tmp/kh-setup.XXXXXXXX 2>/dev/null) || _flag="/tmp/kh-setup-$$"
    rm -f "$_flag"
    ( wget -q -O /dev/null "$_url" 2>/dev/null && : > "$_flag" ) &
    _wpid=$!
    ( sleep 3; kill "$_wpid" 2>/dev/null ) &
    _kpid=$!
    wait "$_wpid" 2>/dev/null
    kill "$_kpid" 2>/dev/null
    if [ -f "$_flag" ]; then
        rm -f "$_flag"
        return 0
    fi
    rm -f "$_flag"
    return 1
}

find_mac() {
    my_ip=$(ifconfig wlan0 2>/dev/null | grep 'inet addr' | sed 's/.*addr:\([0-9.]*\).*/\1/' | head -1)
    [ -z "$my_ip" ] && my_ip=$(ip -4 addr show wlan0 2>/dev/null | grep -o "inet [0-9.]*" | awk '{print $2}' | head -1)
    [ -z "$my_ip" ] && return 1
    prefix=$(echo "$my_ip" | cut -d. -f1-3)
    self=$(echo "$my_ip" | cut -d. -f4)
    echo "扫描 $prefix.0/24 找 Dashboard ..."
    found=/tmp/kh-setup-found
    rm -f "$found"
    i=2
    while [ "$i" -le 254 ]; do
        batch=0
        while [ "$batch" -lt 32 ] && [ "$i" -le 254 ]; do
            if [ "$i" != "$self" ]; then
                ( probe_port "http://$prefix.$i:8787/health" && echo "$prefix.$i" >> "$found" ) &
            fi
            i=$((i + 1))
            batch=$((batch + 1))
        done
        wait
        if [ -s "$found" ]; then
            head -1 "$found"
            rm -f "$found"
            return 0
        fi
    done
    rm -f "$found"
    return 1
}

MAC_IP="$1"
if [ -z "$MAC_IP" ]; then
    MAC_IP=$(find_mac) || { echo "FAIL 未找到 Dashboard（8787），可用 sh -s -- <mac-ip> 显式指定"; exit 1; }
fi
echo "Dashboard 在 $MAC_IP"
BASE="http://$MAC_IP:8792"

mkdir -p "$EXT/bin"
if curl -sL "$BASE/kindlehub.sh" -o "$EXT/bin/kindlehub.sh"; then
    chmod 755 "$EXT/bin/kindlehub.sh"
    echo "OK 脚本: $(wc -c < "$EXT/bin/kindlehub.sh") 字节"
else
    echo "FAIL 下载 kindlehub.sh"; exit 1
fi

cat > "$EXT/kindlehub.conf" <<CONF
DASHBOARD_URL="http://$MAC_IP:8787/screen.png"
REFRESH_SECONDS=60
SCREEN_WIDTH=600
SCREEN_HEIGHT=800
CONF
echo "OK 配置"

if curl -sL "http://$MAC_IP:8787/screen.png" -o /tmp/kh-test.png; then
    echo "OK 能拉到图: $(wc -c < /tmp/kh-test.png) 字节"
else
    echo "WARN 拉图失败"
fi

if [ -d /etc/init ]; then
    mntroot rw 2>/dev/null || mount -o remount,rw / 2>/dev/null
    cat > "$JOB" <<UPSTART
# KindleHub 电子墨水屏刷新服务
# 注意 start/stop 条件都不能挂 framework：脚本自己会停掉 GUI 省电，
# 挂上去就会被连坐（stop on stopping framework 会把本服务一起杀）。
start on runlevel [2345]
stop on runlevel [!2345]
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
