#!/usr/bin/env python3
"""Summarize saved transition captures without opening a radio connection."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path


def records(paths):
    for path in sorted(set(Path(path).resolve() for path in paths)):
        with path.open() as source:
            for line in source:
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict) or item.get("schema") != 1:
                        raise ValueError()
                    if (not isinstance(item.get("at"), (int, float)) or not math.isfinite(item["at"])
                            or not isinstance(item.get("event"), str)):
                        raise ValueError()
                    yield item
                except (ValueError, KeyError, TypeError):
                    yield None


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def summarize(paths):
    counts, queries, tx_statuses = Counter(), Counter(), Counter()
    accepted = set()
    responses, radio_times, heartbeats, timeline = [], [], [], []
    first = last = None
    invalid = 0
    for item in records(paths):
        if item is None:
            invalid += 1
            continue
        at, event = item["at"], item["event"]
        first = min(first, at) if first is not None else at
        last = max(last, at) if last is not None else at
        counts[event] += 1
        if event == "response":
            accepted.add(tuple(item["frame_id"]))
            responses.append(item)
        elif event == "rx" and item.get("inverter"):
            radio_times.append(item["frame"]["observed_at"])
        elif event == "heartbeat":
            heartbeats.append(at)
        elif event == "tx" and item["purpose"] == "query":
            queries[item["kind"]] += 1
        elif event == "tx_result":
            tx_statuses[str(item["status"])] += 1
        elif event in {"network_event", "session_start", "session_ready", "session_end",
                       "session_error", "query_cycle", "query_timeout", "tx_error"}:
            # Do not copy raw frames, paths, firmware identities, or error text.
            timeline.append({"at": at, "event": event, **{
                key: item[key] for key in ("kind", "mode", "state", "tx_status") if key in item}})

    state = None
    for response in sorted(responses, key=lambda item: item["observed_at"]):
        values = response.get("decoded", {})
        current = values.get("operating_state")
        if current is not None and current != state:
            state = current
            timeline.append({"at": response["observed_at"], "event": "operating_state",
                             "state": current, **{key: values[key] for key in (
                                 "dc_voltage_v", "dc_power_w", "fault_bits", "event_bits_2") if key in values}})

    unaccepted = Counter()
    for item in records(paths):
        if item is None or item["event"] != "rx" or not item.get("inverter"):
            continue
        frame = item["frame"]
        payload = frame.get("application_payload")
        if not payload or (frame.get("capture_sweep"), frame.get("timestamp")) in accepted:
            continue
        kind = "startup_hello" if payload == "f400010101" else "other_unaccepted"
        unaccepted[kind] += 1

    power = sorted((item for item in responses if item["kind"] == "power"),
                   key=lambda item: item["observed_at"])
    gaps = [{"from": stamp(a["observed_at"]), "to": stamp(b["observed_at"]),
             "seconds": round(b["observed_at"] - a["observed_at"], 1),
             "before_w": a["values"]["solar_w_precise"], "after_w": b["values"]["solar_w_precise"]}
            for a, b in zip(power, power[1:]) if b["observed_at"] - a["observed_at"] > 180]
    beats = sorted(heartbeats)
    heartbeat_gaps = [{"from": stamp(a), "to": stamp(b), "seconds": round(b - a, 1)}
                      for a, b in zip(beats, beats[1:]) if b - a > 120]
    return {
        "first_record": stamp(first) if first is not None else None,
        "last_record": stamp(last) if last is not None else None,
        "event_counts": dict(counts), "invalid_or_incomplete_lines": invalid,
        "query_counts": dict(queries), "final_tx_status_counts": dict(tx_statuses),
        "unaccepted_application_frames": dict(unaccepted),
        "power_gaps_over_180_seconds": gaps, "heartbeat_gaps_over_120_seconds": heartbeat_gaps,
        "last_inverter_packet": stamp(max(radio_times)) if radio_times else None,
        "last_power_reading": ({"at": stamp(power[-1]["observed_at"]),
                                "watts": power[-1]["values"]["solar_w_precise"]} if power else None),
        "timeline": [{**item, "at": stamp(item["at"])} for item in sorted(timeline, key=lambda item: item["at"])],
        "limitations": [
            "Only retained, received frames are represented; missing frames cannot be ruled out.",
            "Unaccepted application frames include retries, late replies, and unknown messages; they are not proven shutdown notices.",
            "Power gaps are observation gaps, not proof of zero generation or a particular cause.",
            "Review heartbeat gaps and incomplete lines before interpreting silence; rotation can discard older coverage.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path, help="JSONL files, including retained rotations")
    args = parser.parse_args()
    print(json.dumps(summarize(args.captures), indent=2))


if __name__ == "__main__":
    main()
