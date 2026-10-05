# Sources and related radio projects

Vendor references document the standard pieces. The Power-One application startup
bytes and register layout come from direct observations behind this implementation;
they are not claimed to be documented by Digi or universally applicable.

## Vendor documentation

- [SMLIGHT RCP firmware setup](https://smlight.tech/manual/slzb-06/guide/thread-matter/) explains the radio mode and network bridge. This project uses that interface without creating a Thread network.
- [Digi application profiles and endpoints](https://www.digi.com/support/knowledge-base/using-digi-s-applicaiton-s-cluster-id-s-and-end-po) documents profile `0xc105`, endpoint `0xe8`, serial cluster `0x0011`, and DDO services.
- [Digi coordinator verification](https://docs.digi.com/resources/documentation/digidocs/90002002/reference/r_cmd_jv.htm) documents `JV`.
- [Digi network watchdog](https://docs.digi.com/resources/documentation/digidocs/90002002/reference/r_cmd_nw.htm) documents the separate `NW` timer.
- [Digi stack profile](https://docs.digi.com/resources/documentation/digidocs/90002002/reference/r_cmd_zs.htm) documents `ZS` and matching network profiles.
- [Digi joining behavior](https://docs.digi.com/resources/documentation/digidocs/90001537/references/r_joining_under_xbee.htm) distinguishes configured and operating PAN identities.
- [OpenThread pyspinel](https://github.com/openthread/pyspinel) provides the host Spinel interface used here.

## SolarCity work

- [hufman/solarcity_sniff](https://github.com/hufman/solarcity_sniff) captures and decodes unencrypted SolarCity X2e traffic. It is a passive monitoring project, not a replacement coordinator.
- [José Fernandez's Frony Fronius presentation](https://www.slideshare.net/slideshow/frony-fronius-exploring-zigbee-signals-from-solar-city/86981607) explores SolarCity Zigbee, Digi configuration, capture, and replay. The [author's account](https://compsecdirect.com/compsec-directs-president-presents-zigbee-research-local-security-conference-inner-harbor/) explains that some technical details were omitted. The published material does not establish a complete replacement service for the supported Power-One setup.
- [gwendalg/solarcity_mqtt](https://github.com/gwendalg/solarcity_mqtt) extracts the existing collector's network uploads; its interception point is Ethernet rather than a replacement radio coordinator.

## Replacement coordinators for other inverter families

- [Enecsys Zigbee HA](https://github.com/bulldog5046/Enecsys-Zigbee-HA) combines TI firmware and Python to replace an Enecsys gateway. Its startup application reply and legacy network configuration are useful architectural comparisons, but its payloads and security settings differ.
- [ESP32 read APS inverters](https://github.com/patience4711/ESP32-read-APS-inverters) replaces an APsystems ECU using an ESP32 and a Zigbee radio. It targets another inverter protocol and is not interchangeable with this implementation.

These projects deserve credit as prior work. This repository makes no claim to
be the first solar radio gateway replacement or a drop-in implementation for
other manufacturers.
