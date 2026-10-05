"""Experimental monitoring of an original collector's read-only Modbus exchanges."""

from collections import deque
import hashlib
import time

from collector.radio_protocol import decode_modbus_message, decode_sunspec_values, signed_16
from collector.smlight_collector import RadioSession, SmlightCollector, measurement
from tools.smlight_poll import CHANNEL, COLLECTOR, INVERTER, PAN, READS


class PassiveRadioSession(RadioSession):
    """Raw receiver without a transmit or coordinator-configuration path."""

    def initialize(self, stop):
        c = self.constants
        self.api.cmd_send(c.CMD_RESET)
        if stop.wait(1):
            return
        self.firmware = self.api.prop_get_value(c.PROP_NCP_VERSION)
        self.set_property(c.PROP_PHY_ENABLED, 1)
        # Configure before starting reception. Retain the radio's own hardware
        # EUI, with no network membership or usable short address. Some drivers
        # retain automatic ACK logic in promiscuous mode; never impersonate a peer.
        self.set_property(c.PROP_MAC_15_4_PANID, 0xFFFF, "H")
        self.set_property(c.PROP_MAC_15_4_SADDR, 0xFFFF, "H")
        self.set_property(c.PROP_MAC_FILTER_MODE, c.MAC_FILTER_MODE_MONITOR)
        self.set_property(c.PROP_PHY_CHAN, CHANNEL)
        self.set_property(c.PROP_MAC_RAW_STREAM_ENABLED, 1)

    def transmit(self, frame):
        raise RuntimeError("Passive mode cannot transmit")

    def addressed_mode(self):
        raise RuntimeError("Passive mode cannot assume a coordinator identity")

    def set_pending_inverter(self, pending):
        raise RuntimeError("Passive mode cannot manage associations")

    def check_health(self):
        super().check_health()
        if self.api.prop_get_value(self.constants.PROP_MAC_FILTER_MODE) != self.constants.MAC_FILTER_MODE_MONITOR:
            raise ConnectionError("Passive radio is no longer in monitor mode")


class PassiveTransactions:
    """Bounded request/reply matching; never infer an address from reply length.

    Modbus RTU has no transaction ID. Only a single observed outstanding read
    is accepted. Overlapping reads quarantine the window rather than guessing.
    Missing an entire exchange can still make RTU attribution unknowable.
    """

    window = 10

    def __init__(self):
        self.pending = None
        self.fragments = None
        self.ambiguous_until = 0
        self.seen = deque(maxlen=256)
        self.requests = 0
        self.unmatched = 0
        self.expired = 0

    @staticmethod
    def accepts(frame):
        source, destination = frame.get("network_source_ieee"), frame.get("network_destination_ieee")
        peer = frame.get("network_destination" if source == COLLECTOR else "network_source")
        try:
            if not isinstance(peer, str) or not 1 <= int(peer, 16) < 0xFFF8:
                return False
        except ValueError:
            return False
        return (
            (source, destination) in ((COLLECTOR, INVERTER), (INVERTER, COLLECTOR))
            and frame.get("channel") == CHANNEL
            and frame.get("source_pan_id") == frame.get("destination_pan_id") == f"0x{PAN:04x}"
            and frame.get("mac_source") == frame.get("network_source")
            and frame.get("mac_destination") == frame.get("network_destination")
            and frame.get("network_source" if source == COLLECTOR else "network_destination") == "0x0000"
            and frame.get("aps_frame_type") == "data"
            and frame.get("aps_delivery_mode") == "unicast"
            and frame.get("profile_id") == "0xc105" and frame.get("cluster_id") == "0x0011"
            and frame.get("source_endpoint") == frame.get("destination_endpoint") == 0xE8
            and not any(frame.get(key) for key in ("receive_error", "bad_fcs", "network_secured", "aps_secured"))
        )

    def expire(self, now):
        if self.pending and now - self.pending[0] > self.window:
            self.pending = self.fragments = None
            self.expired += 1

    def feed(self, frame, now):
        self.expire(now)
        if not self.accepts(frame):
            return None
        payload = bytes.fromhex(frame.get("application_payload", ""))
        if not payload or len(payload) > 255:
            return None
        identity = (frame["network_source_ieee"], frame.get("aps_counter"),
                    frame.get("aps_fragmentation"), frame.get("aps_block_number"), payload)
        if any(key == identity and now - at <= self.window for at, key in self.seen):
            return None
        self.seen.append((now, identity))
        if now < self.ambiguous_until:
            return None
        outgoing = frame["network_source_ieee"] == COLLECTOR
        fragmentation = frame.get("aps_fragmentation", "none")
        if outgoing and fragmentation != "none":
            return None
        if not outgoing and fragmentation != "none":
            if not self.pending:
                self.unmatched += 1
                return None
            key = (frame.get("aps_counter"), frame["network_source"], frame["network_destination"])
            if fragmentation == "first":
                count = frame.get("aps_block_count", 0)
                if not 1 <= count <= 8:
                    return None
                self.fragments = (key, count, {0: payload})
            elif fragmentation == "continuation" and self.fragments:
                old_key, count, parts = self.fragments
                block = frame.get("aps_block_number", -1)
                if old_key != key or not 1 <= block < count:
                    return None
                if block in parts and parts[block] != payload:
                    self.pending = self.fragments = None
                    return None
                parts[block] = payload
            else:
                return None
            _, count, parts = self.fragments
            if sum(map(len, parts.values())) > 255:
                self.pending = self.fragments = None
                return None
            if len(parts) != count:
                return None
            payload = b"".join(parts[i] for i in range(count))
            self.fragments = None
        message = decode_modbus_message(payload)
        if not message.get("crc_valid") or message.get("unit_id") != 1:
            return None
        if outgoing:
            if (message.get("kind") != "request" or not 1 <= message["register_count"] <= 125
                    or message["register_start"] + message["register_count"] > 65536):
                return None
            self.requests += 1
            if self.pending:
                self.pending = self.fragments = None
                self.ambiguous_until = now + self.window
            else:
                self.pending = (now, frame, message)
            return None
        pending, self.pending = self.pending, None
        self.fragments = None
        if not pending:
            self.unmatched += 1
            return None
        _, request_frame, request = pending
        if (message.get("kind") != "response"
                or message.get("byte_count") != 2 * request["register_count"]
                or frame["network_source"] != request_frame["network_destination"]
                or frame["network_destination"] != request_frame["network_source"]):
            self.unmatched += 1
            return None
        return dict(enumerate(message["register_values"], request["register_start"]))


