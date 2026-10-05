import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import collector


class CollectorApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = collector.SolarHistoryStore(Path(self.directory.name) / "history.sqlite3")
        self.collector = Mock()
        self.collector.interval = 60
        self.collector.snapshot.return_value = {"state": "waiting", "host": "radio.test"}
        self.history_patch = patch.object(collector, "solar_history", self.history)
        self.collector_patch = patch.object(collector, "smlight_collector", self.collector)
        self.history_patch.start()
        self.collector_patch.start()
        self.addCleanup(self.history_patch.stop)
        self.addCleanup(self.collector_patch.stop)
        self.httpd = collector.ThreadingHTTPServer(("127.0.0.1", 0), collector.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=2)
        try:
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_removed_ingestion_endpoint_cannot_store_data(self):
        self.assertEqual(self.request("GET", "/api/radio")[0], 404)
        self.assertEqual(self.request("POST", "/api/radio", b'{"solar_w":9999}')[0], 501)
        self.assertIsNone(self.history.latest())

    def test_live_and_history_use_only_smlight_and_sqlite(self):
        self.history.record([{
            "capture_id": "saved", "observed_at": 100, "capture_sweep": 1,
            "radio_timestamp": 1, "solar_w": 500.0, "lifetime_wh": 1000,
        }])
        status, body = self.request("GET", "/api/live")
        self.assertEqual(status, 200)
        live = json.loads(body)
        self.assertEqual(live["mode"], "smlight")
        self.assertEqual(live["solar_w"], 500.0)
        self.assertEqual(live["collector"]["host"], "radio.test")
        status, body = self.request("GET", "/api/history?range=all")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sample_count"], 1)
        self.assertEqual(self.request("GET", "/api/history?range=invalid")[0], 400)
        self.assertEqual(self.request("GET", "/healthz")[0], 200)

    def test_startup_has_no_legacy_or_sample_fallback(self):
        with patch.object(collector, "smlight_collector", None):
            self.assertEqual(self.request("GET", "/api/live")[0], 503)
        with patch("config.require_configured", side_effect=ValueError("Create radio.local.json")):
            with self.assertRaisesRegex(SystemExit, "radio.local.json"):
                collector.main()

    def test_history_includes_configured_poll_interval_for_chart_gaps(self):
        for interval in (15, 60):
            with self.subTest(interval=interval):
                self.collector.interval = interval
                status, body = self.request("GET", "/api/history?range=1h")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["poll_interval_seconds"], interval)

    def test_collector_api_never_serves_dashboard_or_local_files(self):
        for path in ("/", "/index.html", "/app.js", "/radio.local.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_cached_energy_keeps_its_own_timestamp_when_power_updates(self):
        self.history.record([
            {"capture_id": "energy", "observed_at": 100, "capture_sweep": 1,
             "radio_timestamp": 1, "solar_w": 500, "lifetime_wh": 1000},
            {"capture_id": "power", "observed_at": 1000, "capture_sweep": 1,
             "radio_timestamp": 2, "solar_w": 600, "lifetime_wh": None},
        ])
        _, body = self.request("GET", "/api/live")
        payload = json.loads(body)
        self.assertEqual(payload["timestamp"], 1000)
        self.assertEqual(payload["solar"], {"lifetime_wh": 1000, "lifetime_observed_at": 100})

    def test_importing_collector_and_discovery_does_not_load_dashboard(self):
        import os
        import subprocess
        import sys
        subprocess.run([sys.executable, "-c", "import sys; import collector, tools.discover_radio, smlight_collector; assert 'server' not in sys.modules"],
                       check=True, cwd=Path(__file__).resolve().parents[1],
                       env={**os.environ, "SOLAR_CONFIG": str(Path(__file__).with_name("config.synthetic.json"))})


if __name__ == "__main__":
    unittest.main()
