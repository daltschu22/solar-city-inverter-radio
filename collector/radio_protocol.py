"""Offline IEEE 802.15.4, Digi, Modbus and SunSpec decoding."""

import hashlib


ZIGBEE_OUI_VENDORS = {
    "0013a2": "MaxStream / Digi XBee",
    "001788": "Philips Lighting",
}

ZIGBEE_NETWORK_COMMANDS = {
    0x01: "Route Request",
    0x02: "Route Reply",
    0x03: "Network Status",
    0x04: "Leave",
    0x05: "Route Record",
    0x06: "Rejoin Request",
    0x07: "Rejoin Response",
    0x08: "Link Status",
    0x09: "Network Report",
    0x0A: "Network Update",
    0x0B: "End Device Timeout Request",
    0x0C: "End Device Timeout Response",
}

ZIGBEE_APS_FRAME_TYPES = {
    0: "data",
    1: "command",
    2: "acknowledgment",
    3: "inter-PAN",
}

ZIGBEE_APS_DELIVERY_MODES = {
    0: "unicast",
    1: "reserved",
    2: "broadcast",
    3: "group",
}

ZIGBEE_APS_FRAGMENTATION = {
    0: "none",
    1: "first",
    2: "continuation",
    3: "reserved",
}

SUNSPEC_INVERTER_MODEL_START = 40069
SUNSPEC_METER_MODEL_START = 40342


