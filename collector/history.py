"""Persistent solar readings, independent of the radio and dashboard."""

from pathlib import Path
import sqlite3
import threading
import time


class SolarHistoryStore:
    RANGE_SECONDS = {
        "1h": 60 * 60,
        "6h": 6 * 60 * 60,
        "24h": 24 * 60 * 60,
        "7d": 7 * 24 * 60 * 60,
        "30d": 30 * 24 * 60 * 60,
        "1y": 365 * 24 * 60 * 60,
        "all": None,
    }
    MAX_CHART_POINTS = 5000

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA synchronous=NORMAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS solar_readings (
                    id INTEGER PRIMARY KEY,
                    capture_id TEXT NOT NULL UNIQUE,
                    observed_at REAL NOT NULL,
                    capture_sweep INTEGER NOT NULL,
                    radio_timestamp INTEGER NOT NULL,
                    solar_w REAL NOT NULL,
                    lifetime_wh REAL
                )
                """
            )
            database.execute(
                """
                CREATE INDEX IF NOT EXISTS solar_readings_observed_at
                ON solar_readings (observed_at)
                """
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, measurements):
        rows = [
            (
                item["capture_id"],
                item["observed_at"],
                item["capture_sweep"],
                item["radio_timestamp"],
                item["solar_w"],
                item.get("lifetime_wh"),
            )
            for item in measurements
        ]
        if not rows:
            return 0
        with self.lock, self.connect() as database:
            before = database.total_changes
            database.executemany(
                """
                INSERT OR IGNORE INTO solar_readings (
                    capture_id,
                    observed_at,
                    capture_sweep,
                    radio_timestamp,
                    solar_w,
                    lifetime_wh
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return database.total_changes - before

    def latest(self):
        with self.lock, self.connect() as database:
            database.row_factory = sqlite3.Row
            row = database.execute(
                """
                SELECT
                    observed_at,
                    capture_sweep,
                    radio_timestamp,
                    solar_w,
                    lifetime_wh
                FROM solar_readings
                ORDER BY observed_at DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def latest_energy(self):
        reading = self.latest_energy_reading()
        return reading["lifetime_wh"] if reading else None

    def latest_energy_reading(self):
        with self.lock, self.connect() as database:
            database.row_factory = sqlite3.Row
            row = database.execute(
                "SELECT lifetime_wh, observed_at FROM solar_readings WHERE lifetime_wh IS NOT NULL "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def query(self, range_name):
        if range_name not in self.RANGE_SECONDS:
            raise ValueError("Unknown history range")

        duration = self.RANGE_SECONDS[range_name]
        now = time.time()
        start = now - duration if duration else None
        where = "WHERE observed_at >= ?" if start is not None else ""
        parameters = (start,) if start is not None else ()

        with self.lock, self.connect() as database:
            database.row_factory = sqlite3.Row
            summary = database.execute(
                f"""
                SELECT
                    COUNT(*) AS sample_count,
                    MIN(observed_at) AS first_at,
                    MAX(observed_at) AS last_at,
                    MAX(solar_w) AS peak_w
                FROM solar_readings
                {where}
                """,
                parameters,
            ).fetchone()
            sample_count = int(summary["sample_count"])
            energy_where = f"{where} AND lifetime_wh IS NOT NULL" if where else "WHERE lifetime_wh IS NOT NULL"
            energy_rows = [database.execute(
                f"SELECT observed_at, lifetime_wh FROM solar_readings {energy_where} "
                f"ORDER BY observed_at {order} LIMIT 1", parameters,
            ).fetchone() for order in ("ASC", "DESC")]

            if sample_count <= self.MAX_CHART_POINTS:
                rows = database.execute(
                    f"""
                    SELECT observed_at, solar_w, lifetime_wh
                    FROM solar_readings
                    {where}
                    ORDER BY observed_at ASC
                    """,
                    parameters,
                ).fetchall()
            else:
                chart_start = float(summary["first_at"])
                span = max(1.0, float(summary["last_at"]) - chart_start)
                bucket_seconds = span / max(1, self.MAX_CHART_POINTS - 1)
                bucket_where = (
                    "WHERE observed_at >= ?" if start is not None else ""
                )
                bucket_parameters = (
                    (start, chart_start, bucket_seconds)
                    if start is not None
                    else (chart_start, bucket_seconds)
                )
                rows = database.execute(
                    f"""
                    SELECT
                        AVG(observed_at) AS observed_at,
                        AVG(solar_w) AS solar_w,
                        MAX(lifetime_wh) AS lifetime_wh
                    FROM solar_readings
                    {bucket_where}
                    GROUP BY CAST((observed_at - ?) / ? AS INTEGER)
                    ORDER BY observed_at ASC
                    """,
                    bucket_parameters,
                ).fetchall()

        generated_wh = None
        if (
            energy_rows[0]
            and energy_rows[1]
            and energy_rows[1]["observed_at"] > energy_rows[0]["observed_at"]
        ):
            difference = energy_rows[1]["lifetime_wh"] - energy_rows[0]["lifetime_wh"]
            generated_wh = round(difference, 3) if difference >= 0 else None

        return {
            "range": range_name,
            "window_start": start if start is not None else summary["first_at"],
            "window_end": now,
            "retention": "unlimited",
            "sample_count": sample_count,
            "point_count": len(rows),
            "downsampled": sample_count > self.MAX_CHART_POINTS,
            "first_at": summary["first_at"],
            "last_at": summary["last_at"],
            "peak_w": summary["peak_w"],
            "generated_wh": generated_wh,
            "energy_first_at": energy_rows[0]["observed_at"] if energy_rows[0] else None,
            "energy_last_at": energy_rows[1]["observed_at"] if energy_rows[1] else None,
            "points": [
                {
                    "timestamp": row["observed_at"],
                    "solar_w": round(float(row["solar_w"]), 3),
                    "lifetime_wh": row["lifetime_wh"],
                }
                for row in rows
            ],
        }
