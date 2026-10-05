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

Use the tested SMLIGHT SLZB-06U with its CC2652P radio running the official
OpenThread RCP build `20260304`. The working firmware version string was:

```text
OPENTHREAD/1.4.0.0; CC13XX_CC26XX thread-v1.4-ti-1.0-ea-1.0; SLZB-06U 20260304
```

Configure its network serial bridge for TCP port `6638` and UART `460800`.
Give the bridge a stable hostname or IP address reachable from the host running
Python. The host does not need a USB connection. The tested bridge used USB-C
power and Wi-Fi.

SMLIGHT's [RCP setup instructions](https://smlight.tech/manual/slzb-06/guide/thread-matter/)
cover the firmware mode. Use the RCP portion of that guide; this collector owns
the TCP bridge directly. Do not run an OpenThread Border Router against it.
A different radio, firmware build, or Spinel implementation needs separate validation.

## Obtain the network identity

These are distinct parameters, even when their names sound similar:

| Config field | Meaning and source |
| --- | --- |
| `host` | Hostname or IP of your SMLIGHT bridge |
| `port` | TCP bridge port; normally `6638` |
| `channel` | Actual IEEE 802.15.4 channel, decimal 11 through 26 |
| `pan_id` | 16-bit operating PAN ID, shown in MAC headers or XBee `OI` |
| `extended_pan_id` | 64-bit operating network identity, found in beacons or XBee `OP` |
| `collector_eui` | Original collector's 64-bit radio identity |
| `inverter_eui` | Inverter radio's 64-bit identity |
| `initial_address` | Optional short address to offer on association; normally omit |

XBee `ID` is the **configured** extended PAN setting. `ID=0` means automatic
selection; it does not mean the operating extended PAN is zero. Record the
operating value. Channel displays may be hexadecimal: `0x14` is decimal 20.

