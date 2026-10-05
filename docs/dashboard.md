# Optional dashboard

The dashboard is a separate viewer for the [collector API](api.md). The collector
must be running to provide readings. It owns the radio and database; the dashboard
needs only the API URL and Python 3.12 or newer. It does not load `radio.local.json`
or require the radio's Python dependencies.

## On the same computer

Finish [part 1: collection](setup.md) and leave `collector.py` running. In another
terminal, from the repository directory:

```sh
python3 server.py
```

Open <http://127.0.0.1:8765>. The dashboard proxies its read requests to
`http://127.0.0.1:8766`. You can close the browser or stop/restart this process
while the collector keeps gathering data.

## On a different computer

Allow the collector API to listen on a trusted network interface. For example,
on the collector computer:

```sh
SOLAR_API_BIND=0.0.0.0 .venv/bin/python collector.py
```

On the dashboard computer, set the API URL to the **collector computer**, using
its reachable hostname or IP:

```sh
SOLAR_COLLECTOR_URL=http://COLLECTOR_HOST:8766 python3 server.py
```

The browser talks to the dashboard; the dashboard talks to the collector. No
browser CORS configuration is needed. Both services are unauthenticated, so use
trusted network access or your own authenticated reverse proxy.

| Dashboard setting | Default |
| --- | --- |
| `SOLAR_COLLECTOR_URL` | `http://127.0.0.1:8766`; HTTP(S) origin with no path |
| `SOLAR_BIND` | `127.0.0.1`; dashboard listening address |
| `PORT` | `8765`; dashboard HTTP port |

If the collector is unreachable, the dashboard's API requests return `502` and
the UI shows the connection failure. When the collector returns, subsequent
requests resume normally. `/healthz` on the dashboard checks only this viewer.

## Separate dashboard container

With the collector running on the `solar-city` network from the setup guide,
use the same image and override its command to start only the dashboard:

```sh
podman run --rm --name solar-city-dashboard --network solar-city \
  -p 127.0.0.1:8765:8765 \
  -e SOLAR_COLLECTOR_URL=http://solar-city-collector:8766 \
  solar-city-inverter-radio python server.py
```

This command starts only the viewer. It needs no radio config or database mount.
Container DNS resolves `solar-city-collector` on the shared network. Using
`localhost` inside the dashboard container would refer to that container itself.

## Combined container

Set `SOLAR_DASHBOARD=true` when starting the container to run both programs.
No different image or build flag is needed. Leave the variable unset (or set
it to `false`) to run only the collector. Both modes expose the collector API
on port `8766`; the dashboard adds port `8765`.

Finish radio discovery and fill in `.env` using the
[environment setup](setup.md#environment-variables). Stop any existing collector
that owns the bridge, then:

```sh
podman build -t solar-city-inverter-radio -f Containerfile .
podman volume create solar-city-inverter-radio-data
podman run --rm --name solar-city-combined --stop-timeout 30 \
  -p 127.0.0.1:8766:8766 \
  -p 127.0.0.1:8765:8765 \
  -e SOLAR_DASHBOARD=true \
  --env-file .env \
  -v solar-city-inverter-radio-data:/data \
  solar-city-inverter-radio
```

Open <http://127.0.0.1:8765>. Existing installations should reuse their history
volume. To use JSON instead of `.env`, replace `--env-file .env` with the
read-only config mount from the [collector setup](setup.md#run-the-collector-in-a-container).

The container starts two Python processes:

```mermaid
flowchart LR
    inverter[Inverter] --> smlight[SMLIGHT]
    smlight --> collector["Collector + API :8766"]
    collector --> database[(SQLite)]
    browser[Browser] --> dashboard["Dashboard :8765"]
    dashboard -->|GET /api/live or /api/history| collector
```

The collector owns the radio and database. The dashboard proxies `/api/live`
and `/api/history` to its API; these requests never trigger radio polls. The
collector API listens on all container interfaces by default; the example publishes
both ports only to host loopback. `SOLAR_API_PORT` changes the API port, and the
launcher connects the dashboard to that local API automatically. `PORT` sets the
dashboard port; the two ports must differ. `SOLAR_API_BIND` can restrict the API's
listening address. `SOLAR_COLLECTOR_URL` is set automatically in this mode.

Closing the browser leaves collection running. Stopping the container stops
both processes. If either process exits, the launcher stops the other and exits
with an error, so your container service manager can restart the whole service.
It forwards termination signals, waits up to 15 seconds for graceful shutdown,
and reaps both processes. The example allows 30 seconds before the container
runtime forces a stop.

[Home Assistant](home-assistant.md) and other API consumers can keep using the
collector on port `8766` whether the dashboard is enabled or not. The dashboard
also proxies `/api/live` and `/api/history` on port `8765`.
