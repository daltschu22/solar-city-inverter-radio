# SolarCity Inverter Radio

A local replacement for the **SolarCity / Tesla solar monitoring box**, using a
SMLIGHT SLZB-06U and Python.

**Tested inverter: Power-One PVI-5000-OUTD-US-Z**, equipped with its SolarCity-era
Digi XBee radio. Power-One is the inverter manufacturer; SolarCity supplied the
monitoring setup this project replaces.

The collector maintains the inverter's existing radio network, answers startup
verification, and reads SunSpec measurements over Modbus RTU. It saves readings
to SQLite and exposes a local JSON API. Normal operation reads
inverter registers and does not require a Tesla account or cloud connection.

**The collector runs on its own.** Use its API with your own software or Home
Assistant, or run the included dashboard as a separate, optional process.

| Component | Command / interface | Role |
| --- | --- | --- |
| Collector | `python collector.py`, port `8766` | Owns the SMLIGHT connection, gathers readings, saves history, serves JSON |
| Optional dashboard | `python server.py`, port `8765` | Reads the collector API and displays the readings |
| Home Assistant | [REST sensor example](docs/home-assistant.md) | Reads the collector API directly; the dashboard is optional |

**Status:** experimental, verified on one installation. The original implementation
recovered from a radio reset, a leave/rejoin cycle, and one overnight-to-morning
transition with the original collector powered off. This standalone export has
software tests; it has not separately been deployed against a second inverter.

## Compatibility

| Component | Tested configuration |
| --- | --- |
| Inverter | Power-One PVI-5000-OUTD-US-Z |
| Inverter radio | Legacy Digi XBee, firmware reported as `0x23a6` |
| Bridge | SMLIGHT SLZB-06U with CC2652P |
| Bridge firmware | OpenThread RCP, SMLIGHT build `20260304` |
| Bridge transport | Spinel over TCP port `6638`, UART `460800` |
| Over-the-air protocol | Unsecured legacy Digi Zigbee, stack profile `0` |
| Host | Python 3.12 or newer; tested with 3.12 and 3.14 |

The RCP firmware provides raw IEEE 802.15.4 access. Python supplies the coordinator
behavior. This project does not create a Thread network or use Zigbee2MQTT/ZHA.
Encrypted networks, other inverter families, multiple inverters, and pairing with
a completely new collector identity are not supported or validated here.

The working method **reuses the original collector's EUI-64 and network settings**.
The included [discovery tool](docs/discovery.md) can learn those values from
inverter traffic without a site config or packets from the original collector.
This has been demonstrated on an operating replacement network; discovery from
a fully unjoined inverter remains unverified. If you have the original collector,
keep it powered off while this replacement runs.

### Identify the inverter

<img src="docs/images/power-one-pvi-5000-outd-us-z-front.jpg" alt="Front of the tested inverter, with upper cooling fins, a display strip, SolarCity branding, and a lower PV DC disconnect" width="360">

Front of the tested **Power-One PVI-5000-OUTD-US-Z**, with SolarCity branding
and the PV DC disconnect below. Use the enclosure as a visual reference, then
confirm the exact model on your equipment's label and check radio compatibility
against the table above. A matching enclosure alone does not confirm support.

### Original SolarCity collector

<img src="docs/images/original-solarcity-collector.jpg" alt="Original white SolarCity monitoring collector with an external black antenna and three indicator symbols" width="360">

The original SolarCity monitoring box replaced by this project. The SMLIGHT
SLZB-06U and Python collector take over its radio-network and measurement role.
Keep the original box powered off while running the replacement, which reuses
its radio identity.

## Quick start

**Part 1: collect data.** You can stop after these steps and use the JSON API.
Run one collector per SMLIGHT. Other consumers share its API.

1. Configure the SLZB-06U for the tested RCP firmware and network serial bridge.
   See the [setup guide](docs/setup.md) for firmware details and obtaining your
   network settings.
