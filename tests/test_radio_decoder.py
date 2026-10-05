import json
import tempfile
import time
import unittest
from pathlib import Path

from collector.history import SolarHistoryStore
from collector.radio_protocol import (
    build_modbus_transactions,
    decode_ieee802154_frame,
    decode_sunspec_values,
    extract_solar_measurements,
)


class RadioDecoderTests(unittest.TestCase):
    def decode(self, raw, timestamp):
        return decode_ieee802154_frame(
            {
                "type": "data",
                "channel": 14,
                "capture_sweep": 3,
                "timestamp": timestamp,
                "raw": raw,
            }
        )

    def test_decodes_xbee_link_status(self):
        frame = self.decode(
            "4188c13412ffff00000910fcff0000015401000000000000020861785611",
            1,
        )

        self.assertEqual(frame["network_command"], "Link Status")
        self.assertEqual(frame["network_source_ieee"], "0200000000000001")
        self.assertEqual(frame["network_source_vendor"], "")

    def test_reassembles_and_validates_fragmented_modbus_response(self):
        response = (
            bytes.fromhex("010384007e0040")
            + bytes(128)
            + bytes.fromhex("f3db")
        )
        fragment_base = {
            "application_protocol": "digi-transparent",
            "aps_frame_type": "data",
            "capture_sweep": 3,
            "network_source_ieee": "0200000000000002",
            "network_destination_ieee": "0200000000000001",
            "aps_counter": 120,
        }
        frames = [
            self.decode(
                "61882f341278560000481c785600001ed3020000000000000201000000000000"
                "02000040e8110005c1e89601039d0f0042da54",
                100,
            ),
            {
                **fragment_base,
                "timestamp": 200,
                "aps_fragmentation": "first",
                "aps_block_count": 2,
                "application_payload": response[:84].hex(),
            },
            {
                **fragment_base,
                "timestamp": 300,
                "aps_fragmentation": "continuation",
                "aps_block_number": 1,
                "application_payload": response[84:].hex(),
            },
        ]

        messages, transactions, registers, values = build_modbus_transactions(frames)

        self.assertEqual(len(messages), 2)
        self.assertEqual(len(transactions), 1)
        self.assertTrue(transactions[0]["response"]["crc_valid"])
        self.assertEqual(transactions[0]["response"]["fragment_count"], 2)
        self.assertEqual(registers[40207], 126)
        self.assertEqual(registers[40208], 64)
        self.assertNotIn("solar_w", values)

    def test_applies_sunspec_scale_factors(self):
        values = decode_sunspec_values(
            {
                40083: 3210,
                40084: 0,
                40093: 0,
                40094: 12345,
                40095: 0,
                40100: 330,
                40101: 1,
            }
        )

        self.assertEqual(values["solar_w"], 3210)
        self.assertEqual(values["dc_w"], 3300)
        self.assertEqual(values["lifetime_wh"], 12345)

        meter_values = decode_sunspec_values(
            {
                40360: 0x79B0,
                40364: 0xFFFF,
                40380: 0x0001,
                40381: 0xE240,
                40396: 0,
            }
        )

        self.assertEqual(meter_values["solar_w"], 3115)
        self.assertAlmostEqual(meter_values["solar_w_precise"], 3115.2)
        self.assertEqual(meter_values["power_model"], 201)
        self.assertEqual(meter_values["lifetime_wh"], 123456)

    def test_decodes_synthetic_inverter_exchange(self):
        from synthetic_radio import records as make_records
        records = make_records()
        frames = [decode_ieee802154_frame(record) for record in records]
        _, transactions, _, values = build_modbus_transactions(frames)
        measurements = extract_solar_measurements(transactions)

        self.assertEqual(len(records), 6)
        self.assertEqual(values["power_model"], 201)
        self.assertEqual(values["solar_w"], 750)
        self.assertEqual(values["lifetime_wh"], 123456)
        self.assertEqual(len(measurements), 2)

    def test_persists_each_verified_measurement_once(self):
        registers = [0] * 55
        registers[18] = 31076
        registers[22] = 0xFFFF
        registers[38] = 1
        registers[39] = 57920
        registers[54] = 0
        measurements = extract_solar_measurements(
            [
                {
                    "request": {
                        "kind": "request",
                        "register_start": 40342,
                    },
                    "response": {
                        "kind": "response",
                        "capture_sweep": 7,
                        "timestamp": 1000,
                        "observed_at": 1700000000.0,
                        "raw": "verified-modbus-response",
                        "register_values": registers,
                    },
                }
            ]
        )

        self.assertEqual(len(measurements), 1)
        self.assertAlmostEqual(measurements[0]["solar_w"], 3107.6)
        self.assertEqual(measurements[0]["lifetime_wh"], 123456)

        with tempfile.TemporaryDirectory() as directory:
            store = SolarHistoryStore(Path(directory) / "history.sqlite3")
            self.assertEqual(store.record(measurements), 1)
            self.assertEqual(store.record(measurements), 0)
            history = store.query("all")
            self.assertEqual(history["retention"], "unlimited")
            self.assertEqual(history["sample_count"], 1)
            self.assertEqual(len(history["points"]), 1)
            self.assertAlmostEqual(history["points"][0]["solar_w"], 3107.6)

            store.record(
                [
                    {
                        "capture_id": f"capture-{index}",
                        "observed_at": 1700000000.0 + index,
                        "capture_sweep": index,
                        "radio_timestamp": index,
                        "solar_w": 1000 + index,
                        "lifetime_wh": 123456 + index,
                    }
                    for index in range(1, 5202)
                ]
            )
            downsampled = store.query("all")

        self.assertEqual(downsampled["sample_count"], 5202)
        self.assertTrue(downsampled["downsampled"])
        self.assertEqual(downsampled["point_count"], len(downsampled["points"]))
        self.assertLessEqual(len(downsampled["points"]), 5000)

    def test_supports_granular_history_ranges(self):
        now = time.time()
        readings = [
            {
                "capture_id": "older-reading",
                "observed_at": now - 2 * 60 * 60,
                "capture_sweep": 1,
                "radio_timestamp": 1,
                "solar_w": 900,
                "lifetime_wh": 1000,
            },
            {
                "capture_id": "recent-reading",
                "observed_at": now - 30 * 60,
                "capture_sweep": 2,
                "radio_timestamp": 2,
                "solar_w": 1200,
                "lifetime_wh": 1100,
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            store = SolarHistoryStore(Path(directory) / "history.sqlite3")
            store.record(readings)
            one_hour = store.query("1h")
            six_hours = store.query("6h")

        self.assertEqual(one_hour["sample_count"], 1)
        self.assertEqual(six_hours["sample_count"], 2)
        self.assertFalse(one_hour["downsampled"])
        with self.assertRaisesRegex(ValueError, "Unknown history range"):
            store.query("15m")


if __name__ == "__main__":
    unittest.main()
