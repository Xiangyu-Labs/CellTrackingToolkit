#!/usr/bin/env python3
"""Start Cell Tracking Studio on the local computer."""

import argparse
from pathlib import Path
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

import uvicorn


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args(argv)


def open_browser_when_ready(host: str, port: int) -> None:
    health_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    health_url = f"http://{health_host}:{port}/api/health"
    for _ in range(120):
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(f"http://{health_host}:{port}")
                    return
        except (URLError, TimeoutError, OSError):
            time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if not args.no_browser:
        threading.Thread(
            target=open_browser_when_ready,
            args=(args.host, args.port),
            daemon=True,
        ).start()
    uvicorn.run(
        "celltrack.web.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
