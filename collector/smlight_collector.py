"""One reconnecting, read-only SMLIGHT radio connection owned by the collector."""

from collections import deque
import hashlib
import socket
import struct
import threading
import time

from tools.smlight_capture import decode_record
from collector.inverter import TelemetryStore
from collector.coordinator import Coordinator, inverter_left
from collector.config import require_configured
from collector.transition_capture import TransitionCapture, TRANSITION_CYCLE, near_transition
from tools.smlight_poll import CHANNEL, COLLECTOR, INVERTER, PAN, acknowledgment, is_inverter, read_request, response_values


QUERY_CYCLE = ("power", "inverter_ac", "power", "energy", "power", "inverter_dc",
               "power", "energy", "power", "meter_ac", "power", "common1",
               "power", "common2", "power", "energy")


class SocketStream:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(1)
        self.closed = threading.Event()
        self.error = None

    def write(self, data):
        self.sock.sendall(data)

    def read(self, size=1):
        while not self.closed.is_set():
            try:
                data = self.sock.recv(size)
                if not data:
                    raise ConnectionError("SMLIGHT closed the radio connection")
                return data[0]
            except socket.timeout:
                continue
            except OSError as exc:
                self.error = str(exc)
                raise
        raise ConnectionError("Radio connection stopped")

    def close(self):
        self.closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class RadioSession:
    def __init__(self, host, port):
        require_configured()
        from spinel.codec import WpanApi
        from spinel.const import SPINEL

        self.constants = SPINEL
        self.stream = SocketStream(host, port)
        self.api = WpanApi(self.stream, "34", timeout=2)
        self.api.queue_register(SPINEL.HEADER_DEFAULT)
        self.api.queue_register(SPINEL.HEADER_ASYNC)
        self.sweep = time.time_ns() // 1000
        self.firmware = None

    def set_property(self, prop, value, fmt="B"):
        actual = self.api.prop_set_value(prop, value, fmt)
        if actual != value:
            raise ConnectionError(f"Radio configuration failed ({prop:#x})")

    def initialize(self, stop):
        c = self.constants
        self.api.cmd_send(c.CMD_RESET)
        if stop.wait(1):
            return
        self.firmware = self.api.prop_get_value(c.PROP_NCP_VERSION)
        self.set_property(c.PROP_PHY_ENABLED, 1)
        self.set_property(c.PROP_PHY_CHAN, CHANNEL)
        self.set_property(c.PROP_PHY_TX_POWER, 5, "b")
        self.set_property(c.PROP_MAC_FILTER_MODE, 2)
        self.set_property(c.PROP_MAC_RAW_STREAM_ENABLED, 1)

    def addressed_mode(self):
        c = self.constants
        self.set_property(c.PROP_MAC_15_4_PANID, PAN, "H")
        self.set_property(c.PROP_MAC_15_4_SADDR, 0, "H")
        self.set_property(c.PROP_MAC_15_4_LADDR, bytes.fromhex(COLLECTOR), "8s")
        self.set_property(c.PROP_MAC_FILTER_MODE, 0)
        self.set_property(c.PROP_MAC_SRC_MATCH_ENABLED, 1)
        self.pending_inverter = False  # Hardware table is empty after reset.

    def set_pending_inverter(self, pending):
        if pending == self.pending_inverter:
            return
        # RCP supports insert/remove but not reading back the entire table.
        # Those operations echo the affected EUI on success.
        change = self.api.prop_insert_value if pending else self.api.prop_remove_value
        address = bytes.fromhex(INVERTER)
        actual = change(self.constants.PROP_MAC_SRC_MATCH_EXTENDED_ADDRESSES, address, "8s")
        if actual != address:
            raise ConnectionError("Radio association pending-bit configuration failed")
        self.pending_inverter = pending

    def receive(self):
        if self.stream.error or not self.api.receiver_thread.is_alive():
            raise ConnectionError(self.stream.error or "Radio reader stopped")
        result = self.api.queue_wait_for_prop(
            self.constants.PROP_STREAM_RAW, self.constants.HEADER_ASYNC, timeout=0.25,
        )
        if result is None:
            return None
        length = self.api.parse_S(result.value)
        if not length:
            return None
        tail = result.value[2 + length:]
        if len(tail) < 19:
            raise ValueError("Truncated radio metadata")
        metadata = self.api.parse_fields(tail[:19], "ccSt(CCX)t(i)")
        return decode_record(result.value[2:2 + length], metadata, self.sweep, time.time())

    def transmit(self, frame):
        c = self.constants
        # Raw RCP drivers do not all implement the requested hardware retry count.
        # Resend the identical MAC frame here, with bounded spacing, so the peer
        # can suppress duplicate delivery. Broadcasts never request an ACK.
        retries = 3 if frame[0] & 0x20 else 0
        payload = struct.pack("<H", len(frame)) + frame + bytes([CHANNEL, 4, 0, 1])
        for attempt in range(retries + 1):
            self.api.prop_change_async(c.CMD_PROP_VALUE_SET, c.PROP_STREAM_RAW, payload, f"{len(payload)}s")
            status = self.api.queue_wait_for_prop(c.PROP_LAST_STATUS, timeout=2)
            if status is None:
                raise ConnectionError("SMLIGHT transmit timed out")
            if status.value not in (17, 18) or attempt == retries:
                return status.value
            print("Solar radio retry:", attempt + 1, "status", status.value, flush=True)
            if self.stream.closed.wait(0.1 * 2 ** attempt):
                raise ConnectionError("Radio connection stopped")

    def check_health(self):
        if self.api.prop_get_value(self.constants.PROP_PHY_CHAN) != CHANNEL:
            raise ConnectionError("SMLIGHT radio health check failed")

    def close(self):
        try:
            if self.api.receiver_thread.is_alive() and not self.stream.error:
                self.api.cmd_send(self.constants.CMD_RESET)
        except OSError:
            pass
        finally:
            self.api._reader_alive = False
            self.stream.close()
            self.api.receiver_thread.join(timeout=2)


