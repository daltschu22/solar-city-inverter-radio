#!/usr/bin/env python3
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SOLAR_HISTORY_PATH = Path(
    os.environ.get("SOLAR_HISTORY_PATH", ROOT / "data" / "solar-history.sqlite3")
)

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


class SolarHistoryStore:
    RANGE_SECONDS = {
        "1h": 60 * 60,
        "6h": 6 * 60 * 60,
        "24h": 24 * 60 * 60,
        "7d": 7 * 24 * 60 * 60,
        "30d": 30 * 24 * 60 * 60,
        "1y": 365 * 24 * 60 * 60,
        "all": None,
    }
    MAX_CHART_POINTS = 5000

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA synchronous=NORMAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS solar_readings (
                    id INTEGER PRIMARY KEY,
                    capture_id TEXT NOT NULL UNIQUE,
                    observed_at REAL NOT NULL,
                    capture_sweep INTEGER NOT NULL,
                    radio_timestamp INTEGER NOT NULL,
                    solar_w REAL NOT NULL,
                    lifetime_wh REAL
                )
                """
            )
            database.execute(
                """
                CREATE INDEX IF NOT EXISTS solar_readings_observed_at
                ON solar_readings (observed_at)
                """
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, measurements):
        rows = [
            (
                item["capture_id"],
                item["observed_at"],
                item["capture_sweep"],
                item["radio_timestamp"],
                item["solar_w"],
                item.get("lifetime_wh"),
            )
            for item in measurements
        ]
        if not rows:
            return 0
        with self.lock, self.connect() as database:
            before = database.total_changes
            database.executemany(
                """
                INSERT OR IGNORE INTO solar_readings (
                    capture_id,
                    observed_at,
                    capture_sweep,
                    radio_timestamp,
                    solar_w,
                    lifetime_wh
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return database.total_changes - before

    def latest(self):
        with self.lock, self.connect() as database:
            database.row_factory = sqlite3.Row
            row = database.execute(
                """
                SELECT
                    observed_at,
                    capture_sweep,
                    radio_timestamp,
                    solar_w,
                    lifetime_wh
                FROM solar_readings
                ORDER BY observed_at DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def latest_energy(self):
        with self.lock, self.connect() as database:
            row = database.execute(
                "SELECT lifetime_wh FROM solar_readings WHERE lifetime_wh IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def query(self, range_name):
        if range_name not in self.RANGE_SECONDS:
            raise ValueError("Unknown history range")

        duration = self.RANGE_SECONDS[range_name]
        start = time.time() - duration if duration else None
        where = "WHERE observed_at >= ?" if start is not None else ""
        parameters = (start,) if start is not None else ()

        with self.lock, self.connect() as database:
            database.row_factory = sqlite3.Row
            summary = database.execute(
                f"""
                SELECT
                    COUNT(*) AS sample_count,
                    MIN(observed_at) AS first_at,
                    MAX(observed_at) AS last_at,
                    MAX(solar_w) AS peak_w
                FROM solar_readings
                {where}
                """,
                parameters,
            ).fetchone()
            sample_count = int(summary["sample_count"])
            energy_where = f"{where} AND lifetime_wh IS NOT NULL" if where else "WHERE lifetime_wh IS NOT NULL"
            energy_rows = [database.execute(
                f"SELECT observed_at, lifetime_wh FROM solar_readings {energy_where} "
                f"ORDER BY observed_at {order} LIMIT 1", parameters,
            ).fetchone() for order in ("ASC", "DESC")]

            if sample_count <= self.MAX_CHART_POINTS:
                rows = database.execute(
                    f"""
                    SELECT observed_at, solar_w, lifetime_wh
                    FROM solar_readings
                    {where}
                    ORDER BY observed_at ASC
                    """,
                    parameters,
                ).fetchall()
            else:
                chart_start = float(summary["first_at"])
                span = max(1.0, float(summary["last_at"]) - chart_start)
                bucket_seconds = span / max(1, self.MAX_CHART_POINTS - 1)
                bucket_where = (
                    "WHERE observed_at >= ?" if start is not None else ""
                )
                bucket_parameters = (
                    (start, chart_start, bucket_seconds)
                    if start is not None
                    else (chart_start, bucket_seconds)
                )
                rows = database.execute(
                    f"""
                    SELECT
                        AVG(observed_at) AS observed_at,
                        AVG(solar_w) AS solar_w,
                        MAX(lifetime_wh) AS lifetime_wh
                    FROM solar_readings
                    {bucket_where}
                    GROUP BY CAST((observed_at - ?) / ? AS INTEGER)
                    ORDER BY observed_at ASC
                    """,
                    bucket_parameters,
                ).fetchall()

        generated_wh = None
        if (
            energy_rows[0]
            and energy_rows[1]
            and energy_rows[1]["observed_at"] > energy_rows[0]["observed_at"]
        ):
            difference = energy_rows[1]["lifetime_wh"] - energy_rows[0]["lifetime_wh"]
            generated_wh = round(difference, 3) if difference >= 0 else None

        return {
            "range": range_name,
            "retention": "unlimited",
            "sample_count": sample_count,
            "point_count": len(rows),
            "downsampled": sample_count > self.MAX_CHART_POINTS,
            "first_at": summary["first_at"],
            "last_at": summary["last_at"],
            "peak_w": summary["peak_w"],
            "generated_wh": generated_wh,
            "energy_first_at": energy_rows[0]["observed_at"] if energy_rows[0] else None,
            "energy_last_at": energy_rows[1]["observed_at"] if energy_rows[1] else None,
            "points": [
                {
                    "timestamp": row["observed_at"],
                    "solar_w": round(float(row["solar_w"]), 3),
                    "lifetime_wh": row["lifetime_wh"],
                }
                for row in rows
            ],
        }


solar_history = None
smlight_collector = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def end_headers(self):
        if not any(
            header.lower().startswith(b"cache-control:")
            for header in self._headers_buffer
        ):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/healthz":
            return self.send_json(200, {"ok": True})

        if parsed.path == "/api/live":
            if smlight_collector is None or solar_history is None:
                return self.send_json(503, {"error": "Solar poller is starting"})
            return self.send_json(200, smlight_payload())

        if parsed.path == "/api/history":
            if solar_history is None:
                return self.send_json(503, {"error": "History store is starting"})
            range_name = urllib.parse.parse_qs(parsed.query).get("range", ["24h"])[0]
            try:
                history = solar_history.query(range_name)
                if smlight_collector is not None:
                    history["poll_interval_seconds"] = smlight_collector.interval
                return self.send_json(200, history)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})

        if parsed.path.startswith("/api/"):
            return self.send_json(404, {"error": "Unknown endpoint"})

        return super().do_GET()


def smlight_payload():
    status = smlight_collector.snapshot()
    latest = solar_history.latest()
    return {
        "mode": "smlight", "timestamp": latest["observed_at"] if latest else None,
        "source": "SMLIGHT / SunSpec Modbus over XBee",
        "solar_w": latest["solar_w"] if latest else None,
        "solar": {"lifetime_wh": solar_history.latest_energy()},
        "collector": status,
    }


def secure_runtime_file_permissions():
    paths = [SOLAR_HISTORY_PATH]
    paths.extend(
        SOLAR_HISTORY_PATH.parent.glob(f"{SOLAR_HISTORY_PATH.name}-*")
    )
    for path in paths:
        try:
            path.chmod(0o640)
        except FileNotFoundError:
            continue


def main():
    global solar_history, smlight_collector
    from config import require_configured
    try:
        configuration = require_configured()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    host = configuration.host
    latitude, longitude = os.environ.get("SOLAR_LATITUDE"), os.environ.get("SOLAR_LONGITUDE")
    night_schedule = None
    if latitude is not None or longitude is not None:
        from daylight import NightSchedule
        if latitude is None or longitude is None:
            raise SystemExit("Set both SOLAR_LATITUDE and SOLAR_LONGITUDE")
        night_schedule = NightSchedule(float(latitude), float(longitude))
    os.umask(0o027)
    solar_history = SolarHistoryStore(SOLAR_HISTORY_PATH)
    secure_runtime_file_permissions()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((os.environ.get("SOLAR_BIND", "127.0.0.1"), port), Handler)
    server.daemon_threads = True
    shutdown_started = threading.Event()

    def request_shutdown(_signum, _frame):
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    print(f"SolarCity Inverter Radio: http://127.0.0.1:{port}")
    print(f"Solar history: {SOLAR_HISTORY_PATH}")
    from smlight_collector import SmlightCollector
    smlight_collector = SmlightCollector(host, solar_history, port=configuration.port,
                                       night_schedule=night_schedule)
    smlight_collector.start()
    print(f"Radio poller: SMLIGHT at {host}:{configuration.port}")
    try:
        server.serve_forever()
    finally:
        smlight_collector.stop()
        server.server_close()


if __name__ == "__main__":
    main()
