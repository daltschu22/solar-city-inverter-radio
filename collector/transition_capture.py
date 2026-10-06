"""Temporary, bounded evidence recording on the collector's existing connection."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import time


# Keep power and energy in the normal cycle's slots, even when switching cycles.
# Replace meter/identity reads with more DC/state reads around sunrise/sunset.
TRANSITION_CYCLE = (
    "power", "inverter_ac", "power", "energy", "power", "inverter_dc",
    "power", "energy", "power", "inverter_dc", "power", "inverter_ac",
    "power", "inverter_dc", "power", "energy",
)


def near_transition(schedule, now):
    if schedule is None:
        return False
    day = datetime.fromtimestamp(now, timezone.utc).date()
    for offset in (-1, 0, 1):
        try:
            events = schedule.events(day + timedelta(days=offset))
        except ValueError:
            continue
        if any(abs(event - now) <= 90 * 60 for event in events):
            return True
    return False


class TransitionCapture:
    """One writer in the polling thread; snapshots never write or open files."""

    def __init__(self, directory, until=None, *, max_bytes=16 * 1024 * 1024, backups=3):
        self.path = Path(directory) / "transition-capture.jsonl"
        self.until = until
        self.max_bytes, self.backups = max_bytes, backups
        self.lock = threading.Lock()
        self.error = None
        self.records = 0
        self.last_record_at = None

    def active(self, now=None):
        now = time.time() if now is None else now
        return self.until is not None and now < self.until and self.error is None

    def snapshot(self):
        with self.lock:
            return {"active": self.active(), "until": self.until,
                    "records": self.records, "last_record_at": self.last_record_at,
                    "error": self.error}

    def record(self, event, **fields):
        now = time.time()
        if not self.active(now):
            return
        try:
            line = (json.dumps({"schema": 1, "at": now, "event": event, **fields},
                               separators=(",", ":"), allow_nan=False) + "\n").encode()
            if len(line) > self.max_bytes:
                raise ValueError("capture record exceeds file limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = self.path if index == 1 else self.path.with_suffix(f".jsonl.{index - 1}")
                    if source.exists():
                        source.replace(self.path.with_suffix(f".jsonl.{index}"))
            # Refuse symlinks and restrict even an existing file's permissions.
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "ab") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(line)
            with self.lock:
                self.records += 1
                self.last_record_at = now
        except (OSError, ValueError, TypeError) as exc:
            # A diagnostics failure must not interrupt collection or radio replies.
            with self.lock:
                self.error = f"Recording stopped ({type(exc).__name__}); check storage and service logs"
            print("Solar transition capture stopped:", str(exc), flush=True)
