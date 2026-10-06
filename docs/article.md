# Decoding SolarCity Inverter Radio Data

My Power-One solar inverter already had a radio. What I wanted was a local way
to read it: a small radio bridge, software I could inspect, and measurements stored
on my own machine.

The result is [SolarCity Inverter Radio](https://github.com/daltschu22/solar-city-inverter-radio), a
Python collector for one specific legacy Power-One/Digi setup. It offers two
ways to gather readings: replace the original SolarCity box, or leave it running
and listen to its radio exchanges.

This has worked with a **Power-One PVI-5000-OUTD-US-Z**, its existing Digi XBee radio, and a
**SMLIGHT SLZB-06U** in replacement mode. It is a working prototype for that
combination. Replacement requires the original collector's radio identity and
network settings; a fresh pairing with an entirely new identity remains untested.

## Choose who manages the inverter

**Replacement mode**, the default, maintains the radio network, answers the
inverter's startup messages, and reads production and diagnostic registers.
The original box stays powered off because the replacement uses its radio identity.

**Passive mode** leaves the original box powered and working. Our collector
listens for its requests and the inverter's replies, then saves complete matched
exchanges. It does not issue queries or perform network recovery. The original
box determines which readings are available and how often they arrive.

Passive decoding has passed software tests and replay of recorded exchanges,
but live measurement capture remains unverified. The official SMLIGHT firmware
can omit the required unicast packets. A capture fix is under evaluation; there
is no validated passive firmware package yet. Selecting passive mode alone does
not fix reception. Missing packets produce gaps; the listener never switches to
replacement mode automatically.

Both options feed the same SQLite history, JSON API, optional dashboard, and
Home Assistant integration. The radio coordination and query sequence below
describe replacement mode. The [setup guide](setup.md#choose-how-to-collect-readings)
explains how to select either option.

## The radio carries a familiar protocol

The measurement path has several layers:

```text
Python collector
    Spinel over TCP
SMLIGHT raw radio
    IEEE 802.15.4 / Digi Zigbee
Inverter XBee radio
    Serial Modbus RTU
Power-One SunSpec registers
```

The useful discovery is at the bottom of that stack: the payload contains Modbus
RTU register reads and replies. Once delivered to the correct application
endpoint, a request can read power, exported energy, voltage, current, frequency,
temperature, and operating state.

Digi documents its serial-data service under application profile `0xc105`, cluster
`0x0011`, and endpoint `0xe8`. Those fields provide the route to the serial data
behind the radio. [Digi application profile documentation](https://www.digi.com/support/knowledge-base/using-digi-s-applicaiton-s-cluster-id-s-and-end-po)

The supported network uses legacy stack profile `0` and unencrypted packets.
These details matter: ordinary Zigbee hardware does not make every Zigbee
application compatible. This project implements the particular network and
application behavior the inverter expects.

## Connecting to the radio

The SLZB-06U exposes its radio through a network serial bridge. That lets the
collector run on a machine with no USB connection to the radio.

The tested replacement configuration uses SMLIGHT's CC2652P OpenThread RCP firmware build
`20260304`, a `460800` baud serial bridge, and TCP port `6638`.
[SMLIGHT documents this firmware mode](https://smlight.tech/manual/slzb-06/guide/thread-matter/).

Although the firmware is called OpenThread RCP, the project uses it as a raw
IEEE 802.15.4 interface. Python supplies the legacy Digi Zigbee frames. There is
no Thread network in this arrangement.

The radio handles transmission, reception, frame checksums, and MAC
acknowledgments. Python handles coordinator
messages, application acknowledgments, measurement requests, and decoding.
The repository pins the Python radio dependency to a specific revision so that
another person can reproduce the same interface.

## Managing the radio network

Reading registers is only part of the job. The inverter expects a coordinator
that advertises the network and answers the messages needed to stay connected.

The implementation handles beacons, association, device announcements, address
verification, direct routing, and recovery after the inverter leaves or rejoins.
It accepts only the configured inverter and learns its current short address from
identifiable traffic. A remembered address alone is not enough to start polling.

The tested XBee has coordinator verification enabled through `JV=1`. Digi's
[JV documentation](https://docs.digi.com/resources/documentation/digidocs/90002002/reference/r_cmd_jv.htm)
describes this startup check. The separate timer-based network watchdog was
configured as `NW=0`, meaning disabled.

The replacement answers startup verification as part of its coordinator duties.

## The small startup reply that matters

There is also an application exchange above the ordinary radio acknowledgments.
The observed request and response are only a few bytes:

```text
Inverter:   f4 00 01 01 01
Collector:  f5 00 00 00
```

The collector sends the response on the same Digi profile, serial-data cluster,
and endpoints. It uses a fresh APS counter and requests an APS acknowledgment.
It also sends the ordinary APS acknowledgment for the incoming request.

These are separate responsibilities. A MAC acknowledgment means a radio frame
arrived. An APS acknowledgment confirms delivery at the Zigbee application layer.
The `F5` message answers the startup request itself.

The implementation reproduces the observed bytes. I have not fully decoded the
proprietary fields, so the project does not claim they are a universal SolarCity
handshake. An already joined inverter can also proceed to readings without
sending this startup message on every reconnect.

## Requesting and validating a measurement

A power read in the supported register map asks Modbus unit `1` for five registers
starting at address `40360`:

```text
01 03 9d a8 00 05 2b 85
```

That is a complete Modbus RTU request, including its CRC. It sits inside the Digi
radio message. TCP is used between Python and the bridge, but the payload is not
Modbus TCP and has no Modbus TCP header.

The response contains a power value and scale factor. For a synthetic example,
a raw value of `5000` with scale `-1` means `500.0 W`. Register values are
big-endian; the Modbus CRC is sent low byte first.

The collector checks identity, addressing, application fields, expected length,
and CRC before accepting a reading. It rejects radio errors and unsupported
fragmentation. Missing readings remain gaps rather than becoming invented zeroes.

The default schedule is one measurement query per minute, with
power alternating with energy and diagnostics. Power normally updates every two
minutes. Network maintenance continues independently, and packet delivery retries
are bounded. The integration reads inverter registers; it does not change inverter
operating settings.

## Reproducing the setup

The repository includes the collector, decoder, synthetic protocol tests, an
optional dashboard, and a single container image with an optional dashboard flag.
The detailed
[setup guide](https://github.com/daltschu22/solar-city-inverter-radio/blob/main/docs/setup.md)
covers the exact configuration fields and startup sequence.

The main steps are:

1. Confirm the inverter and radio match the supported legacy setup.
2. Configure the SMLIGHT RCP bridge, then use the included
   [discovery script](https://github.com/daltschu22/solar-city-inverter-radio/blob/main/docs/discovery.md)
   with `--write-env .env` to gather the radio settings and generate configuration.
3. Review the evidence and generated `.env`, then validate it with
   `uv run --env-file .env python -m collector.config`.
4. Select replacement or passive mode in `.env`. Power off the original box for
   replacement, or leave it working for passive. After capture finishes, give the
   Python collector exclusive access to the SMLIGHT bridge.
5. Validate fresh readings and saved history. Check startup and overnight recovery
   in replacement mode, or complete captured exchanges in passive mode.

From the repository root, run `uv run --env-file .env python -m collector` for collection and the JSON
API on port `8766`. That is
a complete setup for anyone who wants to consume the data themselves. The
included dashboard runs separately with `uv run python -m dashboard` on port `8765` and
reads the collector API. Home Assistant can use the same API through its REST
sensors; the repository includes a
[power and energy example](https://github.com/daltschu22/solar-city-inverter-radio/blob/main/docs/home-assistant.md).
Starting or stopping the dashboard does not restart the radio connection, and
API reads do not increase inverter polling frequency.

A capture used to inspect the application exchange needs to include unicast
traffic. Stock TI RCP promiscuous reception can miss ACK-requested unicasts, so a
quiet capture is not conclusive. An independent, verified sniffer can help during
initial characterization. Replacement collection uses the SMLIGHT without the
original box; passive collection requires the original box to keep querying.

The repository contains fictional identities and synthetic telemetry.
Installation configuration, captures, databases, and logs stay out of version
control. The collector API and dashboard bind to localhost by default because
the data includes information about the local equipment.

## What is proven and what remains open

With the original collector powered off, testing covered recovery from a radio
reset, a leave/rejoin cycle, and an overnight-to-morning transition on one
installation. Independent hardware reproductions and long-term reliability
remain unverified.

Passive request/reply matching, fragmented replies, storage, and reconnect
behavior have offline tests. Receiving complete exchanges alongside the original
box, and confirming that the radio does not send unintended hardware
acknowledgments, still need over-the-air testing.

The included discovery tool can recover candidate settings from inverter traffic
and explains the evidence for each value. On an operating replacement network,
it recovered all five required radio settings from inverter-originated frames.
An inverter that has already left its network may expose less information.
Recovering from that state, or teaching it a new coordinator identity, remains
unverified.

There is useful prior work. [solarcity_sniff](https://github.com/hufman/solarcity_sniff)
records and decodes SolarCity traffic. Other communities have built replacement
coordinators for [Enecsys](https://github.com/bulldog5046/Enecsys-Zigbee-HA) and
[APsystems](https://github.com/patience4711/ESP32-read-APS-inverters). Their
protocols differ, but they show why both network behavior and application replies
matter. The repository includes further [sources and credits](https://github.com/daltschu22/solar-city-inverter-radio/blob/main/docs/references.md).
