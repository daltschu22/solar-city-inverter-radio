# Discover radio settings without the original collector

`tools/discover_radio.py` gathers candidate network settings from radio traffic.
It does not read `radio.local.json`, import configured device identities, pair
with a device, or start the replacement collector. Unknown settings remain unknown.

The tool can analyze saved captures or collect new traffic using an exclusively
owned SMLIGHT bridge. It identifies potential inverters from the supported startup
message or valid Digi/Modbus read responses. This is a protocol match, not proof
of the device's model or ownership. Confirm the equipment before using the values.

## Gather a new capture

Prepare the bridge with the supported RCP firmware and install the repository's
Python dependencies. A site configuration is not required, even if a partially
edited `radio.local.json` already exists.

Use a spare receiver, or stop the collector that currently owns this bridge.
The capture process resets and configures the bridge, so it must have exclusive
access. It never stops another service automatically.

```sh
.venv/bin/python tools/discover_radio.py \
  --host YOUR_BRIDGE_HOST \
  --exclusive-radio \
  --output captures/discovery.json
```

By default it listens on channels 11 through 26 for 20 seconds each, about five
minutes total. It does not transmit. Use `--channels 14 --seconds 120`, for
example, to spend two minutes on a known channel. The number is an example, not
a default network setting for every installation.

To solicit beacon responses, optionally install `scapy==2.7.0` and add
`--beacon-request`. That sends one standard IEEE 802.15.4 beacon request per
channel visit. It does not join a network or change inverter settings. A router
that still considers itself joined may answer; a device that has already left
may provide no useful response.

The tool writes the report plus capture files alongside it:

- `discovery.json`: settings, unknowns, conflicts, and supporting frame references.
- `discovery-frames.json`: captured and decoded frames.
- `discovery-frames.pcap`: raw packet capture for independent inspection.
- `discovery-frames.summary.json`: traffic counts and decoded capture summary.

These files contain private radio identities and may contain telemetry. The
`captures/` directory is ignored by Git. New files use private permissions, and
existing output files are not overwritten. Do not attach them to a public issue
without review and sanitization.

Stock TI RCP promiscuous reception can miss ACK-requested unicast packets. This
can leave identity fields unknown even when traffic is present. The report only
claims what the receiver actually captured. A receiver with verified unicast
capture support may be needed for a complete result.

## Analyze existing captures

The offline path opens no network connection:

```sh
.venv/bin/python tools/discover_radio.py \
  --input captures/reference.json \
  --output captures/reference-discovery.json
```

`--input` accepts one or more JSON or JSONL files. Supported JSON forms are a list
of frames, a single frame, or an object with a `frames`, `received_frames`, or
`packets` list. Each record needs `raw` as hexadecimal MAC-frame bytes **without
the two FCS bytes**, plus a numeric `channel`. Bad-FCS and receive-error flags
are honored. Cached decoded identity fields are ignored; identities are decoded
again from the raw bytes. The tool does not directly import PCAP files.

## Read the evidence

The report groups observations by channel and 16-bit PAN ID. It lists visible
nodes, anonymous beacon searches, security observations, and candidate inverters.
Each candidate has two views:

- **`inverter_only`** uses fields found in frames sent by that candidate.
- **`whole_network`** can also use coordinator traffic and network beacons.

Each field is `observed`, `unknown`, or `conflict`. A conflicting field has no
selected value. The report includes frame indexes, counts, and source identities
so the observation can be checked against the input files. Frame indexes are
one-based across the concatenated input records.

The five settings are the channel, 16-bit PAN ID, extended PAN ID, inverter EUI,
and expected collector EUI. The bridge hostname and port come from the receiver's
network configuration, not the captured inverter packets.

The inverter's application messages can expose its own EUI and the collector EUI
in the same packet. Its beacon can expose the extended PAN ID. A beacon may use
only a short source address; the tool attributes it to an EUI only when the
capture provides an unambiguous binding from that device's own traffic. Ambiguous
short addresses are listed and are not used to fill in missing identities.

A node list without an inverter candidate is still useful, but it is not enough
to claim a complete configuration. Anonymous beacon requests reveal a channel on
which searching occurred; they do not supply a PAN or permanent address.

## What has been demonstrated

A bounded passive capture of a working replacement's existing bridge connection
recovered all five settings from inverter-originated frames. The recovered
values matched the installation's known settings, and the beacon's extended PAN
and stack profile were cross-checked with a separate packet decoder. The
original SolarCity collector was absent from that operating setup. No radio
configuration was supplied to the discovery analyzer.

This establishes that a joined inverter can expose the necessary values without
receiving packets from the original collector. The replacement coordinator was
still maintaining the network during the capture. It does not establish recovery
from a cold, fully unjoined inverter with an unknown original collector identity.
The report deliberately makes that distinction.

## Apply reviewed values

When the candidate and network have been verified, copy `config.example.json` to
`radio.local.json`. Enter the five recovered values plus your bridge host and
port. A complete report is not applied automatically. Confirm the supported
hardware, unsecured network, and stack profile before starting the collector.
Follow the [setup guide](setup.md) for validation and startup.
