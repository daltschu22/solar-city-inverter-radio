# Recording evening shutdown and morning startup

The replacement collector can temporarily record radio traffic and validated
measurements to investigate when the inverter stops answering and how it returns.
The recorder uses the collector's existing connection. Keep the original
SolarCity/Tesla box off and keep the working radio firmware and configuration.

## What the manuals establish

The [ABB PVI-5000/6000-US manual](https://www.fimer.com/sites/default/files/PVI-5000-6000-TL-S-US%20%28-A%29%20Product%20manual.pdf)
describes a low-input-voltage shutdown delay, during which the inverter draws
power from the grid (printed page 44). It also describes electrical grid-connection
waits (page 41), stored statistics (page 33), and clock/energy-reset notifications
(page 61). The [Power-One PVI-5000/6000-US manual](https://gridalternatives.org/sites/default/files/Linked%20documents/PVI-5000-6000-OUTD-US%20Manual.pdf)
describes input-specific measurements and stored history (pages 45–46 and 56).

These family manuals do not specify the SolarCity radio adapter's overnight power
supply, a radio shutdown notification, or the commands for retrieving those logs
over this project's radio transport. Descriptions and defaults also vary between
editions. Treat these as test leads; identify the exact equipment and firmware
before applying operating limits or register mappings.

## Enable a temporary recording

Complete [setup](setup.md) first. In replacement mode, set
`SOLAR_TRANSITION_CAPTURE_UNTIL` to an absolute ISO 8601 timestamp with a timezone.
For example, generate a deadline four days from now:

```sh
uv run python -c 'from datetime import datetime, timedelta, timezone; print("SOLAR_TRANSITION_CAPTURE_UNTIL=" + (datetime.now(timezone.utc) + timedelta(days=4)).isoformat())'
```

Add the resulting line to the collector's `.env` or service environment and
restart it using the same persistent data volume. Recording stops at that
deadline, including across restarts. Removing the setting and restarting disables
it earlier. This setting is rejected in passive mode.

With the existing `SOLAR_LATITUDE` and `SOLAR_LONGITUDE` settings, the collector
prioritizes DC voltage, operating state, event bits, and AC diagnostics during the
90 minutes before and after each sunrise and sunset. It uses these verified reads:

```text
power, inverter_ac, power, energy, power, inverter_dc, power, energy,
power, inverter_dc, power, inverter_ac, power, inverter_dc, power, energy
```

The interval between queries stays unchanged. Power and energy occupy the same
slots as in the normal cycle, including when switching cycles. At the default
60-second interval, DC/state observations are four to eight minutes apart.
Identity and meter-detail reads resume outside the transition windows. Their
saved values retain their original timestamps and can become stale.

Without coordinates, recording still works, using the normal query cycle.
Recording covers the entire enabled period, including quiet hours. Normal
traffic requirements still apply: the collector waits for identifiable inverter
traffic before querying and does not probe a silent inverter continuously.
The normal query cycle resumes on expiry or recording failure.

## Files and status

Files are saved next to `SOLAR_HISTORY_PATH`:

- `transition-capture.jsonl`: current records.
- `transition-capture.jsonl.1` through `.3`: rotations, newest first.

Each file is limited to 16 MiB, for up to 64 MiB retained. Older records are
discarded on rotation. Files use owner-only permissions and contain private radio
identities and raw messages. Keep them out of Git and public reports. The recorder
does not change the SQLite schema or create estimated production measurements.

`/api/live` exposes `collector.transition_capture` with `active`, `until` (Unix
seconds), `records`, `last_record_at`, and `error`. Counts start with the process.
`collector.query_cycle` reports `normal` or `transition` as selected at the last
polling opportunity. A storage failure disables recording and reports an error
while collection continues. Verify the record count increases after enabling it.

The JSONL includes relevant received frames, application/network transmissions,
final delivery statuses, accepted responses and registers, query timeouts,
network announcements/leaves, session boundaries, bridge health checks, and
one-minute heartbeats. Transmit results cover the existing retry procedure as a
whole; individual hardware ACKs and retry attempts are not separately captured.
Receive-error counts cannot attribute corrupted frames to a particular device.

## Review the saved evidence

After several evenings and mornings, copy the retained files to a private local
directory. Include the rotations when running the offline summary:

```sh
uv run python tools/summarize_transitions.py data/transition-capture.jsonl*
```

This opens files only. It reports state changes, query/delivery counts, network
events, gaps between power observations, and gaps between recorder heartbeats.
The summary omits raw frames and equipment identities; review its timestamps and
measurements before sharing. The fixed 180-second power-gap threshold is a review
aid; longer configured polling intervals naturally produce such gaps.

| Question | Evidence to examine |
| --- | --- |
| Does the inverter report a shutdown state? | Validated DC/state replies before the last power reply, with voltage and event bits |
| Does it leave the network? | A decoded self-leave from the known inverter, followed by its recovery traffic |
| Is there an unsolicited message? | Raw application frames around shutdown that were not accepted as query responses; verify their meaning separately |
| Did the bridge or collector stop? | Session errors, failed health checks, heartbeat gaps, and recording errors |
| Does it recover by itself? | Morning traffic, coordinator replies, and fresh validated power without intervention |

Unaccepted frames also include retransmissions, startup exchanges, and late
replies. They are not automatically shutdown notices. A state that lasts less
than a polling interval can be missed, and the radio cannot record packets it
does not receive. Silence alone does not prove the radio lost power or establish
why generation stopped. The recorder's expiry and retained coverage must also be
considered before drawing conclusions from missing events.

## Follow-up tests

Per-input MPPT measurements, manufacturer-specific faults, and stored-history
retrieval need verified register addresses or command documentation for this
firmware. Do not infer them from LCD menus or the wired Aurora protocol.

For each feature, first confirm its read-only request and response against live
observations, then add offline decoding tests before enabling it in collection.
Test any historical import against a database copy with a deliberately omitted
interval; verify inverter clock offsets, counter units, ordering, and duplicate
handling before writing to production history. The manuals' clock-change and
energy-reset notifications make those checks necessary.