2. Install the application:

   ```sh
   git clone https://github.com/daltschu22/solar-city-inverter-radio.git
   cd solar-city-inverter-radio
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. **Find your inverter's radio settings.** Skip to step 4 if you already know
   its channel, both PAN IDs, and the inverter and original collector EUIs.

   Use the same SMLIGHT that will run the replacement. Close any other program
   connected to it, including this project's `collector.py`, before scanning.
   `--exclusive-radio` confirms that this script has the SMLIGHT to itself;
   scanning resets the SMLIGHT radio into listening mode.

   Run this from the repository folder on your computer. Replace
   `YOUR_BRIDGE_HOST` with the **SMLIGHT's IP address or hostname**—the address
   you use to open its web interface, without `http://` or a trailing slash:

   ```sh
   .venv/bin/python tools/discover_radio.py \
     --host YOUR_BRIDGE_HOST \
     --exclusive-radio \
     --output captures/discovery.json
   ```

   Leave the inverter powered and wait about five minutes for the scan to finish.
   If your original SolarCity box is still working, it can stay on during this
   listening step; power it off before starting the replacement in step 6.

   Look for `5/5 settings` in the terminal output. Open `captures/discovery.json`
   to review the detected inverter and its values, then continue to step 4.
   If it finds fewer settings or no inverter, follow
   [If the report is incomplete](docs/discovery.md#if-the-report-is-incomplete)
   before continuing. Discovery may be incomplete, especially without a working
   collector; leave missing values unresolved rather than guessing them.
4. Copy the template, then edit `radio.local.json`:

   ```sh
   cp config.example.json radio.local.json
   ```

   Enter your SMLIGHT address and the five reviewed radio settings. The
   [field-by-field mapping](docs/discovery.md#apply-reviewed-values) shows exactly
   which report value goes into each config field. Required fields are blank or
   placeholders; use your own settings. Capture reports and local configs are
   ignored by Git.

   You can also supply all settings through environment variables, without a
   JSON file, or override selected JSON fields. Copy `config.example.env` to
   `.env` and follow the [environment setup](docs/setup.md#environment-variables).
5. Validate without touching the radio:

   ```sh
   .venv/bin/python config.py
   ```

6. Power off the original collector, if present, and stop any other program
   connected to the SMLIGHT bridge. Then start the replacement:

   ```sh
   .venv/bin/python collector.py
   ```

Read the JSON at <http://127.0.0.1:8766/api/live> or use:

```sh
curl http://127.0.0.1:8766/api/live
```

The collector first listens for 30 seconds, then
maintains the network and waits for an identifiable inverter packet before
polling. Measurements normally update every two minutes. A quiet inverter may
need to return to operation before data appears.

`/api/history?range=24h` returns stored readings. API requests read collected
data; they do not trigger extra radio polls. See the [API guide](docs/api.md)
for fields, timestamps, and handling stale values.

## Part 2: optional dashboard

Leave the collector running. In another terminal, from this repository, run:

```sh
python3 server.py
```

Open <http://127.0.0.1:8765>. The dashboard connects to the collector on port
`8766` by default and needs only Python's standard library. Stopping or
restarting it leaves collection running. It can also run on a different computer;
see the [dashboard guide](docs/dashboard.md).

## Home Assistant

Home Assistant can read the collector directly using its built-in REST sensors.
The [copyable configuration](docs/home-assistant.md) provides power in W and
cumulative energy in kWh for the Energy dashboard, including availability checks
for stale or missing readings. This works with the dashboard process stopped.

**Upgrading from the combined process:** `server.py` now runs only the dashboard.
Start `collector.py` with your existing `radio.local.json` and database first.
Existing SQLite data needs no migration. Update service/container commands using
the [setup guide](docs/setup.md) and [dashboard guide](docs/dashboard.md).

## Documentation

- [Discover radio settings](docs/discovery.md): gather evidence without a preconfigured inverter address.
- [Reproduce the integration](docs/setup.md): hardware, settings, startup, validation, and containers.
- [Radio and measurement protocol](docs/protocol.md): coordinator exchanges, startup reply, wire format, and register reads.
- [Collector API](docs/api.md): read collected data from your own software.
- [Optional dashboard](docs/dashboard.md): a separate viewer for the collector API.
- [Home Assistant](docs/home-assistant.md): REST sensors and Energy dashboard setup.
- [Article draft](docs/article.md): a publishable explanation focused on the useful implementation details.
- [Sources and related projects](docs/references.md): vendor documentation and prior community work.
- [Contributing](CONTRIBUTING.md): offline tests and privacy rules for reports and captures.

## Data and operation

One read-only query is sent every 60 seconds, alternating production power with
energy and diagnostics. The coordinator also handles event-driven replies and
sends link status every 15 seconds. These maintenance packets are separate from
measurement polling. Queries are serialized, and MAC delivery retries are bounded.

Only validated responses create readings. Missing data stays missing. Optional
sunrise/sunset inference can label an overnight gap; it is not a reported inverter
sleep state. No scheduled radio resets or watchdog-setting changes are performed.

The database lives in `data/solar-history.sqlite3` by default. Configuration,
captures, database files, and logs are ignored by Git. Tests use fictional device
identities and synthetic telemetry; no household production history is included.

Both HTTP services bind to localhost by default and have no authentication.
Set `SOLAR_API_BIND` for the collector API or `SOLAR_BIND` for the dashboard when
you intend to expose them on a trusted network. The API includes operational
details and the inverter's reported serial number.

## Development

```sh
.venv/bin/python -m pip install -r requirements.txt
PATH="$PWD/.venv/bin:$PATH" ./check
```

`./check` runs the Python and JavaScript suites and the publication-hygiene check.
Node.js 20 or newer is needed for the JavaScript tests. Tests use a separate,
synthetic configuration and do not connect to a radio.

## License

Project code and documentation are MIT licensed. Bundled Apache ECharts retains
its Apache-2.0 license and notices. See [third-party notices](THIRD_PARTY_NOTICES.md).
This independent project is not affiliated with Tesla, SolarCity, Power-One,
Digi, or SMLIGHT.
