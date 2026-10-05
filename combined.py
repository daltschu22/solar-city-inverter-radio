#!/usr/bin/env python3
"""Optional launcher for a collector and dashboard in one container."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
GRACEFUL_TIMEOUT = 15


def supervise(services):
    children = []
    stopping = False

    def shutdown(_signum, _frame):
        nonlocal stopping
        stopping = True

    handlers = {signum: signal.signal(signum, shutdown)
                for signum in (signal.SIGTERM, signal.SIGINT)}
    try:
        for name, command, environment in services:
            children.append((name, subprocess.Popen(command, env=environment)))
        while not stopping:
            for name, process in children:
                code = process.poll()
                if code is not None:
                    print(f"{name} exited with code {code}; stopping combined service", flush=True)
                    return code if code > 0 else 1
            time.sleep(0.1)
        return 0
    finally:
        for _name, process in children:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + GRACEFUL_TIMEOUT
        for _name, process in children:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def main():
    try:
        api_port = int(os.environ.get("SOLAR_API_PORT", "8766"))
        dashboard_port = int(os.environ.get("PORT", "8765"))
        if not all(1 <= port <= 65535 for port in (api_port, dashboard_port)):
            raise ValueError()
        if api_port == dashboard_port:
            raise ValueError()
    except ValueError as exc:
        raise SystemExit("SOLAR_API_PORT and PORT must be distinct ports between 1 and 65535") from exc

    collector_env = {**os.environ, "SOLAR_API_BIND": "127.0.0.1"}
    dashboard_env = {**os.environ, "SOLAR_COLLECTOR_URL": f"http://127.0.0.1:{api_port}"}
    return supervise([
        ("collector", [sys.executable, str(ROOT / "collector.py")], collector_env),
        ("dashboard", [sys.executable, str(ROOT / "server.py")], dashboard_env),
    ])


if __name__ == "__main__":
    sys.exit(main())
