#!/usr/bin/env python3
"""Optional dashboard. Reads the collector API; never opens a radio or database."""

import json
import os
from pathlib import Path
import signal
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.parse
import urllib.request


STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_COLLECTOR_URL = "http://127.0.0.1:8766"


def collector_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("SOLAR_COLLECTOR_URL must be an http(s) origin, without credentials or a path")
    parsed.port  # Validate the optional port before starting the server.
    return value.rstrip("/")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy(self, path, query):
        url = self.server.collector_url + path
        if query:
            url += "?" + query
        try:
            try:
                upstream = urllib.request.urlopen(url, timeout=5)
            except urllib.error.HTTPError as exc:
                upstream = exc
            with upstream:
                body = upstream.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError("Collector response too large")
                payload = json.loads(body)
                status = upstream.status
        except (OSError, ValueError, urllib.error.URLError):
            return self.send_json(502, {"error": "Collector API unavailable"})
        return self.send_json(status, payload)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/healthz":
            return self.send_json(200, {"ok": True, "service": "dashboard"})
        if parsed.path in {"/api/live", "/api/history"}:
            return self.proxy(parsed.path, parsed.query)
        if parsed.path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown endpoint"})
        return super().do_GET()


def main():
    try:
        upstream = collector_url(os.environ.get("SOLAR_COLLECTOR_URL", DEFAULT_COLLECTOR_URL))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    bind = os.environ.get("SOLAR_BIND", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    server.collector_url = upstream
    shutdown_started = threading.Event()

    def request_shutdown(_signum, _frame):
        if not shutdown_started.is_set():
            shutdown_started.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    print(f"SolarCity dashboard: http://{bind}:{port}")
    print(f"Collector API: {upstream}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
