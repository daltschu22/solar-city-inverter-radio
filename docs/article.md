# Replacing a SolarCity collector with a local Power-One radio integration

My Power-One solar inverter already had a radio. What I wanted was a local way
to read it: a small radio bridge, software I could inspect, and measurements stored
on my own machine.

The result is [Power One Radio](https://github.com/daltschu22/power-one-radio), a
Python collector that takes over the original SolarCity collector's role for one
specific legacy Power-One/Digi setup. It maintains the radio network, answers the
inverter's startup messages, and reads production and diagnostic registers.

The important qualification is compatibility. This has worked with a
**Power-One PVI-5000-OUTD-US-Z**, its existing Digi XBee radio, and a
**SMLIGHT SLZB-06U**. It is a working prototype for that combination. The method
currently requires the original collector's radio identity and network settings;
a fresh pairing with an entirely new identity remains untested.

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

## Using a network radio from Python

The SLZB-06U exposes its radio through a network serial bridge. That lets the
collector run on a machine with no USB connection to the radio.

The tested configuration uses SMLIGHT's CC2652P OpenThread RCP firmware build
`20260304`, a `460800` baud serial bridge, and TCP port `6638`.
[SMLIGHT documents this firmware mode](https://smlight.tech/manual/slzb-06/guide/thread-matter/).

Although the firmware is called OpenThread RCP, the project uses it as a raw
IEEE 802.15.4 interface. Python supplies the legacy Digi Zigbee frames. There is
no Thread network in this arrangement.

The division of work is straightforward. The radio handles transmission,
reception, frame checksums, and MAC acknowledgments. Python handles coordinator
messages, application acknowledgments, measurement requests, and decoding.
The repository pins the Python radio dependency to a specific revision so that
another person can reproduce the same interface.

## A replacement must act as the coordinator

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

That distinction changes the design. The replacement needs to answer startup
verification correctly. Periodically restarting the radio or changing the
measurement interval does not implement that exchange.

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

The schedule is deliberately simple: one measurement query per minute, with
power alternating with energy and diagnostics. Power normally updates every two
minutes. Network maintenance continues independently, and packet delivery retries
are bounded. The integration reads inverter registers; it does not change inverter
operating settings.

## Reproducing the setup

The repository includes the collector, decoder, synthetic protocol tests, a local
web interface, and a container definition. The detailed
[setup guide](https://github.com/daltschu22/power-one-radio/blob/main/docs/setup.md)
covers the exact configuration fields and startup sequence.

The main steps are:

1. Confirm the inverter and radio match the supported legacy setup.
2. Obtain your channel, operating PAN IDs, original collector EUI, and inverter EUI
   from radio configuration or suitable working-network captures.
3. Configure the SMLIGHT RCP bridge and put those values in `radio.local.json`.
4. Power off the original collector and give the Python collector exclusive access
   to the radio bridge.
5. Validate fresh readings, then observe startup and overnight recovery.

A capture used to inspect the application exchange needs to include unicast
traffic. Stock TI RCP promiscuous reception can miss ACK-requested unicasts, so a
quiet capture is not conclusive. An independent, verified sniffer can help during
initial characterization; it is not needed for normal operation.

The public repository contains fictional identities and synthetic telemetry.
Installation configuration, captures, databases, and logs stay out of version
control. The dashboard binds to localhost by default because its API contains
information about the local equipment.

## What is proven and what remains open

With the original collector powered off, the original implementation has recovered
from a controlled radio reset and a leave/rejoin cycle. It also resumed readings
after an overnight quiet period. That is evidence of working recovery on one
installation, not a claim of universal compatibility or established long-term
reliability. The standalone export has offline tests and still needs independent
hardware reproductions.

The open commissioning question is what happens when the original collector is
missing and its identity is unknown. It may be possible to recover the required
settings from the inverter or teach it a new coordinator identity. Neither path
is a completed feature of this release.

There is useful prior work. [solarcity_sniff](https://github.com/hufman/solarcity_sniff)
records and decodes SolarCity traffic. Other communities have built replacement
coordinators for [Enecsys](https://github.com/bulldog5046/Enecsys-Zigbee-HA) and
[APsystems](https://github.com/patience4711/ESP32-read-APS-inverters). Their
protocols differ, but they show why both network behavior and application replies
matter. The repository includes further [sources and credits](https://github.com/daltschu22/power-one-radio/blob/main/docs/references.md).

For someone with this Power-One/Digi combination, the useful starting point is
now concrete: the coordinator behavior, startup bytes, register requests, and
software needed to reproduce a local collector are available together.