def decode_ieee802154_frame(record):
    item = dict(record)
    try:
        frame = bytes.fromhex(str(item.get("raw", "")))
    except ValueError:
        return item
    if len(frame) < 2:
        return item

    frame_control = int.from_bytes(frame[0:2], "little")
    destination_mode = (frame_control >> 10) & 0x03
    source_mode = (frame_control >> 14) & 0x03
    sequence_suppressed = bool(frame_control & (1 << 8))
    pan_compressed = bool(frame_control & (1 << 6))
    offset = 2 if sequence_suppressed else 3

    def read_address(mode):
        nonlocal offset
        length = 2 if mode == 2 else 8 if mode == 3 else 0
        if not length or offset + length > len(frame):
            return None
        value = int.from_bytes(frame[offset : offset + length], "little")
        offset += length
        return f"0x{value:0{length * 2}x}"

    if destination_mode:
        if offset + 2 > len(frame):
            return item
        item["destination_pan_id"] = f"0x{int.from_bytes(frame[offset:offset + 2], 'little'):04x}"
        offset += 2
        item["mac_destination"] = read_address(destination_mode)

    if source_mode:
        if not pan_compressed:
            if offset + 2 > len(frame):
                return item
            item["source_pan_id"] = f"0x{int.from_bytes(frame[offset:offset + 2], 'little'):04x}"
            offset += 2
        else:
            item["source_pan_id"] = item.get("destination_pan_id")
        item["mac_source"] = read_address(source_mode)

    if item.get("type") == "command" and not frame_control & 8 and offset < len(frame):
        item["mac_command_id"] = frame[offset]
        item["mac_command_payload"] = frame[offset + 1:].hex()

    if offset + 2 > len(frame) or item.get("type") != "data":
        return item

    network_frame_control = int.from_bytes(frame[offset : offset + 2], "little")
    network_version = (network_frame_control >> 2) & 0x0F
    if network_version != 2:
        return item

    network_types = {
        0: "data",
        1: "command",
        2: "inter-PAN",
    }
    item["protocol"] = "zigbee"
    item["network_frame_type"] = network_types.get(
        network_frame_control & 0x03,
        "reserved",
    )
    item["network_secured"] = bool(network_frame_control & (1 << 9))
    network_header_end = offset + 8
    if offset + 8 <= len(frame):
        item["network_destination"] = (
            f"0x{int.from_bytes(frame[offset + 2:offset + 4], 'little'):04x}"
        )
        item["network_source"] = (
            f"0x{int.from_bytes(frame[offset + 4:offset + 6], 'little'):04x}"
        )
        item["network_radius"] = frame[offset + 6]
        item["network_sequence"] = frame[offset + 7]

        if network_frame_control & (1 << 8):
            network_header_end += 1
        if network_frame_control & (1 << 11):
            if network_header_end + 8 > len(frame):
                return item
            item["network_destination_ieee"] = (
                f"{int.from_bytes(frame[network_header_end:network_header_end + 8], 'little'):016x}"
            )
            network_header_end += 8
        if network_frame_control & (1 << 12):
            if network_header_end + 8 > len(frame):
                return item
            source_ieee = (
                f"{int.from_bytes(frame[network_header_end:network_header_end + 8], 'little'):016x}"
            )
            item["network_source_ieee"] = source_ieee
            item["network_source_vendor"] = ZIGBEE_OUI_VENDORS.get(
                source_ieee[:6],
                "",
            )
            network_header_end += 8
        if network_frame_control & (1 << 10):
            if network_header_end + 2 > len(frame):
                return item
            relay_count = frame[network_header_end]
            network_header_end += 2 + relay_count * 2

        if (
            not item["network_secured"]
            and item["network_frame_type"] == "command"
            and network_header_end < len(frame)
        ):
            command_id = frame[network_header_end]
            item["network_command_id"] = command_id
            item["network_command_payload"] = frame[network_header_end + 1:].hex()
            item["network_command"] = ZIGBEE_NETWORK_COMMANDS.get(
                command_id,
                f"Command 0x{command_id:02x}",
            )

        if (
            not item["network_secured"]
            and item["network_frame_type"] == "data"
            and network_header_end < len(frame)
        ):
            payload = frame[network_header_end:]
            item["network_payload"] = payload.hex()
            aps_control = payload[0]
            aps_type_id = aps_control & 0x03
            aps_delivery_id = (aps_control >> 2) & 0x03
            aps_ack_format = bool(aps_control & (1 << 4))
            item["aps_frame_type"] = ZIGBEE_APS_FRAME_TYPES.get(
                aps_type_id,
                "reserved",
            )
            item["aps_delivery_mode"] = ZIGBEE_APS_DELIVERY_MODES.get(
                aps_delivery_id,
                "reserved",
            )
            item["aps_secured"] = bool(aps_control & (1 << 5))
            item["aps_ack_requested"] = bool(aps_control & (1 << 6))
            aps_offset = 1

            if aps_type_id == 1 and aps_offset + 2 <= len(payload):
                item["aps_counter"] = payload[aps_offset]
                item["aps_command_id"] = payload[aps_offset + 1]
            elif aps_type_id == 2 and aps_ack_format:
                if aps_offset < len(payload):
                    item["aps_counter"] = payload[aps_offset]
            elif aps_type_id in (0, 2):
                if aps_delivery_id in (0, 2):
                    if aps_offset >= len(payload):
                        return item
                    item["destination_endpoint"] = payload[aps_offset]
                    aps_offset += 1
                elif aps_delivery_id == 3:
                    if aps_offset + 2 > len(payload):
                        return item
                    item["group_address"] = (
                        f"0x{int.from_bytes(payload[aps_offset:aps_offset + 2], 'little'):04x}"
                    )
                    aps_offset += 2

                if aps_offset + 6 > len(payload):
                    return item
                cluster_id = int.from_bytes(
                    payload[aps_offset : aps_offset + 2],
                    "little",
                )
                profile_id = int.from_bytes(
                    payload[aps_offset + 2 : aps_offset + 4],
                    "little",
                )
                item["cluster_id"] = f"0x{cluster_id:04x}"
                item["profile_id"] = f"0x{profile_id:04x}"
                item["source_endpoint"] = payload[aps_offset + 4]
                item["aps_counter"] = payload[aps_offset + 5]
                aps_offset += 6

                if profile_id == 0xC105:
                    item["profile_name"] = "Digi XBee"
                if profile_id == 0xC105 and cluster_id == 0x0011:
                    item["cluster_name"] = "Transparent data"
                    item["application_protocol"] = "digi-transparent"

                if aps_control & (1 << 7) and aps_offset < len(payload):
                    extended_control = payload[aps_offset]
                    fragmentation = extended_control & 0x03
                    item["aps_fragmentation"] = ZIGBEE_APS_FRAGMENTATION.get(
                        fragmentation,
                        "reserved",
                    )
                    aps_offset += 1
                    if fragmentation in (1, 2) and aps_offset < len(payload):
                        if fragmentation == 1:
                            item["aps_block_count"] = payload[aps_offset]
                        else:
                            item["aps_block_number"] = payload[aps_offset]
                        aps_offset += 1
                    if (
                        aps_type_id == 2
                        and fragmentation in (1, 2)
                        and aps_offset < len(payload)
                    ):
                        item["aps_ack_bitfield"] = payload[aps_offset]
                        aps_offset += 1

                if aps_type_id == 0:
                    item["application_payload"] = payload[aps_offset:].hex()
    return item


