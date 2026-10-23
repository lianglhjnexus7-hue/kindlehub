# KindleHub

给老 Kindle 一块**常亮的局域网信息屏**：服务端渲染成灰度 PNG，Kindle 只用实验性浏览器打开一个页面。

不需要越狱、不装 App、不改固件、不碰 USB/KUAL/SSH。

```text
Mac（采集 + 渲染）                    Kindle 第 7 代（KT2）
┌──────────────────────┐             ┌────────────────────┐
│ Agent / 天气 / 用量   │  HTTP  PNG  │ 实验性浏览器        │
│ Pillow 渲染 600x800   │ ──────────► │ 只显示一张图        │
└──────────────────────┘             └────────────────────┘
```

## 为什么是服务端渲染

实测 Kindle 第 7 代实验性浏览器的 UA：

```
Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+
(KHTML, like Gecko) Version/5.0 Safari/533.2+ Kindle/3.0+
```

WebKit 531/533 ≈ Safari 5.0（2010 年）。因此：

- 不能用前端渲染、AJAX 轮询、复杂 CSS
- `object-fit`（需 Safari 10+）不可用
- JS 默认关闭（打开后可用作增强，见下文）
- **服务端出 PNG + 页面只放一张 `<img>`，是唯一稳定路线**

Kindle 只负责解码一张图，所有排版、字体、灰度量化都在 Mac 上完成。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e kindlehub

export KINDLE_DASHBOARD_STATE="$PWD/.state/dashboard.json"
export KINDLEHUB_SECRETS="$PWD/.state/secrets.json"

.venv/bin/kindlehub preview --output preview.png   # 本地出图
.venv/bin/kindlehub serve --host 0.0.0.0 --port 8787 --refresh 60
```

启动后日志会打印实际地址：

```
KindleHub 显示: http://192.168.x.x:8787/kindle
KindleHub 后台: http://127.0.0.1:8787/admin （仅本机）
```

Kindle 打开显示地址，管理后台打开 `http://127.0.0.1:8787/admin`。

**零配置即可运行**：状态与密钥默认存放在项目内 `.state/`，
首次启动自动生成，不需要设任何环境变量。

开机自启与崩溃守护：

```bash
bash deploy/install-launchd.sh      # 卸载：bash deploy/uninstall-launchd.sh
```

## 设备适配

内置两个 profile，默认 KT2：

| profile | 分辨率 | 灰度 |
|---|---|---|
| `kt2`（默认） | 600×800 | 16 级 |
| `ey21` | 758×1024 | 16 级 |

改 `kindlehub/src/kindle_mcp_dashboard/defaults.py` 里的 `DEFAULT_DEVICE` 即可。
布局用 1000 宽归一化画布，高度按设备长宽比推导，所以非方形屏幕不会被压扁。

## 第一屏是什么样

默认布局刻意只放**任何人都有**的模块，所以 clone 下来第一眼就是完整可用的画面：

```
本地时间
11:44

AGENT 监控
● Codex · 执行中 · 最近活动 11:44（刚刚）
   项目 quant
   [确证] 任务开始事件未配对结束事件，且事件仍在写入
● DSH · 执行中 · 最近活动 11:42（1分钟前）
○ ZCode · 空闲 · stock-news-collector · 最近活动 17:36（1天前）
○ OpenCode · 空闲 · myproject · 最近活动 15:09（7天前）

更新 11:44 · 异常：天气无数据；OpenCode 状态为推测
```

天气与模型用量需要外部服务，做成**可选模块**：
在管理后台左侧「可选模块」区一键添加，不用手写 JSON。

## 数据源

| 源 | 依赖 | 说明 |
|---|---|---|
| 时间 / 日期 | 无 | 本机 |
| 天气 | Open-Meteo | 免费、无需注册、无需 Key。城市在后台设置 |
| WorkBuddy 用量 | 自建 wb-proxy | 见下方"接入自己的服务" |
| Agent 监控 | 各 Agent 的本地文件 | 见下方"适配新 Agent" |

天气与用量都走**后台预热**，渲染路径只读缓存、绝不联网——
e-ink 客户端等不起 DNS/TLS 握手。

## Agent 监控

```
● DSH · 执行中 · 最近活动 11:05（刚刚）
   项目 kindle dashboard
   [确证] turn 开始事件未配对结束事件，且事件仍在写入
○ Codex · 空闲 · quant · 最近活动 13:13
```

