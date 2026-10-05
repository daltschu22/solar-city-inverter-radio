# Discover radio settings without the original collector

Run discovery after preparing the SMLIGHT bridge and before configuring the
collector. Start with the bridge's LAN address; the scan gathers evidence for
the inverter's channel, PAN IDs, and radio identities. Add `--write-env .env`
to generate the collector configuration, then review it before starting collection.

`tools/discover_radio.py` gathers candidate network settings from radio traffic.
It does not load installation settings, import configured device identities, pair
with a device, or start the replacement collector. Unknown settings remain unknown.

The tool can analyze saved captures or collect new traffic using an exclusively
owned SMLIGHT bridge. It identifies potential inverters from the supported startup
message or valid Digi/Modbus read responses. This is a protocol match, not proof
of the device's model or ownership. Confirm the equipment before using the values.

## Gather a new capture

Prepare the bridge with the supported RCP firmware and install the repository's
Python dependencies. The tool gathers settings directly from radio traffic.
If `.env` already exists, use `--write-env .env.review` to save a separate file.

You can scan with the same SMLIGHT you will later use for the replacement.
First stop any program connected to that SMLIGHT, including this project's collector, ZHA,
Zigbee2MQTT, or OTBR. The `--exclusive-radio` flag confirms you have done this:
the capture process resets and configures the bridge for listening. It never
stops another service automatically. A separate receiver is useful if you want
to leave an existing replacement running during capture.

Run the command below from the repository directory on your computer. Replace
`YOUR_BRIDGE_HOST` with the SMLIGHT's IP address or hostname, as used to open its
web interface, without `http://` or a trailing slash. Leave the inverter powered
during the scan. A working original SolarCity collector can remain on while you
listen; power it off before starting the replacement collector.

```sh
uv run python tools/discover_radio.py \
  --host YOUR_BRIDGE_HOST \
  --exclusive-radio \
  --output captures/discovery.json \
  --write-env .env
```

By default it listens on channels 11 through 26 for 20 seconds each, about five
minutes total. It does not transmit. Use `--channels 14 --seconds 120`, for
example, to spend two minutes on a known channel. The number is an example, not
a default network setting for every installation.

To solicit beacon responses, run with `uv run --extra capture python` and add
`--beacon-request` to the discovery command. The extra installs the pinned Scapy
dependency. This sends one standard IEEE 802.15.4 beacon request per
channel visit. It does not join a network or change inverter settings. A router
that still considers itself joined may answer; a device that has already left
may provide no useful response.

With `--write-env .env`, a complete, consistent result also produces a private
`.env` file containing the bridge address, port, and discovered radio settings.
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
uv run python tools/discover_radio.py \
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

Add `--write-env .env` to discovery, as in the command above. The tool writes a
complete environment file when there is exactly one matching inverter/network
candidate. It uses observed values from that inverter and its network, checks
for conflicts, and requires unsecured application traffic with stack profile `0`.
Unknown values are never filled with defaults. Existing files are never overwritten.

Review `.env` and the selected inverter in `captures/discovery.json` before
starting the collector. Nearby equipment can appear in a capture; confirm the
supported inverter model and, when available, compare the radio EUI with its
label or known radio configuration. Export verifies the evidence and settings,
not equipment ownership or whether the inverter will connect.

If export fails, the JSON report and captured frames remain available and the
command exits with an error. Resolve the reported issue before retrying. When
multiple inverter candidates are present, select one using `--inverter-eui`
with its `inverter_eui` value from the report. You can export from the saved
frames without another scan:

```sh
uv run python tools/discover_radio.py \
  --input captures/discovery-frames.json \
  --bridge-host YOUR_BRIDGE_HOST \
  --inverter-eui YOUR_INVERTER_EUI \
  --output captures/selected.json \
  --write-env .env
```

`--bridge-host` supplies the address for the generated file; this command opens
no radio connection. `--port` sets the bridge port and defaults to `6638`.
Omit `--inverter-eui` when the capture contains only one candidate. If the same
EUI appears on multiple networks, analyze a capture of the intended network
rather than selecting by EUI alone. Use new output paths for repeated attempts,
such as `--output captures/selected-2.json --write-env .env.review`.

The generated variables are:

| Environment variable | Source |
| --- | --- |
| `SOLAR_RADIO_HOST` | `--host` for a live scan, or `--bridge-host` for saved captures |
| `SOLAR_RADIO_PORT` | `--port`, default `6638` |
| `SOLAR_RADIO_CHANNEL` | Observed channel |
| `SOLAR_PAN_ID` | Observed 16-bit operating PAN ID |
| `SOLAR_EXTENDED_PAN_ID` | Observed extended PAN ID |
| `SOLAR_INVERTER_EUI` | Selected inverter's radio identity |
| `SOLAR_COLLECTOR_EUI` | Collector identity expected by that inverter |

The export preserves EUI leading zeroes and byte order. The collector EUI comes
from the inverter's network, even when the original box is unavailable.
`SOLAR_INITIAL_ADDRESS` is omitted so the collector can learn the current short
address at runtime.

Validate the reviewed file without opening a radio connection:

```sh
uv run --env-file .env python -m collector.config
```

The [environment setup](setup.md#environment-variables) covers all variables.
Once capture has finished, power off the original collector if you have one,
give the replacement exclusive access to the bridge, and follow
[Start and verify](setup.md#start-and-verify). Keep the report, captures, and
`.env` private; the repository ignores them under the paths above.

## If the report is incomplete

| Observation | Next step |
| --- | --- |
| No candidate, or only anonymous beacon searches | Capture longer on a channel with traffic, preferably while the inverter is operating. A quiet capture does not prove that it cannot reconnect. |
| Candidate found, but extended PAN unknown | Try a longer capture on that channel or the optional `--beacon-request`. A joined router may return a beacon containing the extended PAN. |
| Inverter or collector EUI unknown | Obtain packets carrying the permanent addresses. Check the unicast reception limitation above; a longer capture cannot fix a receiver that omits the needed frames. |
| Any field is `conflict`, or short-address bindings are ambiguous | Inspect the cited frames and separate captures from different operating networks or periods. Do not apply a conflicting identity. |

Use a new `--output` filename for each attempt. If the inverter has fully left
its network and never exposes its former settings, this tool cannot reconstruct
them from anonymous searches. Cold commissioning from that state remains
unverified; unknown fields stay unknown.
