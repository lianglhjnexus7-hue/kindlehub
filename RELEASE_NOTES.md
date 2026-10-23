# KindleHub v1.0.0

> 给老 Kindle 一块常亮的局域网信息屏：服务端渲染成灰度 PNG，Kindle 只用实验性浏览器打开一个页面。
> 不需要越狱、不装 App、不改固件、不碰 USB/KUAL/SSH。

面向 **Kindle 第 7 代（KT2，600×800，16 级灰）**，同时保留 Paperwhite EY21 profile。

---

## 为什么做了这个项目

老 Kindle 的性能跑不动现代网页，但它的 e-ink 屏幕恰好适合做一块
「不刺眼、超低功耗、常亮」的信息屏。问题是官方生态不给这条路。

实测 Kindle 第 7 代实验性浏览器的 UA：

```
Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+
(KHTML, like Gecko) Version/5.0 Safari/533.2+ Kindle/3.0+
```

WebKit 531/533 ≈ Safari 5.0（2010 年），JavaScript 默认关闭。

所以这个项目做了一个核心决定：**所有渲染都在 Mac 上完成，Kindle 只解码一张 PNG**。
这一举消掉全部前端兼容问题——JS 关闭、`object-fit` 不支持、横屏锁定、
SPA 打不开，都跟渲染无关。

---

## 功能

### 多 Agent 活动监控（本项目的主要价值）

```
● DSH · 执行中 · 最近活动 11:44（刚刚）
   项目 kindle dashboard
   [确证] turn 开始事件未配对结束事件，且事件仍在写入
○ Codex · 空闲 · quant · 最近活动 13:13
```

内置四个适配器，均**只读**，不修改任何数据库或配置：

| Agent | 数据来源 | 状态依据 |
|---|---|---|
| Codex | `~/.codex/sessions/**.jsonl` | `task_started` 未配对 `task_complete` |
| DSH | `~/.dsh/sessions/**/session.v4.jsonl.zstd` | `turn/start` 未配对 `turn/end` |
| ZCode | `~/.zcode/v2/tasks-index.sqlite` | `tasks.task_status` 字段 |
| OpenCode | `~/.local/share/opencode/opencode.db` | `session.time_updated`（仅推测） |

**每条状态都带依据与置信度**，屏幕上直接显示 `[确证]` 或 `[推测]`。
两条刻意的设计：

1. **"没有结束事件"不能单独证明还在跑**。Codex 会话被中断时不补写
   `task_complete`，所以还要看事件是否仍在写入。
2. **读不到就标 unknown，绝不编**。OpenCode 没有 turn 事件，只能靠最后
   写入时间推测，于是一律标低置信度。

状态分六级：`waiting` / `running` / `idle` / `error` / `unknown` / `disconnected`。

### 其他模块

- **时间 / 日期** — 零依赖，任何人都有
- **天气** — Open-Meteo，免费、无需注册、无需 Key
- **模型用量** — 调用次数、Token、费用、积分余额（按国内/国际分区）
- **数据健康摘要** — 自证新鲜度，采集异常直接写出来

天气与用量依赖外部服务，做成**可选模块**，后台一键添加。

### 管理后台

`http://127.0.0.1:8787/admin`，**仅本机可访问**（局域网 403）。

- 拖拽调整模块位置与大小
- 增删模块、改标题与内容、绑定数据源
- 实时预览（改完即渲染，不写入磁盘）
- 保存 / 重新载入 / 导出 / 导入 JSON
- 多页面切换
- 设置刷新间隔与天气城市

原生 JS，无前端框架、无构建步骤。

---

## Kindle 适配要点

全部来自真机实测，不是文档推测：

| 项 | 结论 |
|---|---|
视口 | 600×800，与屏幕物理宽度一致，**无缩放** |
`object-fit` | 不支持（需 Safari 10+） |
JavaScript | 默认关闭；打开后升级为局部刷图 + 唤醒即刷新 |
横屏 | 浏览器锁死竖屏 |
`.local` 主机名 | 不支持（不解析 mDNS） |
屏保 | 官方固件无法关闭，唤醒后浏览器仍在原页面 |

**刷新策略**：Mac 端 25 秒采一次，与 Kindle 刷新周期解耦。
JS 可用时只替换 `img.src`，边框标题不重绘——这是减轻 e-ink 残影的关键。

字体用 macOS 中文链（Hiragino / STHeiti / Songti），Linux 回退 DejaVu。
注意 Pillow 的 `load_default()` **无中文字形**，必须显式指定 truetype 路径。

---

## 安全边界

| 端点 | 本机 | 局域网 |
|---|---|---|
| `/kindle`、`/screen.png` | ✅ | ✅ 只读 |
| `/admin`、`/api/*` | ✅ | **403** |

- 密钥存 `.state/secrets.json`（0600），**不进入布局配置**——
  因为布局会被导出、也会被 `/api/state` 返回
- 适配器只读聚合字段，不读对话正文、不碰账号身份
- CI 内置敏感信息扫描（密码/路径/IP/MAC/手机号）

---

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -e kindlehub

.venv/bin/kindlehub serve --host 0.0.0.0 --port 8787 --refresh 60
```

Kindle 打开日志里打印的地址即可。**零配置可跑**，
状态与密钥自动存放在项目内 `.state/`。

开机自启与崩溃守护：

```bash
bash deploy/install-launchd.sh
```

---

## 适配一个新 Agent

引擎不认识任何具体 Agent。写一个文件放进 `plugins/`：

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

---

## 测试

```bash
.venv/bin/pytest kindlehub -q
```

50 项通过，其中 Agent 相关重点守「不伪造状态」这条底线。

---

## 致谢

- [codecube/kindle-mcp-dashboard](https://github.com/codecube/kindle-mcp-dashboard) — 架构基础（MIT）
- [zihengjackchen/kindle-dashboard](https://github.com/zihengjackchen/kindle-dashboard) — 管理后台交互参考（MIT）

详见 [NOTICE](NOTICE)。

## 许可证

[MIT](LICENSE)