状态分六级，每条都带**依据**与**置信度**：

| 状态 | 判断依据 | 置信度 |
|---|---|---|
| `waiting` | 检测到 `permission/request` 等授权事件 | 高 |
| `running` | 开始事件未配对结束事件，且仍在写入 | 高 |
| `idle` | 收到结束事件，或状态接口报告 completed | 高 |
| `error` | 结束事件带 error | 高 |
| `unknown` | 无边界事件可依 | 低 |
| `disconnected` | 采集器不可达 | — |

两条刻意的设计：

1. **"没有结束事件"不能单独证明还在跑**。Codex 会话被中断时不补写
   `task_complete`，所以还要看事件是否仍在写入。
2. **读不到就标 unknown，绝不编**。OpenCode 没有 turn 事件，只能靠最后写入时间
   推测，于是一律标低置信度，屏幕上直接写"推测"。

### 适配一个新 Agent

引擎（`agents.py`）不认识任何具体 Agent。写一个适配器文件放到 `plugins/`：

```python
from ..agents import AgentState, _project_name, _short
from . import register

def state() -> AgentState:
    out = AgentState(agent="MyTool", source="session_event")
    # 1. 找到能证明"正在执行"的生命周期事件
    # 2. 找到项目路径
    # 3. 找不到就返回 unknown，别猜
    return out

register("MyTool", state)
```

30 行左右。没装对应软件的机器上那条直接缺席，其余功能照常。
参考 `plugins/codex.py`（JSONL 事件流）或 `plugins/zcode.py`（SQLite 状态表）。

内置四个：`codex` / `dsh` / `zcode` / `opencode`。
不想用哪个，把对应文件删掉即可。

## 接入自己的服务

WorkBuddy 适配器读的是一个自建网关。改成你的服务只需两处：

```python
# workbuddy.py
DEFAULT_URL = "http://127.0.0.1:8788"   # 你的服务地址
```

密码不放代码、不放布局配置，单独存 `secrets.json`（权限 0600）：

```python
from kindle_mcp_dashboard import secrets
secrets.put("workbuddy_password", "你的密码")
```

**布局配置（dashboard.json）会被导出、也会被 /api/state 返回，所以密钥绝不放那里。**

## 安全边界

| 端点 | 本机 | 局域网 |
|---|---|---|
| `/kindle`、`/screen.png` | ✅ | ✅ 只读 |
| `/admin`、`/static/*`、`/api/*` | ✅ | **403** |

- 显示服务无认证，只应在可信局域网使用
- 不要把 API Token、对话内容写进 region 的 `value`
- 适配器只读聚合字段，不读对话正文、不碰账号身份

## Kindle 适配要点

| 项 | 处理 |
|---|---|
| 视口 | 实测 600×800（设备上报自动获取） |
| 刷新 | JS 关闭时 `meta refresh`；JS 打开时局部刷图 + 唤醒即刷新 |
| 残影 | JS 可用时只换 `img.src`，边框标题不重绘 |
| 字体 | macOS 中文链（Hiragino / STHeiti / Songti），Linux 回退 DejaVu |
| 灰度 | 16 级，关闭抖动 |
| 字号 | 区域放不下时自动收缩，不会整块消失 |

## 测试

```bash
.venv/bin/pytest kindlehub -q
```

50 项，其中 Agent 相关重点守"不伪造状态"这条底线。

## 目录

```
kindlehub/src/kindle_mcp_dashboard/
├── agents.py          引擎：状态机、排序、格式化（不认识具体 Agent）
├── plugins/           适配器：codex / dsh / zcode / opencode
├── renderer.py        Pillow 渲染：字体链、等比映射、CJK 折行、16 级灰
├── sources.py         数据源汇总
├── weather.py         Open-Meteo
├── workbuddy.py       自建网关用量
├── secrets.py         密钥（0600，不进布局配置）
├── web.py             只读显示 + 本机后台
└── static/            管理后台（原生 JS，无构建）
```

## 许可证

MIT。派生自 [codecube/kindle-mcp-dashboard](https://github.com/codecube/kindle-mcp-dashboard)，
管理后台交互参考 [zihengjackchen/kindle-dashboard](https://github.com/zihengjackchen/kindle-dashboard)。
二者均为 MIT，详见 [NOTICE](NOTICE)。