from __future__ import annotations

import argparse
import json
from pathlib import Path

from .defaults import DEFAULT_DASHBOARD, build_k3b_dashboard
from .renderer import render_dashboard
from .sources import snapshot
from .store import DashboardStore
from .web import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="KindleHub — 面向 Kindle 第 7 代（KT2）的局域网只读电子墨水仪表盘")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve", help="serve the Kindle display page")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", default=8787, type=int)
    server.add_argument("--refresh", default=60, type=int)
    preview = sub.add_parser("preview", help="render a PNG preview")
    preview.add_argument("--output", default="preview.png")
    init_cmd = sub.add_parser("init", help="reset state to the example or preset dashboard")
    init_cmd.add_argument("--preset", choices=("default", "k3b", "workbench"), default="default")
    sub.add_parser("stats", help="print available live system sources")
    args = parser.parse_args()
    store = DashboardStore()
    if args.command == "serve":
        serve(args.host, args.port, args.refresh)
    elif args.command == "preview":
        path = Path(args.output).resolve()
        render_dashboard(store.read()).save(path, "PNG")
        print(path)
    elif args.command == "init":
        if args.preset in {"k3b", "workbench"}:
            base = store.read() if store.path.exists() else DEFAULT_DASHBOARD
            store.write(build_k3b_dashboard(base))
        else:
            store.write(DEFAULT_DASHBOARD)
        print(store.path)
    elif args.command == "stats":
        print(json.dumps(snapshot(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

