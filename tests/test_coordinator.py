import itertools
import struct
import tempfile
import unittest
from pathlib import Path

from collector.coordinator import Coordinator, beacon, inverter_left, link_status
from collector.radio_protocol import decode_ieee802154_frame
from tools.smlight_poll import COLLECTOR, INVERTER, PAN, is_inverter


def decode(raw, kind="data"):
    return decode_ieee802154_frame({"raw": raw.hex(), "type": kind, "channel": 14})


def request(cluster=1, data=b"\x55\0\0\0\0", ack=False, ieee=True):
    mac = struct.pack("<HBHHH", 0x8861, 1, PAN, 0, 0x2345)
    nwk = struct.pack("<HHHBB", 0x1008 if ieee else 8, 0, 0x2345, 30, 10)
    if ieee:
        nwk += bytes.fromhex(INVERTER)[::-1]
    aps = struct.pack("<BBHHBB", 0x40 if ack else 0, 0, cluster, 0, 0, 0x33)
    return decode(mac + nwk + aps + data)


def association_request():
    # IEEE 802.15.4 association: long source, coordinator short destination,
    # source PAN unknown, allocate address + always-listening powered router.
    return decode(bytes.fromhex("23c80134120000ffff0200000000000002018e"), "command")


def data_request():
    return decode(bytes.fromhex("63c80234120000020000000000000204"), "command")


def device_announce(address=0x2345):
    mac = struct.pack("<HBHHH", 0x8841, 3, PAN, 0xFFFF, address)
    nwk = struct.pack("<HHHBB", 8, 0xFFFD, address, 1, 20)
    aps = struct.pack("<BBHHBB", 8, 0, 0x0013, 0, 0, 40)
    return decode(mac + nwk + aps + struct.pack("<BHQB", 50, address, int(INVERTER, 16), 0x8E))


def leave_notification():
    # Synthetic self-leave with the observed frame layout.
    return decode(bytes.fromhex("4188773412ffff45230910fdff4523016a02000000000000020400"))


