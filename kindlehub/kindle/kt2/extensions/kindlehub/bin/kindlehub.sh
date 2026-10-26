#!/bin/sh
# KindleHub 直写客户端 —— Kindle 第 7 代（KT2，600x800）
#
# 与浏览器方案的区别：
#   浏览器：Kindle 开实验浏览器访问页面，靠 meta refresh 整页重载，会锁屏、有残影
#   直写：  wget 拉 PNG + eips 直接画到屏幕，lipc 关屏保，无浏览器、无残影、更省电
#
# 依赖：越狱 + KUAL（本目录即 KUAL 扩展），不需要改固件

VERSION="1.5.0"
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


# ---------- 自更新：Mac 上改了脚本，Kindle 下次循环自动跟上 ----------
check_update() {
    # 只在这个文件确实位于 /mnt/us 时才自更新，避免调试时误覆盖
    case "$0" in
        /mnt/us/*) ;;
        *) return 0 ;;
    esac
    read_conf || return 0
    # 上传地址从 DASHBOARD_URL 推导（同主机、8792 端口），脚本里不硬编码 IP
    host=$(echo "$DASHBOARD_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
    [ -z "$host" ] && return 0
    base="${UPLOAD_BASE:-http://$host:8792}"
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
                  check_update
                  refresh_once
                  sleep "${REFRESH_SECONDS:-60}"
              done ;;
    version)  echo "KindleHub $VERSION" ;;
    *)        echo "用法: $0 {start|stop|restart|status|once|version}" ;;
esac