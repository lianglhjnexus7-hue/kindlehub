#!/bin/sh
# KindleHub 直写客户端 —— Kindle 第 7 代（KT2，600x800）
#
# 与浏览器方案的区别：
#   浏览器：Kindle 开实验浏览器访问页面，靠 meta refresh 整页重载，会锁屏、有残影
#   直写：  wget 拉 PNG + eips 直接画到屏幕，lipc 关屏保，无浏览器、无残影、更省电
#
# 依赖：越狱 + KUAL（本目录即 KUAL 扩展），不需要改固件

VERSION="1.9.0"
FAIL_COUNT=0
CONF="/mnt/us/extensions/kindlehub/kindlehub.conf"
IMAGE="/mnt/us/extensions/kindlehub/dashboard.png"
PIDFILE="/mnt/us/extensions/kindlehub/daemon.pid"
LOG="/mnt/us/extensions/kindlehub/daemon.log"

# ---------- 配置读取（key=value，兼容空格与引号） ----------
read_conf() {
    [ -f "$CONF" ] || return 1
    eval "$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$CONF")"
}

# ---------- 抑制系统状态栏 ----------
# 不同固件/机型属性名不一样，逐个尝试；失败也无害。
suppress_statusbar() {
    for target in         "com.lab126.pillow disableEnableStatusBar 0"         "com.lab126.chromebar controls 0"         "com.lab126.pillow statusBarVisible 0"         "com.lab126.pillow statusBarEnable 0"
    do
        lipc-set-prop $target 2>/dev/null
    done
}


# 解析到可用地址后写回配置，下次刷新就不用再探一遍
save_url() {
    _new="$1"
    [ -f "$CONF" ] || return 0
    case "$(cat "$CONF" 2>/dev/null)" in
        *"DASHBOARD_URL="$_new""*) return 0 ;;
    esac
    _tmp="$CONF.tmp"
    if sed "s|^DASHBOARD_URL=.*|DASHBOARD_URL="$_new"|" "$CONF" > "$_tmp" 2>/dev/null; then
        mv -f "$_tmp" "$CONF" 2>/dev/null || rm -f "$_tmp"
        echo "$(date +%H:%M:%S) 地址已写回配置: $_new" >> "$LOG"
    else
        rm -f "$_tmp"
    fi
}

