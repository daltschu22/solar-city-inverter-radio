import unittest

from radio_protocol import decode_ieee802154_frame, decode_modbus_message
from tools.smlight_poll import acknowledgment, is_inverter, read_request, response_values


def power_response():
    return decode_ieee802154_frame({
        "raw": "6188313412000067450818000067451e9a01000000000000020200000000000002"
               "40e8110005c1e84601030a1388138880008000ffffad8b",
        "type": "data", "rssi": -47,
    })


class SmlightPollTests(unittest.TestCase):
    def test_read_only_requests_match_verified_protocol(self):
        for kind, start, count in (("power", 40360, 5), ("energy", 40380, 17)):
            raw = read_request(0x4567, 89, kind)
            frame = decode_ieee802154_frame({"raw": raw[:-2].hex(), "type": "data"})
            self.assertEqual(frame["network_destination"], "0x4567")
            self.assertEqual(frame["network_destination_ieee"], "0200000000000002")
            self.assertEqual(frame["network_source_ieee"], "0200000000000001")
            self.assertEqual(frame["cluster_id"], "0x0011")
            message = decode_modbus_message(bytes.fromhex(frame["application_payload"]))
            self.assertTrue(message["crc_valid"])
            self.assertEqual(message["function"], 3)
            self.assertEqual(message["register_start"], start)
            self.assertEqual(message["register_count"], count)
        self.assertEqual(read_request(0x4567, 89, "power")[:-2].hex(),
                         "618859341267450000481c674500001ea802000000000000020100000000000002"
                         "000040e8110005c1e8e401039da800052b85")

    def test_synthetic_inverter_response_decodes_500_watts(self):
        frame = power_response()
        self.assertTrue(is_inverter(frame))
        self.assertEqual(response_values(frame, "power")["solar_w_precise"], 500.0)
        self.assertIsNone(response_values(frame, "energy"))

    def test_outgoing_request_never_counts_as_a_reading(self):
        frame = decode_ieee802154_frame({"raw": read_request(0x4567, 1, "power")[:-2].hex(), "type": "data"})
        self.assertIsNone(response_values(frame, "power"))

    def test_bad_crc_wrong_sender_and_fragments_rejected(self):
        for changes in (
            {"application_payload": "01030a1388138880008000ffffad80"},
            {"network_source_ieee": "0200000000000099"},
            {"network_destination_ieee": "0200000000000099"},
            {"receive_error": 1}, {"bad_fcs": True},
            {"aps_fragmentation": "first"}, {"source_pan_id": "0x1111"},
        ):
            frame = power_response()
            frame.update(changes)
            self.assertIsNone(response_values(frame, "power"))

    def test_aps_ack_echoes_counter_without_requesting_another_ack(self):
        frame = power_response()
        raw = acknowledgment(frame, 90)
        ack = decode_ieee802154_frame({"raw": raw[:-2].hex(), "type": "data"})
        self.assertEqual(ack["aps_frame_type"], "acknowledgment")
        self.assertFalse(ack["aps_ack_requested"])
        self.assertEqual(ack["aps_counter"], frame["aps_counter"])
        self.assertEqual(ack["network_destination"], frame["network_source"])
        self.assertIsNone(acknowledgment(ack, 91))

    def test_no_broadcast_or_arbitrary_register_writes(self):
        for destination in (0, 0xFFFF, 0xFFFC, -1):
            with self.assertRaises(ValueError):
                read_request(destination, 1, "power")
        with self.assertRaises(KeyError):
            read_request(0x4567, 1, "write")


if __name__ == "__main__":
    unittest.main()
