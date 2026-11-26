# 更新记录

## 2026-11-26 — 接入工作台 v2 契约与 K3-B 重墨双层框副屏

**执行 Agent**：Antigravity
**模型**：Orchestrator (Carbon)
**进度**：5/5
**改了什么**：代码提交 d04e4f8 接入本机工作台 `/api/kindle-summary?v=2` 白名单契约并实现 K3-B 黑条双层框重墨版全屏渲染（右上角 210px 留白避让系统栏、按激活屏幕按需采集并保留轻量积分池拼装），同时修复发布脚本扫描范围、`relay.conf` 暴露面、越狱直写模式 `kindle_seen` 遥测与 Antigravity T3 路径扫描。
**验证**：在 `main` 执行 `.venv/bin/pytest kindlehub -q` 共 234 项测试全部通过，本地隐私扫描零命中。
**没做完**：无
**下一步**：无
**部署/发布**：代码已合入本地 `main`；公开镜像通过 `scripts/publish-to-github.sh` 同步推送至 `.release/` 远端 `main`；本机 `:8787` 服务已在 `main` 重启生效并持续向 `relay` 推送 K3-B 帧（Kindle 端脚本仍为 `v1.13.1` 无需更新，设备下轮拉图自动刷新画面）。

## 2026-11-22 — 渲染 Workbench Kindle 摘要

**执行 Agent**：未记录
**模型**：未记录
**改了什么**：本地开发提交 3f690d2 增加 Workbench Kindle 摘要渲染；改动涉及 kindlehub/src/kindle_mcp_dashboard/workbench.py 和 kindlehub/tests/test_workbench.py。
**验证**：测试文件有对应改动；是否执行及结果未记录。
**没做完**：本地 .release/ 镜像 HEAD 仍是 2026-11-17 的 4f4f9cc；此后是否同步未见本地证据。
**下一步**：按 [CONTRIBUTING-AGENTS](CONTRIBUTING-AGENTS.md) 核对测试、隐私扫描与镜像同步，并分别记录结果。
**部署/发布**：开发仓库提交于 11-22；本地发布镜像及 Kindle 实机均未见此改动的部署证据，远端最新状态未刷新核对。
