import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from collector import api as collector
from collector.history import SolarHistoryStore
from dashboard import server


def request(httpd, path, method="GET"):
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=3)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class DashboardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = SolarHistoryStore(Path(directory.name) / "history.sqlite3")
        self.history.record([{"capture_id": "saved", "observed_at": 100, "capture_sweep": 1,
                             "radio_timestamp": 1, "solar_w": 500.0, "lifetime_wh": 1000}])
        self.radio = Mock(interval=60)
        self.radio.snapshot.return_value = {"state": "live", "requests": 7, "responses": 7}
        for name, value in (("solar_history", self.history), ("smlight_collector", self.radio)):
            patcher = patch.object(collector, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.backend = self.start_http(collector.Handler)
        self.dashboard = self.start_http(server.Handler)
        self.dashboard.collector_url = f"http://127.0.0.1:{self.backend.server_port}"

    def start_http(self, handler):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        return httpd

    def test_dashboard_serves_assets_and_proxies_collector_without_polling(self):
        status, body = request(self.dashboard, "/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", body)
        self.assertEqual(request(self.dashboard, "/app.js")[0], 200)
        for _ in range(3):
            status, body = request(self.dashboard, "/api/live")
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual(payload["solar_w"], 500)
            self.assertEqual(payload["collector"]["requests"], 7)
            self.assertEqual(payload["solar"]["lifetime_observed_at"], 100)
        status, body = request(self.dashboard, "/api/history?range=all")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sample_count"], 1)
        self.assertEqual(request(self.dashboard, "/api/history?range=invalid")[0], 400)
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(set(call[0] for call in self.radio.method_calls), {"snapshot"})

    def test_collector_outage_and_recovery_do_not_stop_dashboard(self):
        self.backend.shutdown()
        self.backend.server_close()
        self.assertEqual(request(self.dashboard, "/api/live")[0], 502)
        self.assertEqual(request(self.dashboard, "/")[0], 200)
        self.assertEqual(request(self.dashboard, "/healthz")[0], 200)
        resumed = self.start_http(collector.Handler)
        self.dashboard.collector_url = f"http://127.0.0.1:{resumed.server_port}"
        self.assertEqual(request(self.dashboard, "/api/live")[0], 200)

    def test_stopping_dashboard_leaves_collector_available(self):
        self.dashboard.shutdown()
        self.dashboard.server_close()
        self.assertEqual(request(self.backend, "/api/live")[0], 200)
        self.radio.stop.assert_not_called()

    def test_proxy_only_allows_reading_documented_endpoints(self):
        self.assertEqual(request(self.dashboard, "/api/radio")[0], 404)
        self.assertEqual(request(self.dashboard, "/api/live", method="POST")[0], 501)
        self.radio.snapshot.assert_not_called()

    def test_invalid_upstream_json_becomes_unavailable(self):
        response = Mock(status=200)
        response.read.return_value = b"not JSON"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("dashboard.server.urllib.request.urlopen", return_value=response):
            status, body = request(self.dashboard, "/api/live")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body), {"error": "Collector API unavailable"})

    def test_dashboard_starts_with_only_standard_library_and_invalid_radio_config(self):
        code = '''
import sys
from unittest.mock import Mock, patch
from dashboard import server
httpd = Mock()
with patch('dashboard.server.ThreadingHTTPServer', return_value=httpd), patch('dashboard.server.signal.signal'):
    server.main()
httpd.serve_forever.assert_called_once()
httpd.server_close.assert_called_once()
assert not {'config', 'collector', 'history', 'smlight_collector', 'spinel', 'radio_protocol'} & sys.modules.keys()
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.local.json"
            path.write_text("invalid JSON")
            subprocess.run([sys.executable, "-S", "-c", code], check=True,
                           cwd=Path(__file__).resolve().parents[1],
                           env={**os.environ, "SOLAR_CONFIG": str(path),
                                "SOLAR_COLLECTOR_URL": "http://127.0.0.1:8766"})

    def test_upstream_url_validation(self):
        self.assertEqual(server.collector_url("http://collector.example.invalid:8766/"),
                         "http://collector.example.invalid:8766")
        for value in ("file:///tmp/anything", "http://", "http://user:pass@example.invalid",
                      "http://example.invalid/api/live", "http://example.invalid?query=1",
                      "http://example.invalid:bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.collector_url(value)
