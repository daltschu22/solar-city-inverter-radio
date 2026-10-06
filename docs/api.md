# Collector API

Complete [collector setup](setup.md) to start gathering measurements. The
read-only JSON API defaults to `http://127.0.0.1:8766`. HTTP requests read saved
data and collector status; they do not send extra queries to the inverter.

```sh
curl http://127.0.0.1:8766/api/live
curl 'http://127.0.0.1:8766/api/history?range=24h'
```

## Latest measurements: `GET /api/live`

| JSON field | Meaning |
| --- | --- |
| `solar_w` | Last validated power reading in W, or `null` before any reading |
| `timestamp` | Unix seconds when that power reading was observed |
| `solar.lifetime_wh` | Latest saved cumulative exported energy in Wh, or `null` |
| `solar.lifetime_observed_at` | Unix seconds when that energy value was saved with a validated power reading |
| `collector.connected` | Whether the collector's bridge session is connected |
| `collector.mode` | `replacement` or `passive`; top-level `mode` remains `smlight` for the transport |
| `collector.state` | Current status, such as `live`, `stale`, `discovering`, or `standby` |
| `collector.interval_seconds` | Replacement query interval (default 60); `null` in passive mode |
| `collector.reconnect_interval_seconds` | Delay before retrying a failed radio session; defaults to 15 |
| `collector.telemetry_stale_after_seconds` | Diagnostic age threshold; derived from the replacement polling interval or the passive freshness setting |
| `collector.requests`, `collector.responses`, `collector.timeouts` | Replacement polling counters; passive mode counts matched replies in `responses` and leaves the other two at zero |
| `collector.telemetry` | Identity and diagnostic groups, each with values, observation time, and a stale flag |

In passive mode, `collector.state` starts as `listening`. `packets_observed`
counts matching Digi data frames, including retries and fragments;
`observed_requests` counts complete, valid requests seen from the original box;
`responses` counts matched complete replies, including diagnostic replies.
`unmatched_responses` and `expired_requests` help identify incomplete reception.
These counters start with the process and accumulate across reconnects. They
cannot count packets the receiver never delivers.
`requests`, `timeouts`, and network-transmission counters remain zero because this
collector sends no queries. `last_poll_at` is `null`.

Passive `reading_stale_after_seconds` and `telemetry_stale_after_seconds` use
`SOLAR_PASSIVE_STALE_SECONDS` (default 300). The original box controls the cadence;
polling settings do not affect it. `warning` describes the experimental reception
limitation. Passive mode does not report inferred nighttime standby. Energy from
a separate reply is reused for at most 120 seconds, capped by the passive freshness
threshold. The following polling-cycle timings apply to replacement mode.

Power normally arrives every two polling intervals (two minutes by default).
Retained values stay visible during
outages, so a number alone is not a fresh measurement. Check its timestamp and
collector status. Zero is a valid measurement only when actually reported;
missing values are `null`.

Energy is queried less often than power. Its last saved value has its own
timestamp, which does not advance just because a newer power-only reading
arrives. Energy is attached to a power reading only when the energy response was
at most two polling intervals old (two minutes by default), so
`lifetime_observed_at` is the time of that saved power
reading, not an exact timestamp for the original energy response.

The [Home Assistant example](home-assistant.md) treats power older than three
minutes and saved energy older than fifteen minutes as unavailable with the
default polling cycle. An overnight gap remains missing data; it is not filled
with an assumed zero.

## History: `GET /api/history?range=24h`

Accepted ranges: `1h`, `6h`, `24h`, `7d`, `30d`, `1y`, and `all`. The response
contains `points` with `timestamp`, `solar_w`, and optional `lifetime_wh`, plus
summary fields and `poll_interval_seconds`. Large ranges are downsampled for
charting; check `downsampled`. Missing energy remains `null`. An invalid range
returns `400`.

Passive history returns `poll_interval_seconds: null` and
`reading_stale_after_seconds` for chart-gap handling. It uses the same points and
storage format as replacement mode.

History also includes `window_start` and `window_end` (Unix seconds) and
`night_intervals` (`start`/`end` pairs). With `SOLAR_LATITUDE` and
`SOLAR_LONGITUDE` configured, these intervals describe sunset through sunrise,
clipped to the requested window and the current time. Otherwise the list is
empty. They are display metadata for estimated zero generation, not observations;
`points`, counts, peaks, and energy totals remain measured data.

## Process health: `GET /healthz`

Returns `200` while the HTTP service responds. Use `/api/live` to determine radio
connection and measurement freshness. Unknown endpoints return `404`; the API
does not accept writes. The collector serves no dashboard assets.

## Network access

Set `SOLAR_API_BIND` and `SOLAR_API_PORT` on the collector to change its listener.
Consumers on other computers need a reachable collector host and port; the
SMLIGHT's port `6638` is a separate radio transport.

The API has no authentication and can include equipment serial numbers and local
network details. Keep it on trusted networks or put it behind your own access
controls. The dashboard proxies the same data and needs equivalent protection.
