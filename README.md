# Power One Radio

A local replacement collector for a **Power-One PVI-5000-OUTD-US-Z with a
SolarCity-era Digi XBee radio**, using a SMLIGHT SLZB-06U and Python.

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
Those values must come from your equipment or a suitable capture. The original
collector must remain powered off while this replacement runs. The project does
not yet provide automatic commissioning when those settings are unknown.

## Quick start

1. Configure the SLZB-06U for the tested RCP firmware and network serial bridge.
   See the [setup guide](docs/setup.md) for firmware details and obtaining your
   network settings.
2. Install and configure the application:

   ```sh
   git clone https://github.com/daltschu22/power-one-radio.git
   cd power-one-radio
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp config.example.json radio.local.json
   ```

3. Edit `radio.local.json`. Every required radio field is intentionally blank or
   a placeholder. Supply your bridge hostname, channel, PAN IDs, original collector
   EUI, and inverter EUI. Do not copy identities from another installation.
4. Validate without touching the radio:

   ```sh
   .venv/bin/python config.py
   ```

5. Power off the original collector and stop any other program connected to the
   SMLIGHT bridge. Then start the replacement:

   ```sh
   .venv/bin/python server.py
   ```

Open <http://127.0.0.1:8765>. The collector first listens for 30 seconds, then
maintains the network and waits for an identifiable inverter packet before
polling. Measurements normally update every two minutes. A quiet inverter may
need to return to operation before data appears.

## Documentation

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
