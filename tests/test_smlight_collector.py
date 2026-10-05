import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import collector
from history import SolarHistoryStore
from radio_protocol import decode_ieee802154_frame
from smlight_collector import CHANNEL, COLLECTOR, QUERY_CYCLE, RadioSession, SmlightCollector
from test_inverter_details import verified_registers
from test_smlight_poll import power_response
from test_coordinator import (association_request, data_request, device_announce, leave_notification,
                              request as verification_request)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = SolarHistoryStore(Path(self.directory.name) / "history.sqlite3")
        self.collector = SmlightCollector("radio.test", self.history)

    def test_radio_pending_bit_uses_confirmed_insert_remove_without_table_reads(self):
        from spinel.const import SPINEL
        from tools.smlight_poll import INVERTER

        session = RadioSession.__new__(RadioSession)
        session.constants = SPINEL
        session.pending_inverter = False
        session.api = Mock()
        address = bytes.fromhex(INVERTER)
        session.api.prop_insert_value.return_value = address
        session.api.prop_remove_value.return_value = address
        session.set_pending_inverter(True)
        session.set_pending_inverter(True)
        session.api.prop_insert_value.assert_called_once_with(SPINEL.PROP_MAC_SRC_MATCH_EXTENDED_ADDRESSES, address, "8s")
        session.set_pending_inverter(False)
        session.api.prop_remove_value.assert_called_once_with(SPINEL.PROP_MAC_SRC_MATCH_EXTENDED_ADDRESSES, address, "8s")
        session.api.prop_get_value.assert_not_called()
        session.api.prop_insert_value.return_value = None
        with self.assertRaisesRegex(ConnectionError, "pending-bit"):
            session.set_pending_inverter(True)
        self.assertFalse(session.pending_inverter)

    def test_join_flow_sets_pending_before_poll_reply_and_waits_for_announced_identity(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock = [0]
        events = iter([(31, association_request()), (32, data_request()), (92, device_announce()), (93, None)])

        def receive():
            clock[0], frame = next(events)
            if frame:
                frame.update(observed_at=time.time(), rssi=-70)
            else:
                self.collector.stop_event.set()
            return frame

        session.receive.side_effect = receive
        with patch("smlight_collector.time.monotonic", side_effect=lambda: clock[0]):
            self.collector.run_session(session)
        calls = session.method_calls
        pending = next(i for i, call in enumerate(calls) if call[0] == "set_pending_inverter" and call.args == (True,))
        response = next(i for i, call in enumerate(calls)
                        if call[0] == "transmit" and call.args[0][0] & 7 == 3)
        clear = next(i for i, call in enumerate(calls) if call[0] == "set_pending_inverter" and call.args == (False,))
        self.assertLess(pending, response)
        self.assertLess(response, clear)
        self.assertEqual(self.collector.snapshot()["requests"], 1)
        self.assertEqual(self.collector.snapshot()["responses"], 0)
        self.assertIsNone(self.history.latest())

    def test_radio_retries_unicast_but_not_broadcast_and_preserves_failure(self):
        from spinel.const import SPINEL

        session = RadioSession.__new__(RadioSession)
        session.constants = SPINEL
        session.api = Mock()
        session.stream = Mock()
        session.stream.closed.wait.return_value = False
        session.api.queue_wait_for_prop.return_value = Mock(value=17)
        for frame, retries in ((b"\x61\x88\x01\x00\x00", 3),
                               (b"\x41\x88\x01\x00\x00", 0)):
            session.api.reset_mock()
            session.stream.closed.wait.reset_mock()
            self.assertEqual(session.transmit(frame), 17)
            self.assertEqual(session.api.prop_change_async.call_count, retries + 1)
            self.assertEqual(session.stream.closed.wait.call_count, retries)
            payloads = [call.args[2] for call in session.api.prop_change_async.call_args_list]
            self.assertTrue(all(payload == payloads[0] for payload in payloads))
            self.assertEqual(payloads[0][2:-4], frame)
            self.assertEqual(payloads[0][-4:], bytes([CHANNEL, 4, 0, 1]))

    def test_radio_retry_stops_on_success_and_never_retries_unknown_delivery(self):
        from spinel.const import SPINEL

        session = RadioSession.__new__(RadioSession)
        session.constants = SPINEL
        session.api = Mock()
        session.stream = Mock()
        session.stream.closed.wait.return_value = False
        frame = b"\x61\x88\x01\x00\x00"
        for statuses, result, attempts in (([17, 0], 0, 2), ([18, 0], 0, 2), ([4], 4, 1)):
            session.api.reset_mock()
            session.api.queue_wait_for_prop.side_effect = [Mock(value=s) for s in statuses]
            self.assertEqual(session.transmit(frame), result)
            self.assertEqual(session.api.prop_change_async.call_count, attempts)
        session.api.reset_mock()
        session.api.queue_wait_for_prop.side_effect = [None]
        with self.assertRaisesRegex(ConnectionError, "transmit timed out"):
            session.transmit(frame)
        self.assertEqual(session.api.prop_change_async.call_count, 1)

    def frame(self, sequence=1, observed_at=None):
        frame = power_response()
        frame.update(capture_sweep=42, timestamp=sequence, aps_counter=sequence,
                     observed_at=time.time() if observed_at is None else observed_at)
        return frame

    def test_real_response_persists_once_with_full_precision(self):
        frame = self.frame()
        self.assertTrue(self.collector.accept_response(frame, "power"))
        self.assertFalse(self.collector.accept_response(frame, "power"))
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(self.history.latest()["solar_w"], 500.0)
        self.assertIsNone(self.history.latest_energy())
        self.assertEqual(self.collector.snapshot()["responses"], 1)

    def test_bad_response_never_writes_zero(self):
        frame = self.frame()
        frame["application_payload"] = "01030a1388138880008000ffffad80"
        self.assertFalse(self.collector.accept_response(frame, "power"))
        self.assertIsNone(self.history.latest())

    def test_diagnostics_do_not_create_production_or_energy_samples(self):
        registers = verified_registers()
        for index, kind, start, end in ((1, "inverter_ac", 40069, 40096),
                                       (2, "inverter_dc", 40096, 40121)):
            values = {"registers": {k: registers[k] for k in range(start, end)}}
            with patch("smlight_collector.response_values", return_value=values):
                self.assertTrue(self.collector.accept_response(self.frame(index), kind))
        self.assertIsNone(self.history.latest())
        self.assertIsNone(self.collector.energy)
        snapshot = self.collector.snapshot()["telemetry"]
        self.assertEqual(snapshot["inverter_dc"]["values"]["cabinet_c"], 40.0)

    def test_query_cycle_keeps_power_frequent_and_covers_diagnostics(self):
        self.assertTrue(all(kind == "power" for kind in QUERY_CYCLE[::2]))
        energy = [i for i, kind in enumerate(QUERY_CYCLE * 2) if kind == "energy"]
        self.assertLessEqual(max(b - a for a, b in zip(energy, energy[1:])) * self.collector.interval, 480)
        self.assertTrue({"inverter_ac", "inverter_dc", "meter_ac", "common1", "common2"} <= set(QUERY_CYCLE))

    def test_energy_only_packet_does_not_create_power_reading(self):
        frame = self.frame()
        with patch("smlight_collector.response_values", return_value={"lifetime_wh": 1000}):
            self.assertTrue(self.collector.accept_response(frame, "energy"))
        self.assertIsNone(self.history.latest())
        self.collector.accept_response(self.frame(2, frame["observed_at"] + 15), "power")
        self.assertEqual(self.history.latest()["lifetime_wh"], 1000)
        self.collector.accept_response(self.frame(3, frame["observed_at"] + 130), "power")
        self.assertIsNone(self.history.latest()["lifetime_wh"])
        self.assertEqual(self.history.latest_energy(), 1000)

    def test_stale_status_retains_last_verified_power(self):
        self.collector.accept_response(self.frame(observed_at=time.time() - 120), "power")
        self.assertEqual(self.collector.snapshot()["state"], "live")
        self.collector.accept_response(self.frame(2, observed_at=time.time() - 181), "power")
        self.assertEqual(self.collector.snapshot()["state"], "stale")
        with patch.object(collector, "solar_history", self.history), patch.object(
            collector, "smlight_collector", self.collector
        ):
            payload = collector.smlight_payload()
        self.assertEqual(payload["solar_w"], 500.0)
        self.assertEqual(payload["collector"]["state"], "stale")

    def test_empty_history_has_no_fabricated_reading_or_timestamp(self):
        with patch.object(collector, "solar_history", self.history), patch.object(
            collector, "smlight_collector", self.collector
        ):
            payload = collector.smlight_payload()
        self.assertIsNone(payload["solar_w"])
        self.assertIsNone(payload["timestamp"])
        self.assertIsNone(payload["solar"]["lifetime_wh"])

    def test_old_collector_conflict_prevents_transmission(self):
        session = Mock(firmware="test")
        session.receive.return_value = {"network_source_ieee": COLLECTOR, "mac_source": "0x0000"}
        with self.assertRaisesRegex(RuntimeError, "Old Tesla collector"):
            self.collector.run_session(session)
        session.transmit.assert_not_called()
        session.addressed_mode.assert_not_called()

    def test_verification_reply_works_while_meter_query_is_pending(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock = [0]
        frame = verification_request()
        frame.update(observed_at=time.time(), rssi=-70)
        events = iter([(1, frame), (31, None), (32, frame), (33, None)])

        def receive():
            clock[0], incoming = next(events)
            if clock[0] == 33:
                self.collector.stop_event.set()
            return incoming

        session.receive.side_effect = receive
        with patch("smlight_collector.time.monotonic", side_effect=lambda: clock[0]):
            self.collector.run_session(session)
        frames = [decode_ieee802154_frame({
            "raw": call.args[0][:-2].hex(), "type": "data",
        }) for call in session.transmit.call_args_list]
        self.assertEqual(sum(f.get("cluster_id") == "0x8001" for f in frames), 1)
        self.assertEqual(self.collector.snapshot()["requests"], 1)
        self.assertEqual(self.collector.snapshot()["network_replies"], 1)
        self.assertEqual(self.collector.snapshot()["responses"], 0)
        self.assertIsNone(self.history.latest())

    def test_one_query_per_minute_with_power_every_other_query(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock = [0]
        timestamps = iter([1, 31, 46, 61, 90, 91, 106, 150, 151])
        requests = []

        def receive():
            clock[0] = next(timestamps)
            if clock[0] == 151:
                self.collector.stop_event.set()
            return dict(verification_request(), observed_at=time.time(), rssi=-70)

        def read_request(destination, sequence, kind):
            requests.append((clock[0], kind))
            return b"test request"

        session.receive.side_effect = receive
        with patch("smlight_collector.time.monotonic", side_effect=lambda: clock[0]), patch(
            "smlight_collector.read_request", side_effect=read_request,
        ):
            self.collector.run_session(session)
        self.assertEqual(requests, [(31, "power"), (91, "inverter_ac"), (151, "power")])

    def test_leave_cancels_query_and_rejoin_resumes_with_power(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 0
        clock = [0]
        events = iter([(31, device_announce()), (32, leave_notification()),
                       (38, None), (91, None), (151, device_announce()), (152, None)])
        requests = []

        def receive():
            clock[0], frame = next(events)
            if clock[0] == 38:
                self.assertIsNone(self.collector.snapshot()["inverter_address"])
                self.assertIn("left the radio network", self.collector.snapshot()["last_error"])
            if clock[0] == 152:
                self.collector.stop_event.set()
            return dict(frame, observed_at=time.time(), rssi=-70) if frame else None

        def read_request(destination, sequence, kind):
            requests.append((clock[0], kind))
            return b"test request"

        session.receive.side_effect = receive
        with patch("smlight_collector.time.monotonic", side_effect=lambda: clock[0]), patch(
            "smlight_collector.read_request", side_effect=read_request,
        ):
            self.collector.run_session(session)
        self.assertEqual(requests, [(31, "power"), (151, "power")])
        self.assertEqual(self.collector.snapshot()["timeouts"], 0)
        self.assertIsNone(self.history.latest())

    def test_failed_radio_delivery_is_retained_after_response_timeout(self):
        session = Mock(firmware="test")
        session.transmit.return_value = 17
        clock = [0]
        timestamps = iter([1, 31, 37])

        def receive():
            clock[0] = next(timestamps)
            if clock[0] == 37:
                self.collector.stop_event.set()
            return dict(verification_request(), observed_at=time.time(), rssi=-70)

        session.receive.side_effect = receive
        with patch("smlight_collector.time.monotonic", side_effect=lambda: clock[0]):
            self.collector.run_session(session)
        status = self.collector.snapshot()
        self.assertEqual(status["requests"], 1)
        self.assertEqual(status["query_tx_failures"], 1)
        self.assertEqual(status["timeouts"], 1)
        self.assertEqual(status["responses"], 0)
        self.assertEqual(status["last_query_kind"], "power")
        self.assertEqual(status["last_query_tx_status"], 17)
        self.assertIn("status 17", status["last_error"])
        self.assertIsNone(self.history.latest())

    def test_failure_closes_radio_and_retries_until_stopped(self):
        sessions = [Mock(), Mock()]
        self.collector.session_factory = Mock(side_effect=sessions)
        self.collector.stop_event = Mock()
        self.collector.stop_event.is_set.side_effect = [False, False, True]
        self.collector.run_session = Mock(side_effect=ConnectionError("Wi-Fi lost"))
        self.collector.run()
        self.assertEqual(self.collector.session_factory.call_count, 2)
        for session in sessions:
            session.close.assert_called_once()
        self.assertEqual(self.collector.snapshot()["state"], "disconnected")
        self.assertIsNone(self.history.latest())

    def test_energy_range_uses_non_null_counters_and_rejects_reset(self):
        at = time.time() - 100
        for index, energy in enumerate([None, 1000, None, 1050, None]):
            self.history.record([{
                "capture_id": str(index), "capture_sweep": 1, "radio_timestamp": index,
                "observed_at": at + index, "solar_w": 500, "lifetime_wh": energy,
            }])
        result = self.history.query("all")
        self.assertEqual(result["generated_wh"], 50)
        self.assertEqual(result["energy_first_at"], at + 1)
        self.assertEqual(result["energy_last_at"], at + 3)
        self.history.record([{
            "capture_id": "reset", "capture_sweep": 1, "radio_timestamp": 9,
            "observed_at": at + 9, "solar_w": 500, "lifetime_wh": 10,
        }])
        self.assertIsNone(self.history.query("all")["generated_wh"])

    def test_standby_survives_restart_without_fabricating_measurements(self):
        now = time.time()
        with patch("smlight_collector.response_values", return_value={"solar_w_precise": -0.32}):
            self.collector.accept_response(self.frame(observed_at=now - 3600), "power")
        schedule = Mock()
        schedule.standby_until.return_value = now + 3600
        collector = SmlightCollector("radio.test", self.history, night_schedule=schedule)
        collector.update(connected=True, state="waiting", last_error="Waiting for the inverter radio")
        status = collector.snapshot()
        self.assertEqual(status["state"], "standby")
        self.assertEqual(status["standby_until"], now + 3600)
        self.assertEqual(status["last_reading_at"], now - 3600)
        self.assertIsNone(status["last_error"])
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(self.history.latest()["solar_w"], -0.32)

        schedule.standby_until.return_value = None
        self.assertEqual(collector.snapshot()["state"], "stale")
        self.assertEqual(collector.snapshot()["last_error"], "Waiting for the inverter radio")

    def test_standby_never_hides_transport_conflicts_faults_or_active_radio(self):
        self.collector.night_schedule = Mock()
        self.collector.night_schedule.standby_until.return_value = time.time() + 3600
        for state, connected in (("disconnected", False), ("connecting", False), ("conflict", True)):
            self.collector.update(state=state, connected=connected, last_error="test error")
            status = self.collector.snapshot()
            self.assertEqual(status["state"], state)
            self.assertEqual(status["last_error"], "test error")
        self.collector.update(state="waiting", connected=True, last_packet_at=time.time())
        self.assertNotEqual(self.collector.snapshot()["state"], "standby")
        self.collector.update(last_packet_at=None)
        with patch.object(self.collector.telemetry, "snapshot", return_value={
            "inverter_dc": {"values": {"fault_bits": 1}}
        }):
            self.assertNotEqual(self.collector.snapshot()["state"], "standby")

    def test_quiet_night_checks_radio_health_instead_of_resetting_it(self):
        session = Mock(firmware="test")
        session.receive.return_value = None
        self.collector.snapshot = Mock(return_value={"state": "standby"})
        session.check_health.side_effect = self.collector.stop_event.set
        with patch("smlight_collector.time.monotonic", side_effect=[0, 0, 100, 101, 101]):
            self.collector.run_session(session)
        session.check_health.assert_called_once()
        # A quiet inverter still needs the coordinator's network maintenance.
        self.assertEqual(session.transmit.call_count, 1)
        frame = decode_ieee802154_frame({
            "raw": session.transmit.call_args.args[0][:-2].hex(), "type": "data",
        })
        self.assertEqual(frame["network_command_id"], 8)

    def test_silent_day_keeps_healthy_radio_available_without_claiming_recovery(self):
        session = Mock(firmware="test")
        session.receive.return_value = None
        session.check_health.side_effect = self.collector.stop_event.set
        with patch("smlight_collector.time.monotonic", side_effect=[0, 0, 100, 101, 101]):
            self.collector.run_session(session)
        session.check_health.assert_called_once()
        status = self.collector.snapshot()
        self.assertEqual(status["state"], "waiting")
        self.assertIsNone(status["last_reading_at"])
        self.assertEqual(status["requests"], 0)
        self.assertEqual(status["last_error"], "Waiting for the inverter radio")

    def test_silent_radio_with_failed_health_check_still_reconnects(self):
        session = Mock(firmware="test")
        session.receive.return_value = None
        session.check_health.side_effect = ConnectionError("SMLIGHT radio health check failed")
        with patch("smlight_collector.time.monotonic", side_effect=[0, 0, 100, 101]):
            with self.assertRaisesRegex(ConnectionError, "radio health check failed"):
                self.collector.run_session(session)
        session.check_health.assert_called_once()


if __name__ == "__main__":
    unittest.main()
