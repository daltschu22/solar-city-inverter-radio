#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyspinel @ git+https://github.com/openthread/pyspinel.git@5e0627abd04b7d2c7ca47c6615bd14b31227eb4d",
#   "pyserial==3.5",
# ]
# ///
"""Bounded, read-only solar polling using the existing collector's radio identity."""

import argparse
import json
from pathlib import Path
import socket
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radio_protocol import decode_modbus_message, decode_sunspec_values, modbus_crc16
from tools.smlight_capture import decode_record

from config import CONFIG, require_configured

PAN = CONFIG.pan_id
CHANNEL = CONFIG.channel
INVERTER = CONFIG.inverter_eui
COLLECTOR = CONFIG.collector_eui
READS = {
    "power": (40360, 5), "energy": (40380, 17),
    "common1": (40000, 35), "common2": (40035, 34),
    "inverter_ac": (40069, 27), "inverter_dc": (40096, 25),
    "meter_ac": (40342, 38),
}


def is_inverter(frame):
    ieee = frame.get("network_source_ieee")
    if (ieee is None and frame.get("aps_frame_type") == "data"
            and frame.get("profile_id") == "0x0000" and frame.get("cluster_id") == "0x0013"
            and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0
            and not frame.get("network_secured") and not frame.get("aps_secured")
            and "aps_fragmentation" not in frame):
        # Device_annce carries the identity in its ZDO payload rather than
        # necessarily in the NWK header. Bind it only to its own direct source.
        data = bytes.fromhex(frame.get("application_payload", ""))
        if len(data) == 12 and f"0x{int.from_bytes(data[1:3], 'little'):04x}" == frame.get("network_source"):
            ieee = data[3:11][::-1].hex()
    return (
        frame.get("source_pan_id") == f"0x{PAN:04x}"
        and ieee == INVERTER
        and frame.get("mac_source") == frame.get("network_source")
        and not frame.get("receive_error") and not frame.get("bad_fcs")
    )


def envelope(destination, sequence, aps, discover=True):
    if not 1 <= destination < 0xFFF8:
        raise ValueError("Expected a unicast inverter address")
    mac = struct.pack("<HBHHH", 0x8861, sequence & 255, PAN, destination, 0)
    # Same direct, zero-relay source route and EUI fields as the verified C6 fixture.
    nwk = struct.pack("<HHHBBQQBB", 0x1C48 if discover else 0x1C08,
                      destination, 0, 30, (sequence + 79) & 255,
                      int(INVERTER, 16), int(COLLECTOR, 16), 0, 0)
    # OpenThread's radio driver generates the FCS; the PSDU includes two reserved bytes.
    return mac + nwk + aps + b"\x00\x00"


def read_request(destination, sequence, kind):
    start, count = READS[kind]
    modbus = struct.pack(">BBHH", 1, 3, start, count)
    modbus += modbus_crc16(modbus).to_bytes(2, "little")
    aps = struct.pack("<BBHHBB", 0x40, 0xE8, 0x11, 0xC105, 0xE8, (sequence + 139) & 255)
    return envelope(destination, sequence, aps + modbus)


def response_registers(frame, kind):
    if not (is_inverter(frame) and frame.get("network_destination_ieee") == COLLECTOR
            and frame.get("aps_frame_type") == "data"
            and frame.get("cluster_id") == "0x0011"
            and frame.get("profile_id") == "0xc105"
            and frame.get("source_endpoint") == 0xE8
            and frame.get("destination_endpoint") == 0xE8
            and "aps_fragmentation" not in frame):
        return None
    message = decode_modbus_message(bytes.fromhex(frame.get("application_payload", "")))
    start, count = READS[kind]
    if not (message.get("crc_valid") and message.get("kind") == "response"
            and message.get("unit_id") == 1 and message.get("function") == 3
            and message.get("byte_count") == count * 2):
        return None
    return dict(enumerate(message["register_values"], start))


