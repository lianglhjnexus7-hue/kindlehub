# Kindle 第 7 代（KT2）适配笔记

实测环境：macOS + Kindle 第 7 代（2014，600×800，167ppi，16 级灰）。
以下都是这台设备上验证过的结论，不是文档推测。

## 浏览器能力

UA：

```
Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+
(KHTML, like Gecko) Version/5.0 Safari/533.2+ Kindle/3.0+
```

WebKit 531/533 ≈ Safari 5.0。实测：

| 特性 | 支持 |
|---|---|
PNG 解码显示 | ✅ |
`<meta http-equiv="refresh">` | ✅ 可靠 |
`<img>` 自然尺寸显示 | ✅ |
`max-width:100%; height:auto` | ✅ |
`object-fit` | ❌ 需 Safari 10+ |
JavaScript | ⚠️ 默认关闭，打开后可用 |
横屏 | ❌ 浏览器锁死竖屏 |
mDNS / `.local` 主机名 | ❌ |

## 视口

实测 CSS 视口宽 **600px**，与屏幕物理宽度一致，**无缩放**。
所以图片按 600px 宽输出即可 1:1 呈现，不需要任何缩放技巧。

高度受浏览器 chrome 影响，约 730–800px。底部内容放在 760px 以上最稳。

## 关键架构选择

**服务端渲染 PNG，而不是下发 HTML/CSS。**

这条一举消掉所有前端兼容问题：JS 关闭、`object-fit` 不支持、横屏锁定、
950KB 的 SPA 打不开——都跟渲染无关，Kindle 只解码一张图。

## 刷新与残影

| 方案 | 效果 |
|---|---|
`meta refresh` 整页重载 | 可用，但边框标题反复重绘，残影明显 |
JS 局部刷 `img.src` | **推荐**，边框标题不重绘，残影大幅减轻 |
JS 监听 `window.onfocus` | 锁屏唤醒后立刻刷新，不用等周期 |

JS 可用时还会自动移除 `meta refresh`，避免两种机制叠加。
JS 再关掉会自动回退整页刷新。

采集频率与刷新周期应解耦：Mac 端 15–30 秒采一次，
屏幕 60 秒左右重绘一次。别为了让时钟每分钟跳就让整页持续重绘。

## 屏保

官方固件**无法关闭屏保**。锁屏后浏览器会挂起，唤醒后仍在原页面，
点一下屏幕或等一个刷新周期即恢复。这是不越狱的固有限制。

## 换 Wi-Fi 后失联

DHCP 会重新分配 IP，Kindle 上的旧地址随即失效。三种应对：

1. 路由器绑定静态 IP（注意 macOS 默认对每个网络用随机 MAC，
   需先在「系统设置 → Wi-Fi → 私有 Wi-Fi 地址」里关掉）
2. 手动配置静态 IP
3. 服务启动时打印当前地址，后台顶部也显示一份

`.local` 主机名不可用（浏览器不解析 mDNS）。

## 字体

macOS 中文可用：Hiragino Sans GB、STHeiti Light/Medium、Songti、Arial Unicode。
PingFang.ttc 不在 `/System/Library/Fonts/` 下。

注意 Pillow 的 `load_default()` 内置字体**无中文字形**，
必须显式指定 truetype 路径，否则中文全渲染成方框。

另外 Hiragino 的 `°C` 会有额外字距，看起来像 `° C`，
用全角 `℃`（U+2103）可避免。

## 灰度

16 级。量化时**关闭抖动**（`dither=Image.Dither.NONE`），
否则 e-ink 上会出现噪点状的脏面。
---

# 越狱直写模式（eips）

浏览器方案能用，但有三个绕不过去的限制：**会锁屏、有残影、多一层浏览器进程**。
越狱后用 `eips` 直接写 framebuffer 可以全部解决。以下是在 5.12.2.2 上实测的完整路径。

## 为什么直写更好

| 维度 | 浏览器 | eips 直写 |
|---|---|---|
| 锁屏 | 无法阻止 | `lipc-set-prop com.lab126.powerd preventScreenSaver 1` |
| 残影 | 整页重载，边框反复重绘 | `-w gc16` 指定刷新波形 |
| 功耗 | 常驻 WebKit | 只有 wget + eips |
| 显示区域 | 受浏览器 chrome 挤压（约 730–800px） | 全屏 600×800 |
| 刷新控制 | meta refresh | 完全自控（60 秒） |

## 越狱：WinterBreak（5.12.2.2）

