"""Verified read-only SunSpec telemetry from this Power-One inverter and meter."""

import json
import sqlite3
import threading
import time

from radio_protocol import signed_16


def decode_details(registers):
    """SunSpec models 1, 101 and 201; offsets include each model's two-word header."""
    result = {}

    def scaled(name, address, sf_address, signed=False):
        raw, sf = registers.get(address), registers.get(sf_address)
        if raw is None or sf is None or raw == (0x8000 if signed else 0xFFFF):
            return
        sf = signed_16(sf)
        if not -10 <= sf <= 10:
            return
        result[name] = round((signed_16(raw) if signed else raw) * 10 ** sf, 6)

    if registers.get(40002) == 1 and registers.get(40003) == 65:
        for name, address, length in (("manufacturer", 40004, 16), ("model", 40020, 16), ("options", 40036, 8),
                                      ("firmware", 40044, 8), ("serial", 40052, 16)):
            if all(i in registers for i in range(address, address + length)):
                text = b"".join(registers[i].to_bytes(2, "big") for i in range(address, address + length))
                result[name] = text.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    if registers.get(40069) == 101 and registers.get(40070) == 50:
        for name, address, sf, signed in (
            ("ac_current_a", 40071, 40075, False), ("ac_voltage_v", 40076, 40082, False),
            ("inverter_ac_w", 40083, 40084, True), ("frequency_hz", 40085, 40086, False),
            ("dc_current_a", 40096, 40097, False), ("dc_voltage_v", 40098, 40099, False),
            ("dc_power_w", 40100, 40101, True), ("cabinet_c", 40102, 40106, True),
            ("heatsink_c", 40103, 40106, True), ("transformer_c", 40104, 40106, True),
        ):
            scaled(name, address, sf, signed)
        states = {1: "Off", 2: "Sleeping", 3: "Starting", 4: "MPPT", 5: "Throttled",
                  6: "Shutting down", 7: "Fault", 8: "Standby"}
        state = registers.get(40107)
        if state in states:
            result["operating_state"] = states[state]
        energy_words = [registers.get(i) for i in (40093, 40094, 40095)]
        if all(v is not None for v in energy_words):
            high, low, sf = energy_words
            energy = (high << 16) | low
            if energy != 0xFFFFFFFF and -10 <= signed_16(sf) <= 10:
                result["inverter_lifetime_wh"] = energy * 10 ** signed_16(sf)
        for name, address in (("fault_bits", 40109), ("event_bits_2", 40111)):
            if address in registers and address + 1 in registers:
                value = (registers[address] << 16) | registers[address + 1]
                if value != 0xFFFFFFFF:
                    result[name] = value
    if registers.get(40342) == 201 and registers.get(40343) == 105:
        for name, address, sf in (
            ("meter_current_a", 40344, 40348), ("meter_voltage_v", 40353, 40357),
            ("meter_frequency_hz", 40358, 40359), ("apparent_va", 40365, 40369),
            ("meter_real_w", 40360, 40364),
            ("reactive_var", 40370, 40374), ("power_factor_pct", 40375, 40379),
        ):
            scaled(name, address, sf, True)
    return result


class TelemetryStore:
    """Append-only diagnostic observations, separate from production energy history."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS inverter_telemetry (
                id INTEGER PRIMARY KEY, capture_id TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, observed_at REAL NOT NULL, registers TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS telemetry_kind_id ON inverter_telemetry(kind, id)")
            rows = db.execute("""SELECT kind, observed_at, registers FROM inverter_telemetry
                WHERE id IN (SELECT MAX(id) FROM inverter_telemetry GROUP BY kind)""").fetchall()
        self.blocks = {kind: (at, {int(k): v for k, v in json.loads(raw).items()})
                       for kind, at, raw in rows}

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, capture_id, kind, observed_at, registers):
        with self.lock:
            with self.connect() as db:
                db.execute("""INSERT OR IGNORE INTO inverter_telemetry
                    (capture_id, kind, observed_at, registers) VALUES (?, ?, ?, ?)""",
                           (capture_id, kind, observed_at, json.dumps(registers)))
            self.blocks[kind] = (observed_at, dict(registers))

    def snapshot(self, stale_after=450):
        with self.lock:
            blocks = dict(self.blocks)
        result = {}
        headers = blocks.get("inverter_ac", (0, {}))[1]
        for kind in ("inverter_ac", "inverter_dc", "meter_ac"):
            at, registers = blocks.get(kind, (None, {}))
            registers = dict(registers)
            if kind == "inverter_dc":
                registers.update({k: headers[k] for k in (40069, 40070) if k in headers})
            result[kind] = self.group(at, decode_details(registers), stale_after)
        first, second = blocks.get("common1"), blocks.get("common2")
        at, values = None, {}
        if first and second and abs(first[0] - second[0]) <= stale_after:
            at = min(first[0], second[0])
            values = decode_details({**first[1], **second[1]})
        result["identity"] = self.group(at, values, stale_after)
        return result

    @staticmethod
    def group(at, values, stale_after):
        return {"observed_at": at, "values": values,
                "stale": at is None or time.time() - at > stale_after}
