# SolarCity Inverter Radio

Read solar production locally using a **SMLIGHT SLZB-06U** in place of the
SolarCity / Tesla monitoring box. The collector talks to the inverter's radio,
saves readings to SQLite, and provides a JSON API.

**Tested inverter: Power-One PVI-5000-OUTD-US-Z** with its SolarCity-era Digi XBee
radio. Power-One made the inverter; SolarCity supplied the monitoring equipment.

Use the collector on its own, connect Home Assistant, or add the included
optional dashboard. Normal operation uses your local network and does not require
a Tesla account or cloud connection.

## Quick start

**[Follow the setup guide →](docs/setup.md)**

Configure the SMLIGHT, discover your inverter, and start collecting data with
or without the included dashboard.

## Compatibility

| Equipment | Tested configuration |
| --- | --- |
| Inverter | Power-One PVI-5000-OUTD-US-Z |
| Inverter radio | Legacy Digi XBee, firmware `0x23a6` |
| Bridge | SMLIGHT SLZB-06U with CC2652P; firmware setup is in the installation guide |
| Computer | Python 3.12 or newer; tested with 3.12 and 3.14 |

**Status:** experimental, based on one installation. Independent hardware
reproductions and long-term reliability remain unverified.

The collector uses the inverter's existing radio network and the original
collector's radio identity. Discovery can recover these from inverter traffic
on an operating network. Recovery from a fully unjoined inverter is unverified.
Other inverter families, encrypted networks, multiple inverters, and pairing
with a new collector identity are not supported or validated here.

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

## Use your readings

Once the collector is working, you can use its API directly or add:

- [The dashboard](docs/dashboard.md) for a web view of production and history.
- [Home Assistant](docs/home-assistant.md) for power and energy sensors.

## Reference

- [Configuration](docs/setup.md#environment-variables): environment variables and timing settings.
- [Discovery help](docs/discovery.md): incomplete scans, selecting an inverter, and saved captures.
- [API](docs/api.md): reading measurements from your own software.
- [Radio protocol](docs/protocol.md): how communication with the inverter works.
- [Background article](docs/article.md) and [sources](docs/references.md).
- [Contributing](CONTRIBUTING.md) and [agent instructions](AGENTS.md).

## License

Project code and documentation are MIT licensed. Bundled Apache ECharts retains
its Apache-2.0 license and notices. See [third-party notices](THIRD_PARTY_NOTICES.md).
This independent project is not affiliated with Tesla, SolarCity, Power-One,
Digi, or SMLIGHT.
