from datetime import datetime
import unittest

from daylight import NightSchedule


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


class DaylightTests(unittest.TestCase):
    def setUp(self):
        # New York is a test site, not a default for an unconfigured installation.
        self.schedule = NightSchedule(40.71, -74.01)
        self.reading = {"observed_at": timestamp("2026-10-02T18:41:00-04:00"), "solar_w": -0.32}

    def test_evening_and_after_midnight_use_same_sunrise(self):
        until = self.schedule.standby_until(self.reading, timestamp("2026-10-02T22:00:00-04:00"))
        self.assertGreater(until, timestamp("2026-10-03T06:30:00-04:00"))
        self.assertLess(until, timestamp("2026-10-03T07:30:00-04:00"))
        self.assertEqual(until, self.schedule.standby_until(
            self.reading, timestamp("2026-10-03T03:00:00-04:00")))
        self.assertIsNone(self.schedule.standby_until(self.reading, until))

    def test_daytime_high_output_old_reading_and_missing_data_do_not_mask_outages(self):
        night = timestamp("2026-10-02T22:00:00-04:00")
        for reading, now in (
            (None, night),
            ({**self.reading, "solar_w": 500}, night),
            ({**self.reading, "solar_w": -500}, night),
            ({**self.reading, "solar_w": float("nan")}, night),
            ({**self.reading, "observed_at": timestamp("2026-10-02T12:00:00-04:00")}, night),
            (self.reading, timestamp("2026-10-03T22:00:00-04:00")),
            (self.reading, timestamp("2026-10-03T12:00:00-04:00")),
            (self.reading, self.reading["observed_at"] + 30),
        ):
            with self.subTest(reading=reading, now=now):
                self.assertIsNone(self.schedule.standby_until(reading, now))

    def test_dst_transition_uses_actual_sunrise_not_fixed_clock_hours(self):
        reading = {"observed_at": timestamp("2026-10-31T17:45:00-04:00"), "solar_w": 0}
        before = self.schedule.standby_until(reading, timestamp("2026-11-01T01:30:00-04:00"))
        after = self.schedule.standby_until(reading, timestamp("2026-11-01T01:30:00-05:00"))
        self.assertIsNotNone(before)
        self.assertEqual(before, after)
        self.assertIsNone(self.schedule.standby_until(reading, before + 1))

    def test_invalid_coordinates_and_polar_conditions_fail_closed(self):
        for coordinates in ((91, 0), (0, 181), (float("nan"), 0)):
            with self.assertRaises(ValueError):
                NightSchedule(*coordinates)
        polar = NightSchedule(89, 0)
        self.assertIsNone(polar.standby_until(self.reading, timestamp("2026-10-02T22:00:00-04:00")))


if __name__ == "__main__":
    unittest.main()
