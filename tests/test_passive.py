import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from collector.history import SolarHistoryStore
from collector.passive import PassiveCollector, PassiveRadioSession, PassiveTransactions
from collector.radio_protocol import decode_ieee802154_frame
from synthetic_radio import crc, records


def exchange(start=40360, values=None, counter=2, at=100):
    if values is None:
        values = [5000, 5000, 0x8000, 0x8000, 0xFFFF]
    request, response = [decode_ieee802154_frame(r) for r in records()[:2]]
    query = struct.pack(">BBHH", 1, 3, start, len(values))
    reply = bytes([1, 3, 2 * len(values)]) + struct.pack(">" + "H" * len(values), *values)
    for i, (frame, payload) in enumerate(((request, query), (response, reply))):
        frame.update(application_payload=(payload + crc(payload)).hex(), aps_counter=counter + i,
                     timestamp=counter + i, observed_at=at + i)
    return request, response


class PassiveMatchingTests(unittest.TestCase):
    def test_requires_captured_request_and_deduplicates_retries(self):
        matcher = PassiveTransactions()
        request, response = exchange()
        self.assertIsNone(matcher.feed(response, 1))
        matcher = PassiveTransactions()
        self.assertIsNone(matcher.feed(request, 0))
        self.assertIsNone(matcher.feed(request, .1))
        self.assertEqual(matcher.feed(response, 1)[40360], 5000)
        self.assertIsNone(matcher.feed(response, 1.1))
        self.assertEqual(matcher.requests, 1)
        # Repeated identical measurements with new APS counters are real samples.
        request, response = exchange(counter=4)
        matcher.feed(request, 2)
        self.assertEqual(matcher.feed(response, 3)[40360], 5000)

    def test_filters_foreign_corrupt_secured_and_indirect_frames(self):
        request, response = exchange()
        bad = ({"channel": 25}, {"source_pan_id": "0x1111"},
               {"destination_pan_id": "0x1111"}, {"network_source_ieee": "0200000000000009"},
               {"network_destination_ieee": "0200000000000009"}, {"mac_source": "0x1111"},
               {"mac_destination": "0x1111"}, {"source_endpoint": 0}, {"destination_endpoint": 0},
               {"profile_id": "0x0000"}, {"cluster_id": "0x0012"}, {"network_secured": True},
               {"aps_secured": True}, {"receive_error": 1}, {"bad_fcs": True},
               {"application_payload": response["application_payload"][:-4] + "0000"})
        for changes in bad:
            with self.subTest(changes=changes):
                matcher = PassiveTransactions()
                matcher.feed(request, 0)
                self.assertIsNone(matcher.feed({**response, **changes}, 1))

    def test_expiry_overlap_and_wrong_length_never_guess_registers(self):
        request, response = exchange()
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        self.assertIsNone(matcher.feed(response, 11))
        self.assertEqual(matcher.expired, 1)
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        other, _ = exchange(start=40069, counter=4)
        matcher.feed(other, .1)
        self.assertIsNone(matcher.feed(response, 1))
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        _, other = exchange(values=[1] * 17)
        self.assertIsNone(matcher.feed(other, 1))

    def test_unit_mismatch_and_exception_consume_no_reading(self):
        request, response = exchange()
        for payload in (bytes.fromhex("02030a") + bytes(10), bytes.fromhex("018302")):
            matcher = PassiveTransactions()
            matcher.feed(request, 0)
            self.assertIsNone(matcher.feed({**response, "application_payload": (payload + crc(payload)).hex()}, 1))

    def test_fragmented_reply_needs_all_blocks_and_valid_crc(self):
        request, response = exchange(start=40342, values=[0] * 55)
        payload = bytes.fromhex(response["application_payload"])
        first = {**response, "aps_fragmentation": "first", "aps_block_count": 2,
                 "application_payload": payload[:70].hex()}
        last = {**response, "aps_fragmentation": "continuation", "aps_block_number": 1,
                "application_payload": payload[70:].hex()}
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        self.assertIsNone(matcher.feed(last, .1))
        self.assertIsNone(matcher.feed(first, .2))
        # A continuation seen before its first fragment is discarded, including
        # its duplicate inside the retry window. A complete fresh exchange works.
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        self.assertIsNone(matcher.feed(first, 1))
        self.assertIsNone(matcher.feed(first, 1.1))
        self.assertEqual(len(matcher.feed(last, 2)), 55)
        self.assertIsNone(matcher.feed(last, 2.1))
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        matcher.feed(first, 1)
        self.assertIsNone(matcher.feed(last, 11))


class PassiveCollectorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = SolarHistoryStore(Path(directory.name) / "history.sqlite3")
        self.collector = PassiveCollector("radio.example.invalid", self.history)

    def test_saves_meter_power_energy_and_diagnostics_from_large_read(self):
        values = [0] * 55
        values[0:2] = [201, 105]
        values[18], values[22], values[38], values[39] = 5000, 0xFFFF, 1, 57920
        request, response = exchange(40342, values)
        matcher = PassiveTransactions()
        matcher.feed(request, 0)
        self.collector.save_registers(response, matcher.feed(response, 1))
        reading = self.history.latest()
        self.assertEqual(reading["solar_w"], 500)
        self.assertEqual(reading["lifetime_wh"], 123456)
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(self.collector.telemetry.snapshot()["meter_ac"]["values"]["meter_real_w"], 500)

    def test_does_not_mix_inverter_energy_or_stale_energy_into_meter_history(self):
        _, response = exchange()
        self.collector.save_registers(response, {40093: 1, 40094: 2, 40095: 0})
        self.assertIsNone(self.collector.energy)
        self.collector.save_registers(response, {40380: 1, 40381: 57920, 40396: 0})
        _, later = exchange(counter=6, at=500)
        self.collector.save_registers(later, {40360: 5000, 40364: 0xFFFF})
        self.assertIsNone(self.history.latest()["lifetime_wh"])

    def test_snapshot_is_honest_about_stale_data_and_tesla_cadence(self):
        _, response = exchange()
        self.collector.save_registers(response, {40360: 5000, 40364: 0xFFFF})
        with patch("collector.passive.time.time", return_value=500):
            status = self.collector.snapshot()
        self.assertEqual(status["state"], "stale")
        self.assertEqual(status["mode"], "passive")
        self.assertIsNone(status["interval_seconds"])
        self.assertIsNone(status["standby_until"])
        self.assertEqual(status["reading_stale_after_seconds"], 300)
        self.assertEqual(status["requests"], 0)

    def test_reconnect_discards_pending_requests_and_never_calls_transmit(self):
        collector = self.collector
        request, response = exchange()
        fresh_request, fresh_response = exchange(counter=6, at=200)
        sessions = []

        class Session:
            firmware = "synthetic"

            def __init__(self, host, port):
                self.events = iter([request, ConnectionError("Synthetic disconnect")] if not sessions
                                   else [response, fresh_request, fresh_response, None])
                self.closed = False
                sessions.append(self)

            def initialize(self, stop):
                pass

            def receive(self):
                event = next(self.events)
                if isinstance(event, Exception):
                    raise event
                if event is None:
                    collector.stop_event.set()
                return event

            def close(self):
                self.closed = True

        collector.session_factory = Session
        collector.reconnect_interval = 0
        collector.run()
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s.closed for s in sessions))
        self.assertEqual(self.history.query("all")["sample_count"], 1)
        self.assertEqual(self.history.latest()["observed_at"], 201)
        self.assertEqual(collector.snapshot()["network_transmissions"], 0)


class PassiveRadioTests(unittest.TestCase):
    def session(self):
        from spinel.const import SPINEL
        session = PassiveRadioSession.__new__(PassiveRadioSession)
        session.constants = SPINEL
        session.api = Mock()
        session.api.prop_set_value.side_effect = lambda prop, value, fmt: value
        session.stream = Mock(error=None)
        return session

    def test_startup_health_shutdown_and_blocked_transmission(self):
        session = self.session()
        c = session.constants
        stop = Mock()
        stop.wait.return_value = False
        session.initialize(stop)
        settings = [(call.args[0], call.args[1]) for call in session.api.prop_set_value.call_args_list]
        self.assertEqual(settings, [(c.PROP_PHY_ENABLED, 1),
                                    (c.PROP_MAC_15_4_PANID, 0xFFFF), (c.PROP_MAC_15_4_SADDR, 0xFFFF),
                                    (c.PROP_MAC_FILTER_MODE, c.MAC_FILTER_MODE_MONITOR),
                                    (c.PROP_PHY_CHAN, 14),
                                    (c.PROP_MAC_RAW_STREAM_ENABLED, 1)])
        for action in (lambda: session.transmit(b"test"), session.addressed_mode,
                       lambda: session.set_pending_inverter(True)):
            with self.assertRaises(RuntimeError):
                action()
        session.api.prop_get_value.side_effect = [14, c.MAC_FILTER_MODE_MONITOR]
        session.check_health()
        session.close()
        session.api.prop_change_async.assert_not_called()
        session.api.prop_insert_value.assert_not_called()
        session.stream.close.assert_called_once()
        self.assertTrue(all(call.args == (c.CMD_RESET,) for call in session.api.cmd_send.call_args_list))

    def test_unsupported_monitor_mode_fails_before_reception(self):
        session = self.session()
        c = session.constants
        session.api.prop_set_value.side_effect = lambda prop, value, fmt: 0 if prop == c.PROP_MAC_FILTER_MODE else value
        stop = Mock()
        stop.wait.return_value = False
        with self.assertRaises(ConnectionError):
            session.initialize(stop)
        self.assertNotIn(c.PROP_MAC_RAW_STREAM_ENABLED, [call.args[0] for call in session.api.prop_set_value.call_args_list])

    def test_stop_during_reset_does_not_enable_reception(self):
        session = self.session()
        stop = threading.Event()
        stop.set()
        session.initialize(stop)
        session.api.prop_set_value.assert_not_called()

    def test_health_check_rejects_a_radio_that_lost_monitor_mode(self):
        session = self.session()
        session.api.prop_get_value.side_effect = [14, 0]
        with self.assertRaisesRegex(ConnectionError, "monitor mode"):
            session.check_health()
        session.api.prop_change_async.assert_not_called()
