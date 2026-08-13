#!/usr/bin/env python3
"""Cross-platform application launcher for Cell Tracking Studio."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

from celltrack import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "workspace" / "logs"
LOG_PATH = LOG_DIR / "launcher.log"
DEFAULT_MODEL_PATH = (
    PROJECT_ROOT / "models" / "segmentation" / "yolo11x-seg.pt"
)
HOST = "127.0.0.1"
PORTS = range(8000, 8011)
HEALTH_RESPONSE = {
    "status": "ok",
    "app": "cell-tracking-studio",
    "version": __version__,
}


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("celltrack.launcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(file_handler)
    return logger


LOGGER = configure_logging()


def health_payload(port: int, timeout: float = 0.8) -> dict[str, Any] | None:
    try:
        with urlopen(f"http://{HOST}:{port}/api/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def is_celltrack_service(port: int) -> bool:
    return health_payload(port) == HEALTH_RESPONSE


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def select_port() -> tuple[int, bool]:
    for port in PORTS:
        if is_celltrack_service(port):
            return port, True
        if port_is_available(port):
            return port, False
    raise RuntimeError(
        "All ports from 8000 through 8010 are in use. "
        "Close some applications and try again."
    )


def wait_until_healthy(
    port: int,
    process: subprocess.Popen[str],
    timeout: float = 60.0,
    interval: float = 0.5,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_celltrack_service(port):
            return True
        time.sleep(interval)
    return False


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            LOGGER.error("Server process did not exit after terminate and kill")


def model_path() -> Path:
    configured = os.environ.get("CELLTRACK_WEIGHTS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_MODEL_PATH


def validate_model() -> None:
    path = model_path()
    if not path.is_file():
        if os.environ.get("CELLTRACK_WEIGHTS_PATH"):
            raise RuntimeError(f"Missing custom cell segmentation model: {path}")
        raise RuntimeError(
            "Missing cell segmentation model: models/segmentation/yolo11x-seg.pt\n"
            "Place the model supplied by the laboratory at the path above and "
            "restart the application."
        )


def self_check() -> int:
    import fastapi  # noqa: F401
    import matplotlib  # noqa: F401
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    import scipy  # noqa: F401
    import ultralytics  # noqa: F401
    import uvicorn  # noqa: F401
    import celltrack  # noqa: F401

    print("Self-check passed: Python and application dependencies are available.")
    return 0


def run_application(*, no_browser: bool = False) -> int:
    validate_model()
    port, already_running = select_port()
    url = f"http://{HOST}:{port}"

    if already_running:
        print(f"Cell Tracking Studio is already running: {url}")
        LOGGER.info("Using existing Cell Tracking Studio service at %s", url)
        if not no_browser:
            webbrowser.open(url)
        return 0

    server_log_path = LOG_DIR / "server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        str(PROJECT_ROOT / "run.py"),
        "--host",
        HOST,
        "--port",
        str(port),
        "--no-browser",
    ]
    LOGGER.info("Starting server: %r", command)
    process_options: dict[str, Any] = {
        "cwd": PROJECT_ROOT,
        "stdout": server_log,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_options["start_new_session"] = True
    process = subprocess.Popen(command, **process_options)

    def handle_signal(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s", signum)
        if process.poll() is None:
            process.terminate()

    previous_handlers: dict[int, Any] = {}
    handled_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        handled_signals.append(signal.SIGHUP)
    for signum in handled_signals:
        previous_handlers[signum] = signal.signal(signum, handle_signal)

    try:
        if not wait_until_healthy(port, process):
            stop_process(process)
            raise RuntimeError(
                "The server did not start within 60 seconds.\n"
                f"Detailed log: {server_log_path}"
            )

        print(f"Cell Tracking Studio: {url}")
        print("Close this terminal window to stop the application.")
        LOGGER.info("Server is healthy at %s", url)
        if not no_browser:
            webbrowser.open(url)
        exit_code = process.wait()
        return 0 if exit_code < 0 else exit_code
    finally:
        stop_process(process)
        server_log.close()
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Validate imports without starting the web server.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the default browser.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return self_check() if args.self_check else run_application(
            no_browser=args.no_browser
        )
    except Exception as exc:
        LOGGER.exception("Launcher failed")
        print()
        print(f"Startup failed: {exc}")
        print(f"Detailed log: {LOG_PATH}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
