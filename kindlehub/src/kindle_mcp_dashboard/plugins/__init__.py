"""Agent 适配器注册表。

引擎（状态机、排序、格式化）在 agents.py，具体适配器在 plugins/ 下。
二者通过本注册表解耦：
- 引擎不认识任何具体 Agent，换机器时缺哪个适配器就少哪一行，不会报错
- 新增适配器只需放一个文件并调 register()，不用改引擎
- 用户可以删掉不想用的适配器文件，其余功能照常
"""
from __future__ import annotations

from typing import Any, Callable

Adapter = Callable[[], Any]

_adapters: dict[str, Adapter] = {}


def register(name: str, factory: Adapter) -> None:
    """注册一个适配器。name 是显示名，factory 是无参函数，返回 AgentState。"""
    _adapters[name] = factory


def names() -> list[str]:
    return sorted(_adapters)


def collect_all() -> list[Any]:
    """依次调用所有已注册适配器，单个失败不影响其余。"""
    from ..agents import AgentState
    rows = []
    for name in sorted(_adapters):
        try:
            rows.append(_adapters[name]())
        except Exception:
            # 适配器自身炸了也要给出一行，而不是让整块监控消失
            rows.append(AgentState(agent=name))
    return rows


def load_builtin() -> list[str]:
    """导入内置适配器包，返回成功加载的名字。

    每个适配器单独 try：缺文件、缺依赖、路径不存在都不影响其他适配器。
    """
    loaded: list[str] = []
    modules = (
        ("Codex", "codex"),
        ("DSH", "dsh"),
        ("ZCode", "zcode"),
        ("OpenCode", "opencode"),
    )
    for name, module in modules:
        try:
            __import__(f"{__name__}.{module}", fromlist=["register"])
            loaded.append(name)
        except Exception:
            # 没装对应软件、或文件被删掉，静默跳过
            pass
    return loaded