def response_values(frame, kind):
    registers = response_registers(frame, kind)
    if registers is None:
        return None
    values = decode_sunspec_values(registers)
    if kind not in ("power", "energy"):
        values["registers"] = registers
    return values


def acknowledgment(frame, sequence):
    if not (is_inverter(frame) and frame.get("network_destination_ieee") == COLLECTOR
            and frame.get("aps_frame_type") == "data" and frame.get("aps_ack_requested")
            and frame.get("profile_id") == "0xc105" and frame.get("cluster_id") == "0x0011"
            and "aps_fragmentation" not in frame):
        return None
    aps = struct.pack("<BBHHBB", 2, frame["source_endpoint"], 0x11, 0xC105,
                      frame["destination_endpoint"], frame["aps_counter"])
    return envelope(int(frame["network_source"], 16), sequence, aps, discover=False)


def poll(args):
    require_configured()
    from spinel.codec import WpanApi
    from spinel.const import SPINEL
    from spinel.stream import StreamSocket

    # A second collector using short address zero can interfere with the original.
    # No network formation, joins, inverter writes, or persistent radio changes occur.
    output = args.output.open("x", encoding="utf-8")
    stream = None
    frames, transmissions, samples = [], [], []
    old = {}
    api = None
    pending = None
    sequence = int(time.time()) & 255
    sweep = int(time.time())
    seen = set()
    addressed = False

    def set_property(prop, value, fmt="B"):
        result = api.prop_set_value(prop, value, fmt)
        if result != value:
            raise RuntimeError(f"Radio rejected {prop:#x}={value}: {result}")

    def transmit(frame, kind):
        payload = struct.pack("<H", len(frame)) + frame + bytes([CHANNEL, 4, 0, 1])
        api.prop_change_async(SPINEL.CMD_PROP_VALUE_SET, SPINEL.PROP_STREAM_RAW,
                              payload, f"{len(payload)}s")
        status = api.queue_wait_for_prop(SPINEL.PROP_LAST_STATUS, timeout=3)
        entry = {"kind": kind, "observed_at": time.time(), "raw": frame[:-2].hex(),
                 "status": status.value if status else None}
        transmissions.append(entry)
        print("TX", kind, "status", entry["status"], flush=True)

    def receive(seconds):
        nonlocal pending, sequence
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = api.queue_wait_for_prop(SPINEL.PROP_STREAM_RAW, SPINEL.HEADER_ASYNC, timeout=0.5)
            if not result:
                continue
            length = api.parse_S(result.value)
            if not length:
                continue
            tail = result.value[2 + length:]
            if len(tail) < 19:
                raise ValueError("Truncated Spinel metadata")
            metadata = api.parse_fields(tail[:19], "ccSt(CCX)t(i)")
            frame = decode_record(result.value[2:2 + length], metadata, sweep, time.time())
            frames.append(frame)
            ack = acknowledgment(frame, sequence) if addressed else None
            if ack is not None:
                sequence = (sequence + 1) & 255
                transmit(ack, "aps_ack")
            identity = (frame.get("network_source_ieee"), frame.get("aps_counter"),
                        frame.get("application_payload"))
            if pending and identity not in seen:
                values = response_values(frame, pending)
                if values:
                    sample = {"kind": pending, "observed_at": frame["observed_at"],
                              "rssi": frame["rssi"], "values": values,
                              "registers": response_registers(frame, pending),
                              "radio_timestamp": frame["timestamp"], "capture_sweep": sweep,
                              "raw": frame["raw"]}
                    samples.append(sample)
                    seen.add(identity)
                    pending = None
                    print("READING", json.dumps(sample), flush=True)

    try:
        stream = StreamSocket(args.host, args.port)
        api = WpanApi(stream, "34", timeout=4)
        api.queue_register(SPINEL.HEADER_DEFAULT)
        api.queue_register(SPINEL.HEADER_ASYNC)
        api.cmd_send(SPINEL.CMD_RESET)
        time.sleep(1)
        print("Firmware:", api.prop_get_value(SPINEL.PROP_NCP_VERSION), flush=True)
        for prop, fmt in ((SPINEL.PROP_PHY_TX_POWER, "b"), (SPINEL.PROP_MAC_15_4_PANID, "H"),
                          (SPINEL.PROP_MAC_15_4_SADDR, "H"), (SPINEL.PROP_MAC_FILTER_MODE, "B")):
            old[prop] = (api.prop_get_value(prop), fmt)
        set_property(SPINEL.PROP_PHY_ENABLED, 1)
        set_property(SPINEL.PROP_PHY_CHAN, CHANNEL)
        set_property(SPINEL.PROP_PHY_TX_POWER, 5, "b")
        set_property(SPINEL.PROP_MAC_FILTER_MODE, 2)
        set_property(SPINEL.PROP_MAC_RAW_STREAM_ENABLED, 1)
        receive(30)
        if any(f.get("network_source_ieee") == COLLECTOR and f.get("mac_source") == "0x0000"
               for f in frames):
            raise RuntimeError("The old collector is still broadcasting; power it off before polling")
        set_property(SPINEL.PROP_MAC_15_4_PANID, PAN, "H")
        set_property(SPINEL.PROP_MAC_15_4_SADDR, 0, "H")
        # TI's RCP can hide ACK-requested unicast frames in promiscuous mode.
        set_property(SPINEL.PROP_MAC_FILTER_MODE, 0)
        addressed = True
        for index in range(args.reads):
            live = next((f for f in reversed(frames) if is_inverter(f)
                         and time.time() - f["observed_at"] <= 60), None)
            if live is None:
                raise RuntimeError("No recent inverter EUI; refusing to guess its short address")
            kinds = getattr(args, "kinds", None)
            pending = kinds[index % len(kinds)] if kinds else ("energy" if index % 4 == 2 else "power")
            request = read_request(int(live["network_source"], 16), sequence, pending)
            sequence = (sequence + 1) & 255
            transmit(request, pending)
            receive(args.interval)
            if pending:
                print("No valid", pending, "response", flush=True)
            pending = None
    finally:
        try:
            if api is not None:
                api.prop_set_value(SPINEL.PROP_MAC_RAW_STREAM_ENABLED, 0)
                for prop, (value, fmt) in old.items():
                    if value is not None:
                        api.prop_set_value(prop, value, fmt)
                api.prop_set_value(SPINEL.PROP_PHY_ENABLED, 0)
        finally:
            if api is not None:
                api._reader_alive = False
            if stream is not None:
                try:
                    stream.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.sock.close()
            result = {"received_frames": frames, "transmissions": transmissions, "samples": samples}
            json.dump(result, output, indent=2)
            output.write("\n")
            output.close()
            print(f"Saved {len(samples)} valid readings to {args.output}", flush=True)
    return len(samples) == args.reads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="SMLIGHT hostname or Wi-Fi address")
    parser.add_argument("--port", type=int, default=6638)
    parser.add_argument("--output", type=Path, help="New local JSON evidence file")
    parser.add_argument("--reads", type=int, default=4)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--kinds", nargs="+", choices=tuple(READS))
    parser.add_argument("--send", action="store_true", help="Transmit as the old collector (short address zero)")
    args = parser.parse_args()
    if not 1 <= args.reads <= 120 or not 5 <= args.interval <= 60:
        parser.error("Use 1-120 reads and a 5-60 second interval")
    if not args.send:
        for kind in READS:
            print(kind, read_request(0x4567, 1, kind).hex())
        print("Dry run only. --send requires the original collector to be powered off.")
        return
    if not args.host or not args.output:
        parser.error("--send requires --host and --output")
    raise SystemExit(0 if poll(args) else 1)


if __name__ == "__main__":
    main()
