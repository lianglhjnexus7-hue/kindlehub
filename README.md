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

## 两条接入路径

**主线不需要越狱。** 开箱即用、不碰设备，这是本项目的默认形态，也是下面文档通篇介绍的内容。

越狱直写是**可选的进阶模式**，只为解决一个浏览器方案无法解决的问题：**锁屏**。
需要长期把 Kindle 摆在桌上当信息屏、又不想每几分钟按一下唤醒时，才需要考虑它。

| | 浏览器模式（默认） | 越狱直写（可选） |
|---|---|---|
| 前提 | **无，开箱即用** | 需越狱（WinterBreak） |
| Kindle 做什么 | 实验性浏览器打开一个页面 | `wget` 拉图 + `eips` 写屏 |
| 会锁屏吗 | 会，无法阻止 | **不会** |
| 残影 | 明显（整页重载） | 极轻（`-w gc16` 指定波形） |
| 电量 | 系统状态栏 | 服务端自绘 10 格电量条 |
| 是否需要动设备 | 否 | 是（刷机有风险） |
| 适合 | 大多数人 | 长期固定摆放、不能忍锁屏 |

两种模式共用同一套服务端与渲染逻辑，切换只改 Kindle 侧怎么取图。

越狱路径的完整实机记录（含踩过的所有坑）见
[docs/kindle-kt2-notes.md](docs/kindle-kt2-notes.md)，**不看也能正常用浏览器模式**。

> 越狱有风险（变砖概率低但非零）。刷机前请备份 `documents/`，并确认能接受这个风险。

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

接线完成后第一眼看到的是这样（越狱直写模式，600×800 全屏）：

```text
┌────────────────────────────────────────────┐
│ 33℃ 晴         ▮▮▮▮▮▮▮▮▯▯ 85%            │ ← 状态栏：天气 + 电量
├────────────────────────────────────────────┤
│ AGENT 监控                                  │
│ ● DSH · 执行中                              │
│    myproject · 重构数据采集层                │
│ ○ Codex · 空闲 · quant · 20分钟前           │
│ ○ ZCode · 空闲 · other-project · 1天前      │
│                                             │
│ 模型消耗 · 按模型                            │
│   统计周期：今天                             │
│   deepseek-v4.1-flash                       │
│   3438 次 · 823.1M · ¥81.28                 │
│   step-5-preview                            │
│   528 次 · 173.0M · ¥168.12                 │
│   国内积分 16,890 / 22,637（剩 75%）· 今日 831│
│   国际积分 1,120 / 1,280（剩 87%）· 今日 0   │
│                                             │
│        更新 11:44 · 异常：OpenCode 状态为推测 │
└────────────────────────────────────────────┘
```

几个设计取舍：

- **状态栏自己画**。Kindle 系统状态栏会覆盖顶部 40px 且无法关闭，所以服务端把
  天气摘要 + 10 格电量画进图片，`eips -f` 全屏接管。内容全部靠左——系统栏的
  时间/电池在右上角，靠右放会被它重绘时盖住。
- **时间不重复**。系统状态栏已经显示时间，再画一遍没意义，那个位置让给天气。
- **Agent 是主角**。活跃的展开两行（项目 + 任务），空闲的压成一行，超过 3 个空闲折叠成
  「… 另有 A、B」。
- **不显示依据**。`[确证]` / 置信度这些留在 API 和后台，屏幕上只回答「谁在干活、在干什么」。
- **会话标题会过滤**。有些 Agent 的会话标题其实是用户输入的 prompt，这类一律不显示，
  避免把对话内容带上墙。

模型用量需要外部服务，默认在可选模块里：
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