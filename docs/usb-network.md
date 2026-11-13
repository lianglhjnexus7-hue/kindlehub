# 不依赖 Wi-Fi 的传输方案（USB 网络）

> 调研日期 2026-11-08，针对 KT2 / 固件 5.12.2.2 + macOS。
> 结论：**USB 网络（usbnet）是唯一真正达标且成熟的方案**。

## 为什么现有方案会断

现在 Kindle 用 `wget http://<Mac LAN IP>:8787/screen.png` 拉图。Mac 在家里 / 公司 / 手机热点之间切换时，DHCP 每次给不同网段，配置就失效。脚本已做三层兜底（缓存地址 → 候选列表 → 扫描网段），但前提是两者**在同一个二层网络**。手机热点还常开客户端隔离，连 ARP 都不通。

## 方案对比

| 方案 | 零 Wi-Fi | 实时 | 改动量 | 风险 |
|---|---|---|---|---|
| **A. USB 网络（usbnet）** | ✅ | ✅ | 中 | Mac 驱动需实测 |
| B. 云端中继 / 固定域名 | ❌（仍需 Wi-Fi，但不要求同网） | ✅ | 极小 | 隐私、旧 wget 的 HTTPS |
| C. USB U 盘预载 + 拔线显示 | ✅ | ❌ | 小 | 只能半自动 |
| D. 蓝牙 / ADB / USB 串口 | — | — | — | 硬件不支持 |

**蓝牙**：KT2（2014）没有蓝牙硬件；Amazon 官方说明 2016 年后机型才支持且仅限音频。
**ADB**：只属于 Kindle Fire 平板，e-ink Kindle 是 Lab126 Linux。
**USB 串口**：主板上的 1.8V TTL 调试焊盘，需拆机，只用于救砖。
**mDNS**：只解决「IP 变了」，不解决「不在同一局域网」，手机热点常禁组播。

## 方案 A：USB 网络（推荐）

### 原理

越狱后装 USBNetwork hack，把 Kindle 的 USB gadget 从 `g_file_storage`（U 盘）切成 `g_ether`（网卡）。此时：

```
Mac 192.168.15.201  <-- USB 线 -->  Kindle 192.168.15.244
```

- **IP 写死，与 Wi-Fi 完全无关**，换任何网络都不用改配置
- USB 同时供电，长期插线反而更稳（Kindle 不会深睡）
- `/mnt/us` 在 Kindle 侧照常可读写，**eips 直写链路一行都不用改**
- 实测 USB 约 6.7 MB/s，比 Wi-Fi（3.3 MB/s）还快，一张 PNG 是毫秒级

### 版本选择（重要）

KT2 / 5.12.2.2 属于**旧固件 armel**，必须用 **NiLuJe USBNetwork 0.22.N r19297**：

- 快照帖：https://www.mobileread.com/forums/showthread.php?t=225030
- 包名形如 `Update_usbnet_0.21.N_install_pw2_kt2_kv_pw3.bin`（含 kt2 字样）
- 包归档明确列出支持 KT2