# 单次探测，最多等 3 秒。Kindle 的 busybox wget 不一定支持 -T，
# 所以用后台进程 + kill 兜底，避免一个死地址卡住整轮刷新。
probe_url() {
    _url="$1"
    _flag="/tmp/kh-probe-$$"
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
# ---------- 找到能用的 Dashboard 地址 ----------
# 换网络后 Mac 的 IP 会变，所以支持多候选 + 兜底扫描。
resolve_url() {
    # 1) 先试上次成功的地址
    if [ -n "$DASHBOARD_URL" ] && probe_url "$DASHBOARD_URL"; then
        return 0
    fi
    # 2) 依次试候选列表
    for u in $DASHBOARD_URLS; do
        [ -z "$u" ] && continue
        if probe_url "$u"; then
            DASHBOARD_URL="$u"
            save_url "$u"
            return 0
        fi
    done
    # 3) 连续失败够多才扫网段，避免每次刷新都花几分钟
    FAIL_COUNT=$((FAIL_COUNT + 1))
    if [ "$FAIL_COUNT" -lt 3 ]; then
        return 1
    fi
    FAIL_COUNT=0
    scan_lan
}

scan_lan() {
    my_ip=$(ifconfig wlan0 2>/dev/null | grep 'inet addr' | sed 's/.*addr:\([0-9.]*\).*/\1/' | head -1)
    [ -z "$my_ip" ] && my_ip=$(ip -4 addr show wlan0 2>/dev/null | grep -o "inet [0-9.]*" | awk '{print $2}' | head -1)
    [ -z "$my_ip" ] && return 1
    prefix=$(echo "$my_ip" | cut -d. -f1-3)
    self=$(echo "$my_ip" | cut -d. -f4)
    echo "$(date +%H:%M:%S) 扫描 $prefix.0/24 找 Dashboard" >> "$LOG"
    found=/tmp/kh-found
    rm -f "$found"
    i=2
    while [ "$i" -le 254 ]; do
        batch=0
        while [ "$batch" -lt 32 ] && [ "$i" -le 254 ]; do
            if [ "$i" != "$self" ]; then
                ( wget -q -O /dev/null "http://$prefix.$i:8787/health" 2>/dev/null && echo "$prefix.$i" >> "$found" ) &
            fi
            i=$((i + 1))
            batch=$((batch + 1))
        done
        wait
        if [ -s "$found" ]; then
            hit=$(head -1 "$found")
            rm -f "$found"
            DASHBOARD_URL="http://$hit:8787/screen.png"
            save_url "$DASHBOARD_URL"
            echo "$(date +%H:%M:%S) 找到 Dashboard: $hit" >> "$LOG"
            return 0
        fi
    done
    echo "$(date +%H:%M:%S) 网段内没找到 Dashboard" >> "$LOG"
    return 1
}
# ---------- 自更新：Mac 上改了脚本，Kindle 下次循环自动跟上 ----------
check_update() {
    # 只在这个文件确实位于 /mnt/us 时才自更新，避免调试时误覆盖
    case "$0" in
        /mnt/us/*) ;;
        *) return 0 ;;
    esac
    read_conf || return 0
    # 上传地址可能和 DASHBOARD_URL 不是同一台机器（换网络后尤其如此），
    # 所以逐个候选试一遍，取第一个能拿到 version.txt 的
    base=""
    for u in $DASHBOARD_URL $DASHBOARD_URLS; do
        [ -z "$u" ] && continue
        h=$(echo "$u" | sed -E 's|^https?://([^:/]+).*|\1|')
        [ -z "$h" ] && continue
        if probe_url "http://$h:8792/version.txt"; then
            base="http://$h:8792"
            break
        fi
    done
    [ -z "$base" ] && return 0
    remote=$(wget -q -O - "$base/version.txt" 2>/dev/null | tr -d ' \r\n')
    [ -z "$remote" ] && return 0
    [ "$remote" = "$VERSION" ] && return 0
    echo "$(date +%H:%M:%S) 发现新版本 $remote（当前 $VERSION），更新中" >> "$LOG"
    if wget -q -O "$0.new" "$base/kindlehub.sh"; then
        if [ -s "$0.new" ] && sh -n "$0.new" 2>/dev/null; then
            chmod 755 "$0.new"
            mv -f "$0.new" "$0"
            echo "$(date +%H:%M:%S) 更新完成，重新加载" >> "$LOG"
            exec sh "$0" loop
        fi
        rm -f "$0.new"
        echo "$(date +%H:%M:%S) 新脚本校验失败，保留旧版" >> "$LOG"
    fi
    return 0
}


# ---------- 单次刷新 ----------
refresh_once() {
    read_conf || { echo "$(date +%H:%M:%S) 缺少配置文件" >> "$LOG"; return 1; }
    resolve_url || { echo "$(date +%H:%M:%S) 没有可用的 Dashboard 地址" >> "$LOG"; return 1; }
    # 每次刷新都重新压一次系统状态栏：framework 可能把它恢复出来
    suppress_statusbar
    # 读电量。优先 sysfs（Linux 标准接口，最可靠），
    # 再退回 lipc 属性；两者都取不到就留空，服务端不画电量部分。
    BATT=""
    for f in /sys/class/power_supply/*/capacity; do
        [ -f "$f" ] || continue
        v=$(cat "$f" 2>/dev/null | tr -dc '0-9')
        [ -n "$v" ] && BATT="$v" && break
    done
    if [ -z "$BATT" ]; then
        BATT=$(lipc-get-prop com.lab126.powerd batteryLevel 2>/dev/null | tr -dc '0-9')
    fi
    if [ -z "$BATT" ]; then
        BATT=$(lipc-get-prop com.lab126.powerd status 2>/dev/null | grep -i batterylevel | head -1 | tr -dc '0-9')
    fi

    # 注意：不能用 *harging* 通配——"Discharging" 里也有 "charging"，
    # 会把放电误判成充电，导致没插电也显示闪电。必须精确匹配。
    CHG=0
    for f in /sys/class/power_supply/*/status; do
        [ -f "$f" ] || continue
        case "$(cat "$f" 2>/dev/null)" in
            Charging|Full) CHG=1; break ;;
        esac
    done
    [ "$CHG" = "0" ] && [ "$(lipc-get-prop com.lab126.powerd isCharging 2>/dev/null)" = "1" ] && CHG=1
    URL="$DASHBOARD_URL"
    case "$URL" in
        *\?*) URL="$URL&batt=$BATT&chg=$CHG" ;;
        *)    URL="$URL?batt=$BATT&chg=$CHG" ;;
    esac

    if wget -q -O "$IMAGE.new" "$URL"; then
        mv -f "$IMAGE.new" "$IMAGE"
        # 全屏绘制：图片已包含自绘状态栏，不再依赖系统栏
        eips -f -g -w gc16 "$IMAGE" 2>/dev/null || eips -f -g "$IMAGE"
        return $?
    fi
    echo "$(date +%H:%M:%S) 拉取失败: $DASHBOARD_URL" >> "$LOG"
    return 1
}

# ---------- 后台循环 ----------
start_daemon() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "已在运行 (PID $(cat "$PIDFILE"))"
        return 0
    fi
    read_conf || { echo "请先编辑 kindlehub.conf 填写 DASHBOARD_URL"; return 1; }

    # 关键：禁止屏保，这是直写方案能常亮的前提
    lipc-set-prop com.lab126.powerd preventScreenSaver 1

    # 后台跑主循环，重定向日志，脱离 KUAL 会话
    (
        echo "$$" > "$PIDFILE"
        trap 'rm -f "$PIDFILE"; lipc-set-prop com.lab126.powerd preventScreenSaver 0; exit' TERM INT
        while true; do
            refresh_once
            sleep "${REFRESH_SECONDS:-60}"
        done
    ) >> "$LOG" 2>&1 &

    sleep 1
    echo "已启动 (PID $(cat "$PIDFILE" 2>/dev/null))，地址: $DASHBOARD_URL"
}

# ---------- 停止 ----------
stop_daemon() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
    fi
    pkill -f "kindlehub.sh daemon" 2>/dev/null
    # 恢复屏保
    lipc-set-prop com.lab126.powerd preventScreenSaver 0
    echo "已停止，屏保已恢复"
}

# ---------- 状态 ----------
status_daemon() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "运行中 (PID $(cat "$PIDFILE"))"
        [ -f "$CONF" ] && echo "地址: $(grep ^DASHBOARD_URL "$CONF" | cut -d= -f2)"
        [ -f "$IMAGE" ] && echo "图片: $(ls -l "$IMAGE" | awk '{print $5, $6, $7, $8}')"
    else
        echo "未运行"
    fi
}

case "$1" in
    start)    start_daemon ;;
    stop)     stop_daemon ;;
    restart)  stop_daemon; sleep 1; start_daemon ;;
    status)   status_daemon ;;
    once)     refresh_once && echo "已刷新" ;;
    loop)     # 供 upstart 调用：前台无限循环，不 fork
              lipc-set-prop com.lab126.powerd preventScreenSaver 1
              suppress_statusbar
              while true; do
                  refresh_once
                  check_update
                  sleep "${REFRESH_SECONDS:-60}"
              done ;;
    version)  echo "KindleHub $VERSION" ;;
    *)        echo "用法: $0 {start|stop|restart|status|once|version}" ;;
esac