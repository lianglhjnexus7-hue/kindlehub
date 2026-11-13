# 变更与发布准则（所有 Agent 必读）

> **硬性要求：任何 Agent 对这个项目的更新或优化，都必须 commit，并同步到 GitHub。**
> 不允许只改工作目录就结束。

## 为什么

本项目有两个仓库：

| | 位置 | 作用 |
|---|---|---|
| 工作目录 | 仓库根（本目录） | 日常开发、本地运行、含个人配置 |
| 发布仓库 | `.release/` | 独立的 git 仓库，只放脱敏后的公开内容 |

两者**不会自动同步**。过去多次出现"本地改了一堆，GitHub 还停在几周前"的情况，
所以把同步定为每次变更的收尾动作。

## 每次变更的收尾流程

```bash
# 1) 提交到工作目录（必须）
git add -A
git commit -m "feat/fix/docs: 说明改了什么"

# 2) 跑测试（必须）
export KINDLE_DASHBOARD_STATE="$PWD/.state/kt2.json"
export KINDLEHUB_SECRETS="$PWD/.state/secrets.json"
.venv/bin/pytest kindlehub -q

# 3) 同步到 GitHub（必须）
bash scripts/publish-to-github.sh
```

第 3 步的脚本会：导出受控文件 → 发布前隐私扫描 → 提交到 `.release` → push → 打 tag → 创建 Release。

## 隐私红线（发布前必查）

发布仓库是 **PUBLIC**。以下内容绝不入库：

- `/Users/<name>`、任何本机绝对路径
- 真实内网 IP（`192.168.x.x`、`10.x.x.x` 等具体值；示例用 `192.168.1.100`）
- 邮箱、GitHub token / API key / 密码
- `.state/`（含 `secrets.json`）、`vendor/`、`artifacts/`、`internal/`
- `kindlehub-dist/relay.conf`（本机中继开关，含真实仓库 URL）
- Kindle 备份（`kindlehub/.backup/`，含用户购买的电子书）

一句话：**只发布代码、文档和 `.example` 配置；不发布任何运行时的真实值。**

脚本会自动扫描这些模式，命中就中止。若确有必要保留某个疑似项，先人工确认再改脚本白名单。

## 版本号约定

- Kindle 端脚本：`kindlehub-dist/kindlehub.sh` 里的 `VERSION`，改动后必须递增，
  并同步 `kindlehub-dist/version.txt`（两者一致，否则 Kindle 会无限重复下载）
- 屏幕页脚会显示设备上真正跑的版本（`v1.12.2` 这种），用来确认自更新成功
- 正式发布：`v1.x.0` tag + GitHub Release

## 分支

- `main`：正式发布历史，只放版本发布
- `relay`：跨网络中继的帧文件，每 5 分钟自动 commit，**不属于发布内容**，
  不要把它合并回 main

## 检查清单（结束前逐项确认）

- [ ] 改动已 `git commit`（工作目录无遗留的功能性改动）
- [ ] `pytest` 通过
- [ ] 隐私扫描通过，`.release` 里没有真实路径 / IP / 密钥 / relay.conf
- [ ] 已 `git push origin main`
- [ ] 若 Kindle 端脚本有变：`VERSION` 与 `version.txt` 一致
- [ ] 若是正式版本：tag + Release 已创建

## 给后续 Agent 的一句话

**改完不算完，推上去才算完。** 用 `bash scripts/publish-to-github.sh` 收尾。
