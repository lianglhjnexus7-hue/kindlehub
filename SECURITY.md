# 安全说明

## 网络暴露面

| 端点 | 本机 | 局域网 |
|---|---|---|
| `/kindle`、`/screen.png` | 可读 | **可读（无认证）** |
| `/admin`、`/static/*` | 可读可写 | **403** |
| `/api/state` | 可读可写 | **403** |
| `/api/preview`、`/api/secrets` | 可用 | **403** |

显示服务**没有认证**，任何能访问该端口的设备都能读到屏幕内容。
因此：

- 只应在可信局域网运行
- 不要把 API Token、对话内容、私人文件路径写进任何 region 的 `value` 字段
- 需要公网访问时，请自行在前面加反向代理 + 认证，**不要**直接暴露 8787

## 密钥存放

| 文件 | 内容 | 是否入库 |
|---|---|---|
| `.state/secrets.json` | 看板密码等，权限 0600 | **否**（.gitignore） |
| `.state/dashboard.json` | 布局配置，会被 `/api/state` 返回 | **否**（.gitignore） |
| `deploy/ai.kindlehub.dashboard.plist` | 由模板生成本机实例，含绝对路径 | **否**（.gitignore） |

**密钥绝不写进 dashboard.json。** 该文件会经管理后台导出、导入，
也会被 `/api/state` 返回，混入密钥就会跟着布局一起泄露。

## Agent 适配器的读取边界

所有适配器**只读**，且对可写数据库一律用 `mode=ro` 连接：

- 只取事件类型、工作目录、时间戳、标题
- 不读对话正文（`searchable_text`、`message`、`part` 表一律不碰）
- 不读账号身份（uid、昵称、凭证文件名）
- 不写任何文件，不改任何配置

## 上报前自查

```bash
# 确认没有密钥/路径/IP 残留在被跟踪的文件里
git ls-files | xargs grep -lEi "password|token|secret" 2>/dev/null
git ls-files "*.py" "*.md" "*.sh" | xargs grep -lE "/Users/[a-z]|/home/[a-z]" 2>/dev/null

# 确认运行时状态没被跟踪
git ls-files | grep -E "^\.state/|secrets\.json" && echo "有泄露" || echo "干净"
```

## 报告漏洞

这是个人项目，没有 SLO 承诺。发现问题请开 Issue，
涉及密钥的请不要在 Issue 里贴明文。