固件 5.12.2.2（build 379151038）适用 [WinterBreak](https://github.com/KindleModding/WinterBreak)。

**关键点：它走 Kindle Store 的 Mesquito，不是浏览器。**

```bash
# 1. 下载 WinterBreak.tar.gz 并在电脑上解压（不能直接解压到 Kindle）
mkdir -p /tmp/wb && tar xzf WinterBreak.tar.gz -C /tmp/wb

# 2. 拷贝到 Kindle 根目录，含隐藏目录 .active_content_sandbox
cp -R /tmp/wb/mesquito /tmp/wb/apps /Volumes/Kindle/
cp -R /tmp/wb/.active_content_sandbox/. /Volumes/Kindle/.active_content_sandbox/

# 3. 拔 USB，开飞行模式再关（让 Kindle 重连网络）
# 4. 主页 → 购物车图标 → 打开 Kindle Store
# 5. Mesquito 加载后点 WinterBreak 图标
# 6. 约 30 秒后屏幕刷出大量文字并重启 GUI → 越狱完成
```

验证：搜索框输入 `;un`，有反应即成功。

### 走过的弯路

WinterBreak2（Mesquito-less 变体）在本机**不可用**。它依赖 `nativeBridge` 对象，
而实测该对象不存在——JS 本身是工作的，只是桥接层没有：

```javascript
// 用图片探针回报的结果
typeof nativeBridge   // → undefined（nb=0）
// 但 JS 本身在执行（js=1）
```

所以走 Mesquito 才是正路。

## hotfix：不需要手动装（也别装）

官方文档原文：

> You may skip this if you jailbroke using Winterbreak... **The hotfix has already been installed for you.**

我们试过手动装 hotfix，**全部报错误 007**（签名验证失败）：

| 版本 | 结果 |
|---|---|
| Hotfix 2.5.0（2026） | 错误 007 |
| Hotfix v1.14.0（2025） | 错误 007 |

原因在官方 Troubleshooting 里写了：

> There is a good chance that after installing an older version of Hotfix you
> **disabled OTA updates**, meaning the Hotfix update file will be **ignored**!

设备 2017 年越狱过，当时很可能禁用了 OTA，所以后续任何 `.bin` 更新都被直接忽略。
**结论：WinterBreak 越狱后不要碰 hotfix。**

## KUAL：旧版必然打不开

设备上是 2016 年的 `KUAL-KDK-2.0.azw2`（Kindlet 格式），在 5.12.2.2 上必然报
"应用程序错误"：

- Kindlet 支持在新固件里已移除
- 且 KUAL 的 developer.keystore 证书早已过期

新版 KUAL 走 **PEKI**（`KUAL.sh` + `KUAL.jar`）：

```bash
curl -L -o PEKI.zip https://github.com/KindleTweaks/PEKI/releases/latest/download/PEKI.zip
unzip PEKI.zip -d PEKI
cp PEKI/KUAL.sh PEKI/KUAL.jar /Volumes/Kindle/documents/
```

但 PEKI 的 `KUAL.sh` 需要 **root 写系统分区**（`mntroot rw`、改 `/var/local/appreg.db`），
而它由 `sh_integration` 以普通用户身份拉起，所以同样报"应用程序错误"。

**结论：KUAL 在本机不可用，绕开它。**

## 执行入口：Mesquito 的 request_upload（有 root）

WinterBreak 本身就是利用这个通道执行 `curl | sh`。它不修改系统，越狱后依然有效。

编辑 `apps/tech.hackerdude.winterbreak/dialoger.html`：

```javascript
nativeBridge.accessHasharrayProperty("com.lab126.transfer", "request_upload", {
    url: "http://127.0.0.1",
    source_command: "sh -c 'curl -L http://<mac>:8792/setup.sh | sh'",
    unique_id: "kindlehub",
    netid: 1,
    // ...
});
```

`source_command` **以 root 执行**。于是可以一次完成：下载脚本 → 写配置 → 注册服务 → 启动。

配套的 `setup.sh` 关键一步是**用 upstart 托管**：

```sh
mntroot rw
cat > /etc/init/kindlehub.conf <<EOF
start on started framework
stop on stopping framework
respawn
respawn limit 20 60
exec /bin/sh /mnt/us/extensions/kindlehub/bin/kindlehub.sh loop
EOF
mntroot ro
initctl start kindlehub
```

### 为什么必须 upstart

最初用 `nohup ... &` 启动，**进程会随 `curl | sh` 退出被杀**。实测日志：

```
GET /setup.sh      200    ← 脚本执行了
GET /kindlehub.sh  200    ← 下载了主程序
GET /screen.png    200    ← 冒烟测试拉了一次图
（此后 8787 再无请求，seconds_ago 从 71 一路涨到 134）
```

改成 upstart 后正常，且有 `respawn` 兜底。

## 电量：sysfs 才可靠

`lipc-get-prop com.lab126.powerd batteryLevel` 在 5.12.2.2 上**返回空**
（请求日志里表现为 `GET /screen.png?batt=&chg=0`）。改用 Linux 标准 sysfs：

```sh
for f in /sys/class/power_supply/*/capacity; do
    v=$(cat "$f" | tr -dc '0-9')
    [ -n "$v" ] && BATT="$v" && break
done
```

三重回退：sysfs → `batteryLevel` → 解析 `status` 字符串。
充电状态同理：先看 `/sys/class/power_supply/*/status` 是否含 `Charging`，再退回 `isCharging`。

## 状态栏：自己画，别让系统画

Kindle 系统状态栏会覆盖屏幕顶部约 40px，且：

- `lipc-set-prop com.lab126.pillow disableEnableStatusBar 0` **无效**
- 用 `eips -f` 全屏绘制会把它一起刷掉，导致看不到电量
- 用 `eips -y 40` 避开它，又会在交界处留一条白线

**最终方案：完全接管。** 服务端把状态栏画进图片，`eips -f -g` 全屏输出。

```python
device = { "width": 600, "height": 800, "top_inset": 40 }
```

- `schema.canvas_height()` 按 `height - top_inset` 推导归一化画布（1266 而非 1333）
- `renderer._draw_status_bar()` 画天气摘要 + 10 格电量条，全部靠左
  （系统栏的时间/电池在右上角，靠右放会被它重绘时盖住）
- 内容整体下移 `inset`
- Kindle 端通过 `?batt=85&chg=0` 把实时电量传上来

电量条用 10 个方块而不是连续长条——e-ink 上一眼能数出剩几格。
1–5% 至少点亮 1 格，避免看起来像关机。

状态栏左侧最初画的是时间，后来换成天气摘要：系统状态栏本来就有时间，
重复显示没意义；而天气摘要（如 `33℃ 晴`）一眼能看完，正好填这个位置。

### 系统栏偶尔会重绘

Kindle 的 framework 会在时间变化、电量变化、WiFi 状态变化时重绘系统状态栏，
它把时间/电池画在**右上角**。所以我们的信息必须靠左：

```text
33℃ 晴  ▮▮▮▮▮▮▮▮▯▯ 85%                      ← 我们的（左侧）
                              15:23 ▮▮      ← 系统重绘区（右侧，不冲突）
```

早期把电量放在右侧时，会被系统栏盖住、看不到百分比。

## 自更新：改脚本不用再插 USB

Kindle 每 60 秒查一次版本号：

```sh
check_update() {
    remote=$(wget -q -O - "http://<mac>:8792/version.txt" | tr -d ' \r\n')
    [ "$remote" = "$VERSION" ] && return 0
    wget -q -O "$0.new" "http://<mac>:8792/kindlehub.sh"
    sh -n "$0.new" && mv -f "$0.new" "$0" && exec sh "$0" loop
}
```

带 `sh -n` 语法校验，坏了就保留旧版。实测日志：

```
GET /version.txt     每分钟一次
GET /kindlehub.sh    版本变更后自动下载
```

## 最终布局（归一化画布 1266）

| 模块 | 范围 | 说明 |
|---|---|---|
| 状态栏 | 0–40px 物理 | 天气摘要 + 10 格电量，服务端自绘、内容靠左 |
| 天气 | 20–170 | 温度/天气 + 湿度/风 |
| Agent 监控 | 190–620 | 活跃 2 行、空闲 1 行，超 3 个空闲折叠 |
| 模型消耗 | 640–1130 | 最多 3 个模型 + 国内/国际积分 |
| 健康栏 | 1150–1220 | 更新时间 + 采集异常 |

## 踩过的坑速查

| 现象 | 原因 | 处理 |
|---|---|---|
| KUAL 报"应用程序错误" | 2016 年 Kindlet + 证书过期 | 用 PEKI 或绕开 KUAL |
| hotfix 错误 007 | OTA 已被禁用，本地 .bin 被忽略 | WinterBreak 自带 hotfix，不要装 |
| 浏览器打开 dialoger.html 无反应 | `nativeBridge` 不存在 | 必须走 Mesquito（Kindle Store） |
| 刷新循环起来又停 | `nohup &` 随安装脚本退出被杀 | 改用 upstart |
| `batt=` 参数为空 | `lipc-get-prop batteryLevel` 返回空 | 改读 sysfs |
| 状态栏区域被刷掉 | `eips -f` 覆盖了系统栏 | 自绘状态栏 + 全屏绘制 |
| 状态栏下方一条白线 | 图片与系统栏交界 | 同上 |
| 充电图标一直显示（没插电） | `*harging*` 会匹配 `Discharging` | 精确匹配 `Charging`/`Full` |
