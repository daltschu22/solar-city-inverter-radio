# Set up local solar monitoring

This guide takes you from a SMLIGHT and a compatible inverter to local readings.
The collector can run by itself; the dashboard and Home Assistant are optional.

Choose **replacement** mode to retire the SolarCity/Tesla box, or **passive** mode
to leave it running and listen to its readings. Passive mode is experimental and
has not been verified on live hardware. The current SMLIGHT RCP firmware can miss
unicast traffic, so discovery succeeding does not guarantee passive readings.

Before starting, confirm that your equipment matches the
[compatibility list and photos](../README.md#compatibility). You will need the
SMLIGHT, a network connection, and a computer to run the collector.

1. [Get the software](#get-the-software).
2. [Prepare the SMLIGHT](#prepare-the-bridge).
3. [Discover the inverter settings](#obtain-the-network-identity).
4. [Review and validate the configuration](#configure-the-collector).
5. [Start collecting and check for readings](#start-and-verify).

## Get the software

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) on
the computer that will run the collector. Then open a terminal and run:

```sh
git clone https://github.com/daltschu22/solar-city-inverter-radio.git
cd solar-city-inverter-radio
uv sync --locked
```

Run the remaining commands from this repository directory. Keep it on persistent
storage: the collector saves its history in `data/solar-history.sqlite3`.

## Prepare the bridge

The SMLIGHT receives radio packets from the inverter and passes them to the
collector over your home network. These steps cover the **SLZB-06U with a
CC2652P radio**.

1. **Power the SMLIGHT and connect it to your network.** For initial setup,
   connect an Ethernet cable to your router or switch and supply power. You can
   configure Wi-Fi in the SMLIGHT's **Network** page if you want to use Wi-Fi
   for the installation.
2. **Open its web interface.** Find the SMLIGHT in your router's connected-device
   list, then open its IP address in a browser. Confirm the model and radio chip.
   Save the address: the discovery command below calls it `YOUR_BRIDGE_HOST`.
   A DHCP reservation in your router keeps that address from changing.
3. **Install the radio firmware.** Close any software connected to the SMLIGHT,
   including ZHA, Zigbee2MQTT, a capture tool, or a running collector. In the
   SMLIGHT web interface, follow **Mode → Matter-over-Thread** and let the firmware
   update finish. This is SMLIGHT's documented way to install **OpenThread RCP**,
   the radio firmware that lets our Python collector control the radio.
   [SMLIGHT's flashing instructions](https://smlight.tech/manual/slzb-06/guide/thread-matter/)
   show this step. Stop after flashing; the guide's Home Assistant and Thread
   Border Router setup is for a different application.
4. **Enable access over your network.** Select the Ethernet or Wi-Fi connection
   you will use, then check the serial connection settings in the web interface.
   Set the **TCP port to `6638`** and the **UART baud rate to `460800`**, then save.
   The port is where the Python collector connects. The baud rate is the speed
   of the connection inside the SMLIGHT between its network processor and radio.
   Menu labels vary by firmware version; SMLIGHT's
   [web-interface guide](https://smlight.tech/manual/slzb-06/guide/configuration/)
   describes the **Mode**, **Network**, and serial settings pages.
5. **Continue to discovery below.** It connects to the SMLIGHT, prints its radio
   firmware version, and scans for inverter traffic.

The tested radio firmware is **SMLIGHT OpenThread RCP build `20260304`**.
The web installer may offer a different build; check the version reported by
discovery. Other builds have not been validated by this project.

<details>
<summary>Full tested radio firmware version (for comparison with discovery output)</summary>

```text
OPENTHREAD/1.4.0.0; CC13XX_CC26XX thread-v1.4-ti-1.0-ea-1.0; SLZB-06U 20260304
```

</details>

## Obtain the network identity

Stop other programs connected to the SMLIGHT, including capture tools, ZHA,
Zigbee2MQTT, OTBR, or another collector. Leave the inverter powered. A working
original SolarCity box can stay on during this listening step.

Run the following command, replacing `YOUR_BRIDGE_HOST` with the SMLIGHT's IP
address or hostname from the previous step. Use the address alone, without
`http://` or a trailing slash:

```sh
uv run python tools/discover_radio.py \
  --host YOUR_BRIDGE_HOST \
  --exclusive-radio \
  --output captures/discovery.json \
  --write-env .env
```

`--exclusive-radio` confirms that this program has the SMLIGHT to itself. The
scan resets the bridge into listening mode and takes about five minutes. It
finds the radio settings needed by the collector and writes:

- `.env`: the collector configuration, when one complete, consistent inverter
  candidate is found.
- `captures/discovery.json`: the findings and evidence, along with raw capture
  files in the same directory.

Existing files are never overwritten. If you have an existing configuration,
use `--write-env .env.review` to save a separate file. If discovery cannot produce
configuration, it keeps its findings and explains the problem; follow
[discovery help](discovery.md#if-the-report-is-incomplete). For multiple candidates,
see [selecting an inverter](discovery.md#apply-reviewed-values).

## Configure the collector

Open the generated `.env` and confirm the SMLIGHT address. Review the selected
inverter in `captures/discovery.json`: nearby equipment can appear in a scan.
Keep these files private; their standard paths are ignored by Git.

### Choose how to collect readings

The default, `SOLAR_COLLECTOR_MODE=replacement`, takes over the original box's
radio identity and queries the inverter. The original box must be powered off
while replacement collection runs.

To keep the original box working alongside this collector, add this line to `.env`:

```dotenv
SOLAR_COLLECTOR_MODE=passive
```

In passive mode, keep the original box powered and operating normally. The SMLIGHT
listens on the discovered channel; it does not send queries, coordinator replies,
or application acknowledgments. The configured EUIs identify the exchanges to
listen for. The original box controls the reading frequency and network recovery.
Only this program should connect to the SMLIGHT's TCP bridge.

Passive readings require both sides of a complete exchange. If the firmware
omits unicast packets, this mode cannot recover them. It never switches to
replacement mode automatically. [Passive protocol details](protocol.md#passive-monitoring)
describe the matching rules and firmware limitations.

Validate the configuration:

```sh
uv run --env-file .env python -m collector.config
```

A successful check prints `Radio configuration is valid` and opens no radio
connection. If you saved a separate `.env.review`, use that filename to validate
it, then put the reviewed settings in `.env` before continuing.

The defaults are enough to start. Optional settings, including polling intervals,
are listed in [Environment variables](#environment-variables) at the end of this guide.

## Start and verify

For **replacement mode, power off the original SolarCity/Tesla collector**, if
present. For **passive mode, leave that box powered and working**. Finish any
capture still using the SMLIGHT. Run one collector per bridge.

Start collection in your terminal:

```sh
uv run --env-file .env python -m collector
```

Leave it running. For Docker or Podman, use the [container instructions](#run-the-collector-in-a-container)
below instead of running a second collector.

In replacement mode, the collector listens for 30 seconds at startup, then waits
for identifiable inverter traffic. With the default settings, power readings
normally update about every two minutes once communication is established.

Passive mode starts listening immediately. Its timing depends on the original
box. Check that `collector.mode` is `passive`, `collector.observed_requests`
increases, and `collector.responses` increases as complete replies are matched.
`collector.requests` and `collector.network_transmissions` stay at zero. An
increasing packet count without fresh readings is insufficient: the receiver may
be missing requests, responses, or fragments. Leave the working Tesla box on;
changing our polling interval cannot fix missing passive packets.

Open <http://127.0.0.1:8766/api/live> on this computer. This is a JSON data page.
Check for a recent `timestamp`, a plausible `solar_w` value, and increasing
`collector.responses`. Zero is a valid power reading; `null` means no reading
has been saved yet. `/healthz` checks only whether the HTTP service responds.

History is available at <http://127.0.0.1:8766/api/history?range=1h>. See the
[API reference](api.md) for the fields and freshness rules. A quiet inverter may
need to return to operation before readings appear. Verify the next
night-to-morning transition before relying on unattended recovery.

Collection is now set up. To add a web interface, follow
[dashboard setup](dashboard.md). To add sensors, follow
[Home Assistant setup](home-assistant.md). Both read the collector API.

## Run the collector in a container

The image can run the collector alone or the collector with the dashboard.
Both container options use the replacement or passive mode selected in `.env`.
Choose either option below after building the image.

Create a network for consumers and a persistent data volume, then build and run
the collector. These commands use Docker; you can substitute `podman` if that
is your container runtime. Both automatically find `Dockerfile`:

### Build the image and prepare storage

```sh
docker build -t solar-city-inverter-radio .
docker network create solar-city
docker volume create solar-city-inverter-radio-data
```

### Start the container

Use the `.env` generated and validated above. Stop any collector already using
the SMLIGHT, then run **one** of the following commands.

#### Collector only

```sh
docker run --rm --name solar-city-collector --network solar-city \
  -p 127.0.0.1:8766:8766 \
  --env-file .env \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Read the API at <http://127.0.0.1:8766/api/live>.

#### Collector with dashboard

Set `SOLAR_DASHBOARD=true` and publish the dashboard's port:

```sh
docker run --rm --name solar-city-collector --network solar-city --stop-timeout 30 \
  -p 127.0.0.1:8766:8766 \
  -p 127.0.0.1:8765:8765 \
  -e SOLAR_DASHBOARD=true \
  --env-file .env \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Open <http://127.0.0.1:8765> for the dashboard. The collector API is also available
on port `8766`. Stopping this container stops both programs.

Both options save history in the same persistent volume and publish HTTP ports
on host loopback only. Add your own service management or reverse proxy according
to your environment.

Keep `.env` private. Do not bake installation values into the image.

Rebuild the image after updating and keep the data volume across container
replacements. The [dashboard guide](dashboard.md) covers running the viewer
separately and explains how the combined container works.

## Environment variables

The collector reads these from `.env` when launched with `--env-file`, or from
its service environment. Restart collection after changing a setting.

| Variable | Requirement or default |
| --- | --- |
| `SOLAR_RADIO_HOST` | Required; SMLIGHT hostname or IP |
| `SOLAR_RADIO_PORT` | `6638` |
| `SOLAR_RADIO_CHANNEL` | Required; channel `11`–`26` |
| `SOLAR_PAN_ID` | Required; observed 16-bit PAN ID |
| `SOLAR_EXTENDED_PAN_ID` | Required; observed 64-bit extended PAN ID |
| `SOLAR_COLLECTOR_EUI` | Required; collector identity the inverter expects |
| `SOLAR_INVERTER_EUI` | Required; inverter radio identity |
| `SOLAR_INITIAL_ADDRESS` | `0x2345`; normally leave unset |

Numeric values accept decimal or `0x`-prefixed hexadecimal strings. EUIs use 16
hexadecimal digits without colons, in the report's display order. Required
variables must be supplied. Optional variables use their defaults when unset.
Empty values are invalid, including for optional variables.

Additional environment settings:

| Variable | Default and purpose |
| --- | --- |
| `SOLAR_HISTORY_PATH` | `data/solar-history.sqlite3` in the repository root |
| `SOLAR_API_BIND` | `127.0.0.1`; collector API listening address |
| `SOLAR_API_PORT` | `8766`; collector API port |
| `SOLAR_COLLECTOR_MODE` | `replacement`; set `passive` to listen alongside the original box (experimental) |
| `SOLAR_POLL_INTERVAL_SECONDS` | `60`; replacement mode only, seconds between measurement queries, integer `15`–`3600` |
| `SOLAR_PASSIVE_STALE_SECONDS` | `300`; passive mode only, freshness and chart-gap threshold in seconds, integer `30`–`86400`; does not change Tesla's query timing |
| `SOLAR_RECONNECT_INTERVAL_SECONDS` | `15`; seconds before retrying a failed radio session, integer `1`–`3600` |
| `SOLAR_DASHBOARD` | `false`; set `true` to also run the dashboard with the container's default command |
| `SOLAR_LATITUDE`, `SOLAR_LONGITUDE` | Unset; optional, supply both for nighttime inference in replacement mode |

To tune collection, add the desired values to the generated `.env` or service
environment and restart the collector. For example:

```dotenv
SOLAR_POLL_INTERVAL_SECONDS=120
SOLAR_RECONNECT_INTERVAL_SECONDS=30
```

In replacement mode, the polling interval is the time between individual
measurement queries. Power alternates with energy and diagnostics, so `120` means a power reading about
every four minutes. Energy queries are at most eight polling intervals apart
in the normal cycle. The API reports the configured interval, and the dashboard
uses it for freshness and chart gaps. See the [Home Assistant guide](home-assistant.md)
for its sensor age thresholds.

The reconnect interval applies after a radio session fails, such as a lost TCP
connection. It does not schedule radio resets. Coordinator replies and the
15-second link-status schedule run independently of measurement polling.
Passive mode reconnects only its listening session, without network maintenance.
It reports old readings as stale; it does not infer nighttime standby from silence.

Coordinates stay in your runtime environment. Nighttime inference never creates
measurements or changes inverter settings. It requires a recent low-power reading
near sunset, a healthy bridge, quiet radio traffic, and no reported fault.

Both HTTP services bind to localhost by default and have no built-in
authentication. If exposing them to other devices, use trusted network access or
your own authenticated reverse proxy. The API can include equipment serial numbers.

Keep a separate database for each installation. `SOLAR_INITIAL_ADDRESS` is an
address offered during association; the collector learns and saves the verified
current address at runtime.