class PassiveCollector(SmlightCollector):
    def __init__(self, host, history, port=6638, reconnect_interval=15, stale_after=300,
                 session_factory=PassiveRadioSession):
        super().__init__(host, history, port=port, interval=None,
                         reconnect_interval=reconnect_interval, session_factory=session_factory)
        self.stale_after = stale_after
        self.update(mode="passive", packets_observed=0, observed_requests=0,
                    unmatched_responses=0, expired_requests=0,
                    warning="Experimental passive reception: missing unicast packets can prevent readings")

    def snapshot(self):
        with self.lock:
            status, reading = dict(self.status), self.last_reading
        at = reading["observed_at"] if reading else None
        age = max(0, time.time() - at) if at is not None else None
        if status["state"] in {"live", "listening"} and age is not None and age > self.stale_after:
            status["state"] = "stale"
        status.update(last_reading_at=at, age_seconds=age, standby_until=None,
                      reading_stale_after_seconds=self.stale_after,
                      telemetry_stale_after_seconds=self.stale_after,
                      telemetry=self.telemetry.snapshot(stale_after=self.stale_after))
        return status

    def save_registers(self, frame, registers):
        # Keep production history on meter model 201, as in replacement mode.
        # Model 101 energy is a separate counter and belongs in diagnostics.
        meter = {k: v for k, v in registers.items() if 40342 <= k <= 40448}
        for scale in (40364, 40396):
            if scale in meter and not -10 <= signed_16(meter[scale]) <= 10:
                del meter[scale]
        values = decode_sunspec_values(meter)
        at = frame["observed_at"]
        if "lifetime_wh" in values:
            self.energy, self.energy_at = values["lifetime_wh"], at
        if "solar_w_precise" in values:
            if self.energy is not None and 0 <= at - self.energy_at <= min(120, self.stale_after):
                values["lifetime_wh"] = self.energy
            reading = measurement(frame, values)
            self.history.record([reading])
            with self.lock:
                self.last_reading = reading
            self.update(state="live", last_error=None)
        for kind, (start, count) in READS.items():
            if kind in {"power", "energy"} or not all(i in registers for i in range(start, start + count)):
                continue
            identity = f"passive|{frame['capture_sweep']}|{frame['timestamp']}|{frame['raw']}|{kind}"
            self.telemetry.record(hashlib.sha256(identity.encode()).hexdigest(), kind, at,
                                  {i: registers[i] for i in range(start, start + count)})
        with self.lock:
            self.status["responses"] += 1

    def run_session(self, session):
        matcher = PassiveTransactions()
        self.energy, self.energy_at = None, 0
        session.initialize(self.stop_event)
        self.update(state="listening", connected=True, firmware=session.firmware, last_error=None)
        health_at = time.monotonic() + 30
        baseline = self.snapshot()
        while not self.stop_event.is_set():
            frame = session.receive()
            now = time.monotonic()
            matcher.expire(now)
            if frame and matcher.accepts(frame):
                with self.lock:
                    self.status["packets_observed"] += 1
                if frame["network_source_ieee"] == INVERTER:
                    self.update(last_packet_at=frame["observed_at"], rssi=frame.get("rssi"),
                                inverter_address=frame["network_source"])
                registers = matcher.feed(frame, now)
                if registers is not None:
                    self.save_registers(frame, registers)
            self.update(observed_requests=baseline["observed_requests"] + matcher.requests,
                        unmatched_responses=baseline["unmatched_responses"] + matcher.unmatched,
                        expired_requests=baseline["expired_requests"] + matcher.expired)
            if now >= health_at:
                session.check_health()
                health_at = now + 30
