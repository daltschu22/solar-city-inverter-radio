from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from collector.daylight import NightSchedule
from collector.history import SolarHistoryStore
from collector.smlight_collector import CHANNEL, QUERY_CYCLE, SmlightCollector
from collector.transition_capture import TransitionCapture, TRANSITION_CYCLE, near_transition
from tools.summarize_transitions import summarize
from test_coordinator import device_announce, leave_notification
from test_smlight_poll import power_response
from test_inverter_details import verified_registers


class TransitionCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_disabled_and_expired_recorders_do_not_create_files_or_resume_after_restart(self):
        for until in (None, 100):
            with patch("collector.transition_capture.time.time", return_value=101):
                for _ in range(2):
                    recorder = TransitionCapture(self.root, until)
                    recorder.record("heartbeat")
                    self.assertFalse(recorder.snapshot()["active"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_expiry_stops_writing_but_retains_evidence(self):
        recorder = TransitionCapture(self.root, 101)
        with patch("collector.transition_capture.time.time", return_value=100):
            recorder.record("heartbeat")
        before = recorder.path.read_bytes()
        with patch("collector.transition_capture.time.time", return_value=101):
            recorder.record("heartbeat")
            self.assertFalse(recorder.snapshot()["active"])
        self.assertEqual(recorder.path.read_bytes(), before)
        self.assertEqual(recorder.snapshot()["records"], 1)

    def test_rotation_is_bounded_and_files_are_private(self):
        recorder = TransitionCapture(self.root, time.time() + 60, max_bytes=200, backups=2)
        for index in range(30):
            recorder.record("heartbeat", sequence=index)
        files = list(self.root.iterdir())
        self.assertEqual(len(files), 3)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 200)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for line in path.read_text().splitlines():
                self.assertEqual(json.loads(line)["schema"], 1)
        self.assertEqual(json.loads(recorder.path.read_text().splitlines()[-1])["sequence"], 29)

    def test_storage_failure_disables_capture_without_throwing(self):
        recorder = TransitionCapture(self.root, time.time() + 60)
        with patch("collector.transition_capture.os.open", side_effect=OSError("disk full")) as opening:
            recorder.record("heartbeat")
            recorder.record("heartbeat")
        opening.assert_called_once()
        self.assertFalse(recorder.snapshot()["active"])
        self.assertIn("OSError", recorder.snapshot()["error"])
        self.assertEqual(recorder.snapshot()["records"], 0)

    def test_symlink_is_not_followed(self):
        target = self.root / "important"
        target.write_text("preserve")
        recorder = TransitionCapture(self.root, time.time() + 60)
        recorder.path.symlink_to(target)
        recorder.record("heartbeat")
        self.assertFalse(recorder.snapshot()["active"])
        self.assertEqual(target.read_text(), "preserve")

    def test_transition_windows_cover_dawn_dusk_and_eastern_longitudes(self):
        for site in ((40.71, -74.01), (-36.85, 174.76)):
            schedule = NightSchedule(*site)
            day = datetime(2026, 10, 6, tzinfo=timezone.utc).date()
            for instant in schedule.events(day):
                for seconds, expected in ((-5400, True), (0, True), (5400, True), (5401, False)):
                    with self.subTest(site=site, instant=instant, seconds=seconds):
                        self.assertEqual(near_transition(schedule, instant + seconds), expected)
        now = datetime(2026, 6, 21, tzinfo=timezone.utc).timestamp()
        self.assertFalse(near_transition(None, now))
        self.assertFalse(near_transition(NightSchedule(89, 0), now))


class RecordedCollectorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = SolarHistoryStore(Path(directory.name) / "history.sqlite3")
        self.collector = SmlightCollector("radio.test", self.history,
                                         transition_capture_until=time.time() + 3600)

    def events(self):
        return [json.loads(line) for line in self.collector.capture.path.read_text().splitlines()]

    def frame(self, sequence=1):
        frame = power_response()
        frame.update(observed_at=time.time(), timestamp=sequence, capture_sweep=42,
                     aps_counter=sequence, channel=CHANNEL)
        return frame

    def test_cycle_switches_preserve_power_and_energy_slots_at_every_boundary(self):
        self.assertEqual(len(QUERY_CYCLE), len(TRANSITION_CYCLE))
        for boundary in range(len(QUERY_CYCLE)):
            for old, new in ((QUERY_CYCLE, TRANSITION_CYCLE), (TRANSITION_CYCLE, QUERY_CYCLE)):
                cycle = old + old[:boundary] + new[boundary:] + new
                self.assertTrue(all(kind == "power" for kind in cycle[::2]))
                energies = [i for i, kind in enumerate(cycle) if kind == "energy"]
                self.assertLessEqual(max(b - a for a, b in zip(energies, energies[1:])), 8)
        self.assertGreater(TRANSITION_CYCLE.count("inverter_dc"), QUERY_CYCLE.count("inverter_dc"))

    def test_priority_ends_at_expiry_or_recording_failure_and_needs_coordinates(self):
        now = time.time()
        self.assertEqual(self.collector.query_cycle(now), QUERY_CYCLE)
        self.collector.night_schedule = Mock()
        self.collector.night_schedule.events.return_value = (now, now + 43200)
        self.assertEqual(self.collector.query_cycle(now), TRANSITION_CYCLE)
        self.assertEqual(self.collector.query_cycle(self.collector.capture.until), QUERY_CYCLE)
        self.collector.capture.error = "disk full"
        self.assertEqual(self.collector.query_cycle(now), QUERY_CYCLE)

    def test_records_unsolicited_input_and_only_validated_measurements(self):
        frame = self.frame()
        self.collector.capture_frame(frame, None)
        self.assertIsNone(self.history.latest())
        other = {**frame, "source_pan_id": "0x5678", "destination_pan_id": "0x5678",
                 "network_source_ieee": "0200000000000033", "network_destination_ieee": "0200000000000044"}
        self.collector.capture_frame(other, None)
        self.collector.capture_frame({**frame, "channel": CHANNEL + 1}, None)
        self.assertEqual(len(self.events()), 1)
        self.assertTrue(self.collector.accept_response(frame, "power"))
        self.assertFalse(self.collector.accept_response(frame, "power"))
        self.assertEqual([event["event"] for event in self.events()], ["rx", "response"])
        self.assertEqual(self.history.query("all")["sample_count"], 1)

    def test_recording_error_does_not_block_radio_transmission_or_measurements(self):
        session = Mock()
        session.transmit.return_value = 0
        with patch("collector.transition_capture.os.open", side_effect=OSError("disk full")):
            self.assertEqual(self.collector.transmit(session, b"\x01\x02\x03\x04", "query", "power"), 0)
        session.transmit.assert_called_once()
        self.assertTrue(self.collector.accept_response(self.frame(), "power"))
        self.assertEqual(self.history.query("all")["sample_count"], 1)

    def test_session_records_timeouts_leave_and_health_without_extra_queries(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock = [0]
        start = time.time()
        events = iter([(31, device_announce()), (37, None), (38, leave_notification()), (140, None), (141, None)])

        def receive():
            clock[0], frame = next(events)
            if frame:
                frame.update(observed_at=start + clock[0], rssi=-70, channel=CHANNEL)
            if clock[0] == 141:
                self.collector.stop_event.set()
            return frame

        session.receive.side_effect = receive
        with patch("collector.smlight_collector.time.monotonic", side_effect=lambda: clock[0]), \
                patch("collector.smlight_collector.time.time", side_effect=lambda: start + clock[0]):
            self.collector.run_session(session)
        capture = self.events()
        self.assertEqual(sum(item["event"] == "tx" and item["purpose"] == "query" for item in capture), 1)
        self.assertEqual(sum(item["event"] == "query_timeout" for item in capture), 1)
        self.assertTrue(any(item["event"] == "network_event" and item["kind"] == "left" for item in capture))
        self.assertTrue(any(item["event"] == "bridge_health" and item["ok"] for item in capture))
        self.assertIsNone(self.history.latest())

    def test_transition_session_keeps_the_configured_query_interval(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock, index = [0], [0]
        start = time.time()

        def receive():
            clock[0] = 31 + index[0] * self.collector.interval
            frame = self.frame(index[0])
            frame["observed_at"] = start + clock[0]
            index[0] += 1
            if index[0] == 17:
                self.collector.stop_event.set()
            return frame

        session.receive.side_effect = receive
        with patch("collector.smlight_collector.time.monotonic", side_effect=lambda: clock[0]), \
                patch("collector.smlight_collector.time.time", side_effect=lambda: start + clock[0]), \
                patch("collector.smlight_collector.near_transition", return_value=True):
            self.collector.run_session(session)
        queries = [item for item in self.events() if item["event"] == "tx" and item["purpose"] == "query"]
        self.assertEqual([item["kind"] for item in queries], list(TRANSITION_CYCLE) + ["power"])
        self.assertTrue(all(b["at"] - a["at"] == 60 for a, b in zip(queries, queries[1:])))

    def test_summary_separates_query_delivery_from_network_traffic_and_reply_timeouts(self):
        # The same final radio status can precede a valid response or a timeout.
        # Retain both observations rather than treating delivery status as a reading.
        with patch("collector.transition_capture.time.time", return_value=100):
            capture = self.collector.capture
            capture.record("tx", purpose="network_reply", kind=None, raw="")
            capture.record("tx_result", purpose="network_reply", kind=None, status=17)
            capture.record("tx", purpose="query", kind="power", raw="")
            capture.record("tx_result", purpose="query", kind="power", status=17)
            capture.record("query_timeout", kind="power", tx_status=17)
            capture.record("tx", purpose="query", kind="inverter_dc", raw="")
            capture.record("tx_result", purpose="query", kind="inverter_dc", status=0)
        with patch("collector.transition_capture.time.time", return_value=3700):
            capture.record("query_timeout", kind="inverter_dc", tx_status=0)
        report = summarize([capture.path])
        self.assertEqual(report["query_outcomes_by_kind"], {
            "power": {"queries": 1, "tx_status_17": 1, "timeout_tx_status_17": 1},
            "inverter_dc": {"queries": 1, "tx_status_0": 1, "timeout_tx_status_0": 1},
        })
        hours = list(report["query_outcomes_by_hour_utc"].values())
        self.assertEqual(hours[0]["queries"], 2)
        self.assertEqual(hours[0]["tx_status_17"], 1)
        self.assertEqual(hours[1], {"timeout_tx_status_0": 1})

    def test_summary_reports_states_gaps_and_unknown_frames_without_identities(self):
        first = self.frame()
        self.collector.capture_frame(first, "power")
        self.collector.accept_response(first, "power")
        later = self.frame(2)
        later["observed_at"] += 600
        self.collector.capture_frame(later, "power")
        self.collector.accept_response(later, "power")
        registers = verified_registers()
        for i, kind, start, end in ((3, "inverter_ac", 40069, 40096), (4, "inverter_dc", 40096, 40121)):
            with patch("collector.smlight_collector.response_values", return_value={
                    "registers": {k: registers[k] for k in range(start, end)}}):
                self.collector.accept_response(self.frame(i), kind)
        unknown = {**self.frame(5), "application_payload": "f400010101"}
        self.collector.capture_frame(unknown, None)
        with self.collector.capture.path.open("a") as output:
            output.write('{"schema":1,')
        report = summarize([self.collector.capture.path])
        self.assertEqual(report["invalid_or_incomplete_lines"], 1)
        self.assertEqual(report["unaccepted_application_frames"], {"startup_hello": 1})
        self.assertEqual(sum(item["event"] == "startup_hello" for item in report["timeline"]), 1)
        self.assertEqual(len(report["power_gaps_over_180_seconds"]), 1)
        self.assertAlmostEqual(report["power_gaps_over_180_seconds"][0]["seconds"], 600, places=1)
        states = [item for item in report["timeline"] if item["event"] == "operating_state"]
        self.assertEqual(states[0]["state"], "MPPT")
        text = json.dumps(report)
        self.assertNotIn(first["network_source_ieee"], text)
        self.assertNotIn(first["raw"], text)
        self.assertEqual(self.history.query("all")["sample_count"], 2)
