# KindleHub

面向 **Kindle 第 7 代（KT2，600×800，16 级灰）** 的局域网只读电子墨水仪表盘。

架构上派生自 [codecube/kindle-mcp-dashboard](https://github.com/codecube/kindle-mcp-dashboard)（MIT），
复用其「MCP 工具 + Pillow 服务端渲染 + HTTP 只读显示」的设计，并针对 KT2 与 macOS 做了适配。

## 为什么是服务端渲染

实测 Kindle 第 7 代实验性浏览器的 UA 为：

```
Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+
(KHTML, like Gecko) Version/5.0 Safari/533.2+ Kindle/3.0+
```

即 WebKit 531.2+/533.2+（约 Safari 5.0 内核），且 **JavaScript 默认关闭**。因此：

- 不能用前端渲染、AJAX 轮询或任何脚本交互
- `object-fit`（需 Safari 10+）不可用
- 服务端出 PNG + `meta refresh` 是唯一稳定路线

Kindle 只解码一张静态图片，所有排版、字体、灰度处理都在 Mac 上完成。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e kindlehub

# 状态文件默认在 ~/.local/state/kindle-mcp-dashboard/dashboard.json
# 建议显式指定，便于备份与回滚
export KINDLE_DASHBOARD_STATE="$PWD/.state/dashboard.json"

.venv/bin/kindlehub preview --output preview.png   # 本地出图
.venv/bin/kindlehub serve --host 0.0.0.0 --port 8787 --refresh 60
```

Kindle 上打开 `http://<Mac 局域网 IP>:8787/kindle`。

## Agent 监控

只读采集本机 AI Agent 的运行状态，**拿不到可靠证据时一律显示"未知"，不伪造**。

| Agent | 数据来源 | 能判断什么 | 置信度 |
|---|---|---|---|
| Codex | `~/.codex/sessions/**.jsonl` | `task_started` / `task_complete` 生命周期 | 高 |
| DSH | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | `step/start`、`tool/call` 事件 + 文件活跃度 | 高 |
| WorkBuddy | `/health` | 仅网关在线与账号数，任务状态不可得 | 低 |
| OpenCode | 本地 API 需认证 | 未接入 | — |

状态判定规则（实测 2026-10-23 核查）：

| 状态 | 判断依据 | 置信度 |
|---|---|---|
| `waiting` | 检测到 `permission/request` / `elicit/request` 等等待授权事件 | 高 |
| `running` | `task_started` / `turn/start` 未配对结束事件，且 120 秒内仍有写入 | 高 |
| `idle` | 收到 `task_complete` / `turn/end`，或状态接口报告 completed | 高 |
| `error` | 结束事件带 error 字段 | 高 |
| `unknown` | 无边界事件可依 | 低 |
| `disconnected` | 采集器不可达 | — |

两个刻意的设计：

1. **"没有结束事件"不能单独证明还在跑**。Codex 会话被中断时不会补写
   `task_complete`，所以还要看事件是否仍在写入；不再写入则标 `idle` 并注明
   "可能被中断"。
2. **OpenCode 只能推测**。它的 event 表只有 message/session.updated，没有 turn
   边界事件，因此一律标低置信度，屏幕上会写"状态为推测"，不与 DSH/Codex 同等看待。

**不会**因为"进程存在"或"文件刚被修改"就判定为执行中。

每个 Agent 都带 `basis`（状态依据）与 `confidence`（确证/半推测/推测），
屏幕直接显示，便于判断要不要信这个状态。

多个 DSH 会话可能同时活跃且只差毫秒（实测 ObsidianVault 与当前项目），
因此按项目聚合列出，不猜哪个是"主项目"。

## 天气

使用 [Open-Meteo](https://open-meteo.com/)：免费、无需注册、无需 API Key。

城市在管理后台顶部"天气城市"填写，支持中文（`湛江`、`上海`、`杭州`）。
地名解析会依次尝试 原名 → 原名+市 → 原名+区，并在多个同名候选里选人口最多的
（否则"上海市"会命中美国伊利诺伊州的同名小镇）。

天气在后台线程每 5 分钟预热一次，渲染路径只读缓存，**绝不发起网络请求**——
e-ink 客户端等不起 DNS/TLS 握手。抓取失败显示"未知"，不编造数据。

## WorkBuddy 用量看板

看板本身是 950KB 的 JS 单页应用，Kindle 无法显示。这里改为调用它的 JSON 接口
（`POST /panel/login` → `GET /usage`，header `X-Panel-Token`），在服务端取聚合数字重绘：

```
请求 770 · 成功率 73%
Token 105.8M · 缓存 92%
花费 ¥44.00
```

- 只读聚合字段，账号 UUID、凭证文件名、日志路径一律不碰
- 看板密码存在 `secrets.json`（权限 0600），**不进入 dashboard.json**，
  所以导出/导入布局不会带出密码
- 密码在管理后台左侧「看板密码」里设置
- 与天气一样走后台预热，渲染路径只读缓存

## 管理后台

Mac 本机浏览器打开 `http://127.0.0.1:8787/admin`（**局域网访问返回 403**）。

- 增删显示模块、改标题与内容、绑定数据源
- 在预览图上直接拖动位置、拖右下角改大小
- 调整刷新间隔（10–3600 秒）、字号、图标、对齐、边框、反色
- 实时预览：改动后自动渲染当前草稿，不写入磁盘
- 保存 / 重新载入 / 导出 JSON / 导入 JSON
- 多页面：新增、删除、切换

深链接：`/admin?select=<region_id>` 可直接定位到某个模块。

## 开机自启与崩溃守护

```bash
bash deploy/install-launchd.sh     # 注册 launchd 用户服务
bash deploy/uninstall-launchd.sh   # 卸载
```

注册后：
- 登录 Mac 自动启动
- 进程崩溃/被误杀后自动拉起（`KeepAlive`）
- 日志写入 `logs/kindlehub.log` 与 `logs/kindlehub.err.log`

## 安全边界

| 端点 | 访问范围 | 说明 |
|---|---|---|
| `/kindle`、`/screen.png` | 局域网可读 | 只读显示内容，不含敏感数据 |
| `/api/state` | **仅本机回环** | 完整配置，可能含 Token，局域网返回 403 |
| `/health` | 局域网可读 | 存活探测 |

- 显示服务无认证，务必只在可信局域网使用
- 不要把 API Token、对话内容写入 region 的 `value` 字段
- 管理后台（规划中）默认只监听 `127.0.0.1`

## 目录

```
kindlehub/          产品代码（派生自上游，见 NOTICE）
docs/               验证报告与架构评估
experiments/        一次性验证脚本
artifacts/          渲染产物（不入库）
vendor/             上游克隆（不入库，commit 见 NOTICE）
```

## 测试

```bash
.venv/bin/pytest kindlehub -q
```

## 许可证

MIT。派生代码的原始版权见 [NOTICE](NOTICE)。
