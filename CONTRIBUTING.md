# 参与贡献

## 最欢迎的贡献

1. **新的 Agent 适配器**。README 里有 30 行模板，写一个文件放进 `plugins/` 即可。
   前提是必须遵守"不伪造状态"：找不到可靠证据就返回 `unknown`。
2. **新设备适配**。除 KT2 / EY21 外的 Kindle 型号，需要实测视口与灰度。
3. **Bug 修复**，尤其是渲染、折行、灰度量化相关。
4. **文档改进**，特别是你在真实设备上遇到的情况。

## 开发环境

```bash
python3 -m venv .venv
.venv/bin/pip install -e kindlehub
.venv/bin/pip install pytest
.venv/bin/pytest kindlehub -q
```

## 提交前检查

```bash
.venv/bin/pytest kindlehub -q          # 测试必须全绿

# 确认没有敏感信息（CI 也会查）
git ls-files | xargs grep -lEi "password|secret|api[_-]?key" 2>/dev/null
git ls-files | xargs grep -lE "/Users/|/home/" 2>/dev/null
```

## 代码约定

- 适配器只读，可写数据库一律 `mode=ro` 连接
- 不读对话正文、不读账号身份
- 新增数据源必须走后台预热，渲染路径不联网
- 页面不得依赖 `object-fit` 等 WebKit 533 不支持的特性
- 状态必须带 `confidence` 与 `basis`，让使用者能判断要不要信

## 提交信息

 Conventional Commits：`feat:` / `fix:` / `refactor:` / `docs:` / `test:`