Start with the [discovery tool](discovery.md) if the addresses are unknown. It
can learn from inverter-originated traffic and also analyze a capture of a working
original collector. Accessible radio configuration is another source. Follow the
[report-to-config walkthrough](discovery.md#apply-reviewed-values) to map the
observed fields into `radio.local.json`. Beacons contain the extended PAN; IEEE
addresses appear in suitable network headers and device announcements. The
collector's short address is `0x0000`. The inverter's short address may change
and is learned at runtime.

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

Install dependencies and copy the template as shown in the README. Use decimal
integers for `channel` and `port`, and `0x`-prefixed strings for PAN IDs. EUIs use
exactly 16 hexadecimal digits, without colons, in the ordinary display order.
The code handles conversion to the wire byte order.

`radio.local.json` is loaded from the working directory. Set `SOLAR_CONFIG` to an
absolute path to keep it elsewhere. Environment variables override fields in the
file and can supply all settings without a JSON file. Validation rejects unknown
JSON fields, missing required settings, invalid ranges, and matching
inverter/collector identities. No live radio session can start without valid
installation settings. Changes require restarting the app. If `SOLAR_CONFIG` is
set, that file must exist and contain valid JSON even when using overrides.

Run `uv run python -m collector.config` to validate without opening a connection.

The optional `initial_address` defaults to a synthetic unicast seed. It is used
when assigning an address to the known inverter; it is not assumed to be a live
neighbor. The current verified short address is persisted in SQLite. Keep a
separate database for each installation.

### Environment variables

| Variable | JSON field | Requirement or default |
| --- | --- | --- |
| `SOLAR_RADIO_HOST` | `host` | Required; SMLIGHT hostname or IP |
| `SOLAR_RADIO_PORT` | `port` | `6638` |
| `SOLAR_RADIO_CHANNEL` | `channel` | Required; channel `11`–`26` |
| `SOLAR_PAN_ID` | `pan_id` | Required; observed 16-bit PAN ID |
| `SOLAR_EXTENDED_PAN_ID` | `extended_pan_id` | Required; observed 64-bit extended PAN ID |
| `SOLAR_COLLECTOR_EUI` | `collector_eui` | Required; collector identity the inverter expects |
| `SOLAR_INVERTER_EUI` | `inverter_eui` | Required; inverter radio identity |
| `SOLAR_INITIAL_ADDRESS` | `initial_address` | `0x2345`; normally leave unset |

Numeric values accept decimal or `0x`-prefixed hexadecimal strings. EUIs use 16
hexadecimal digits without colons, in the report's display order. Unset variables
leave the JSON value or optional default intact. Empty variables are invalid;
they do not fall back to JSON or synthetic identities.

For an environment-only setup, copy and edit the template:

```sh
cp config.example.env .env
```

Fill in your bridge address and the five reviewed settings from
[discovery](discovery.md#apply-reviewed-values). The `.env` file is ignored by
Git. Load its reviewed values explicitly with uv:

```sh
uv run --env-file .env python -m collector.config
uv run --env-file .env python -m collector
```

Use this after capture has stopped and the original collector is powered off,
as described in [Start and verify](#start-and-verify). For a new environment-only
setup, leave `SOLAR_CONFIG` unset and skip creating `radio.local.json`. Your service
manager can also supply these variables directly.

Plain Python does not load `.env` automatically. If using it directly, export
the settings through your shell or service manager first. uv does not load the
file unless requested with `--env-file` (or its corresponding uv environment setting).

Additional environment settings:

| Variable | Default and purpose |
| --- | --- |
| `SOLAR_CONFIG` | `radio.local.json`; optional JSON configuration path |
| `SOLAR_HISTORY_PATH` | `data/solar-history.sqlite3` in the repository root |
| `SOLAR_API_BIND` | `127.0.0.1`; collector API listening address |
| `SOLAR_API_PORT` | `8766`; collector API port |
| `SOLAR_DASHBOARD` | `false`; set `true` to also run the dashboard with the container's default command |
| `SOLAR_LATITUDE`, `SOLAR_LONGITUDE` | Unset; optional, supply both for nighttime inference |

Coordinates stay in your runtime environment. Nighttime inference never creates
measurements or changes inverter settings. It requires a recent low-power reading
near sunset, a healthy bridge, quiet radio traffic, and no reported fault.

## Start and verify

Power off the original collector, if present. Ensure no capture tool, ZHA, Zigbee2MQTT, OTBR,
or second instance owns the SMLIGHT connection. Then run:

```sh
uv run python -m collector
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
unattended recovery. The original implementation has demonstrated those paths on
one installation; the export has not established multi-day reliability or broad
hardware compatibility.

An overnight gap is not direct proof of a hardware sleep mode. The original
installation resumed readings the following morning with the original collector
off. Allow startup time and use actual response freshness, rather than a green
HTTP health check, to judge recovery.

## Run the collector in a container

There is one image. It runs the collector and API by default. To add the
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
  --mount type=bind,src="$PWD/radio.local.json",dst=/config/radio.local.json,ro \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Make the config readable by container UID `10001`; on a single-user host, a
readable config file inside a private parent directory is sufficient. SELinux
hosts may need an appropriate label on the bind mount. Keep a persistent data
volume. Stop the foreground Python process before starting the container.
The HTTP port is published to host loopback only. Add your own service management
or reverse proxy according to your environment.

Alternatively, fill in `.env` as above and supply it to the container without
mounting a JSON file:

```sh
docker run --rm --name solar-city-collector --network solar-city \
  -p 127.0.0.1:8766:8766 \
  --env-file .env \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Keep `.env` private. Do not bake installation values into the image.

For an existing installation, reuse its database volume and config. The database
schema is unchanged. Replace commands that launch old top-level Python scripts
with `uv run python -m collector` or `uv run python -m dashboard`, with the repository root as
the working directory. Containers use `python -m runtime` automatically; rebuild
the image after updating. The [dashboard guide](dashboard.md) covers the separate
viewer and optional combined container.

## Contributing a useful reproduction report

Include inverter model, radio firmware, bridge firmware, Python version, and the
specific protocol exchange that differs. Replace device identities consistently
in both decoded fields and packet bytes. Omit serial numbers, hostnames, IPs,
locations, production history, and credentials. Do not upload a raw database or
capture as a default bug-report attachment.
