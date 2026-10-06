import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from collector import api as collector


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

    def test_undocumented_endpoints_cannot_store_data(self):
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

    def test_night_metadata_does_not_create_measurements_or_energy(self):
        from collector.daylight import NightSchedule
        from datetime import datetime
        now = datetime.fromisoformat("2026-10-03T03:00:00-04:00").timestamp()
        self.history.record([{
            "capture_id": "evening", "observed_at": now - 8 * 3600,
            "capture_sweep": 1, "radio_timestamp": 1, "solar_w": -12,
            "lifetime_wh": 123456,
        }])
        with patch.object(collector, "night_schedule", NightSchedule(40.71, -74.01)), \
                patch("collector.history.time.time", return_value=now):
            _, body = self.request("GET", "/api/history?range=1h")
        result = json.loads(body)
        self.assertEqual(result["night_intervals"], [{"start": now - 3600, "end": now}])
        self.assertEqual(result["points"], [])
        self.assertEqual(result["sample_count"], 0)
        self.assertIsNone(result["generated_wh"])
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(self.history.latest()["solar_w"], -12)

    def test_startup_requires_configured_radio(self):
        with patch.object(collector, "smlight_collector", None):
            self.assertEqual(self.request("GET", "/api/live")[0], 503)
        with patch("collector.config.require_configured", side_effect=ValueError("Set radio environment variables")):
            with self.assertRaisesRegex(SystemExit, "radio environment variables"):
                collector.main()

    def test_history_includes_configured_poll_interval_for_chart_gaps(self):
        for interval in (15, 60, 300):
            with self.subTest(interval=interval):
                self.collector.interval = interval
                status, body = self.request("GET", "/api/history?range=1h")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["poll_interval_seconds"], interval)

    def test_startup_passes_configured_timing_to_radio_collector(self):
        from collector.config import RadioConfig
        configuration = RadioConfig(host="radio.example.invalid", configured=True,
                                    poll_interval_seconds=300, reconnect_interval_seconds=45)
        with patch("collector.config.require_configured", return_value=configuration), \
                patch.object(collector, "SOLAR_HISTORY_PATH", self.history.path), \
                patch.object(collector, "ThreadingHTTPServer"), \
                patch.object(collector.signal, "signal"), \
                patch("collector.smlight_collector.SmlightCollector") as radio:
            collector.main()
        self.assertEqual(radio.call_args.kwargs["interval"], 300)
        self.assertEqual(radio.call_args.kwargs["reconnect_interval"], 45)
        radio.return_value.start.assert_called_once()
        radio.return_value.stop.assert_called_once()

    def test_collector_api_never_serves_dashboard_or_local_files(self):
        for path in ("/", "/index.html", "/app.js", "/.env"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_passive_startup_never_constructs_replacement_collector(self):
        from collector.config import RadioConfig
        configuration = RadioConfig(host="radio.example.invalid", configured=True,
                                    mode="passive", passive_stale_seconds=600)
        with patch("collector.config.require_configured", return_value=configuration), \
                patch.object(collector, "SOLAR_HISTORY_PATH", self.history.path), \
                patch.object(collector, "ThreadingHTTPServer"), \
                patch.object(collector.signal, "signal"), \
                patch("collector.smlight_collector.SmlightCollector") as replacement, \
                patch("collector.passive.PassiveCollector") as passive:
            collector.main()
        replacement.assert_not_called()
        self.assertEqual(passive.call_args.kwargs["stale_after"], 600)
        passive.return_value.start.assert_called_once()
        passive.return_value.stop.assert_called_once()

    def test_passive_history_has_freshness_threshold_without_polling_interval(self):
        self.collector.interval = None
        self.collector.stale_after = 600
        _, body = self.request("GET", "/api/history?range=1h")
        result = json.loads(body)
        self.assertIsNone(result["poll_interval_seconds"])
        self.assertEqual(result["reading_stale_after_seconds"], 600)

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
        subprocess.run([sys.executable, "-c", "import sys; import collector.api, tools.discover_radio, collector.smlight_collector; assert 'dashboard' not in sys.modules"],
                       check=True, cwd=Path(__file__).resolve().parents[1],
                       env=os.environ.copy())

    def test_default_database_is_under_repository_data(self):
        import os
        import subprocess
        import sys
        environment = {key: value for key, value in os.environ.items() if key != "SOLAR_HISTORY_PATH"}
        root = Path(__file__).resolve().parents[1]
        code = "from collector.api import SOLAR_HISTORY_PATH; print(SOLAR_HISTORY_PATH)"
        result = subprocess.run([sys.executable, "-c", code], check=True, cwd=root,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(Path(result.stdout.strip()), root / "data" / "solar-history.sqlite3")


if __name__ == "__main__":
    unittest.main()
