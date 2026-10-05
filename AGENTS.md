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

Run all `python -m ...` commands from the repository root. Configuration stays
in that directory; the source folders do not contain installation settings.

1. Inspect existing configuration and services first. Reuse information already
   provided; ask only for missing details. Establish the inverter model, bridge
   model/firmware and address, intended runtime host, and whether a collector is
   already running. Collection alone is a complete installation; add the
   dashboard or Home Assistant when wanted.
2. Check [compatibility](README.md#compatibility) and
   [bridge setup](docs/setup.md#prepare-the-bridge). The tested hardware is a
   Power-One PVI-5000-OUTD-US-Z with its legacy Digi radio and a SMLIGHT SLZB-06U
   CC2652P running the documented OpenThread RCP firmware. This application owns
   the raw radio connection; it does not use ZHA, Zigbee2MQTT, or an OTBR.
3. Install Python 3.12+ dependencies in a virtual environment if using Python or
   the discovery tools: `python3 -m venv .venv`, then
   `.venv/bin/pip install -r requirements.txt`. Container builds install their
   own runtime dependencies.
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
6. Save installation settings in ignored `.env` or `radio.local.json`. Preserve
   existing settings; do not overwrite them with a template. Environment
   variables override JSON. Python does not load `.env` automatically: export
   its reviewed values as shown in [setup](docs/setup.md#environment-variables).
   If `SOLAR_CONFIG` is set, its file must exist. Validate using
   `.venv/bin/python -m collector.config`; this opens no radio connection.
7. Before starting collection, establish that the original SolarCity/Tesla
   collector is powered off and this process has exclusive use of the bridge.
   Act on existing user authorization and known state; ask when a required
   physical step or service ownership is unknown. Do not change inverter
   operating settings or flash firmware as an incidental setup action.
8. Follow the [container commands](docs/setup.md#run-the-collector-in-a-container)
   or run `.venv/bin/python -m collector`. Build with
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

## Privacy and changes

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
- Run `PATH="$PWD/.venv/bin:$PATH" ./check` before committing code changes.
  Development checks require Node.js 20+ as well as the Python dependencies
  (CI uses Node.js 22);
  Node.js is not required at runtime. The script uses synthetic radio settings
  for offline Python tests, JavaScript tests, and publication checks.
- Do not use live inverter access as a routine test. Build the image after
  packaging changes and use synthetic configuration with networking disabled
  for container checks. Never deploy `tests/config.synthetic.json` to a live
  collector.
- Review tracked and untracked changes before staging. The publication checker
  scans tracked files, so also run it after staging new files. Follow the user's
  requested commit, push, and deployment scope.
