# KindleHub 直写客户端（KT2）

通过 `wget` + `eips` 把 Mac 渲染的 PNG 直接画到 Kindle 屏幕。
不经过浏览器，所以**不锁屏、几乎没有残影、功耗最低**。

## 前提

- Kindle 已越狱（5.12.2.2 用 WinterBreak，见 `docs/kindle-kt2-notes.md`）
- Mac 上 KindleHub 服务已启动（默认 8787）
- 用于分发脚本的 HTTP 服务已启动（默认 8792）

## 目录

```text
extensions/kindlehub/
├── bin/kindlehub.sh        # 主脚本：拉图 + eips 画屏 + 自更新
├── kindlehub.conf          # 配置：Mac 地址、刷新间隔、状态栏高度
├── menu.json               # KUAL 菜单（KUAL 可用时）
└── config.xml              # KUAL 扩展声明
```

## 部署

### 首次安装（需要 USB）

把脚本放到 Kindle：

```bash
cp -R extensions/kindlehub /Volumes/Kindle/extensions/
cp "documents/KindleHub 启动.sh" /Volumes/Kindle/documents/ 2>/dev/null || true
```

然后注册开机自启服务。这一步需要 root，通过 Mesquito 执行：

1. Mac 上起一个 HTTP 服务托管 `setup.sh`（参考 `setup-via-mesquito.sh`）
2. 改 `apps/tech.hackerdude.winterbreak/dialoger.html`，把 `source_command` 指向它
3. Kindle 上打开 Kindle Store → 点 WinterBreak 图标

脚本会用 upstart 注册服务并启动：

```
/etc/init/kindlehub.conf
  start on started framework
  exec /bin/sh /mnt/us/extensions/kindlehub/bin/kindlehub.sh loop
```

### 后续更新（不需要 USB）

脚本带自更新：每 60 秒查一次 `http://<mac>:8792/version.txt`，
版本不同就下载新脚本、`sh -n` 校验、替换、`exec` 重载。

所以改 Kindle 端逻辑只要：

```bash
cp bin/kindlehub.sh <http服务目录>/kindlehub.sh
echo "1.3.0" > <http服务目录>/version.txt    # 版本号变了才会更新
```

最多等一分钟，Kindle 自己跟上。

## 配置

`kindlehub.conf`：

```sh
DASHBOARD_URL="http://<mac-ip>:8787/screen.png"  # Mac 地址，换 Wi-Fi 后要改
REFRESH_SECONDS=60                                    # 刷新间隔
SCREEN_WIDTH=600
SCREEN_HEIGHT=800
TOP_INSET=40                                          # 系统状态栏高度，仅作参考
```

改完重启服务生效：`initctl restart kindlehub`（需 root）。

## 常用命令

在 Kindle 上通过 Mesquito 或 SSH 执行：

```sh
/mnt/us/extensions/kindlehub/bin/kindlehub.sh start    # 后台启动（含禁屏保）
/mnt/us/extensions/kindlehub/bin/kindlehub.sh stop     # 停止 + 恢复屏保
/mnt/us/extensions/kindlehub/bin/kindlehub.sh once     # 手动刷一帧
/mnt/us/extensions/kindlehub/bin/kindlehub.sh status   # 查看状态
```

## 排查

| 现象 | 检查 |
|---|---|
| 屏幕不刷新 | `curl http://<mac>:8787/screen.png` 是否 200 |
| 画面是旧图 | Mac 地址变了，改 `DASHBOARD_URL` |
| 电量显示为空 | `cat /sys/class/power_supply/*/capacity` |
| 服务没起来 | `initctl status kindlehub` |
| 想看日志 | `cat /mnt/us/kindlehub-setup.log` 和 `daemon.log` |