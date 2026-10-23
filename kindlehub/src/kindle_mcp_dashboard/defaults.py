"""默认仪表盘与设备配置。

派生自 codecube/kindle-mcp-dashboard（MIT，见 NOTICE）。
相对上游的改动：
- 抽出 DEVICE_PROFILES，新增 KT2（Kindle 第 7 代，600x800 / 16 级灰）为默认设备
- 默认布局按 KT2 的 1000x1333 归一化画布重排（上游为 EY21 758x1024 设计）
- 文案改为中文，Agent 状态默认显示"未连接"而非伪造运行状态

默认布局刻意只放"任何人都有"的模块：时间、Agent 监控、数据健康。
天气与模型用量需要外部依赖（Open-Meteo / 自建网关），
做成可选模块放在 OPTIONAL_REGIONS，用户在后台一键添加。
这样别人 clone 下来第一眼看到的是完整可用的画面，而不是半屏"未设置"。
"""
from __future__ import annotations

CANVAS_WIDTH = 1000

DEVICE_PROFILES = {
    "kt2": {
        "model": "Amazon Kindle 7th Gen (KT2)",
        "width": 600,
        "height": 800,
        "grayscale": 16,
    },
    "ey21": {
        "model": "Amazon Kindle Paperwhite EY21",
        "width": 758,
        "height": 1024,
        "grayscale": 16,
    },
}

DEFAULT_DEVICE = "kt2"

# 可选模块：依赖外部服务，按需在后台添加。
# key 是数据源 id，value 是可直接粘进布局的 region 定义。
OPTIONAL_REGIONS = {
    "weather.current": {
        "id": "weather", "kind": "metric", "x": 20, "y": 20, "w": 580, "h": 160,
        "label": "天气", "icon": "weather", "source": "weather.current",
        "head_size": 40, "size": 17,
    },
    "workbuddy.models": {
        "id": "wb-models", "kind": "text", "x": 20, "y": 840, "w": 960, "h": 390,
        "label": "模型消耗 · 按模型", "source": "workbuddy.models", "size": 18,
    },
    "workbuddy.usage": {
        "id": "wb-usage", "kind": "metric", "x": 20, "y": 840, "w": 960, "h": 300,
        "label": "用量总量", "source": "workbuddy.usage", "head_size": 34, "size": 20,
    },
    "workbuddy.models_detail": {
        "id": "wb-detail", "kind": "text", "x": 20, "y": 840, "w": 960, "h": 390,
        "label": "模型消耗 · 详细", "source": "workbuddy.models_detail", "size": 16,
    },
}

DEFAULT_DASHBOARD = {
    "version": 1,
    # Kindle 重载周期。锁屏期间刷新会暂停，唤醒后看到的画面最多旧这么久，
    # 所以不宜设长；但整页重绘有残影，也不宜短于 30 秒。
    "refresh_seconds": 60,
    "settings": {
        # 天气城市。留空则显示"未设置城市"，绝不猜测位置。
        # 在管理后台顶部填写，支持中文城市名。
        "weather": {"city": ""},
        # 模型用量看板。不需要就保持 enabled=False，相关模块也不会显示。
        # 密码不放这里，见 secrets.json。
        "workbuddy": {"url": "http://127.0.0.1:8788", "enabled": False},
    },
    "device": dict(DEVICE_PROFILES[DEFAULT_DEVICE]),
    "active_screen": "overview",
    "screens": [
        {
            "id": "overview",
            "name": "总览",
            "regions": [
                # 时间与日期：任何人都有的信息
                {"id": "clock", "kind": "metric", "x": 20, "y": 20, "w": 960, "h": 200,
                 "label": "本地时间", "source": "system.clock", "head_size": 64, "size": 22},
                # Agent 是主角：活跃的显示项目+任务+依据，空闲的压成一行
                {"id": "agents", "kind": "text", "x": 20, "y": 250, "w": 960, "h": 800,
                 "label": "AGENT 监控", "source": "agents.summary", "size": 18},
                # 数据新鲜度与采集异常自证
                {"id": "health", "kind": "text", "x": 20, "y": 1120, "w": 960, "h": 70,
                 "label": "", "border": False, "source": "system.health", "size": 15,
                 "align": "center"},
            ],
        }
    ],
}