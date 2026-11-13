#!/bin/sh
# KindleHub 直写客户端 —— Kindle 第 7 代（KT2，600x800）
#
# 与浏览器方案的区别：
#   浏览器：Kindle 开实验浏览器访问页面，靠 meta refresh 整页重载，会锁屏、有残影
#   直写：  wget 拉 PNG + eips 直接画到屏幕，lipc 关屏保，无浏览器、无残影、更省电
#
# 依赖：越狱 + KUAL（本目录即 KUAL 扩展），不需要改固件

VERSION="1.13.1"
FAIL_COUNT=0
CONF="/mnt/us/extensions/kindlehub/kindlehub.conf"
IMAGE="/mnt/us/extensions/kindlehub/dashboard.png"
PIDFILE="/mnt/us/extensions/kindlehub/daemon.pid"
LOG="/mnt/us/extensions/kindlehub/daemon.log"

# 每 N 次刷新做一次全屏黑闪（GC16），清掉局部刷新积累的残影。
# e-ink 局部刷只重写变化的像素，快且无闪，但旧画面会慢慢"透"出来；
# 参考同类项目（pascalw/kindle-dash 默认 4），信息屏变化少取 10。
FULL_REFRESH_EVERY=${FULL_REFRESH_EVERY:-10}
# 初值取满：开机/自更新后的第一刷走全刷，顺带清掉之前的残留
_refresh_count=$FULL_REFRESH_EVERY

# ---------- 从分发服务读取可选的跨网络配置 ----------
# 有 relay.conf 就启用固定 URL 兜底，没有就完全不启用。
# 这样启用/停用不用插 USB：改分发目录里的一个文件即可。
load_relay_conf() {
    [ -n "$RELAY_URL" ] && return 0      # 本地 conf 优先
    [ -z "$UPLOAD_BASE" ] && return 0
    _rc=/tmp/kh-relay.conf
    if wget -q -O "$_rc" "$UPLOAD_BASE/relay.conf" 2>/dev/null && [ -s "$_rc" ]; then
    RELAY_URL=$(sed -n 's/^RELAY_URL=//p' "$_rc" | head -1 | tr -d "\"'")
        [ -n "$RELAY_URL" ] && echo "$(date +%H:%M:%S) 从分发服务启用中继" >> "$LOG"
    fi
    rm -f "$_rc"
    return 0
}
# ---------- 配置读取（key=value，兼容空格与引号） ----------
read_conf() {
    [ -f "$CONF" ] || return 1
    eval "$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$CONF")"
    load_relay_conf
}

# ---------- 抑制系统状态栏 ----------
# 不同固件/机型属性名不一样，逐个尝试；失败也无害。
suppress_statusbar() {
    for target in         "com.lab126.pillow disableEnableStatusBar 0"         "com.lab126.chromebar controls 0"         "com.lab126.pillow statusBarVisible 0"         "com.lab126.pillow statusBarEnable 0"
    do
        lipc-set-prop $target 2>/dev/null
    done
}

# ---------- 关停 GUI，给刷新循环让路 ----------
# framework（Kindle GUI）+ webreader 常驻要吃掉几十 MB 内存和持续 CPU，
# 还会不定时用自己的波形重绘状态栏——这是"卡"和残影的根源之一。
# 停掉后系统只剩内核 + Wi-Fi + 本循环。重启设备会自动恢复 GUI，安全可逆。
stop_gui() {
    case "$(cat /tmp/kh-gui-stopped 2>/dev/null)" in
        1) return 0 ;;
    esac
    /etc/init.d/framework stop 2>/dev/null
    initctl stop webreader 2>/dev/null
    # powersave 调频：空闲时降频，拉图/刷屏那几秒不受影响
    echo powersave >/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null
    echo 1 > /tmp/kh-gui-stopped
    echo "$(date +%H:%M:%S) GUI 已停（framework/webreader），CPU 调至 powersave" >> "$LOG"
}

# 恢复 GUI：只在手动 stop 时调用，让设备回到正常阅读器形态
start_gui() {
    rm -f /tmp/kh-gui-stopped
    /etc/init.d/framework start 2>/dev/null
    echo "$(date +%H:%M:%S) GUI 已恢复" >> "$LOG"
}

# ---------- Wi-Fi 按需开关（省电最大头） ----------
# 空闲时 Wi-Fi 常驻约多耗 30-50% 电量。拉图前开、拉完就关，
# 每轮只在线几秒。lipc 开关是官方电源管理走的同一通道，可靠。
wifi_on() {
    lipc-set-prop com.lab126.cmd wirelessEnable 1 2>/dev/null
    # 等关联完成，最多 20 秒；连不上也不无限等（后面 wget 自然超时）
    _tries=0
    while [ "$_tries" -lt 20 ]; do
        state=$(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)
        case "$state" in
            *DISCONNECTED*) ;;
            *CONNECTED*) return 0 ;;
        esac
        sleep 1
        _tries=$((_tries + 1))
    done
    return 1
}

