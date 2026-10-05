"""Coordinator discovery and recovery for the existing, single-inverter PAN.

Only the known inverter may associate, rejoin, or recover its network address.
This implements its unsecured Digi network, not a general Zigbee stack.
"""

from contextlib import closing
import sqlite3
import struct

from tools.smlight_poll import CHANNEL, COLLECTOR, INVERTER, PAN, envelope, is_inverter


from config import CONFIG

EXTENDED_PAN = CONFIG.extended_pan_id


def network_frame(destination, sequence, payload, command=False, broadcast=False, radius=None, discover=False):
    mac = struct.pack("<HBHHH", 0x8841 if broadcast else 0x8861,
                      sequence & 255, PAN, 0xFFFF if broadcast else destination, 0)
    control = (0x1008 if broadcast else 0x1808) | int(command) | (0x40 if discover else 0)
    nwk = struct.pack("<HHHBB", control, destination, 0,
                      radius if radius is not None else (1 if broadcast else 30),
                      (sequence + 79) & 255)
    if not broadcast:
        nwk += bytes.fromhex(INVERTER)[::-1]
    nwk += bytes.fromhex(COLLECTOR)[::-1]
    return mac + nwk + payload + b"\0\0"


def beacon(sequence):
    # Legacy stack-profile-zero descriptor with configurable network identity.
    return (struct.pack("<HBHHHBB", 0x8000, sequence & 255, PAN, 0, 0xCFFF, 0, 0)
            + struct.pack("<BBBQ", 0, 0x20, 0x84, EXTENDED_PAN)
            + b"\xff\xff\xff\0\0\0")


def link_status(sequence, neighbor=None):
    payload = b"\x08\x60"
    if neighbor is not None:
        # Report the direct neighbor only while receiving valid packets from it.
        payload = b"\x08\x61" + struct.pack("<HB", neighbor, 0x11)
    return network_frame(0xFFFC, sequence, payload, command=True, broadcast=True)


def association_response(sequence, address):
    mac = struct.pack("<HBHQQ", 0xCC63, sequence & 255, PAN,
                      int(INVERTER, 16), int(COLLECTOR, 16))
    return mac + struct.pack("<BHB", 2, address, 0) + b"\0\0"


def orphan_response(sequence, address):
    mac = struct.pack("<HBHQHQ", 0xCC23, sequence & 255, 0xFFFF,
                      int(INVERTER, 16), PAN, int(COLLECTOR, 16))
    return mac + struct.pack("<BHHBH", 8, PAN, 0, CHANNEL, address) + b"\0\0"


def association_probe(sequence, address):
    # Digi DDO: parameterless AI (Association Indication) GET. The options
    # field is zero and there is no parameter or apply/write command.
    payload = struct.pack(">HBB8sH2s", 0, 0, 1, bytes.fromhex(COLLECTOR), 0, b"AI")
    aps = struct.pack("<BBHHBB", 0x40, 0xE6, 0x21, 0xC105, 0xE6, sequence)
    return envelope(address, sequence, aps + payload)


def inverter_left(frame):
    return (is_inverter(frame) and frame.get("channel") == CHANNEL
            and not frame.get("network_secured")
            and frame.get("network_command_id") == 4
            and frame.get("network_command_payload") in {"00", "20", "80", "a0"})


