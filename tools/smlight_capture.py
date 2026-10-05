#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyspinel @ git+https://github.com/openthread/pyspinel.git@5e0627abd04b7d2c7ca47c6615bd14b31227eb4d",
#   "pyserial==3.5",
#   "scapy==2.7.0",
# ]
# ///
"""Bounded raw-radio capture through a SMLIGHT running OpenThread RCP firmware."""

import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radio_protocol import build_modbus_transactions, decode_ieee802154_frame


def decode_record(packet, metadata, sweep, observed_at):
    if len(packet) < 5:
        raise ValueError("Truncated 802.15.4 frame")
    error = metadata[4][0]
    # Pyspinel represents a packed integer as (value, encoded_length).
    if isinstance(error, tuple):
        error = error[0]
    record = {
        "raw": packet[:-2].hex(),
        "type": {0: "beacon", 1: "data", 2: "ack", 3: "command"}.get(packet[0] & 7, "other"),
        "rssi": metadata[0],
        "lqi": metadata[3][1],
        "channel": metadata[3][0],
        "timestamp": metadata[3][2],
        "capture_sweep": sweep,
        "observed_at": observed_at,
        "receive_error": error,
        "bad_fcs": bool(metadata[2] & 4),
    }
    # The radio supplies FCS bytes, but the existing solar decoder expects none.
    return decode_ieee802154_frame(record) if not error and not record["bad_fcs"] else record


def summarize(frames):
    valid = [f for f in frames if not f.get("receive_error") and not f.get("bad_fcs")]
    messages, transactions, _, values = build_modbus_transactions(valid)
    counts = Counter((f["channel"], f.get("source_pan_id", "none"), f["type"]) for f in frames)
    return {
        "frames": len(frames),
        "digi_serial_frames": sum(f.get("application_protocol") == "digi-transparent" for f in valid),
        "by_channel_pan_type": [
            {"channel": c, "pan": p, "type": t, "count": n}
            for (c, p, t), n in sorted(counts.items())
        ],
        "valid_modbus_messages": sum(bool(m.get("crc_valid")) for m in messages),
        "complete_transactions": sum("request" in t and "response" in t for t in transactions),
        "values": values,
    }


def beacon_request(api, channel, sequence):
    from scapy.layers.dot15d4 import Dot15d4Cmd, Dot15d4FCS
    from spinel.const import SPINEL

    frame = bytes(
        Dot15d4FCS(fcf_frametype=3, fcf_destaddrmode=2, fcf_srcaddrmode=0,
                   fcf_ackreq=0, seqnum=sequence)
        / Dot15d4Cmd(dest_panid=0xFFFF, dest_addr=0xFFFF, cmd_id=7)
    )
    # Spinel TX metadata: channel, CCA backoffs, retries, CSMA enabled.
    payload = struct.pack("<H", len(frame)) + frame + bytes([channel, 4, 0, 1])
    api.prop_change_async(SPINEL.CMD_PROP_VALUE_SET, SPINEL.PROP_STREAM_RAW,
                          payload, f"{len(payload)}s")
    status = api.queue_wait_for_prop(SPINEL.PROP_LAST_STATUS, timeout=2)
    print(f"Beacon request channel {channel}: status {status.value if status else 'timeout'}", flush=True)