def modbus_crc16(payload):
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def decode_modbus_message(payload):
    item = {"raw": payload.hex(), "length": len(payload)}
    if len(payload) < 4:
        return item

    item["crc_valid"] = (
        modbus_crc16(payload[:-2]).to_bytes(2, "little") == payload[-2:]
    )
    item["unit_id"] = payload[0]
    item["function"] = payload[1]
    if not item["crc_valid"]:
        return item

    if payload[1] == 0x03 and len(payload) == 8:
        item["kind"] = "request"
        item["register_start"] = int.from_bytes(payload[2:4], "big")
        item["register_count"] = int.from_bytes(payload[4:6], "big")
    elif (
        payload[1] == 0x03
        and len(payload) >= 5
        and payload[2] + 5 == len(payload)
        and payload[2] % 2 == 0
    ):
        item["kind"] = "response"
        item["byte_count"] = payload[2]
        item["register_values"] = [
            int.from_bytes(payload[offset : offset + 2], "big")
            for offset in range(3, len(payload) - 2, 2)
        ]
    elif payload[1] & 0x80 and len(payload) == 5:
        item["kind"] = "exception"
        item["exception_code"] = payload[2]
    return item


def reassemble_digi_messages(frames):
    ordered = sorted(
        frames,
        key=lambda frame: (
            int(frame.get("capture_sweep", 0)),
            int(frame.get("timestamp", 0)),
        ),
    )
    fragments = {}
    messages = []
    for frame in ordered:
        if (
            frame.get("application_protocol") != "digi-transparent"
            or frame.get("aps_frame_type") != "data"
            or not frame.get("application_payload")
        ):
            continue
        try:
            payload = bytes.fromhex(frame["application_payload"])
        except ValueError:
            continue

        key = (
            int(frame.get("capture_sweep", 0)),
            frame.get("network_source_ieee"),
            frame.get("network_destination_ieee"),
            int(frame.get("aps_counter", -1)),
        )
        fragmentation = frame.get("aps_fragmentation", "none")
        if fragmentation == "first":
            fragments[key] = {
                "expected": int(frame.get("aps_block_count", 0)),
                "parts": {0: payload},
                "frame": frame,
            }
        elif fragmentation == "continuation":
            group = fragments.get(key)
            block_number = int(frame.get("aps_block_number", -1))
            if group and block_number >= 1:
                group["parts"][block_number] = payload
        else:
            messages.append(
                {
                    "capture_sweep": int(frame.get("capture_sweep", 0)),
                    "timestamp": int(frame.get("timestamp", 0)),
                    "observed_at": float(frame.get("last_seen", 0)),
                    "source_ieee": frame.get("network_source_ieee"),
                    "destination_ieee": frame.get("network_destination_ieee"),
                    "rssi": frame.get("rssi"),
                    "fragment_count": 1,
                    "payload": payload,
                }
            )

    for group in fragments.values():
        expected = group["expected"]
        if expected < 1 or any(index not in group["parts"] for index in range(expected)):
            continue
        frame = group["frame"]
        messages.append(
            {
                "capture_sweep": int(frame.get("capture_sweep", 0)),
                "timestamp": int(frame.get("timestamp", 0)),
                "observed_at": float(frame.get("last_seen", 0)),
                "source_ieee": frame.get("network_source_ieee"),
                "destination_ieee": frame.get("network_destination_ieee"),
                "rssi": frame.get("rssi"),
                "fragment_count": expected,
                "payload": b"".join(
                    group["parts"][index] for index in range(expected)
                ),
            }
        )

    messages.sort(
        key=lambda message: (
            message["capture_sweep"],
            message["timestamp"],
        )
    )
    unique = []
    seen = set()
    for message in messages:
        key = (
            message["capture_sweep"],
            message["source_ieee"],
            message["destination_ieee"],
            message["payload"],
        )
        if key in seen:
            continue
        seen.add(key)
        decoded = decode_modbus_message(message.pop("payload"))
        unique.append({**message, **decoded})
    return unique


def signed_16(value):
    return value - 0x10000 if value & 0x8000 else value


