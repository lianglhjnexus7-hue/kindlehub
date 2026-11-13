# 跨网络同步（可选，默认关闭）

## 它解决什么

Mac 和 Kindle 不在同一个 Wi-Fi 时，Kindle 找不到 Mac 的 LAN 地址，
画面会停住。开启后：Mac 把每一帧推到一个**永不变化的公开 URL**，
Kindle 在 LAN 找不到时自动改去那里拿图。

```
1. LAN 直连（同网，60 秒实时，带电量）        ← 优先
2. 固定 URL 中继（跨网，可能有 5 分钟 CDN 延迟）← 兜底
3. 都失败 → 保留上一帧，屏幕不黑
```

## 为什么是 GitHub raw 二进制，而不是加密文本

- 加密方案在 Kindle 端跑不起来：gist 只支持文本，PNG 得 base64；
  KT2 的 busybox 没有 base64 解码，更没有 AES 解密工具。
- 仓库 raw 支持二进制：**Kindle 一行 curl 直接拿到 PNG，零解码**，
  现有 `eips` 直写链路一行不用改。
- 隐私靠随机长路径 + 只放渲染后的图片：画面不含会话正文、
  原始日志、主机名或凭据（服务端已有会话标题过滤）。

## 开启步骤

### 1. Mac 端（一次性）

确保 `gh auth login` 已登录（`gh auth status` 能看到账号）。
然后在 `.state/kt2.json` 的 `settings` 里加：

```json
{
  "relay": {
    "enabled": true,
    "repo": "<owner>/<repo>",
    "branch": "relay"
  }
}
```

**不需要也不应该手填 `path`**：首次启用时服务端自动生成一个随机
capability 路径（形如 `relay/3f9a…16位十六进制….png`）并写回配置。
固定可猜的路径（如 `relay/screen.png`）等于把画面公开给任何知道仓库名的人，
所以代码里没有默认路径，缺路径时直接拒绝发布。
生成后的路径可以在后台 `/api/meta` 的 `relay.path` 里查到。

`branch` 建议用专用的 `relay` 分支（默认值）：每 5 分钟一帧会淹没
main 的发布历史。服务端每 300 秒（对齐 GitHub raw 的 `max-age=300`）
把当前帧推上去。不开 `enabled` 时**完全不影响**现有链路。

### 2. Kindle 端

把上一步生成的随机路径填进 `/mnt/us/extensions/kindlehub/kindlehub.conf`：

```sh
RELAY_URL="https://raw.githubusercontent.com/<owner>/<repo>/relay/relay/<随机串>.png"
```

脚本 1.12.0 起支持；留空则不启用。Kindle 会通过自更新自动升上来。

## 已知限制（诚实说明）

| 项 | 说明 |
|---|---|
| 延迟 | GitHub raw 有 5 分钟 CDN 缓存，跨网时画面最多旧 5 分钟；LAN 仍 60 秒实时 |
| 电量 | 中继帧不含 Kindle 实时电量，状态栏用服务端渲染时的值 |
| 公开性 | URL 可猜到就能看；路径是自动生成的随机串，且只有渲染后的图 |
| 依赖 | Mac 需要能访问 GitHub API；Kindle 需要能访问 raw.githubusercontent.com |
| 频率 | 每 5 分钟一次 commit，仓库历史会增长；可定期清理或改用 gist |

## 回退

把 `settings.relay.enabled` 设为 false、Kindle 配置里 `RELAY_URL` 留空，
即完全回到原来的 LAN 直连。

## 实现位置

- `kindlehub/src/kindle_mcp_dashboard/gist_relay.py`：发布 / 取回
- `web.py` 的 `_publish_relay()`：接入 60 秒预热循环，带 300 秒节流
- `kindlehub/kindle/kt2/extensions/kindlehub/bin/kindlehub.sh`：
  `resolve_url()` 第 3 级兜底 + `curl` 拉取分支
