# Agent guide

This repository replaces a SolarCity/Tesla monitoring collector with a SMLIGHT
bridge and Python. Use this guide when helping someone install it or change the
code. Read [README.md](README.md) and the relevant guide before working on a live
installation.

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

Run all `uv run ...` commands from the repository root. Keep the local `.env`
file in that directory.

1. Inspect existing configuration and services first. Reuse information already
   provided; ask only for missing details. Establish the inverter model, bridge
   model/firmware and address, intended runtime host, and whether a collector is
   already running. Collection alone is a complete installation; add the
   dashboard or Home Assistant when wanted.
2. Check [compatibility](README.md#compatibility), then follow
   [radio firmware and bridge configuration](#radio-firmware-and-bridge-configuration)
   below before discovery or collection.
3. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run
   `uv sync --locked`. `pyproject.toml` declares dependencies, `uv.lock`
   pins them, and `.python-version` selects Python 3.12 by default. Container
   builds use the same lockfile. Keep metadata and lockfile changes together.
4. If radio settings are missing, follow [discovery](docs/discovery.md). Offline
   capture analysis does not touch hardware. A live scan resets/configures the
   SMLIGHT for listening, so establish exclusive access before passing
   `--exclusive-radio`. Do not run it alongside a collector using that bridge.
   A working original SolarCity box may stay on during passive discovery.
5. Review the selected inverter's evidence and use the
   [report-to-config mapping](docs/discovery.md#apply-reviewed-values). Never
   guess unknown/conflicting fields or use test identities for live operation.
   The collector EUI must be the identity the inverter expects, not the new
   bridge's factory EUI. Recovery from a fully unjoined inverter with unknown
   settings is unverified; explain missing evidence instead of promising pairing.
6. Save installation settings in ignored `.env` or the service environment.
   Preserve existing settings; do not overwrite them with a template.
   Pass `--env-file .env` to `uv run` to load reviewed settings, as shown in
   [setup](docs/setup.md#environment-variables). Validate using
   `uv run --env-file .env python -m collector.config`; this opens no radio connection.
7. Before starting collection, establish that the original SolarCity/Tesla
   collector is powered off and this process has exclusive use of the bridge.
   Act on existing user authorization and known state; ask when a required
   physical step or service ownership is unknown. Do not change inverter
   operating settings or flash firmware as an incidental setup action.
8. Follow the [container commands](docs/setup.md#run-the-collector-in-a-container)
   or run `uv run --env-file .env python -m collector`. Build with
   `docker build -t solar-city-inverter-radio .`. Preserve the existing data
   volume on upgrades. Use the user's chosen service manager for unattended
   operation and document the start, stop, and update commands.
9. Verify `/api/live` and `/api/history`, response freshness, and plausible units.
   `/healthz` only confirms HTTP availability. Startup includes 30 seconds of
   listening; measurements normally update about every two minutes. A quiet
   inverter is not proof of failure or nighttime standby. Report what was
   actually verified and any remaining hardware-dependent checks.

For the optional viewer, follow [dashboard setup](docs/dashboard.md). For Home
Assistant, use the [REST sensor example](docs/home-assistant.md); it does not
need the dashboard. Publish HTTP ports on host loopback by default, as the
examples do. Remote access needs the user's intended trusted network or
authenticated proxy; the application has no built-in authentication.

## Radio firmware and bridge configuration

The tested bridge is a **SMLIGHT SLZB-06U with a CC2652P radio**. Confirm the
model and radio chip in its web interface. Record its current core firmware,
radio firmware, and network settings privately before making changes. The core
firmware runs the web interface and network bridge; the radio firmware supplies
the OpenThread RCP interface used by this collector.

| Setting | Tested value |
| --- | --- |
| Radio firmware | Official SMLIGHT CC2652P OpenThread RCP build `20260304` |
| Serial bridge transport | TCP over the local network |
| TCP port | `6638` |
| Radio UART baud rate | `460800` |
| Host connection | Reachable, stable bridge hostname or IP |

1. Open the bridge's web interface at its LAN address. Establish exclusive use
   of the bridge before changing firmware or mode; stop any collector, capture
   tool, ZHA, Zigbee2MQTT, or OTBR connected to it.
2. If the radio needs flashing and bridge setup is within the user's authorized
   scope, follow SMLIGHT's [RCP flashing instructions](https://smlight.tech/manual/slzb-06/guide/thread-matter/).
   The documented web workflow selects **Mode → Matter-over-Thread** and waits
   for flashing to finish. Check the offered image's chip and build before
   applying it. A core firmware update alone does not install radio firmware.
   If the tested build is unavailable, report that gap; another build requires
   validation. Leave a working matching installation as configured.
3. Configure network serial access with TCP port `6638` and UART baud `460800`.
   Use the bridge's Ethernet or Wi-Fi connection and retain a stable address,
   such as a DHCP reservation. SMLIGHT's [web configuration guide](https://smlight.tech/manual/slzb-06/guide/configuration/)
   describes its network and mode controls. Menu labels can vary by core version.
   This application uses raw IEEE 802.15.4 through RCP; stop at bridge setup in
   the vendor guide. Do not create a Thread network or install its OTBR add-on
   for this integration. Leave the inverter's Digi firmware and settings alone.
4. Follow [discovery](docs/discovery.md#gather-a-new-capture) from the intended
   collector host. A successful scan verifies TCP and Spinel access; receiving
   no inverter frames does not by itself establish a firmware problem. Compare
   the capture tool's `Radio firmware:` output with the full tested version in
   [bridge setup](docs/setup.md#prepare-the-bridge).
5. Put the bridge address and port in `SOLAR_RADIO_HOST` and `SOLAR_RADIO_PORT`.
   Populate the channel, PAN IDs, and both EUIs from reviewed discovery evidence
   using the [field mapping](docs/discovery.md#apply-reviewed-values). The collector
   applies these radio settings at startup. The expected collector EUI comes
   from the inverter's network; preserve its displayed byte order and leading
   zeroes. Keep settings in ignored `.env` or the service environment.
6. Run `uv run --env-file .env python -m collector.config`. This validates values
   offline. Finish capture, power off the original SolarCity collector if present,
   then start collection and verify fresh readings using the setup steps above.

## Privacy and changes

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