def capture(args):
    from spinel.codec import WpanApi
    from spinel.const import SPINEL
    from spinel.pcap import PcapCodec
    from spinel.stream import StreamSocket

    print("Warning: TI RCP promiscuous mode can omit ACK-requested unicasts; "
          "no captured Modbus replies does not prove no replies were transmitted.", flush=True)
    paths = {kind: Path(f"{args.output}.{kind}") for kind in ("pcap", "json", "summary.json")}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Choose a new output prefix; existing captures will not be overwritten")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    channels = args.channels * args.rounds
    sweep = int(time.time())
    stream = StreamSocket(args.host, args.port)
    api = WpanApi(stream, "34", timeout=4)
    api.queue_register(SPINEL.HEADER_DEFAULT)
    api.queue_register(SPINEL.HEADER_ASYNC)
    pcap = PcapCodec()

    def set_property(prop, value):
        result = api.prop_set_value(prop, value)
        if result != value:
            raise RuntimeError(f"Radio rejected property {prop:#x}={value}: {result}")

    try:
        api.cmd_send(SPINEL.CMD_RESET)
        time.sleep(1)
        print("Radio firmware:", api.prop_get_value(SPINEL.PROP_NCP_VERSION), flush=True)
        set_property(SPINEL.PROP_PHY_ENABLED, 1)
        set_property(SPINEL.PROP_MAC_FILTER_MODE, SPINEL.MAC_FILTER_MODE_MONITOR)
        with paths["pcap"].open("xb") as output:
            output.write(pcap.encode_header(283))
            output.flush()
            for index, channel in enumerate(channels):
                set_property(SPINEL.PROP_MAC_RAW_STREAM_ENABLED, 0)
                set_property(SPINEL.PROP_PHY_CHAN, channel)
                set_property(SPINEL.PROP_MAC_RAW_STREAM_ENABLED, 1)
                print(f"Listening on channel {channel} for {args.seconds:g}s", flush=True)
                if args.beacon_request:
                    beacon_request(api, channel, (index + 1) % 256)
                deadline = time.monotonic() + args.seconds
                report_at = time.monotonic() + 15
                while time.monotonic() < deadline:
                    result = api.queue_wait_for_prop(
                        SPINEL.PROP_STREAM_RAW, SPINEL.HEADER_ASYNC,
                        timeout=min(1, max(0.001, deadline - time.monotonic())),
                    )
                    if result:
                        length = api.parse_S(result.value)
                        packet = result.value[2:2 + length]
                        tail = result.value[2 + length:]
                        if len(packet) != length or len(tail) < 19:
                            raise ValueError("Truncated Spinel raw frame or unsupported metadata")
                        metadata = api.parse_fields(tail[:19], "ccSt(CCX)t(i)")
                        now = time.time()
                        micros = round(now * 1000000)
                        output.write(pcap.encode_frame(packet, micros // 1000000,
                                     micros % 1000000, True, True, metadata))
                        output.flush()
                        record = decode_record(packet, metadata, sweep, now)
                        frames.append(record)
                        if record.get("application_protocol") == "digi-transparent":
                            print("Digi data:", json.dumps({key: record.get(key) for key in (
                                "channel", "rssi", "network_source", "network_destination",
                                "aps_fragmentation", "application_payload",
                            )}), flush=True)
                    if time.monotonic() >= report_at:
                        print(f"Captured {len(frames)} frames", flush=True)
                        report_at += 15
                paths["json"].write_text(json.dumps(frames, indent=2) + "\n")
    except KeyboardInterrupt:
        print("Capture stopped; preserving received frames", flush=True)
    finally:
        try:
            api.prop_set_value(SPINEL.PROP_MAC_RAW_STREAM_ENABLED, 0)
            api.prop_set_value(SPINEL.PROP_PHY_ENABLED, 0)
        finally:
            api._reader_alive = False
            try:
                stream.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.sock.close()
            paths["json"].write_text(json.dumps(frames, indent=2) + "\n")
            summary = summarize(frames)
            paths["summary.json"].write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SMLIGHT hostname or IP")
    parser.add_argument("--port", type=int, default=6638)
    parser.add_argument("--channels", type=int, nargs="+", default=[14])
    parser.add_argument("--seconds", type=float, default=90, help="Dwell time per channel")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--beacon-request", action="store_true",
                        help="Transmit one standard discovery request per channel visit")
    parser.add_argument("--output", type=Path, required=True, help="New capture filename prefix")
    args = parser.parse_args()
    if args.seconds <= 0 or args.rounds < 1 or any(c not in range(11, 27) for c in args.channels):
        parser.error("Use positive duration/rounds and channels 11 through 26")
    capture(args)


if __name__ == "__main__":
    main()
