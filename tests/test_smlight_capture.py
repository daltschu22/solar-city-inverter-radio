import json
from pathlib import Path
import unittest

from tools.smlight_capture import decode_record, summarize


class SmlightCaptureTests(unittest.TestCase):
    def metadata(self, timestamp=123, error=0, flags=0):
        return (-60, -100, flags, (14, 110, timestamp), ((error, 1),))

    def test_strips_radio_fcs_before_modbus_decoding(self):
        raw = bytes.fromhex(
            "6188fe341278560000481c785600001ec4020000000000000201000000000000"
            "02000040e8110005c1e8b301039d96006a0a65"
        )
        result = decode_record(raw + b"\x12\x34", self.metadata(), 1, 1000)
        self.assertEqual(result["application_payload"], "01039d96006a0a65")
        self.assertEqual(result["rssi"], -60)
        self.assertEqual(result["receive_error"], 0)
        self.assertEqual(result["observed_at"], 1000)
        self.assertEqual(summarize([result])["valid_modbus_messages"], 1)

    def test_bad_frames_are_not_solar_measurements(self):
        raw = bytes.fromhex("0200010000")
        for metadata in (self.metadata(error=1), self.metadata(flags=4)):
            record = decode_record(raw, metadata, 1, 1000)
            self.assertNotIn("application_protocol", record)
            self.assertEqual(summarize([record])["values"], {})

    def test_synthetic_exchange_decodes_through_smlight_adapter(self):
        from synthetic_radio import records
        source = records()
        frames = [
            decode_record(bytes.fromhex(frame["raw"]) + b"\x00\x00",
                          self.metadata(frame["timestamp"]), frame["capture_sweep"], 1000)
            for frame in source
        ]
        summary = summarize(frames)
        self.assertEqual(summary["frames"], 6)
        self.assertGreater(summary["complete_transactions"], 0)
        self.assertEqual(summary["values"]["solar_w"], 750)

    def test_truncated_frame_rejected(self):
        with self.assertRaises(ValueError):
            decode_record(b"\x01", self.metadata(), 1, 1000)


if __name__ == "__main__":
    unittest.main()
