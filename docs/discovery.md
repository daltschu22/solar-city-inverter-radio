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

You can scan with the same SMLIGHT you will later use for the replacement.
First stop any program connected to that SMLIGHT, including `collector.py`, ZHA,
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

Open `captures/discovery.json` in a local editor. Under `networks`, find the
network containing your inverter, then select its entry in `inverter_candidates`.
Do not assume the first network or candidate is yours: nearby equipment can
appear in a capture. Check the supported inverter model and, when available,
compare the radio EUI with its label or known radio configuration.

Start with that candidate's `inverter_only` object. Each setting has a `status`
and a `value`. For example, this **fictional** observation:

```json
{
  "pan_id": {"status": "observed", "value": "0x1234", "evidence": []}
}
```

means the corresponding config entry is `"pan_id": "0x1234"`. Copy only the
value, not the surrounding evidence object. Use your report's values, not this
example. Real observations include the supporting frames in `evidence`.

| Field in `radio.local.json` | Where to get the value |
| --- | --- |
| `host` | Your bridge hostname or IP, as used with `--host` |
| `port` | Your bridge's TCP port, normally the integer `6638` |
| `channel` | Selected candidate's `inverter_only.channel.value` |
| `pan_id` | Selected candidate's `inverter_only.pan_id.value` |
| `extended_pan_id` | Selected candidate's `inverter_only.extended_pan_id.value` |
| `inverter_eui` | Selected candidate's `inverter_only.inverter_eui.value` |
| `collector_eui` | Selected candidate's `inverter_only.collector_eui.value` |

The full path starts at `networks`, then the chosen `inverter_candidates` entry.
Use a radio field only when its status is `observed` and its value is non-null.
If an inverter-only field is unknown, the same candidate's `whole_network` view
may contain an observed value from other traffic on that network. Review its
evidence before using it. Conflicts or disagreements between the views require
investigation; do not silently choose one value or fill in a guessed default.

The collector EUI is the identity the inverter expects, even when the original
box is unavailable. Do not substitute your new bridge's factory EUI. The inverter
EUI is its permanent 64-bit radio address, not a changing 16-bit short address or
the inverter's equipment serial number. Normally omit `initial_address`; the
collector learns the inverter's current short address at runtime.

Confirm compatibility as well as identity. The selected network's
`network_fields.stack_profile` should be observed as `0`, and `security` should
show unsecured application traffic. Secured traffic or an unknown/conflicting
stack profile needs further inspection before using this implementation. A
complete set of addresses alone does not establish protocol compatibility.

Create the config in the repository directory:

```sh
cp config.example.json radio.local.json
```

Edit it using the mapping above. Preserve JSON types: `channel` and `port` are
integers; PAN IDs are quoted `0x`-prefixed hexadecimal strings; EUIs are quoted
16-digit hexadecimal strings without colons. Use the report's display order;
do not reverse the bytes. The script never applies a report automatically.

Then validate the completed file without opening a radio connection:

```sh
.venv/bin/python config.py
```

By default the app reads `radio.local.json` from the working directory. If you
use `SOLAR_CONFIG`, set it to the absolute path of the file you intend to validate
and run. Config validation checks the file's format, not whether the inverter
will connect.

Once capture has finished, power off the original collector if you have one,
give the replacement exclusive access to the bridge, and follow
[Start and verify](setup.md#start-and-verify). Keep the report, captures, and
`radio.local.json` private; the repository ignores them under the paths above.

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
