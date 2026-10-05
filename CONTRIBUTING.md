# Contributing

Get the source and Python dependencies using [Get the software](docs/setup.md#get-the-software).
Development checks also require Node.js 20 or newer. Run `./check` before
submitting changes. `.python-version` selects Python 3.12; use
`UV_PYTHON=3.14 ./check` to test the other version covered by CI. Dependency changes
belong in `pyproject.toml` and must include the updated `uv.lock`. Tests must run
without hardware, network access to a radio, or local site configuration.
`check` selects the synthetic test configuration explicitly.

The intended scope is one known Power-One/Digi inverter on an unsecured legacy
network. Changes to commissioning, security, new hardware, or register maps need
separate evidence. Describe what was tested in software and what was tested on
hardware; do not treat one as proof of the other.

Keep packet examples reproducible. Prefer constructing frames from synthetic
values. Never attach an unreviewed capture, SQLite database, private configuration,
serial number, hostname, network address, location, or production history. If a
packet is needed, replace identities consistently in decoded fields and raw bytes,
including both byte orders, and regenerate checksums for any changed payload.

Hardware photos belong in `docs/images/`. Review visible labels and surroundings
for identifying details, remove embedded metadata and auxiliary images, and
inspect the final image before committing. Record its SHA-256 in `REVIEWED_IMAGES`
in `tools/check_publication.py` after review. The check rejects unreviewed images
and changes to an approved photo, including accidentally restoring its metadata.

Preserve exclusive SMLIGHT connection ownership in both collection modes and the
startup conflict check in replacement mode. Passive mode must leave coordination
and polling to the original box, with no transmit path or automatic switch to
replacement. A test run must never contact a live bridge. Avoid adding automatic
network resets or inverter writes as recovery strategies.

Keep collection independent of the dashboard. `collector/api.py` owns the radio and
database; `dashboard/server.py` serves the optional UI and proxies reads to the collector
API. Decoder and storage modules must import independently of the dashboard.
Consumers must not create extra radio polls when reading cached measurements.

Update the setup guide and protocol documentation when behavior changes. Keep
private deployments and their Git history separate from this repository.

## Source layout

```text
collector/          Replacement polling, passive monitoring, configuration, SQLite, and JSON API
dashboard/          Web server and static assets
runtime/            Container startup and process supervision
tools/              Discovery, capture, polling, and publication utilities
tests/              Offline Python and JavaScript tests
docs/               Setup, protocol, API, and integration guides
```

Run module commands and checks from the repository root. `./check` runs the
Python tests, JavaScript tests, and publication-hygiene checks.

Keep installation commands in `docs/setup.md`. Link to those steps from the
README and specialist guides. Configuration reference follows the installation
steps; discovery internals belong in `docs/discovery.md` and wire details in
`docs/protocol.md`.
