# Replace a SolarCity monitoring collector

This is **part 1: data collection**. The collector owns the radio, saves readings,
and provides a JSON API. You can finish here, connect your own consumer or
[Home Assistant](home-assistant.md), or add the [optional dashboard](dashboard.md).

This guide covers a single legacy Power-One/Digi installation using an existing
collector identity. It assumes the inverter already contains a working radio.
All addresses in the tests are fictional. The repository contains no reusable
installation identity. The [discovery tool](discovery.md) can gather candidate
settings from traffic; review its evidence before configuring the collector.

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
5. **Run discovery next.** Follow [Obtain the network identity](#obtain-the-network-identity)
   below from the computer that will run the collector. Discovery connects to
   the SMLIGHT, prints its radio firmware version, and scans for inverter traffic.

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

Run the [discovery tool](discovery.md#gather-a-new-capture) before filling in
the collector configuration. Start with your SMLIGHT's IP address or hostname,
which you can find in your router's device list and use to open the bridge's
web interface. The tool scans for the channel, PAN IDs, and radio identities.

Install the Python dependencies using the [quick start](../README.md#quick-start).
Stop other programs connected to the SMLIGHT, then run from the repository root:

```sh
uv run python tools/discover_radio.py \
  --host YOUR_BRIDGE_HOST \
  --exclusive-radio \
  --output captures/discovery.json \
  --write-env .env
```

Replace `YOUR_BRIDGE_HOST` with that LAN address. `--exclusive-radio` confirms
the bridge is available for the scan, which resets it into listening mode.
Leave the inverter powered and allow about five minutes. A working original
SolarCity collector can stay on during discovery; power it off before starting
the replacement collector.

The tool writes `.env` when it finds one complete, consistent inverter candidate.
Review the generated settings and selected inverter in `captures/discovery.json`
before starting collection. If export fails, the report is kept; follow
[the export guide](discovery.md#apply-reviewed-values) for multiple candidates or
[the incomplete-report guide](discovery.md#if-the-report-is-incomplete) for missing
or conflicting fields. Existing files are never overwritten. A complete, verified
configuration for this inverter and network can be reused instead of scanning.

The discovered values and bridge settings have these meanings:

| Environment variable | Meaning and source |
| --- | --- |
| `SOLAR_RADIO_HOST` | Hostname or IP of your SMLIGHT bridge |
| `SOLAR_RADIO_PORT` | TCP bridge port; normally `6638` |
| `SOLAR_RADIO_CHANNEL` | Actual IEEE 802.15.4 channel, decimal 11 through 26 |
| `SOLAR_PAN_ID` | 16-bit operating PAN ID, shown in MAC headers or XBee `OI` |
| `SOLAR_EXTENDED_PAN_ID` | 64-bit operating network identity, found in beacons or XBee `OP` |
| `SOLAR_COLLECTOR_EUI` | Original collector's 64-bit radio identity |
| `SOLAR_INVERTER_EUI` | Inverter radio's 64-bit identity |
| `SOLAR_INITIAL_ADDRESS` | Optional short address to offer on association; normally omit |

XBee `ID` is the **configured** extended PAN setting. `ID=0` means automatic
selection; it does not mean the operating extended PAN is zero. Record the
operating value. Channel displays may be hexadecimal: `0x14` is decimal 20.

Discovery can learn from inverter-originated traffic or
[analyze saved captures](discovery.md#analyze-existing-captures). Beacons contain
the extended PAN; IEEE addresses appear in suitable network headers and device
announcements. The collector's short address is `0x0000`. The inverter's short
address may change and is learned at runtime.

Confirm the captured network is unsecured and uses stack profile `0`. Also
confirm the application profile `0xc105`, serial-data cluster `0x0011`, and
endpoints `0xe8`. These protocol fields are implemented explicitly in the code;
changing PAN IDs cannot make another inverter protocol compatible.

For an optional capture with the production collector stopped:

```sh
uv run python tools/smlight_capture.py \
  --host YOUR_BRIDGE_HOST --channels YOUR_DECIMAL_CHANNEL \
  --seconds 90 --output captures/reference
```

Create `captures/` first. A channel sweep can use `--channels 11 12 13 14 15 16
17 18 19 20 21 22 23 24 25 26`. Each channel receives the full dwell interval.
The capture tool resets/configures its bridge and therefore requires exclusive
access even when listening passively. It does not transmit by default.

The optional `--beacon-request` flag actively sends a discovery frame and requires
the optional capture dependency. Use `uv run --extra capture python` in place
of `uv run python` when adding that flag. Passive capture needs no extra.

**Capture limitation:** stock TI RCP promiscuous reception may omit ACK-requested
unicast frames. A partial capture can reveal some settings but cannot establish
that startup replies are absent. Use an independent receiver with verified
unicast capture support for a complete application exchange. Production addressed
reception does not require a separate sniffer or modified RCP firmware.

If the original collector is unavailable, the inverter's own traffic may reveal
the needed identities and network settings. The discovery tool has recovered all
five radio settings from inverter-originated traffic on an operating replacement
network. Discovery from a fully unjoined inverter and fresh pairing under a new
collector EUI remain untested. See the [evidence limits](discovery.md).

## Configure the collector

Use the `.env` generated by discovery. The collector reads environment variables
supplied by uv, Docker, or your service manager. Validation rejects missing
settings, invalid ranges, and
matching inverter/collector identities before opening the radio. Changes require
restarting the app.

Run `uv run --env-file .env python -m collector.config` to validate without
opening a connection.

The optional `SOLAR_INITIAL_ADDRESS` defaults to a synthetic unicast seed. It is
used when assigning an address to the known inverter; it is not assumed to be a
live neighbor. The current verified short address is persisted in SQLite. Keep a
separate database for each installation.

### Environment variables

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
hexadecimal digits without colons, in the report's display order. Required variables must be supplied. Optional variables use the defaults above
when unset. Empty values are invalid, including for optional variables.

Review the `.env` generated by [discovery](discovery.md#apply-reviewed-values).
`config.example.env` also lists the settings for service-managed installations.
The `.env` file is ignored by Git. Load its reviewed values with uv:

```sh
uv run --env-file .env python -m collector.config
uv run --env-file .env python -m collector
```

Use this after capture has stopped and the original collector is powered off,
as described in [Start and verify](#start-and-verify). Your service manager can
also supply these variables directly; `.env` is a convenience for local setup.

Additional environment settings:

| Variable | Default and purpose |
| --- | --- |
| `SOLAR_HISTORY_PATH` | `data/solar-history.sqlite3` in the repository root |
| `SOLAR_API_BIND` | `127.0.0.1`; collector API listening address |
| `SOLAR_API_PORT` | `8766`; collector API port |
| `SOLAR_POLL_INTERVAL_SECONDS` | `60`; seconds between measurement queries, integer `15`–`3600` |
| `SOLAR_RECONNECT_INTERVAL_SECONDS` | `15`; seconds before retrying a failed radio session, integer `1`–`3600` |
| `SOLAR_DASHBOARD` | `false`; set `true` to also run the dashboard with the container's default command |
| `SOLAR_LATITUDE`, `SOLAR_LONGITUDE` | Unset; optional, supply both for nighttime inference |

To tune collection, add the desired values to the generated `.env` or service
environment and restart the collector. For example:

```dotenv
SOLAR_POLL_INTERVAL_SECONDS=120
SOLAR_RECONNECT_INTERVAL_SECONDS=30
```

The polling interval is the time between individual measurement queries. Power
alternates with energy and diagnostics, so `120` means a power reading about
every four minutes. Energy queries are at most eight polling intervals apart
in the normal cycle. The API reports the configured interval, and the dashboard
uses it for freshness and chart gaps. See the [Home Assistant guide](home-assistant.md)
for its sensor age thresholds.

The reconnect interval applies after a radio session fails, such as a lost TCP
connection. It does not schedule radio resets. Coordinator replies and the
15-second link-status schedule run independently of measurement polling.

Coordinates stay in your runtime environment. Nighttime inference never creates
measurements or changes inverter settings. It requires a recent low-power reading
near sunset, a healthy bridge, quiet radio traffic, and no reported fault.

## Start and verify

Power off the original collector, if present. Ensure no capture tool, ZHA, Zigbee2MQTT, OTBR,
or second instance owns the SMLIGHT connection. Then run:

```sh
uv run --env-file .env python -m collector
```

The startup sequence is:

1. Initialize the RCP and listen passively for 30 seconds. Seeing the original
   collector's identity causes a conflict error. Silence is not proof it is off.
2. Set the configured PAN and EUI and use coordinator short address `0x0000`.
3. Answer network discovery, association, coordinator verification, and the
   supported application startup exchange.
4. Learn the inverter's current short address from identifiable direct traffic.
5. Send serialized read requests and store validated responses.

Use <http://127.0.0.1:8766/api/live> for current collector status and
`/api/history?range=1h` on the same port for saved readings. `/healthz` only
verifies the HTTP process is responding; it does not prove the inverter is
producing or communicating. The [API guide](api.md) describes units and freshness.

Keep this process running to collect data. The dashboard can be started and
stopped independently. Reading the API never triggers additional inverter polls.

Verify identity, request/response counters, fresh readings, and plausible units.
Observe startup and the next overnight-to-morning transition before relying on
unattended recovery. Hardware testing covers one installation; long-term reliability
and broader hardware compatibility remain unverified.

An overnight gap is not direct proof of a hardware sleep mode. Allow startup
time and use response freshness to judge recovery.

## Run the collector in a container

The container runs the collector and API by default. To add the
dashboard, set `SOLAR_DASHBOARD=true` when starting the container and publish
port `8765` too; see the [example](dashboard.md#combined-container).

Create a network for consumers and a persistent data volume, then build and run
the collector. These commands use Docker; you can substitute `podman` if that
is your container runtime. Both automatically find `Dockerfile`:

```sh
docker build -t solar-city-inverter-radio .
docker network create solar-city
docker volume create solar-city-inverter-radio-data
docker run --rm --name solar-city-collector --network solar-city \
  -p 127.0.0.1:8766:8766 \
  --env-file .env \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Fill in `.env` before running the container. Keep a persistent data volume and
stop the foreground collector before starting its container replacement. The
HTTP port is published to host loopback only. Add your own service management or
reverse proxy according to your environment.

Keep `.env` private. Do not bake installation values into the image.

Containers start with `python -m runtime`. Rebuild the image after updating and
keep the data volume across container replacements. The [dashboard guide](dashboard.md)
covers the separate viewer and optional combined container.

## Contributing a useful reproduction report

Include inverter model, radio firmware, bridge firmware, Python version, and the
specific protocol exchange that differs. Replace device identities consistently
in both decoded fields and packet bytes. Omit serial numbers, hostnames, IPs,
locations, production history, and credentials. Do not upload a raw database or
capture as a default bug-report attachment.
