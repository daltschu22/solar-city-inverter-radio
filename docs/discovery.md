# Discover radio settings

Run discovery after preparing the SMLIGHT bridge and before configuring the
collector. Start with the bridge's LAN address; the scan gathers evidence for
the inverter's channel, PAN IDs, and radio identities. Add `--write-env .env`
to generate the collector configuration, then review it before starting collection.

Use discovery for both **replacement** and experimental **passive** collection.
A working original SolarCity/Tesla box can stay on during the scan. Discovery can
also recover settings from inverter traffic on an operating network without that
box. After discovery, [choose the collection mode](setup.md#choose-how-to-collect-readings):
replacement requires the original box off, while passive requires it working.
Finding network settings does not prove that the receiver captures enough unicast
traffic for passive readings.

`tools/discover_radio.py` gathers candidate network settings from radio traffic.
It does not load installation settings, import configured device identities, pair
with a device, or start either collection mode. Unknown settings remain unknown.

The tool can analyze saved captures or collect new traffic using an exclusively
owned SMLIGHT bridge. It identifies potential inverters from the supported startup
message or valid Digi/Modbus read responses. This is a protocol match, not proof
of the device's model or ownership. Confirm the equipment before using the values.

## Gather a new capture

For a first installation, follow [the setup guide's discovery step](setup.md#obtain-the-network-identity).
It includes the scan command and generates `.env`. This section covers scan
options and interpreting the results. Always give a live scan exclusive access
to its SMLIGHT; it resets the bridge for capture.

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
Normal addressed reception by the collector uses the SMLIGHT alone.

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

The setup command uses `--write-env .env` to export configuration. The tool writes a
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

The [configuration reference](setup.md#environment-variables) describes the
exported variables. The export preserves EUI leading zeroes and byte order and
uses the collector identity expected by the inverter. `SOLAR_INITIAL_ADDRESS`
is omitted so the collector can learn the current short address at runtime.

Return to [configuration validation](setup.md#configure-the-collector), then
continue with startup. Keep the report, captures, and `.env` private.

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

## Capture raw traffic

For protocol investigation, `tools/smlight_capture.py` saves frames without
building a configuration report. Stop this project's collector in either mode
and give the capture tool exclusive access to the SMLIGHT bridge. The original
SolarCity/Tesla box can remain on. From the repository directory:

```sh
mkdir -p captures
uv run python tools/smlight_capture.py \
  --host YOUR_BRIDGE_HOST --channels YOUR_DECIMAL_CHANNEL \
  --seconds 90 --output captures/reference
```

Each channel receives the full dwell interval. To scan all channels, supply
`--channels 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26`. Passive capture
sends no packets. The optional `--beacon-request` flag and `capture` extra work
as described under [scan options](#gather-a-new-capture).

## Radio identity details

XBee `ID` is the configured extended PAN setting; `ID=0` means automatic
selection. The operating values are XBee `OI` for the 16-bit PAN ID and `OP` for
the extended PAN ID. Use those observed operating values. Channel displays may
be hexadecimal: `0x14` is decimal 20.

The supported network uses stack profile `0`, unsecured traffic, Digi profile
`0xc105`, serial-data cluster `0x0011`, and endpoints `0xe8`. See the
[protocol reference](protocol.md) for the packet layout. Changing network
identities cannot make another inverter protocol compatible.