def measurement(frame, values):
    identity = f"smlight|{frame['capture_sweep']}|{frame['timestamp']}|{frame['raw']}"
    return {
        "capture_id": hashlib.sha256(identity.encode()).hexdigest(),
        "observed_at": frame["observed_at"],
        "capture_sweep": frame["capture_sweep"],
        "radio_timestamp": frame["timestamp"],
        "solar_w": values["solar_w_precise"],
        "lifetime_wh": values.get("lifetime_wh"),
    }


class SmlightCollector:
    def __init__(self, host, history, port=6638, interval=60, session_factory=RadioSession,
                 night_schedule=None, reconnect_interval=15, transition_capture_until=None):
        self.host, self.port, self.interval = host, port, interval
        self.reconnect_interval = reconnect_interval
        self.history = history
        self.telemetry = TelemetryStore(history.path)
        self.session_factory = session_factory
        self.night_schedule = night_schedule
        self.capture = TransitionCapture(history.path.parent, transition_capture_until)
        self.last_reading = history.latest()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.sequence = int(time.time()) & 255
        self.seen = deque(maxlen=128)
        self.energy = None
        self.energy_at = 0
        self.status = {
            "mode": "replacement",
            "state": "connecting", "host": host, "channel": CHANNEL,
            "interval_seconds": interval, "reconnect_interval_seconds": reconnect_interval,
            "last_poll_at": None, "last_reading_at": None,
            "last_packet_at": None, "inverter_address": None, "rssi": None,
            "requests": 0, "responses": 0, "timeouts": 0, "last_error": None,
            "query_tx_failures": 0, "last_query_kind": None, "last_query_tx_status": None,
            "network_transmissions": 0, "network_replies": 0, "network_tx_failures": 0,
            "connected": False, "firmware": None,
            "query_cycle": "normal",
        }

    def update(self, **values):
        with self.lock:
            self.status.update(values)

    def snapshot(self):
        with self.lock:
            status = dict(self.status)
            reading = self.last_reading
        now = time.time()
        at = reading["observed_at"] if reading else None
        status["last_reading_at"] = at
        status["age_seconds"] = max(0, now - at) if at is not None else None
        if (status["state"] in {"live", "waiting"} and at is not None
                and status["age_seconds"] > self.interval * 3):
            status["state"] = "stale"
        status["telemetry_stale_after_seconds"] = self.interval * len(QUERY_CYCLE) * 2
        status["telemetry"] = self.telemetry.snapshot(stale_after=status["telemetry_stale_after_seconds"])
        status["transition_capture"] = self.capture.snapshot()
        status["standby_until"] = None
        dc = status["telemetry"].get("inverter_dc", {}).get("values", {})
        fault = dc.get("fault_bits", 0) or dc.get("event_bits_2", 0) or dc.get("operating_state") == "Fault"
        packet_at = status["last_packet_at"]
        radio_quiet = packet_at is None or now - packet_at >= 90
        if (self.night_schedule and not fault and radio_quiet and status["connected"]
                and status["state"] in {"live", "waiting", "discovering", "stale"}):
            until = self.night_schedule.standby_until(reading, now)
            if until is not None:
                status.update(state="standby", standby_until=until, last_error=None)
        return status

    def next_sequence(self):
        self.sequence = (self.sequence + 1) & 255
        return self.sequence

    def send_network(self, session, frame, reply=False):
        status = self.transmit(session, frame, "network_reply" if reply else "network_maintenance")
        with self.lock:
            self.status["network_transmissions"] += 1
            self.status["network_replies"] += int(reply)
            self.status["network_tx_failures"] += int(status != 0)
        return status

    def transmit(self, session, frame, purpose, kind=None):
        self.capture.record("tx", purpose=purpose, kind=kind, raw=frame[:-2].hex())
        try:
            status = session.transmit(frame)
        except Exception as exc:
            self.capture.record("tx_error", purpose=purpose, kind=kind, error=str(exc))
            raise
        self.capture.record("tx_result", purpose=purpose, kind=kind, status=status)
        return status

    def capture_frame(self, frame, pending):
        if not self.capture.active() or frame.get("channel") != CHANNEL:
            return
        # Include control traffic for our PAN and known EUIs, including association
        # before the inverter has a short address. Hardware MAC ACKs may be absent.
        known = {COLLECTOR, INVERTER}
        if (f"0x{PAN:04x}" not in (frame.get("source_pan_id"), frame.get("destination_pan_id"))
                and not known.intersection(frame.get(key) for key in
                                           ("network_source_ieee", "network_destination_ieee"))
                and not {"0x" + eui for eui in known}.intersection(
                    frame.get(key) for key in ("mac_source", "mac_destination"))):
            return
        self.capture.record("rx", frame=frame, pending=pending, inverter=is_inverter(frame))

    def query_cycle(self, now):
        transition = self.capture.active(now) and near_transition(self.night_schedule, now)
        mode = "transition" if transition else "normal"
        if mode != self.status["query_cycle"]:
            self.update(query_cycle=mode)
            self.capture.record("query_cycle", mode=mode)
        return TRANSITION_CYCLE if transition else QUERY_CYCLE

    def start(self):
        self.thread = threading.Thread(target=self.run, name="solar-poller", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)

    def accept_response(self, frame, kind):
        identity = (frame.get("aps_counter"), frame.get("application_payload"))
        if identity in self.seen:
            return False
        values = response_values(frame, kind)
        if not values:
            return False
        if kind == "power":
            if self.energy is not None and 0 <= frame["observed_at"] - self.energy_at <= self.interval * 2:
                values["lifetime_wh"] = self.energy
            reading = measurement(frame, values)
            self.history.record([reading])
            with self.lock:
                self.last_reading = reading
            self.update(state="live", last_reading_at=frame["observed_at"], last_error=None)
        elif kind == "energy":
            self.energy = values["lifetime_wh"]
            self.energy_at = frame["observed_at"]
        else:
            identity_text = f"{frame['capture_sweep']}|{frame['timestamp']}|{frame['raw']}"
            capture_id = hashlib.sha256(identity_text.encode()).hexdigest()
            self.telemetry.record(capture_id, kind, frame["observed_at"], values["registers"])
        self.seen.append(identity)
        with self.lock:
            self.status["responses"] += 1
        if self.capture.active():
            decoded = self.telemetry.snapshot().get(kind, {}).get("values", {})
            self.capture.record("response", kind=kind, observed_at=frame["observed_at"],
                                frame_id=[frame["capture_sweep"], frame["timestamp"]],
                                values=values, decoded=decoded)
        print("Solar reading:", kind, values, flush=True)
        return True

    def run_session(self, session):
        session.initialize(self.stop_event)
        self.update(state="discovering", connected=True, firmware=session.firmware, last_error=None)
        self.capture.record("session_ready", firmware=session.firmware,
                            interval_seconds=self.interval, coordinates_configured=self.night_schedule is not None,
                            capture_until=self.capture.until)
        startup_until = time.monotonic() + 30
        next_poll = startup_until
        addressed = False
        live = None
        pending = None
        response_deadline = 0
        query_index = 0
        last_radio_activity = time.monotonic()
        coordinator = Coordinator(self.history.path)
        association_pending = False
        last_network_event = None
        next_heartbeat = 0
        rx_errors = 0
        while not self.stop_event.is_set():
            frame = session.receive()
            now = time.monotonic()
            if now >= next_heartbeat:
                next_heartbeat = now + 60
                self.capture.record("heartbeat", state=self.status["state"],
                                    last_packet_at=self.status["last_packet_at"],
                                    last_reading_at=self.status["last_reading_at"],
                                    requests=self.status["requests"], responses=self.status["responses"],
                                    timeouts=self.status["timeouts"], receive_errors=rx_errors)
            if frame:
                self.capture_frame(frame, pending)
                rx_errors += bool(frame.get("receive_error") or frame.get("bad_fcs"))
                last_radio_activity = now
                if (frame.get("network_source_ieee") == COLLECTOR
                        and frame.get("mac_source") == "0x0000"):
                    raise RuntimeError("Old Tesla collector is on; unplug it to avoid duplicate radio identities")
                if is_inverter(frame):
                    event = ("announced" if frame.get("cluster_id") == "0x0013"
                             and frame.get("profile_id") == "0x0000" else
                             "left" if inverter_left(frame) else None)
                    identity = (event, frame.get("network_sequence"))
                    if event and identity != last_network_event:
                        print("Solar inverter network:", event, frame["network_source"],
                              frame.get("network_command_payload", ""), flush=True)
                        last_network_event = identity
                        self.capture.record("network_event", kind=event,
                                            observed_at=frame["observed_at"])
                    coordinator.observe(frame, now)
                    self.update(last_packet_at=frame["observed_at"], rssi=frame["rssi"],
                                inverter_address=frame["network_source"])
                    if event == "left":
                        live = pending = None
                        query_index = 0
                        self.update(state="waiting", inverter_address=None,
                                    last_error="Inverter left the radio network; waiting for rejoin")
                    else:
                        live = frame
                    if addressed and event != "left":
                        ack = acknowledgment(frame, self.next_sequence())
                        if ack:
                            self.transmit(session, ack, "aps_ack")
                        if pending and self.accept_response(frame, pending):
                            pending = None
            if now < startup_until:
                continue
            if not addressed:
                session.addressed_mode()
                addressed = True
                self.update(state="waiting")
            if frame:
                replies = coordinator.replies(frame, now, self.next_sequence)
            else:
                replies = []
            pending_match = coordinator.association_pending(now)
            if pending_match != association_pending:
                session.set_pending_inverter(pending_match)
                association_pending = pending_match
                print("Solar association pending:", pending_match, flush=True)
            if frame:
                for reply in replies:
                    status = self.send_network(session, reply, reply=True)
                    if frame.get("type") == "data" or frame.get("mac_command_id") != 7:
                        kind = ("application hello" if coordinator.application_hello(frame) else
                                frame.get("cluster_id") or frame.get("network_command") or frame.get("mac_command_id"))
                        print("Solar coordinator reply:", frame.get("network_source"),
                              kind, "radio status", status, flush=True)
            maintenance = coordinator.periodic(now, self.next_sequence)
            if maintenance:
                self.send_network(session, maintenance)
            if pending and now >= response_deadline:
                with self.lock:
                    self.status["timeouts"] += 1
                detail = (f"Radio delivery failed (status {tx_status}); no {pending} reply"
                          if tx_status != 0 else f"Inverter did not answer the latest {pending} query")
                self.update(state="stale", last_error=detail)
                self.capture.record("query_timeout", kind=pending, tx_status=tx_status)
                print("Solar query timeout:", pending, "radio status", tx_status, flush=True)
                pending = None
            if now >= next_poll and pending is None:
                next_poll = now + self.interval
                cycle = self.query_cycle(time.time())
                # Allow one response window of scheduling slack between polls.
                if live is None or time.time() - live["observed_at"] > max(60, self.interval + 5):
                    self.update(state="waiting", last_error="Waiting for the inverter radio")
                    continue
                pending = cycle[query_index % len(cycle)]
                query_index += 1
                frame_out = read_request(int(live["network_source"], 16), self.next_sequence(), pending)
                self.update(last_poll_at=time.time(), last_query_kind=pending)
                with self.lock:
                    self.status["requests"] += 1
                tx_status = self.transmit(session, frame_out, "query", pending)
                self.update(last_query_tx_status=tx_status)
                if tx_status != 0:
                    with self.lock:
                        self.status["query_tx_failures"] += 1
                    self.update(state="stale", last_error=f"Radio delivery failed (status {tx_status})")
                response_deadline = time.monotonic() + 5
                last_radio_activity = time.monotonic()
            if now - last_radio_activity > 90:
                # Silence does not establish a bridge failure, including at
                # sunrise before the inverter resumes. Keep the coordinator
                # available; reconnect only when the transport/health check fails.
                session.check_health()
                self.capture.record("bridge_health", ok=True)
                last_radio_activity = time.monotonic()

    def run(self):
        while not self.stop_event.is_set():
            session = None
            try:
                self.update(state="connecting", connected=False)
                self.capture.record("session_start")
                session = self.session_factory(self.host, self.port)
                self.run_session(session)
            except Exception as exc:
                state = "conflict" if "Old Tesla collector" in str(exc) else "disconnected"
                self.update(state=state, connected=False, last_error=str(exc))
                self.capture.record("session_error", state=state, error=str(exc))
                print("Solar poller:", str(exc), flush=True)
            finally:
                self.capture.record("session_end")
                if session:
                    try:
                        session.close()
                    except Exception as exc:
                        print("Solar radio cleanup:", str(exc), flush=True)
            self.stop_event.wait(self.reconnect_interval)
        self.update(state="disconnected", connected=False)
