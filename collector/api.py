#!/usr/bin/env python3
"""Standalone radio collector, SQLite storage and read-only JSON API."""

import json
import os
from pathlib import Path
import signal
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collector.history import SolarHistoryStore

ROOT = Path(__file__).resolve().parents[1]
SOLAR_HISTORY_PATH = Path(os.environ.get("SOLAR_HISTORY_PATH", ROOT / "data" / "solar-history.sqlite3"))


solar_history = None
smlight_collector = None


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        if not any(
            header.lower().startswith(b"cache-control:")
            for header in self._headers_buffer
        ):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/healthz":
            return self.send_json(200, {"ok": True})

        if parsed.path == "/api/live":
            if smlight_collector is None or solar_history is None:
                return self.send_json(503, {"error": "Solar poller is starting"})
            return self.send_json(200, smlight_payload())

        if parsed.path == "/api/history":
            if solar_history is None:
                return self.send_json(503, {"error": "History store is starting"})
            range_name = urllib.parse.parse_qs(parsed.query).get("range", ["24h"])[0]
            try:
                history = solar_history.query(range_name)
                if smlight_collector is not None:
                    history["poll_interval_seconds"] = smlight_collector.interval
                return self.send_json(200, history)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})

        if parsed.path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown endpoint"})

        return self.send_json(404, {"error": "Unknown endpoint"})


def smlight_payload():
    status = smlight_collector.snapshot()
    latest = solar_history.latest()
    energy = solar_history.latest_energy_reading()
    return {
        "mode": "smlight", "timestamp": latest["observed_at"] if latest else None,
        "source": "SMLIGHT / SunSpec Modbus over XBee",
        "solar_w": latest["solar_w"] if latest else None,
        "solar": {
            "lifetime_wh": energy["lifetime_wh"] if energy else None,
            "lifetime_observed_at": energy["observed_at"] if energy else None,
        },
        "collector": status,
    }


def secure_runtime_file_permissions():
    paths = [SOLAR_HISTORY_PATH]
    paths.extend(
        SOLAR_HISTORY_PATH.parent.glob(f"{SOLAR_HISTORY_PATH.name}-*")
    )
    for path in paths:
        try:
            path.chmod(0o640)
        except FileNotFoundError:
            continue


def main():
    global solar_history, smlight_collector
    from collector.config import require_configured
    try:
        configuration = require_configured()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    host = configuration.host
    latitude, longitude = os.environ.get("SOLAR_LATITUDE"), os.environ.get("SOLAR_LONGITUDE")
    night_schedule = None
    if latitude is not None or longitude is not None:
        from collector.daylight import NightSchedule
        if latitude is None or longitude is None:
            raise SystemExit("Set both SOLAR_LATITUDE and SOLAR_LONGITUDE")
        night_schedule = NightSchedule(float(latitude), float(longitude))
    os.umask(0o027)
    solar_history = SolarHistoryStore(SOLAR_HISTORY_PATH)
    secure_runtime_file_permissions()
    port = int(os.environ.get("SOLAR_API_PORT", "8766"))
    server = ThreadingHTTPServer((os.environ.get("SOLAR_API_BIND", "127.0.0.1"), port), Handler)
    server.daemon_threads = True
    shutdown_started = threading.Event()

    def request_shutdown(_signum, _frame):
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    print(f"SolarCity collector API: port {port}; GET /api/live or /api/history")
    print(f"Solar history: {SOLAR_HISTORY_PATH}")
    from collector.smlight_collector import SmlightCollector
    smlight_collector = SmlightCollector(host, solar_history, port=configuration.port,
                                       interval=configuration.poll_interval_seconds,
                                       night_schedule=night_schedule,
                                       reconnect_interval=configuration.reconnect_interval_seconds)
    smlight_collector.start()
    print(f"Radio poller: SMLIGHT at {host}:{configuration.port}")
    try:
        server.serve_forever()
    finally:
        smlight_collector.stop()
        server.server_close()


if __name__ == "__main__":
    main()