wifi_off() {
    lipc-set-prop com.lab126.cmd wirelessEnable 0 2>/dev/null
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
# 标志文件用 mktemp 取唯一名：scan_lan 会并行调用本函数，
# 固定名会互相覆盖造成误判。
probe_url() {
    _url="$1"
    # HTTPS（中继 raw）必须用 curl：busybox wget 不支持 SSL。否则中继地址
    # 的「上次成功」快路径每轮都探测失败，白白退化到全量兜底（多耗 3 秒+候选探测）。
    case "$_url" in
        https://*)
            curl -sSL --max-time 3 -o /dev/null "$_url" 2>/dev/null
            return
            ;;
    esac
    _flag=$(mktemp /tmp/kh-probe.XXXXXXXX 2>/dev/null) || _flag="/tmp/kh-probe-$$"
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
    # 3) 固定 URL 兜底：Mac 与 Kindle 不在同一个网络时 LAN 一定找不到，
    #    但 Mac 已把这一帧推到了一个永不变化的公开 URL。优先于扫网段。
    if [ -n "$RELAY_URL" ]; then
        if curl -sSL --max-time 12 -o /dev/null "$RELAY_URL?t=$(date +%s)" 2>/dev/null; then
            DASHBOARD_URL="$RELAY_URL"
            echo "$(date +%H:%M:%S) 走中继: $RELAY_URL" >> "$LOG"
            return 0
        fi
    fi
    # 4) 连续失败够多才扫网段
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
    rm -f "$found" /tmp/kh-hit
    i=2
    while [ "$i" -le 254 ]; do
        batch=0
        while [ "$batch" -lt 32 ] && [ "$i" -le 254 ]; do
            if [ "$i" != "$self" ]; then
                # probe_url 自带 3 秒超时兜底；裸 wget 遇到不回 RST 的
                # 死 IP 会挂住，wait 一等就是几分钟
                ( probe_url "http://$prefix.$i:8787/health" && echo "$prefix.$i" >> "$found" ) &
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
# 点分数字比较：$1 > $2 时返回 0。只升不降，杜绝降级攻击与版本回退。
version_gt() {
    _a="$1"; _b="$2"
    while [ -n "$_a" ] || [ -n "$_b" ]; do
        _x=${_a%%.*}; _y=${_b%%.*}
        [ -z "$_x" ] && _x=0
        [ -z "$_y" ] && _y=0
        [ "$_x" -gt "$_y" ] 2>/dev/null && return 0
        [ "$_x" -lt "$_y" ] 2>/dev/null && return 1
        case "$_a" in *.*) _a=${_a#*.} ;; *) _a="" ;; esac
        case "$_b" in *.*) _b=${_b#*.} ;; *) _b="" ;; esac
    done
    return 1
}

check_update() {
    # 只在这个文件确实位于 /mnt/us 时才自更新，避免调试时误覆盖
    case "$0" in
        /mnt/us/*) ;;
        *) return 0 ;;
    esac
    read_conf || return 0
    # 上传地址可能和 DASHBOARD_URL 不是同一台机器（换网络后尤其如此），
    # 所以逐个候选试一遍，取第一个能拿到 version.txt 的。
    # conf 里可用 UPDATE_HOST=<ip> 钉死更新源：LAN 里任何设备都能冒充
    # 8792 分发服务，钉住后其余主机一律不提供更新。
    base=""
    for u in $DASHBOARD_URL $DASHBOARD_URLS; do
        [ -z "$u" ] && continue
        h=$(echo "$u" | sed -E 's|^https?://([^:/]+).*|\1|')
        [ -z "$h" ] && continue
        if [ -n "$UPDATE_HOST" ] && [ "$h" != "$UPDATE_HOST" ]; then
            continue
        fi
        if probe_url "http://$h:8792/version.txt"; then
            base="http://$h:8792"
            break
        fi
    done
    [ -z "$base" ] && return 0
    remote=$(wget -q -O - "$base/version.txt" 2>/dev/null | tr -d ' \r\n')
    # 版本号必须是纯数字点分，且严格大于当前版本（相等/降级都跳过）
    case "$remote" in
        ""|*[!0-9.]*) return 0 ;;
    esac
    version_gt "$remote" "$VERSION" || return 0
    echo "$(date +%H:%M:%S) 发现新版本 $remote（当前 $VERSION），更新中" >> "$LOG"
    # 完整性校验：sha256 清单由 deploy/publish-kindle.sh 生成。
    # HTTP 明文下载 + 仅 sh -n 不足以信任：同网段任何主机都能投递恶意脚本。
    if wget -q -O "$0.new" "$base/kindlehub.sh" \
       && wget -q -O "$0.sha256" "$base/kindlehub.sh.sha256"; then
        downloaded=$(sed -n 's/^VERSION="\([^"]*\)"$/\1/p' "$0.new")
        want=$(awk '{print $1}' "$0.sha256" 2>/dev/null)
        got=$(sha256sum "$0.new" 2>/dev/null | awk '{print $1}')
        if [ "$downloaded" = "$remote" ] && [ -s "$0.new" ] \
           && [ -n "$want" ] && [ "$got" = "$want" ] && sh -n "$0.new" 2>/dev/null; then
            chmod 755 "$0.new"
            rm -f "$0.sha256"
            mv -f "$0.new" "$0" || return 1
            echo "$(date +%H:%M:%S) 更新完成（sha256 已校验），重新加载" >> "$LOG"
            exec sh "$0" loop
        fi
        rm -f "$0.new" "$0.sha256"
        echo "$(date +%H:%M:%S) 新脚本校验失败（版本/sha256/语法），保留旧版" >> "$LOG"
    fi
    return 0
}


# 日志不能无限涨：超过 256KB 就只保留尾部 128KB。
# 每轮刷新都检查一次，常驻数月的设备也不会把 /mnt/us 写满。
rotate_log() {
    [ -f "$LOG" ] || return 0
    _size=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
    case "$_size" in
        ""|*[!0-9]*) return 0 ;;
    esac
    if [ "$_size" -gt 262144 ]; then
        tail -c 131072 "$LOG" > "$LOG.tmp" 2>/dev/null && mv -f "$LOG.tmp" "$LOG"
    fi
}

# ---------- 单次刷新 ----------
refresh_once() {
    rotate_log
    read_conf || { echo "$(date +%H:%M:%S) 缺少配置文件" >> "$LOG"; return 1; }
    # 停机开关：framework 已停、没有 KUAL 菜单可用时，
    # 在 conf 里加一行 KILL=1（USB 挂载改文件即可）就能停机并恢复阅读器
    case "${KILL:-0}" in
        1) echo "$(date +%H:%M:%S) conf 里 KILL=1，停机并恢复 GUI" >> "$LOG"
           rm -f "$PIDFILE"
           wifi_off
           lipc-set-prop com.lab126.powerd preventScreenSaver 0
           start_gui
           exit 0 ;;
    esac
    # Wi-Fi 只在拉图期间在线（省电最大头）
    wifi_on || { wifi_off; echo "$(date +%H:%M:%S) Wi-Fi 无法连接" >> "$LOG"; return 1; }
    resolve_url || { wifi_off; echo "$(date +%H:%M:%S) 没有可用的 Dashboard 地址" >> "$LOG"; return 1; }
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
    # 中继是公开 GitHub raw，不接受电量参数，且必须走 HTTPS（curl）
    if [ "$URL" = "$RELAY_URL" ] && [ -n "$RELAY_URL" ]; then
        RELAY_MODE=1
    else
        case "$URL" in
            *\?*) URL="$URL&batt=$BATT&chg=$CHG&v=$VERSION" ;;
            *)    URL="$URL?batt=$BATT&chg=$CHG&v=$VERSION" ;;
        esac
        RELAY_MODE=0
    fi

    if [ "$RELAY_MODE" = "1" ]; then
        ok=0
        curl -sSL --max-time 20 -o "$IMAGE.new" "$URL?t=$(date +%s)" 2>/dev/null && ok=1
    else
        ok=0
        wget -q -O "$IMAGE.new" "$URL" 2>/dev/null && ok=1
    fi
    if [ "$ok" = "1" ] && [ -s "$IMAGE.new" ]; then
        mv -f "$IMAGE.new" "$IMAGE"
        # 平时局部刷新（快、无黑闪），每 FULL_REFRESH_EVERY 次
        # 用 GC16 全刷一次清残影。局部刷失败兜底成全刷。
        if [ "$_refresh_count" -ge "$FULL_REFRESH_EVERY" ]; then
            _refresh_count=0
            eips -f -g -w gc16 "$IMAGE" 2>/dev/null || eips -f -g "$IMAGE"
        else
            _refresh_count=$((_refresh_count + 1))
            eips -g "$IMAGE" 2>/dev/null || eips -f -g -w gc16 "$IMAGE" 2>/dev/null || eips -f -g "$IMAGE"
        fi
        # 自更新检查也在 Wi-Fi 在线窗口内完成，否则 Wi-Fi 关了它永远失效
        check_update
        wifi_off
        return 0
    fi
    wifi_off
    echo "$(date +%H:%M:%S) 拉取失败: $DASHBOARD_URL" >> "$LOG"
    return 1
}

# upstart 的 stop on stopping framework 会把本服务连坐杀掉——
# 我们自己要停 framework，两者互斥。root 身份下把 stop 条件改掉（一次性）。
# 非 root（KUAL 菜单）跑不动 mntroot，静默跳过，setup.sh 会在安装时写对。
fix_upstart_conf() {
    job=/etc/init/kindlehub.conf
    [ -f "$job" ] || return 0
    grep -q "stop on stopping framework" "$job" || return 0
    mntroot rw 2>/dev/null || mount -o remount,rw / 2>/dev/null || return 0
    sed -i 's/^stop on stopping framework$/stop on runlevel [!2345]/' "$job" 2>/dev/null
    mntroot ro 2>/dev/null || mount -o remount,ro / 2>/dev/null
    echo "$(date +%H:%M:%S) upstart stop 条件已改为 runlevel（不再随 framework 连坐）" >> "$LOG"
}

# ---------- 后台循环 ----------

# 退出前清掉 PIDFILE、恢复屏保、Wi-Fi 与 GUI；TERM 也可能来自 stop_daemon 的 kill。
# GUI 必须在这里恢复：手动 kill loop 时 stop_daemon 不在场，
# 不恢复的话设备会停在黑屏无界面状态。
_loop_trap() {
    rm -f "$PIDFILE"
    wifi_off
    lipc-set-prop com.lab126.powerd preventScreenSaver 0
    start_gui
    exit
}

start_daemon() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "已在运行 (PID $(cat "$PIDFILE"))"
        return 0
    fi
    read_conf || { echo "请先编辑 kindlehub.conf 填写 DASHBOARD_URL"; return 1; }

    # 委托给 loop 模式后台跑：POSIX sh 里子 shell 的 $$ 仍是父进程 PID，
    # 旧实现在 ( … ) & 里写 PIDFILE 记的是 KUAL 启动器的短命 PID，
    # stop/status 全都找错进程。loop 模式写的是自己的真实 PID，
    # 且屏保/GUI/trap 清理与 upstart 路径完全一致。
    sh "$0" loop >> "$LOG" 2>&1 &

    sleep 1
    echo "已启动 (PID $(cat "$PIDFILE" 2>/dev/null))，地址: $DASHBOARD_URL"
}

# ---------- 停止 ----------
stop_daemon() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        rm -f "$PIDFILE"
    fi
    # 兜底：PIDFILE 丢失时按名字找。loop 是唯一的前台常驻形态
    pkill -f "kindlehub.sh loop" 2>/dev/null
    # 恢复屏保、Wi-Fi 与 GUI，设备回到正常阅读器形态
    wifi_off
    lipc-set-prop com.lab126.powerd preventScreenSaver 0
    start_gui
    echo "已停止，屏保与 GUI 已恢复"
}

# ---------- 状态 ----------
status_daemon() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "运行中 (PID $(cat "$PIDFILE"))，$(cat /tmp/kh-gui-stopped 2>/dev/null | grep -q 1 && echo GUI已停 || echo GUI运行中)"
        [ -f "$CONF" ] && echo "地址: $(grep '^DASHBOARD_URL=' "$CONF" | cut -d= -f2)"
        [ -f "$IMAGE" ] && echo "图片: $(ls -l "$IMAGE" | awk '{print $5, $6, $7, $8}')"
        echo "全刷间隔：$FULL_REFRESH_EVERY 次局部刷新（重启后首刷为全刷）"
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
    loop)     # 供 upstart / 自更新 exec 调用：前台无限循环，不 fork。
              # 也写 PIDFILE，否则 KUAL 的"停止"（kill PIDFILE + pkill daemon）
              # 既找不到 PID 也匹配不到进程名，loop 就停不下来了。
              echo "$$" > "$PIDFILE"
              trap _loop_trap TERM INT
              fix_upstart_conf
              lipc-set-prop com.lab126.powerd preventScreenSaver 1
              stop_gui
              suppress_statusbar
              while true; do
                  refresh_once
                  sleep "${REFRESH_SECONDS:-60}"
              done ;;
    version)  echo "KindleHub $VERSION" ;;
    *)        echo "用法: $0 {start|stop|restart|status|once|version}" ;;
esac