**不要用** [notmarek/kindle-usbnetlite](https://github.com/notmarek/kindle-usbnetlite)：它面向 5.16.3+ hard-float，issue 明确提示旧固件要用兼容版本。

### 安装步骤

```bash
# 1. USB 插着、U 盘模式下，把 .bin 放进 /Volumes/Kindle/mrpackages/
# 2. 安全弹出并拔线（关键：必须先拔线再切换）
# 3. Kindle 搜索框输入：
;log mrpi          # 安装 MR 包
;un                # 切换 USB 网络模式（toggle，再输一次回 U 盘模式）
;711               # 查看网络信息
# 4. 这时才插上 USB 线
```

自启：把 `/mnt/us/usbnet/DISABLED_auto` 改名为 `auto`。

### Mac 端（唯一需要实测的未知量）

插线后看：**系统设置 → 网络** 是否出现 `RNDIS/Ethernet Gadget` 或 CDC Ethernet 网卡。

- 若出现 → IPv4 选「手动」，填 `192.168.15.201` / `255.255.255.0`
- 然后 `ping 192.168.15.244`，通了就全通

**风险**：macOS 原生支持 CDC ECM/NCM，不支持纯 RNDIS；Apple Silicon + macOS 15+ 若协商成 RNDIS，HoRNDIS kext 已废弃（[issue #174](https://github.com/jwise/HoRNDIS/issues/174) 明确不支持 Apple Silicon）。用户态备选（无需 kext / SIP）：

- [TetherKit](https://github.com/RSokolowskyDev/TetherKit)（libusb + feth，macOS 14/15/26）
- [rndis-mac](https://github.com/Zorglub51/rndis-mac)（Rust + utun，macOS 15 实测过），参数 `--vid 0525 --pid a4a2 --host-ip 192.168.15.201 --peer-ip 192.168.15.244`

**小坑**：该网卡可能抢走 Mac 的默认路由导致 Mac 自己上不了网，在网络服务顺序里把 Wi-Fi 置顶、不要给它设默认网关。

### 接入本项目（改动极小）

配置里已经把 USB 网络地址放在候选列表第一项：

```sh
DASHBOARD_URLS="http://192.168.15.201:8787/screen.png http://192.168.1.100:8787/screen.png"
```

Mac 服务监听 `0.0.0.0:8787`（本来就是），所以 USB 网卡上同样能访问。脚本的 `resolve_url` 会优先命中 USB 地址并写回配置；拔线后自动回落到 Wi-Fi 候选。也可以直接用 SSH 推文件（Kindle 的 busybox sshd 没有 SFTP，要用 `cat > 文件` 流式传）。

### 已知坑

| 坑 | 处理 |
|---|---|
| `192.168.15.0/24` 与家里 LAN 冲突，压过 wlan0 | 改成 `192.168.244.244` / `.201`（[完整命令](https://github.com/gusthanks/kindlelab/blob/main/docs/03-usbnetwork-ssh.md)） |
| 插线时输 `;un` | 会 kernel panic，务必先拔线 |
| `USE_WIFI="true"` | 会在 Wi-Fi 上暴露 root SSH，保持 false |
| U 盘模式与网卡模式互斥 | 要拷书就再输一次 `;un` 切回 |
| 充电专用线 / USB 3.0 口 | 换数据线、直插 USB 2.0 |

## 方案 B：云端中继（改动最小的兜底）

把 PNG PUT 到固定公网 URL（Cloudflare Worker / R2、对象存储），Kindle 端 `DASHBOARD_URL` 换成域名。Mac 和 Kindle 可在任意网络，甚至不同城市，Kindle 继续电池供电挂墙。

参考实现：

- [usetrmnl/trmnl-kindle](https://github.com/usetrmnl/trmnl-kindle)（469★）：`/api/display` 返回 `image_url`
- [saturngod/kindle-family-dashboard](https://github.com/saturngod/kindle-family-dashboard)：`/version` + `/image/{v}.png`，版本不变不重绘，省电
- [samkhawase/kindle-dash-client](https://github.com/samkhawase/kindle-dash-client)：Cloudflare Worker

**注意**：旧 Kindle 的 wget/curl 用很老的 OpenSSL，HTTPS 常失败。[pascalw/kindle-dash](https://github.com/pascalw/kindle-dash) 的做法是带一个预编译的 [ht](https://github.com/ducaale/ht) 客户端，或加 `--no-check-certificate`。

**隐私**：会把仪表盘内容（Agent 项目名、用量）放到公网。本项目已做会话标题过滤（prompt 不上墙），但仍需自行评估。

## 方案 C：USB U 盘预载（离线兜底，非主通道）

`kindlehub/kindle/kt2/preload.sh` 把若干帧 PNG 写进 `/Volumes/Kindle/kindlehub-offline/`，拔线后 Kindle 从本地读图循环显示，屏幕不会黑。

**物理限制**：USB 大容量存储模式下 `/mnt/us` 被卸载交给 Mac 独占，framework 被暂停，Amazon 的 "USB Drive Mode" 提示屏会覆盖画面。[saturngod/kindle-family-dashboard](https://github.com/saturngod/kindle-family-dashboard) 的 dashboard.sh 明确轮询等待卷回来。所以**插线期间无法实时刷新**，只能当断网时的静态兜底。

调研中**没有发现任何同类项目把 U 盘推图当主通道**——全部用 HTTP 拉图或 usbnet 上的 SSH/rsync 推文件。

## 落地顺序

1. 花 10 分钟做 USB 网络连通性实测（装 hack → 插线 → 看 macOS 网络面板 → ping）
2. 通了就用候选列表里的 `192.168.15.201`，eips / 渲染代码一行不动
3. 拔线时自动回落到 Wi-Fi 候选；若要跨城市，再叠加方案 B 的云 URL
4. 别动 `eips` 和 PNG 渲染链路

## 参考

- https://wiki.mobileread.com/wiki/USBNetwork
- https://wiki.mobileread.com/wiki/Ethernet-over-USB_RNDIS_driver_setup
- https://github.com/gusthanks/kindlelab/blob/main/docs/03-usbnetwork-ssh.md
- https://github.com/zevisvei/kindle-reading-dashboard/blob/main/docs/usb-networking.md
- https://gist.github.com/adtac/eb639d3c707b55a28f0ee9a420aa7e0c （Mac + usbnet + netcat + eips 的 e-ink monitor，证明整条链路可跑）
- https://github.com/codecube/kindle-mcp-dashboard （本项目上游，README 明确要求同 LAN，未解决换网络）
