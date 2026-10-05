# Contributing

Use Python 3.12 or newer and Node.js 20 or newer. Install `requirements.txt` in a
virtual environment and run `PATH="$PWD/.venv/bin:$PATH" ./check` before submitting
changes. Tests must run without hardware, network access to a radio, or local site
configuration. `check` selects the synthetic test configuration explicitly.

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

Preserve the one-connection ownership rule and the startup conflict check. A test
run must never contact a live bridge. Avoid adding automatic network resets or
inverter writes as recovery strategies.

Update the setup guide and protocol documentation when behavior changes. Keep
private deployments and their Git history separate from this repository.