def decode_sunspec_values(registers):
    def scaled(value_address, scale_address, signed=False):
        if value_address not in registers or scale_address not in registers:
            return None
        raw_value = registers[value_address]
        raw_scale = registers[scale_address]
        if raw_value in (0x8000, 0xFFFF) or raw_scale == 0x8000:
            return None
        value = signed_16(raw_value) if signed else raw_value
        return value * (10 ** signed_16(raw_scale))

    values = {}
    inverter_w = scaled(
        SUNSPEC_INVERTER_MODEL_START + 14,
        SUNSPEC_INVERTER_MODEL_START + 15,
        signed=True,
    )
    meter_w = scaled(
        SUNSPEC_METER_MODEL_START + 18,
        SUNSPEC_METER_MODEL_START + 22,
        signed=True,
    )
    solar_w = meter_w if meter_w is not None else inverter_w
    if solar_w is not None:
        values["solar_w"] = round(solar_w)
        values["solar_w_precise"] = solar_w
        values["power_model"] = 201 if meter_w is not None else 101

    dc_w = scaled(
        SUNSPEC_INVERTER_MODEL_START + 31,
        SUNSPEC_INVERTER_MODEL_START + 32,
        signed=True,
    )
    if dc_w is not None:
        values["dc_w"] = round(dc_w)

    wh_high = registers.get(SUNSPEC_INVERTER_MODEL_START + 24)
    wh_low = registers.get(SUNSPEC_INVERTER_MODEL_START + 25)
    wh_scale = registers.get(SUNSPEC_INVERTER_MODEL_START + 26)
    if (
        wh_high is not None
        and wh_low is not None
        and wh_scale not in (None, 0x8000)
        and (wh_high, wh_low) != (0xFFFF, 0xFFFF)
    ):
        values["lifetime_wh"] = round(
            ((wh_high << 16) | wh_low) * (10 ** signed_16(wh_scale)),
            3,
        )

    meter_wh_high = registers.get(SUNSPEC_METER_MODEL_START + 38)
    meter_wh_low = registers.get(SUNSPEC_METER_MODEL_START + 39)
    meter_wh_scale = registers.get(SUNSPEC_METER_MODEL_START + 54)
    if (
        meter_wh_high is not None
        and meter_wh_low is not None
        and meter_wh_scale not in (None, 0x8000)
        and (meter_wh_high, meter_wh_low) != (0xFFFF, 0xFFFF)
    ):
        values["lifetime_wh"] = round(
            ((meter_wh_high << 16) | meter_wh_low)
            * (10 ** signed_16(meter_wh_scale)),
            3,
        )
    return values


def build_modbus_transactions(frames):
    messages = reassemble_digi_messages(frames)
    pending = []
    transactions = []
    registers = {}
    latest_timestamp = None

    for message in messages:
        if message.get("kind") == "request":
            pending.append(message)
            transactions.append({"request": message})
            continue
        if message.get("kind") != "response":
            continue

        match = None
        for request in reversed(pending):
            if (
                request.get("register_count") * 2 == message.get("byte_count")
                and request.get("source_ieee") == message.get("destination_ieee")
                and request.get("destination_ieee") == message.get("source_ieee")
            ):
                match = request
                break
        if match is None:
            transactions.append({"response": message})
            continue

        pending.remove(match)
        transaction = next(
            item for item in reversed(transactions) if item.get("request") is match
        )
        transaction["response"] = message
        start = match["register_start"]
        for offset, value in enumerate(message["register_values"]):
            registers[start + offset] = value
        latest_timestamp = (
            message.get("capture_sweep", 0),
            message.get("timestamp", 0),
        )

    values = decode_sunspec_values(registers)
    if latest_timestamp:
        values["capture_sweep"], values["radio_timestamp"] = latest_timestamp
    return messages, transactions, registers, values


def extract_solar_measurements(transactions):
    measurements = []
    for transaction in transactions:
        request = transaction.get("request", {})
        response = transaction.get("response", {})
        if (
            not response
            or request.get("kind") != "request"
            or response.get("kind") != "response"
        ):
            continue

        start = int(request.get("register_start", 0))
        registers = {
            start + offset: value
            for offset, value in enumerate(response.get("register_values", []))
        }
        values = decode_sunspec_values(registers)
        if values.get("solar_w_precise") is None:
            continue

        observed_at = float(response.get("observed_at", 0))
        if observed_at <= 0:
            continue
        identity = "|".join(
            (
                f"{observed_at:.6f}",
                str(response.get("capture_sweep", 0)),
                str(response.get("timestamp", 0)),
                str(response.get("raw", "")),
            )
        )
        measurements.append(
            {
                "capture_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "observed_at": observed_at,
                "capture_sweep": int(response.get("capture_sweep", 0)),
                "radio_timestamp": int(response.get("timestamp", 0)),
                "solar_w": float(values["solar_w_precise"]),
                "lifetime_wh": values.get("lifetime_wh"),
            }
        )
    return measurements
