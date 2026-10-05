import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import combined


WORKER = '''
import os, signal, sys, time
from pathlib import Path
def stop(signum, frame):
    Path(sys.argv[2]).write_text('terminated')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop if sys.argv[3] == 'responsive' else signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
while True:
    time.sleep(0.05)
'''


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def start(self, services, timeout=2):
        code = f"import combined,sys; combined.GRACEFUL_TIMEOUT={timeout!r}; sys.exit(combined.supervise({services!r}))"
        process = subprocess.Popen([sys.executable, "-c", code],
                                   cwd=Path(__file__).resolve().parents[1],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)

        def cleanup():
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(cleanup)
        return process

    def worker(self, name, responsive=True):
        ready, stopped = self.root / f"{name}.ready", self.root / f"{name}.stopped"
        return (name, [sys.executable, "-c", WORKER, str(ready), str(stopped),
                       "responsive" if responsive else "unresponsive"], None), ready, stopped

    def wait_ready(self, paths, process):
        deadline = time.monotonic() + 5
        while not all(path.exists() and path.read_text() for path in paths):
            self.assertIsNone(process.poll(), "Supervisor stopped before the test workers started")
            if time.monotonic() >= deadline:
                self.fail("Test workers did not start")
            time.sleep(0.02)

    def assert_reaped(self, ready):
        with self.assertRaises(ProcessLookupError):
            os.kill(int(ready.read_text()), 0)

    @unittest.skipUnless(os.name == "posix", "Container process supervision uses POSIX signals")
    def test_peer_exit_stops_and_reaps_other_process(self):
        for code in (0, 7):
            with self.subTest(exit_code=code):
                worker, ready, stopped = self.worker(f"peer-{code}")
                failure = "import sys,time; from pathlib import Path; p=Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(0.02)\nsys.exit(int(sys.argv[2]))"
                process = self.start([worker, ("exiting", [sys.executable, "-c", failure,
                                                          str(ready), str(code)], None)])
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, code or 1, stderr)
                self.assertIn(f"exiting exited with code {code}", stdout)
                self.assertEqual(stopped.read_text(), "terminated")
                self.assert_reaped(ready)

    @unittest.skipUnless(os.name == "posix", "Container process supervision uses POSIX signals")
    def test_sigterm_stops_and_reaps_both_children(self):
        first, ready1, stopped1 = self.worker("first")
        second, ready2, stopped2 = self.worker("second")
        process = self.start([first, second])
        self.wait_ready([ready1, ready2], process)
        process.terminate()
        _stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, stderr)
        for ready, stopped in ((ready1, stopped1), (ready2, stopped2)):
            self.assertEqual(stopped.read_text(), "terminated")
            self.assert_reaped(ready)

    @unittest.skipUnless(os.name == "posix", "Container process supervision uses POSIX signals")
    def test_shutdown_kills_and_reaps_an_unresponsive_child(self):
        worker, ready, stopped = self.worker("unresponsive", responsive=False)
        process = self.start([worker], timeout=0.2)
        self.wait_ready([ready], process)
        process.terminate()
        _stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertFalse(stopped.exists())
        self.assert_reaped(ready)

    def test_main_connects_dashboard_to_local_collector_and_passes_radio_environment(self):
        settings = {"SOLAR_API_PORT": "9876", "PORT": "9875", "SOLAR_API_BIND": "0.0.0.0",
                    "SOLAR_COLLECTOR_URL": "http://other.example.invalid:8766",
                    "SOLAR_RADIO_HOST": "radio.example.invalid"}
        with patch.dict(os.environ, settings, clear=True), patch("combined.supervise", return_value=7) as supervise:
            self.assertEqual(combined.main(), 7)
        collector, dashboard = supervise.call_args.args[0]
        self.assertEqual(Path(collector[1][1]).name, "collector.py")
        self.assertEqual(Path(dashboard[1][1]).name, "server.py")
        self.assertEqual(collector[2]["SOLAR_API_BIND"], "127.0.0.1")
        self.assertEqual(collector[2]["SOLAR_RADIO_HOST"], "radio.example.invalid")
        self.assertEqual(dashboard[2]["SOLAR_COLLECTOR_URL"], "http://127.0.0.1:9876")
        self.assertEqual(dashboard[2]["PORT"], "9875")

    def test_invalid_or_colliding_ports_fail_before_starting_services(self):
        for api, dashboard in (("8765", "8765"), ("0", "8765"), ("8766", "65536"), ("bad", "8765")):
            with self.subTest(api=api, dashboard=dashboard), patch.dict(os.environ, {"SOLAR_API_PORT": api, "PORT": dashboard}, clear=True):
                with patch("combined.supervise") as supervise, self.assertRaises(SystemExit):
                    combined.main()
                supervise.assert_not_called()