class Coordinator:
    def __init__(self, history_path=None):
        self.neighbor = None
        self.history_path = history_path
        # Last address observed with the original collector. Subsequent verified
        # addresses persist across application and bridge restarts.
        self.assigned_address = CONFIG.initial_address
        if history_path is not None:
            with closing(sqlite3.connect(history_path)) as database, database:
                database.execute("CREATE TABLE IF NOT EXISTS solar_radio_node ("
                                 "ieee TEXT PRIMARY KEY, address INTEGER NOT NULL "
                                 "CHECK(address BETWEEN 1 AND 65527))")
                row = database.execute("SELECT address FROM solar_radio_node WHERE ieee=?", (INVERTER,)).fetchone()
                if row:
                    self.assigned_address = row[0]
                else:
                    database.execute("INSERT INTO solar_radio_node VALUES (?, ?)", (INVERTER, self.assigned_address))
        self.association_until = float("-inf")
        self.last_join_reply = float("-inf")
        self.last_seen = float("-inf")
        self.last_beacon = float("-inf")
        self.last_address_reply = float("-inf")
        self.last_application_reply = float("-inf")
        self.last_announcement = None
        self.next_link_status = 0

    def observe(self, frame, now):
        if inverter_left(frame):
            self.neighbor = None
            self.last_seen = float("-inf")
            self.association_until = float("-inf")
            self.last_announcement = None
            self.last_application_reply = float("-inf")
            self.next_link_status = now
            return
        if is_inverter(frame):
            address = int(frame["network_source"], 16)
            if self.neighbor != address or now - self.last_seen > 60:
                # Establish the bidirectional link immediately after a join;
                # startup verification must not wait for the 15-second timer.
                self.next_link_status = now
            self.neighbor = address
            self.last_seen = now
            self.remember_address(self.neighbor)
            self.association_until = float("-inf")

    def remember_address(self, address):
        if not 0 < address < 0xFFF8:
            return
        if address != self.assigned_address and self.history_path is not None:
            with closing(sqlite3.connect(self.history_path)) as database, database:
                database.execute("INSERT INTO solar_radio_node VALUES (?, ?) "
                                 "ON CONFLICT(ieee) DO UPDATE SET address=excluded.address", (INVERTER, address))
        self.assigned_address = address

    def association_pending(self, now):
        return now < self.association_until

    def trusted(self, frame, now):
        return (
            self.neighbor is not None and now - self.last_seen <= 60
            and frame.get("source_pan_id") == f"0x{PAN:04x}"
            and frame.get("network_source") == f"0x{self.neighbor:04x}"
            and frame.get("mac_source") == frame.get("network_source")
            and frame.get("network_source_ieee", INVERTER) == INVERTER
            and not frame.get("network_secured") and not frame.get("aps_secured")
            and not frame.get("receive_error") and not frame.get("bad_fcs")
        )

    def replies(self, frame, now, next_sequence):
        if (frame.get("type") == "command" and frame.get("mac_command_id") == 7
                and frame.get("mac_command_payload") == ""
                and frame.get("destination_pan_id") == "0xffff"
                and frame.get("mac_destination") == "0xffff"
                and frame.get("channel") == CHANNEL
                and not frame.get("receive_error") and not frame.get("bad_fcs")
                and now - self.last_beacon >= 0.1):
            self.last_beacon = now
            return [beacon(next_sequence())]
        if frame.get("type") == "command":
            return self.mac_recovery(frame, now, next_sequence)
        if self.application_hello(frame):
            if now - self.last_application_reply < 1:
                return []
            self.last_application_reply = now
            sequence = next_sequence()
            # Observed application startup response. An APS delivery
            # acknowledgment alone does not complete this exchange.
            # Request an APS ACK, with a fresh counter, as the reference does.
            aps = struct.pack("<BBHHBB", 0x40, 0xE8, 0x11, 0xC105, 0xE8, sequence)
            return [network_frame(int(frame["network_source"], 16), sequence,
                                  aps + bytes.fromhex("f5000000"), discover=True)]
        if (is_inverter(frame) and frame.get("channel") == CHANNEL
                and frame.get("profile_id") == "0x0000" and frame.get("cluster_id") == "0x0013"
                and frame.get("aps_delivery_mode") == "broadcast"
                and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0
                and not frame.get("network_secured") and not frame.get("aps_secured")
                and "aps_fragmentation" not in frame):
            identity = (frame.get("network_source"), frame.get("network_sequence"),
                        frame.get("aps_counter"), frame.get("application_payload"))
            if identity == self.last_announcement:
                return []
            self.last_announcement = identity
            address = int(frame["network_source"], 16)
            if not 0 < address < 0xFFF8:
                return []
            # The successful Tesla-free join sent link status and a direct
            # read-only probe immediately, before answering the inverter's JV
            # request. Do this once per announcement, not on the poll timer.
            self.next_link_status = now + 15
            return [link_status(next_sequence(), address), association_probe(next_sequence(), address)]
        if (is_inverter(frame) and frame.get("channel") == CHANNEL
                and frame.get("network_destination_ieee") == COLLECTOR
                and frame.get("network_destination") == "0x0000"
                and frame.get("mac_destination") == "0x0000"
                and frame.get("aps_frame_type") == "data" and frame.get("aps_delivery_mode") == "unicast"
                and frame.get("aps_ack_requested") and not frame.get("network_secured")
                and not frame.get("aps_secured") and "aps_fragmentation" not in frame
                and frame.get("profile_id") == "0xc105" and frame.get("cluster_id") == "0x00a1"
                and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0xE6):
            data = bytes.fromhex(frame.get("application_payload", ""))
            if data[:3] == b"\x01AI" and len(data) in (4, 5):
                aps = struct.pack("<BBHHBB", 2, 0xE6, 0xA1, 0xC105, 0xE6, frame["aps_counter"])
                return [network_frame(int(frame["network_source"], 16), next_sequence(), aps)]
        if frame.get("network_command_id") == 6:
            return self.rejoin(frame, now, next_sequence)
        if frame.get("network_command_id") == 1:
            return self.route_reply(frame, next_sequence) if self.trusted(frame, now) else []
        return self.address_reply(frame, now, next_sequence)

    @staticmethod
    def application_hello(frame):
        return (is_inverter(frame) and frame.get("channel") == CHANNEL
                and frame.get("network_destination_ieee") == COLLECTOR
                and frame.get("network_destination") == frame.get("mac_destination") == "0x0000"
                and frame.get("aps_frame_type") == "data" and frame.get("aps_delivery_mode") == "unicast"
                and frame.get("aps_ack_requested") and not frame.get("network_secured")
                and not frame.get("aps_secured") and "aps_fragmentation" not in frame
                and frame.get("profile_id") == "0xc105" and frame.get("cluster_id") == "0x0011"
                and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0xE8
                and frame.get("application_payload") == "f400010101")

    def mac_recovery(self, frame, now, next_sequence):
        if not (frame.get("channel") == CHANNEL and not frame.get("receive_error")
                and not frame.get("bad_fcs") and frame.get("mac_source") == "0x" + INVERTER):
            return []
        command, payload = frame.get("mac_command_id"), frame.get("mac_command_payload", "")
        if (command == 6 and payload == "" and frame.get("source_pan_id") == "0xffff"
                and frame.get("destination_pan_id") == "0xffff" and frame.get("mac_destination") == "0xffff"
                and now - self.last_join_reply >= 0.1):
            self.last_join_reply = now
            return [orphan_response(next_sequence(), self.assigned_address)]
        if not (frame.get("destination_pan_id") == f"0x{PAN:04x}"
                and frame.get("source_pan_id") in {"0xffff", f"0x{PAN:04x}"}
                and frame.get("mac_destination") in {"0x0000", "0x" + COLLECTOR}):
            return []
        if command == 1:
            capabilities = bytes.fromhex(payload)
            # This inverter is an always-listening router requesting an address.
            if len(capabilities) == 1 and capabilities[0] & 0xBA == 0x8A:
                self.association_until = now + 60
            return []
        if (command == 4 and payload == "" and self.association_pending(now)
                and now - self.last_join_reply >= 0.1):
            # IEEE association is indirect: queue on Association Request, then
            # deliver after Data Request. Keep it retryable until the inverter
            # announces itself (or the transaction expires).
            self.last_join_reply = now
            return [association_response(next_sequence(), self.assigned_address)]
        return []

    def rejoin(self, frame, now, next_sequence):
        if not (is_inverter(frame) and frame.get("channel") == CHANNEL
                and not frame.get("network_secured")
                and frame.get("mac_destination") == "0x0000"
                and frame.get("network_destination") == "0x0000"
                and frame.get("network_destination_ieee", COLLECTOR) == COLLECTOR
                and now - self.last_join_reply >= 0.1):
            return []
        data = bytes.fromhex(frame.get("network_command_payload", ""))
        address = int(frame["network_source"], 16)
        if len(data) != 1 or data[0] & 0x3A != 0x0A or not 0 < address < 0xFFF8:
            return []
        self.last_join_reply = now
        self.remember_address(address)
        # Preserve the existing address on this single-device network. Both EUI
        # fields identify the peer and parent, as required for a rejoin response.
        payload = struct.pack("<BHB", 7, address, 0)
        return [network_frame(address, next_sequence(), payload, command=True, radius=1)]

    def address_reply(self, frame, now, next_sequence):
        # A startup verification request may omit the source EUI and arrive
        # before any identifiable link status. Discovery returns our public
        # address without learning a neighbor or authorizing telemetry. Replies
        # still carry the known inverter's destination EUI at the NWK layer.
        source = frame.get("network_source", "")
        if not (frame.get("channel") == CHANNEL
                and frame.get("source_pan_id") == f"0x{PAN:04x}"
                and source == frame.get("mac_source")
                and len(source) == 6 and source.startswith("0x")
                and all(char in "0123456789abcdef" for char in source[2:])
                and 0 < int(source, 16) < 0xFFF8
                and frame.get("network_source_ieee", INVERTER) == INVERTER
                and frame.get("mac_destination") in {"0x0000", "0xffff"}
                and frame.get("network_destination") in {"0x0000", "0xfffc", "0xfffd", "0xffff"}
                and frame.get("network_destination_ieee", COLLECTOR) == COLLECTOR
                and not frame.get("network_secured") and not frame.get("aps_secured")
                and not frame.get("receive_error") and not frame.get("bad_fcs")
                and now - self.last_address_reply >= 1
                and frame.get("aps_frame_type") == "data" and frame.get("profile_id") == "0x0000"
                and frame.get("aps_delivery_mode") in {"unicast", "broadcast"}
                and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0
                and "aps_fragmentation" not in frame):
            return []
        data = bytes.fromhex(frame.get("application_payload", ""))
        cluster = frame.get("cluster_id")
        if cluster == "0x0001" and len(data) == 5 and data[1:3] == b"\0\0":
            request_type, start = data[3:5]
            response_cluster = 0x8001
        elif (cluster == "0x0000" and len(data) == 11
              and data[1:9] == bytes.fromhex(COLLECTOR)[::-1]):
            request_type, start = data[9:11]
            response_cluster = 0x8000
        else:
            return []
        if request_type not in (0, 1):
            return []
        self.last_address_reply = now
        destination = int(source, 16)
        response = bytes([data[0], 0]) + bytes.fromhex(COLLECTOR)[::-1] + b"\0\0"
        if request_type == 1:
            response += bytes([0, start])  # No end-device children.
        frames = []
        if frame.get("aps_ack_requested") and frame.get("aps_delivery_mode") == "unicast":
            aps = struct.pack("<BBHHBB", 2, 0, int(cluster, 16), 0, 0, frame["aps_counter"])
            frames.append(network_frame(destination, next_sequence(), aps))
        sequence = next_sequence()
        aps = struct.pack("<BBHHBB", 0, 0, response_cluster, 0, 0, sequence)
        frames.append(network_frame(destination, sequence, aps + response))
        return frames

    def route_reply(self, frame, next_sequence):
        data = bytes.fromhex(frame.get("network_command_payload", ""))
        # Only direct, ordinary requests for this coordinator, not many-to-one
        # or multicast route discovery. Optional destination EUI must match.
        if not (len(data) in (5, 13) and data[0] in (0, 0x20)
                and len(data) == (13 if data[0] & 0x20 else 5) and data[2:4] == b"\0\0"):
            return []
        if len(data) == 13 and data[5:] != bytes.fromhex(COLLECTOR)[::-1]:
            return []
        payload = struct.pack("<BBBHHB", 2, 0x30, data[1], self.neighbor, 0, min(data[4] + 1, 255))
        payload += bytes.fromhex(INVERTER)[::-1] + bytes.fromhex(COLLECTOR)[::-1]
        return [network_frame(self.neighbor, next_sequence(), payload, command=True)]

    def periodic(self, now, next_sequence):
        if now < self.next_link_status:
            return None
        self.next_link_status = now + 15
        neighbor = self.neighbor if now - self.last_seen <= 60 else None
        return link_status(next_sequence(), neighbor)
