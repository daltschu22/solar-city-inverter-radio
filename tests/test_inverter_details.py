import unittest
import tempfile
import time
from pathlib import Path

from inverter import TelemetryStore, decode_details
from tools.smlight_poll import READS, read_request
from server import decode_ieee802154_frame, decode_modbus_message


def verified_registers():
    # Synthetic values exercising the observed register layout and scale factors.
    ac = [101, 50, 2500, 65535, 65535, 65535, 65533, 2400, 65535, 65535,
          65535, 65535, 65535, 65535, 600, 0, 6000, 65534, 32768, 0,
          32768, 0, 32768, 0, 1, 57920, 0]
    dc = [2500, 65533, 2600, 65535, 650, 0, 400, 32768, 32768, 32768,
          65535, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    meter = [201, 105, 25000, 25000, 32768, 32768, 65532, 32768, 32768,
             32768, 32768, 24000, 24000, 32768, 32768, 65534, 6000, 65534,
             6000, 6000, 32768, 32768, 65535, 6250, 6250, 32768, 32768,
             65535, 17500, 17500, 32768, 32768, 65534, 9600, 9600, 32768,
             32768, 65534]
    return {**dict(enumerate(ac, 40069)), **dict(enumerate(dc, 40096)),
            **dict(enumerate(meter, 40342))}


class InverterDetailsTests(unittest.TestCase):
    def test_live_registers_and_scale_factors(self):
        values = decode_details(verified_registers())
        expected = {"ac_current_a": 2.5, "ac_voltage_v": 240.0,
                    "frequency_hz": 60.0, "inverter_ac_w": 600,
                    "dc_current_a": 2.5, "dc_voltage_v": 260.0,
                    "dc_power_w": 650, "cabinet_c": 40.0, "operating_state": "MPPT",
                    "fault_bits": 0, "event_bits_2": 0, "meter_current_a": 2.5,
                    "meter_voltage_v": 240.0, "meter_frequency_hz": 60,
                    "apparent_va": 625.0, "reactive_var": 175.0,
                    "power_factor_pct": 96.0, "meter_real_w": 600.0,
                    "inverter_lifetime_wh": 123456}
        self.assertEqual(values, expected)

    def test_unsupported_and_unknown_models_not_fabricated(self):
        registers = verified_registers()
        registers[40069] = 999
        registers[40342] = 999
        self.assertEqual(decode_details(registers), {})
        self.assertNotIn("heatsink_c", decode_details(verified_registers()))
        self.assertNotIn("transformer_c", decode_details(verified_registers()))

    def test_signed_minus_one_is_not_an_unsigned_missing_sentinel(self):
        registers = verified_registers()
        registers[40102] = 65535
        self.assertEqual(decode_details(registers)["cabinet_c"], -0.1)
        registers[40071] = 32768
        self.assertEqual(decode_details(registers)["ac_current_a"], 32.768)
        registers[40075] = 32768
        self.assertNotIn("ac_current_a", decode_details(registers))

    def test_all_queries_are_small_read_only_requests(self):
        for kind, (start, count) in READS.items():
            raw = read_request(0x4567, 23, kind)
            decoded = decode_ieee802154_frame({"raw": raw[:-2].hex(), "type": "data"})
            message = decode_modbus_message(bytes.fromhex(decoded["application_payload"]))
            self.assertEqual(message["function"], 3)
            self.assertEqual(message["register_start"], start)
            self.assertEqual(message["register_count"], count)
            self.assertTrue(message["crc_valid"])
            self.assertLessEqual(41 + 5 + count * 2 + 2, 127)

    def test_partial_data_does_not_create_zero_measurements(self):
        self.assertEqual(decode_details({40069: 101, 40070: 50}), {})

    def test_telemetry_persists_with_separate_block_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = TelemetryStore(path)
            registers = verified_registers()
            now = time.time()
            ac = {k: v for k, v in registers.items() if 40069 <= k <= 40095}
            dc = {k: v for k, v in registers.items() if 40096 <= k <= 40120}
            store.record("ac", "inverter_ac", now - 600, ac)
            store.record("dc", "inverter_dc", now, dc)
            store.record("dc", "inverter_dc", now, dc)
            loaded = TelemetryStore(path)
            result = loaded.snapshot()
            self.assertTrue(result["inverter_ac"]["stale"])
            self.assertFalse(result["inverter_dc"]["stale"])
            self.assertEqual(result["inverter_dc"]["values"]["cabinet_c"], 40.0)
            self.assertNotIn("inverter_ac_w", result["inverter_dc"]["values"])
            self.assertEqual(result["meter_ac"]["values"], {})
            with loaded.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM inverter_telemetry").fetchone()[0], 2)
            dc[40102] = 0x8000
            loaded.record("new-dc", "inverter_dc", now + 1, dc)
            self.assertNotIn("cabinet_c", loaded.snapshot()["inverter_dc"]["values"])

    def test_identity_requires_both_nearby_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(Path(directory) / "history.sqlite3")
            first = {i: 0 for i in range(40000, 40035)}
            first.update({40002: 1, 40003: 65, 40004: 0x4142, 40034: 0x4344})
            second = {i: 0 for i in range(40035, 40069)}
            now = time.time()
            store.record("first", "common1", now, first)
            self.assertEqual(store.snapshot()["identity"]["values"], {})
            store.record("second", "common2", now + 30, second)
            self.assertEqual(store.snapshot()["identity"]["values"]["manufacturer"], "AB")
            store.record("old", "common1", now - 900, first)
            self.assertEqual(store.snapshot()["identity"]["values"], {})


if __name__ == "__main__":
    unittest.main()
