"""Conservative nighttime inference; never creates production measurements."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import math

from astral import Observer
from astral.sun import sunrise, sunset


class NightSchedule:
    def __init__(self, latitude, longitude):
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Invalid solar site coordinates")
        self.observer = Observer(latitude, longitude)

    @lru_cache(maxsize=8)
    def events(self, day):
        return (sunrise(self.observer, date=day).timestamp(),
                sunset(self.observer, date=day).timestamp())

    def night_intervals(self, start, end):
        """Sunset-to-sunrise windows for chart estimates, never measurements."""
        if start is None or end is None or not start < end:
            return []
        day = datetime.fromtimestamp(start, timezone.utc).date() - timedelta(days=1)
        last_day = datetime.fromtimestamp(end, timezone.utc).date()
        intervals = []
        while day <= last_day:
            try:
                rising, setting = self.events(day)
                if rising <= setting:
                    rising, _ = self.events(day + timedelta(days=1))
            except ValueError:
                # Polar days/nights do not provide an ordinary pair of events.
                day += timedelta(days=1)
                continue
            left, right = max(start, setting), min(end, rising)
            if left < right:
                intervals.append({"start": left, "end": right})
            day += timedelta(days=1)
        return intervals

    def standby_until(self, reading, now):
        if not reading:
            return None
        at, watts = reading["observed_at"], reading["solar_w"]
        if not (math.isfinite(at) and math.isfinite(watts)) or abs(watts) > 50 or now - at < 90:
            return None
        day = datetime.fromtimestamp(now, timezone.utc).date()
        try:
            events = [self.events(day + timedelta(days=offset)) for offset in (-1, 0, 1)]
            last_sunset = max(setting for _, setting in events if setting <= now)
            next_sunrise = min(rising for rising, _ in events if rising > last_sunset)
        except ValueError:
            # No ordinary rise/set cycle: do not suppress an outage warning.
            return None
        # Only a low reading near THIS sunset supports an overnight inference.
        if last_sunset - 2 * 3600 <= at <= now < next_sunrise:
            return next_sunrise
        return None
