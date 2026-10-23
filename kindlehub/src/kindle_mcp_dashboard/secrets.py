"""本机敏感配置（密码、Token）。

与 dashboard.json 分离存放，原因：
- dashboard.json 会经由管理后台导出/导入、也会被 /api/state 返回
- 密码一旦混进去，导出分享布局时就会跟着泄露

文件权限固定 0600。路径可用 KINDLEHUB_SECRETS 覆盖。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def path() -> Path:
    """默认与布局配置同目录（项目内 .state/），保持"密钥单独一个文件"的分离。"""
    override = os.environ.get("KINDLEHUB_SECRETS")
    if override:
        return Path(override).expanduser()
    from .store import project_root
    return project_root() / ".state" / "secrets.json"


def load() -> dict:
    target = path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict) -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="secrets-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target


def get(key: str, default=None):
    return load().get(key, default)


def put(key: str, value) -> Path:
    data = load()
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    return save(data)