def application_hello():
    # Synthetic startup request using the observed application bytes.
    return decode(bytes.fromhex(
        "6188bf3412000056344818000056341ed701000000000000020200000000000002"
        "40e8110005c1e804f400010101"))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Coordinator()
        self.sequence = itertools.count(1).__next__
        # Synthetic inverter link status.
        self.neighbor = decode(bytes.fromhex(
            "4188853412ffff45230910fcff4523019102000000000000020861000011"))
        self.coordinator.observe(self.neighbor, 100)

    def test_beacon_and_link_status_match_protocol_layout(self):
        self.assertEqual(beacon(0x7C)[:-2].hex(),
                         "00807c34120000ffcf00000020848877665544332211ffffff00")
        self.assertEqual(link_status(0x6C, 0x2345)[:-2].hex(),
                         "41886c3412ffff00000910fcff000001bb01000000000000020861452311")

    def test_application_hello_gets_observed_startup_reply(self):
        replies = self.coordinator.replies(application_hello(), 101, self.sequence)
        self.assertEqual(len(replies), 1)
        # Synthetic reference packet with fixed sequence counters.
        expected = bytearray.fromhex(
            "6188493412563400004818563400001e9e02000000000000020100000000000002"
            "40e8110005c1e899f5000000")
        expected[2], expected[16], expected[40] = 1, 80, 1
        self.assertEqual(replies[0][:-2], expected)
        reply = decode(replies[0][:-2])
        self.assertEqual(reply["network_destination"], "0x3456")
        self.assertEqual(reply["application_payload"], "f5000000")
        self.assertTrue(reply["aps_ack_requested"])

    def test_hello_reply_is_bounded_but_repeated_hello_can_recover_delivery_loss(self):
        frame = application_hello()
        self.assertEqual(len(self.coordinator.replies(frame, 101, self.sequence)), 1)
        self.assertEqual(self.coordinator.replies(frame, 101.1, self.sequence), [])
        self.assertEqual(len(self.coordinator.replies(frame, 116, self.sequence)), 1)
        self.coordinator.observe(leave_notification(), 116.1)
        self.assertEqual(len(self.coordinator.replies(frame, 116.2, self.sequence)), 1)

    def test_hello_does_not_replay_initialization_to_unrelated_or_invalid_packets(self):
        frame = application_hello()
        for change in ({"channel": 15}, {"network_source_ieee": COLLECTOR},
                       {"network_destination_ieee": INVERTER}, {"mac_source": "0x0000"},
                       {"network_destination": "0xfffd"}, {"mac_destination": "0xffff"},
                       {"profile_id": "0x0000"}, {"cluster_id": "0x0012"},
                       {"source_endpoint": 0}, {"destination_endpoint": 0},
                       {"aps_frame_type": "acknowledgment"}, {"aps_ack_requested": False},
                       {"aps_delivery_mode": "broadcast"}, {"network_secured": True},
                       {"aps_secured": True}, {"aps_fragmentation": "first"},
                       {"bad_fcs": True}, {"receive_error": 1},
                       {"application_payload": "f400010105"},
                       {"application_payload": "f40001010100"},
                       {"application_payload": "f5000000"}, {"application_payload": "f300010000"}):
            with self.subTest(change=change):
                self.assertEqual(Coordinator().replies(dict(frame, **change), 101, self.sequence), [])

    def test_beacon_discovery_is_rate_limited_and_rejects_bad_frames(self):
        frame = decode(bytes.fromhex("030879ffffffff07"), "command")
        self.assertEqual(frame["mac_command_id"], 7)
        self.assertEqual(len(self.coordinator.replies(frame, 101, self.sequence)), 1)
        self.assertEqual(self.coordinator.replies(frame, 101.05, self.sequence), [])
        for change in ({"bad_fcs": True}, {"receive_error": 1}, {"channel": 15},
                       {"mac_command_payload": "00"}, {"destination_pan_id": "0x4321"}):
            self.assertEqual(self.coordinator.replies(dict(frame, **change), 102, self.sequence), [])

    def test_join_verification_gets_collector_ieee_and_same_transaction(self):
        replies = self.coordinator.replies(request(ack=True), 101, self.sequence)
        self.assertEqual(len(replies), 2)
        ack, response = [decode(raw[:-2]) for raw in replies]
        self.assertEqual(ack["aps_frame_type"], "acknowledgment")
        self.assertEqual(ack["aps_counter"], 0x33)
        self.assertEqual(response["cluster_id"], "0x8001")
        self.assertEqual(response["application_payload"], "550001000000000000020000")
        self.assertEqual(response["network_destination_ieee"], INVERTER)
        self.assertEqual(response["network_source_ieee"], COLLECTOR)
        self.assertEqual(response["network_destination"], "0x2345")
        self.assertFalse(response["aps_ack_requested"])

    def test_network_address_and_extended_queries(self):
        data = b"\x66" + bytes.fromhex(COLLECTOR)[::-1] + b"\x01\x03"
        replies = self.coordinator.replies(request(0, data), 101, self.sequence)
        response = decode(replies[0][:-2])
        self.assertEqual(response["cluster_id"], "0x8000")
        self.assertEqual(response["application_payload"], "6600010000000000000200000003")

    def test_startup_verification_precedes_identifiable_inverter_traffic(self):
        frame = request(ieee=False)
        coordinator = Coordinator()
        replies = coordinator.replies(frame, 101, self.sequence)
        self.assertEqual(len(replies), 1)
        response = decode(replies[0][:-2])
        self.assertEqual(response["application_payload"], "550001000000000000020000")
        self.assertEqual(response["network_destination"], "0x2345")
        self.assertEqual(response["network_destination_ieee"], INVERTER)
        self.assertIsNone(coordinator.neighbor)
        # Expired neighbor visibility must not suppress later boot verification.
        self.assertEqual(len(self.coordinator.replies(frame, 161, self.sequence)), 1)

    def test_discovery_does_not_accept_relayed_other_network_or_wrong_destination_requests(self):
        for change in ({"channel": 15}, {"network_source": "0x1111"},
                       {"mac_source": "0x0000", "network_source": "0x0000"},
                       {"mac_source": "0xffff", "network_source": "0xffff"},
                       {"mac_destination": "0x1111"}, {"network_destination": "0x1111"},
                       {"network_destination_ieee": INVERTER}, {"aps_delivery_mode": "reserved"}):
            self.assertEqual(Coordinator().replies(dict(request(ieee=False), **change), 101, self.sequence), [])

    def test_discovery_is_rate_limited_without_learning_a_neighbor(self):
        coordinator = Coordinator()
        frame = request(ieee=False)
        self.assertEqual(len(coordinator.replies(frame, 101, self.sequence)), 1)
        self.assertEqual(coordinator.replies(frame, 101.5, self.sequence), [])
        self.assertEqual(len(coordinator.replies(frame, 102, self.sequence)), 1)
        self.assertIsNone(coordinator.neighbor)

    def test_other_devices_bad_packets_and_unrelated_queries_get_no_reply(self):
        for changes in ({"network_source_ieee": COLLECTOR}, {"bad_fcs": True},
                        {"receive_error": 1}, {"source_pan_id": "0x4321"},
                        {"network_secured": True}, {"aps_secured": True},
                        {"aps_fragmentation": "first"}, {"source_endpoint": 0xE8},
                        {"application_payload": "5501000000"},
                        {"application_payload": "5500000200"},
                        {"application_payload": "55000000"}, {"cluster_id": "0x0002"}):
            self.assertEqual(self.coordinator.replies(dict(request(), **changes), 101, self.sequence), [])

    def test_inverter_route_request_gets_direct_coordinator_route(self):
        frame = decode(bytes.fromhex(
            "41888c3412ffff45230910fcff45231e9902000000000000020120010000000100000000000002"))
        replies = self.coordinator.replies(frame, 101, self.sequence)
        response = decode(replies[0][:-2])
        self.assertEqual(response["network_command_id"], 2)
        self.assertEqual(response["network_command_payload"],
                         "3001452300000102000000000000020100000000000002")
        for payload in ("0001000100", "0801000000", "2001000000", "00"):
            self.assertEqual(self.coordinator.replies(
                dict(frame, network_command_payload=payload), 102, self.sequence), [])

    def test_periodic_maintenance_expires_neighbor_without_inventing_readings(self):
        first = self.coordinator.periodic(101, self.sequence)
        self.assertEqual(decode(first[:-2])["network_command_payload"], "61452311")
        self.assertIsNone(self.coordinator.periodic(110, self.sequence))
        later = self.coordinator.periodic(161, self.sequence)
        self.assertEqual(decode(later[:-2])["network_command_payload"], "60")

    def test_new_or_returning_neighbor_gets_immediate_link_status(self):
        coordinator = Coordinator()
        coordinator.periodic(100, self.sequence)
        coordinator.observe(device_announce(), 101)
        reply = coordinator.periodic(101, self.sequence)
        self.assertEqual(decode(reply[:-2])["network_command_payload"], "61452311")
        coordinator.observe(self.neighbor, 102)
        self.assertIsNone(coordinator.periodic(102, self.sequence))
        coordinator.periodic(165, self.sequence)
        coordinator.observe(self.neighbor, 166)
        self.assertIsNotNone(coordinator.periodic(166, self.sequence))

    def test_self_leave_clears_neighbor_but_preserves_address_for_reassociation(self):
        frame = device_announce()
        self.coordinator.replies(frame, 100, self.sequence)
        self.coordinator.observe(leave_notification(), 101)
        self.assertIsNone(self.coordinator.neighbor)
        self.assertFalse(self.coordinator.trusted(self.neighbor, 101))
        self.assertEqual(self.coordinator.assigned_address, 0x2345)
        status = self.coordinator.periodic(101, self.sequence)
        self.assertEqual(decode(status[:-2])["network_command_payload"], "60")
        self.coordinator.observe(frame, 102)
        self.assertEqual(len(self.coordinator.replies(frame, 102, self.sequence)), 2)

    def test_leave_requires_own_identity_and_notification_not_a_request(self):
        frame = leave_notification()
        self.assertTrue(inverter_left(frame))
        for change in ({"network_command_payload": "40"}, {"network_command_payload": "01"},
                       {"network_command_payload": "0000"}, {"network_source_ieee": COLLECTOR},
                       {"mac_source": "0x0000"}, {"source_pan_id": "0x4321"},
                       {"channel": 15}, {"bad_fcs": True}, {"network_secured": True}):
            self.assertFalse(inverter_left(dict(frame, **change)))

    def test_association_waits_for_poll_then_returns_known_address_and_identity(self):
        coordinator = Coordinator()
        self.assertEqual(coordinator.replies(association_request(), 100, self.sequence), [])
        self.assertTrue(coordinator.association_pending(100))
        self.assertIsNone(coordinator.neighbor)
        replies = coordinator.replies(data_request(), 101, self.sequence)
        self.assertEqual(len(replies), 1)
        reply = decode(replies[0][:-2], "command")
        self.assertEqual(reply["mac_command_id"], 2)
        self.assertEqual(reply["mac_command_payload"], "452300")
        self.assertEqual(reply["mac_destination"], "0x" + INVERTER)
        self.assertEqual(reply["mac_source"], "0x" + COLLECTOR)
        self.assertEqual(reply["source_pan_id"], "0x1234")
        self.assertTrue(replies[0][0] & 0x20)
        self.assertIsNone(coordinator.neighbor)
        # A lost response remains retryable with the same assigned address.
        repeated = coordinator.replies(data_request(), 102, self.sequence)
        self.assertEqual(decode(repeated[0][:-2], "command")["mac_command_payload"], "452300")
        coordinator.observe(device_announce(), 103)
        self.assertEqual(coordinator.neighbor, 0x2345)
        self.assertFalse(coordinator.association_pending(103))
        self.assertEqual(coordinator.replies(data_request(), 104, self.sequence), [])

    def test_join_announces_direct_link_and_probes_once_before_verification(self):
        coordinator = Coordinator()
        frame = decode(bytes.fromhex(
            "4188f93412ffff45230800fdff45231e7d080013000000000b84452302000000000000028e"))
        coordinator.observe(frame, 100)
        replies = coordinator.replies(frame, 100, self.sequence)
        self.assertEqual(len(replies), 2)
        status, probe = [decode(raw[:-2]) for raw in replies]
        self.assertEqual(status["network_command_payload"], "61452311")
        self.assertEqual(probe["cluster_id"], "0x0021")
        self.assertEqual(probe["profile_id"], "0xc105")
        self.assertEqual(probe["destination_endpoint"], 0xE6)
        self.assertEqual(probe["network_destination_ieee"], INVERTER)
        self.assertEqual(probe["application_payload"], "00000001020000000000000100004149")
        self.assertIsNone(coordinator.periodic(100, self.sequence))
        self.assertEqual(coordinator.replies(frame, 100.5, self.sequence), [])
        # The real subsequent JV request omitted both NWK EUI fields.
        verification = decode(bytes.fromhex(
            "6188fe3412000045234800000045231e81400001000000000c8500000000"))
        ack, response = [decode(raw[:-2]) for raw in coordinator.replies(verification, 103, self.sequence)]
        self.assertEqual(ack["aps_counter"], 12)
        self.assertEqual(response["application_payload"], "850001000000000000020000")

    def test_join_probe_rejects_unrelated_or_invalid_announcements(self):
        for changes in ({"network_source_ieee": COLLECTOR}, {"channel": 15},
                        {"bad_fcs": True}, {"profile_id": "0xc105"},
                        {"source_endpoint": 232}, {"aps_delivery_mode": "unicast"}):
            coordinator = Coordinator()
            self.assertEqual(coordinator.replies(dict(device_announce(), **changes), 100, self.sequence), [])

    def test_association_status_response_is_acknowledged_without_telemetry(self):
        frame = dict(self.neighbor, network_destination_ieee=COLLECTOR,
                     network_destination="0x0000", mac_destination="0x0000",
                     aps_frame_type="data", aps_delivery_mode="unicast", aps_ack_requested=True,
                     profile_id="0xc105", cluster_id="0x00a1", source_endpoint=0xE6,
                     destination_endpoint=0xE6, aps_counter=42, application_payload="0141490000")
        frame.pop("network_command_id")
        replies = self.coordinator.replies(frame, 101, self.sequence)
        ack = decode(replies[0][:-2])
        self.assertEqual(ack["aps_frame_type"], "acknowledgment")
        self.assertEqual(ack["aps_counter"], 42)
        self.assertEqual(ack["cluster_id"], "0x00a1")
        self.assertEqual(ack["network_destination_ieee"], INVERTER)
        for changes in ({"application_payload": "014e57000000"}, {"aps_ack_requested": False},
                        {"network_destination_ieee": INVERTER}, {"bad_fcs": True}):
            self.assertEqual(self.coordinator.replies(dict(frame, **changes), 102, self.sequence), [])

    def test_association_rejects_other_devices_networks_and_unsupported_capabilities(self):
        for changes in ({"mac_source": "0x" + COLLECTOR}, {"bad_fcs": True},
                        {"receive_error": 1}, {"channel": 15}, {"destination_pan_id": "0x4321"},
                        {"source_pan_id": "0x4321"}, {"mac_destination": "0x1111"},
                        {"mac_command_payload": ""}, {"mac_command_payload": "80"},
                        {"mac_command_payload": "be"}, {"mac_command_payload": "8e00"}):
            coordinator = Coordinator()
            self.assertEqual(coordinator.replies(dict(association_request(), **changes), 100, self.sequence), [])
            self.assertFalse(coordinator.association_pending(100))

    def test_association_poll_requires_pending_transaction_and_known_long_address(self):
        coordinator = Coordinator()
        self.assertEqual(coordinator.replies(data_request(), 99, self.sequence), [])
        coordinator.replies(association_request(), 100, self.sequence)
        self.assertEqual(coordinator.replies(dict(data_request(), mac_source="0x2345"), 101, self.sequence), [])
        self.assertEqual(coordinator.replies(data_request(), 160, self.sequence), [])
        self.assertFalse(coordinator.association_pending(160))

    def test_rejoin_preserves_address_without_requiring_recent_traffic(self):
        raw = (struct.pack("<HBHHH", 0x8861, 1, PAN, 0, 0x7362)
               + struct.pack("<HHHBBQBB", 0x1009, 0, 0x7362, 1, 17, int(INVERTER, 16), 6, 0x8E))
        frame = decode(raw)
        coordinator = Coordinator()
        replies = coordinator.replies(frame, 100, self.sequence)
        response = decode(replies[0][:-2])
        self.assertEqual(response["network_command_id"], 7)
        self.assertEqual(response["network_command_payload"], "627300")
        self.assertEqual(response["network_destination_ieee"], INVERTER)
        self.assertEqual(response["network_source_ieee"], COLLECTOR)
        self.assertEqual(response["network_destination"], "0x7362")
        self.assertEqual(response["network_radius"], 1)
        self.assertEqual(coordinator.assigned_address, 0x7362)
        for changes in ({"network_source_ieee": COLLECTOR}, {"network_secured": True},
                        {"network_destination": "0x1111"}, {"mac_destination": "0xffff"},
                        {"network_command_payload": "80"}, {"network_command_payload": ""}):
            self.assertEqual(Coordinator().replies(dict(frame, **changes), 100, self.sequence), [])

    def test_orphan_recovery_uses_persisted_verified_address(self):
        orphan = decode(bytes.fromhex("03c803ffffffffffff020000000000000206"), "command")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            coordinator = Coordinator(path)
            coordinator.observe(device_announce(0x1234), 100)
            restarted = Coordinator(path)
            self.assertIsNone(restarted.neighbor)
            replies = restarted.replies(orphan, 101, self.sequence)
            response = decode(replies[0][:-2], "command")
            self.assertEqual(response["mac_command_id"], 8)
            self.assertEqual(response["mac_command_payload"], "341200000e3412")
            self.assertEqual(response["destination_pan_id"], "0xffff")
            self.assertEqual(response["source_pan_id"], "0x1234")
            self.assertEqual(response["mac_destination"], "0x" + INVERTER)
            self.assertEqual(response["mac_source"], "0x" + COLLECTOR)
        self.assertEqual(Coordinator().replies(dict(orphan, mac_source="0x" + COLLECTOR), 101, self.sequence), [])

    def test_device_announce_binds_only_matching_payload_and_direct_source(self):
        frame = device_announce()
        self.assertNotIn("network_source_ieee", frame)
        self.assertTrue(is_inverter(frame))
        for changes in ({"mac_source": "0x1111"}, {"network_source": "0x1111"},
                        {"network_source_ieee": COLLECTOR}, {"bad_fcs": True},
                        {"aps_secured": True}, {"cluster_id": "0x0000"},
                        {"application_payload": "32452301000000000000028e"},
                        {"application_payload": frame["application_payload"][:-2]}):
            self.assertFalse(is_inverter(dict(frame, **changes)))


if __name__ == "__main__":
    unittest.main()
