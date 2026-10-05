#!/usr/bin/env python3
"""Run the collector, optionally starting the dashboard alongside it."""

import os
import sys


def main():
    dashboard = os.environ.get("SOLAR_DASHBOARD", "false").strip().lower()
    if dashboard in {"true", "1"}:
        from .combined import main as combined_main
        return combined_main()
    if dashboard not in {"false", "0"}:
        raise SystemExit("SOLAR_DASHBOARD must be true or false (or 1 or 0)")

    # Replace the launcher so the collector receives container signals directly.
    os.execv(sys.executable, [sys.executable, "-m", "collector"])


if __name__ == "__main__":
    sys.exit(main())
