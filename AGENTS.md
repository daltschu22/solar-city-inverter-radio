# Agent guide

Read [README.md](README.md) for compatibility and use [docs/setup.md](docs/setup.md)
as the installation procedure. This file records decisions and constraints for
agents helping with setup or changing the project.

## How the application works

- `collector/api.py` owns the SMLIGHT connection, maintains the radio network, polls
  measurements, and reads/writes SQLite. Its HTTP API defaults to port `8766`.
- `dashboard/server.py` is the optional dashboard on port `8765`. It reads the collector's
  HTTP API; it never opens the radio or SQLite database. Set
  `SOLAR_COLLECTOR_URL` when the collector is on another host or container.
- `Dockerfile` builds one image. The default command runs the collector. Setting
  `SOLAR_DASHBOARD=true` also starts the dashboard, connected automatically to
  the local collector. `runtime/entrypoint.py` selects the mode; `runtime/combined.py` supervises
  both processes. API requests do not trigger inverter polls.
- The database belongs in persistent storage: `/data/solar-history.sqlite3`
  inside the container, configurable with `SOLAR_HISTORY_PATH`.

## Helping someone set it up

- Establish the inverter and bridge models, intended collector host, bridge LAN
  address, and whether another program is using the radio. Use information
  already provided and ask only for missing details.
- Follow the setup guide in order. Assume a new user needs discovery; run its
  export command to generate configuration rather than asking for PAN IDs or
  EUIs. A verified configuration for the same equipment can be reused.
- Review the selected equipment and discovered evidence. For missing values or
  multiple candidates, follow [discovery help](docs/discovery.md). Never guess
  identities, use synthetic test settings on live hardware, or overwrite a
  working configuration. Saved captures can be reanalyzed without a live scan.
- Power off the original SolarCity/Tesla collector before replacement collection.
  For `SOLAR_COLLECTOR_MODE=passive`, leave the original box operating. Passive
  mode must never transmit, assume its identity, or fall back to replacement.
  It requires both sides of complete observed exchanges; live SMLIGHT reception
  remains unverified. Do not change a working installation's mode for routine tests.
  Establish exclusive bridge access for scans and collection. Act on existing
  authorization; ask when required physical state or service ownership is unknown.
- Use the user's chosen service manager and persistent storage. Keep local
  settings private. The [configuration reference](docs/setup.md#environment-variables)
  owns the supported environment variables, defaults, and ranges.
- Verify fresh readings and saved history, not just HTTP availability. Report
  what was verified in software and what requires hardware observation. Discovery
  from a fully unjoined inverter and fresh pairing remain unverified.
- Add the [dashboard](docs/dashboard.md) or [Home Assistant](docs/home-assistant.md)
  only when wanted; collection alone is a complete setup.

## Radio firmware and bridge configuration

Follow [Prepare the bridge](docs/setup.md#prepare-the-bridge) for the firmware
and web-interface steps. Inspect the model, radio chip, and current versions
first; record existing network settings privately. The core firmware runs the
web interface, while the radio firmware supplies the RCP interface.

Flash only within the authorized bridge-setup scope. Check the offered image's
chip and build against the tested version in that guide; report an unavailable
build rather than claiming another is equivalent. Preserve a working matching
installation. Leave inverter firmware and operating settings alone.

Give discovery exclusive bridge access and compare its reported firmware with
the setup guide. A scan with no inverter frames does not by itself establish a
firmware problem. Keep radio identity selection tied to observed evidence.

## Privacy and changes

- Keep installation commands in `docs/setup.md`. The README introduces the
  project and links to setup; specialist guides own their options and reference
  details. Link to the owning guide instead of copying its procedure.
- Describe the current setup directly. Leave development history, migration notes,
  and comparisons with previous approaches out of user-facing docs unless requested.
- Keep installation hosts/IPs, EUIs, serial numbers, coordinates, credentials,
  raw captures, logs, and production databases out of commits, images, and public
  reports. Use fictional identities in examples and tests. Summarize validation
  without dumping private settings. `.gitignore` and `.dockerignore` exclude
  the standard local paths; review any additional paths you introduce.
- Preserve the collector/dashboard API boundary and existing SQLite history.
  Keep radio requests serialized; do not shorten polling intervals or change
  coordinator behavior without evidence and relevant protocol tests.
- Read [protocol](docs/protocol.md) before changing radio behavior and
  [API documentation](docs/api.md) before changing response fields. Update the
  corresponding docs when commands, settings, or behavior change.
- Run `./check` before committing code changes.
  Development checks require Node.js 20+ as well as the Python dependencies
  (CI uses Node.js 22);
  Node.js is not required at runtime. The script uses synthetic radio settings
  for offline Python tests, JavaScript tests, and publication checks.
- Do not use live inverter access as a routine test. Build the image after
  packaging changes and use synthetic configuration with networking disabled
  for container checks. Never deploy `tests/radio.synthetic.env` to a live
  collector.
- Review tracked and untracked changes before staging. The publication checker
  scans tracked files, so also run it after staging new files. Follow the user's
  requested commit, push, and deployment scope.
