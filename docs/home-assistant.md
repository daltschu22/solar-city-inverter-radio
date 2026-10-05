# Home Assistant without the dashboard

Run [the collector](setup.md) and point Home Assistant's
[RESTful integration](https://www.home-assistant.io/integrations/rest/) at
`/api/live`. The collector handles the inverter radio; Home Assistant reads the
cached measurements over HTTP. The included dashboard can remain stopped.

Both replacement and experimental passive mode expose this API. In passive mode,
the original box controls the reading cadence. Adjust the sensor age thresholds
below to that cadence and your chosen `SOLAR_PASSIVE_STALE_SECONDS`; changing
Home Assistant's `scan_interval` only changes how often it reads the cached API.

## Make the API reachable

If Home Assistant runs on another computer or in a container, bind the collector
API to a reachable interface on your trusted network:

```sh
SOLAR_API_BIND=0.0.0.0 uv run --env-file .env python -m collector
```

For a containerized collector, also publish port `8766` on the intended host
interface instead of the loopback-only mapping in the setup guide. Use the
**collector computer's** hostname or IP in Home Assistant. `localhost` inside
Home Assistant refers to its own environment, and the SMLIGHT's port `6638` is
not the data API. Confirm that Home Assistant can reach
`http://COLLECTOR_HOST:8766/api/live`.

The API has no authentication. Keep it restricted to trusted clients. No Home
Assistant access token or extra radio connection is needed for these reads.

## Add power and energy sensors

Merge this into `configuration.yaml`, replacing `COLLECTOR_HOST`. If you already
have a `rest:` section, add the resource to its list instead of creating a second
top-level `rest:` key. These example unique IDs assume one installation.

```yaml
rest:
  - resource: "http://COLLECTOR_HOST:8766/api/live"
    scan_interval: 60
    timeout: 10
    sensor:
      - name: "SolarCity power"
        unique_id: solarcity_inverter_power
        device_class: power
        state_class: measurement
        unit_of_measurement: "W"
        value_template: >-
          {{ value_json.solar_w if value_json.solar_w is number else none }}
        availability: >-
          {{ value_json is defined
             and value_json.collector.connected | default(false)
             and value_json.solar_w is number
             and value_json.timestamp is number
             and 0 <= as_timestamp(now()) - value_json.timestamp <= 180 }}
      - name: "SolarCity lifetime energy"
        unique_id: solarcity_inverter_lifetime_energy
        device_class: energy
        state_class: total_increasing
        unit_of_measurement: "kWh"
        value_template: >-
          {{ value_json.solar.lifetime_wh / 1000
             if value_json.solar.lifetime_wh is number else none }}
        availability: >-
          {{ value_json is defined
             and value_json.collector.connected | default(false)
             and value_json.solar.lifetime_wh is number
             and value_json.solar.lifetime_observed_at is number
             and 0 <= as_timestamp(now()) - value_json.solar.lifetime_observed_at <= 900 }}
```

Use Home Assistant's configuration check before restarting to load the YAML.
Verify that the sensors show plausible values after the collector has received
power and energy replies. The `availability` templates follow the documented
[REST sensor options](https://www.home-assistant.io/integrations/sensor.rest/).
They mark old or missing readings unavailable, including quiet overnight periods,
instead of reporting zero. The energy timestamp belongs to its saved reading;
fresh power does not make old energy fresh.

The collector's radio polling cycle is independent of `scan_interval`. Reading
the API every minute does not increase radio query frequency. If you set
`SOLAR_POLL_INTERVAL_SECONDS`, adjust the example's power age threshold (`180`)
to three times that interval and energy age threshold (`900`) to fifteen times
that interval. For a `120`-second query interval, use `360` and `1800` respectively.

## Energy dashboard

Add **SolarCity lifetime energy** as a solar production source in Home Assistant's
[Energy configuration](https://www.home-assistant.io/docs/energy/solar-panels/).
It is a cumulative kWh counter; Home Assistant calculates production changes.
The power sensor is useful for current output. Confirm that your inverter's
cumulative counter is suitable before relying on long-term statistics.

This is an example using Home Assistant's built-in REST support, not a custom
integration or add-on. The API behavior is covered by this repository's tests;
the YAML still needs validation in your own Home Assistant installation.
