# SolarCity Inverter Radio

A local replacement for the **SolarCity / Tesla solar monitoring box**, using a
SMLIGHT SLZB-06U and Python.

**Tested inverter: Power-One PVI-5000-OUTD-US-Z**, equipped with its SolarCity-era
Digi XBee radio. Power-One is the inverter manufacturer; SolarCity supplied the
monitoring setup this project replaces.

The collector maintains the inverter's existing radio network, answers startup
verification, and reads SunSpec measurements over Modbus RTU. A small local
web interface and SQLite history store are included. Normal operation reads
inverter registers and does not require a Tesla account or cloud connection.

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

## Quick start

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

3. If your radio settings are unknown, gather them before creating the config.
   Use a spare receiver or stop any program using this bridge; discovery resets
   and configures the receiver. Replace `YOUR_BRIDGE_HOST` with its hostname or IP:

   ```sh
   .venv/bin/python tools/discover_radio.py \
     --host YOUR_BRIDGE_HOST \
     --exclusive-radio \
     --output captures/discovery.json
   ```

   This listens across all channels for about five minutes. Follow the
   [report-to-config walkthrough](docs/discovery.md#apply-reviewed-values) to
   identify your inverter and check which settings were observed. The guide also
   covers incomplete reports and analyzing existing captures without a radio.
4. Copy the template, then edit `radio.local.json`:

   ```sh
   cp config.example.json radio.local.json
   ```

   Supply your bridge hostname, channel, PAN IDs, expected original collector
   EUI, and inverter EUI. Required fields are blank or placeholders; use your own
   reviewed settings. Capture reports and local configs are ignored by Git.
5. Validate without touching the radio:

   ```sh
   .venv/bin/python config.py
   ```

6. Power off the original collector, if present, and stop any other program
   connected to the SMLIGHT bridge. Then start the replacement:

   ```sh
   .venv/bin/python server.py
   ```

Open <http://127.0.0.1:8765>. The collector first listens for 30 seconds, then
maintains the network and waits for an identifiable inverter packet before
polling. Measurements normally update every two minutes. A quiet inverter may
need to return to operation before data appears.

## Documentation

- [Discover radio settings](docs/discovery.md): gather evidence without a preconfigured inverter address.
- [Reproduce the integration](docs/setup.md): hardware, settings, startup, validation, and containers.
- [Radio and measurement protocol](docs/protocol.md): coordinator exchanges, startup reply, wire format, and register reads.
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

The local HTTP server has no authentication. It binds to localhost by default;
set `SOLAR_BIND` only when you intend to expose it on a trusted network. The API
includes operational details and the inverter's reported serial number.

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
