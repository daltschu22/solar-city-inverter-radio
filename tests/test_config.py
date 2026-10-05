from contextlib import chdir
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from collector.config import ENV_FIELDS, RadioConfig, load_config, require_configured


class ConfigTests(unittest.TestCase):
    def setUp(self):
        settings = {"SOLAR_CONFIG", *ENV_FIELDS.values()}
        environment = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                              if key not in settings}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "radio.local.json"
        self.env = {"SOLAR_RADIO_HOST": "radio.example.invalid", "SOLAR_RADIO_PORT": "12345",
                    "SOLAR_RADIO_CHANNEL": "20", "SOLAR_PAN_ID": "0x4321",
                    "SOLAR_EXTENDED_PAN_ID": "0x8877665544332211",
                    "SOLAR_COLLECTOR_EUI": "0200000000000011",
                    "SOLAR_INVERTER_EUI": "0200000000000022",
                    "SOLAR_INITIAL_ADDRESS": "0x6789"}

    def assert_wire_configuration(self, settings):
        code = '''
from collector.config import CONFIG
from collector.coordinator import Coordinator, beacon
from collector.radio_protocol import decode_ieee802154_frame
from tools.smlight_poll import read_request
assert CONFIG.host == "radio.example.invalid" and CONFIG.port == 12345
assert CONFIG.channel == 20
frame = decode_ieee802154_frame({"raw": read_request(0x6789, 1, "power")[:-2].hex(), "type": "data"})
assert frame["source_pan_id"] == "0x4321"
assert frame["network_source_ieee"] == "0200000000000011"
assert frame["network_destination_ieee"] == "0200000000000022"
assert bytes.fromhex("1122334455667788") in beacon(1)
assert Coordinator().assigned_address == 0x6789
'''
        subprocess.run([sys.executable, "-c", code], check=True,
                       cwd=self.directory.name,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                            **settings})

    def test_environment_controls_wire_identities_beacon_and_address_seed(self):
        self.assert_wire_configuration(self.env)

    def test_optional_environment_settings_use_defaults(self):
        settings = {key: value for key, value in self.env.items()
                    if key not in {"SOLAR_RADIO_PORT", "SOLAR_INITIAL_ADDRESS"}}
        with patch.dict(os.environ, settings):
            config = load_config()
        self.assertTrue(config.configured)
        self.assertEqual((config.port, config.initial_address), (6638, 0x2345))
        self.assertEqual((config.poll_interval_seconds, config.reconnect_interval_seconds), (60, 15))

    def test_timing_environment_settings_are_optional_and_bounded(self):
        for poll, reconnect in ((15, 1), (120, 30), (3600, 3600)):
            with self.subTest(poll=poll, reconnect=reconnect), patch.dict(os.environ, {
                    **self.env, "SOLAR_POLL_INTERVAL_SECONDS": str(poll),
                    "SOLAR_RECONNECT_INTERVAL_SECONDS": str(reconnect)}):
                config = load_config()
                self.assertEqual((config.poll_interval_seconds, config.reconnect_interval_seconds),
                                 (poll, reconnect))
        for name, invalid in (("SOLAR_POLL_INTERVAL_SECONDS", ("", "0", "-1", "14", "3601", "1.5", "nan", "inf")),
                              ("SOLAR_RECONNECT_INTERVAL_SECONDS", ("", "0", "-1", "3601", "1.5", "nan", "inf"))):
            for value in invalid:
                with self.subTest(name=name, value=value), patch.dict(os.environ, {**self.env, name: value}):
                    with self.assertRaisesRegex(ValueError, name):
                        load_config()

    def test_decimal_and_hexadecimal_numbers_are_supported(self):
        with patch.dict(os.environ, {**self.env, "SOLAR_RADIO_CHANNEL": "0x19", "SOLAR_PAN_ID": "4660"}):
            config = load_config()
        self.assertEqual((config.channel, config.pan_id), (25, 0x1234))

    def test_passive_mode_and_freshness_are_explicit_and_validated(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(load_config().mode, "replacement")
        with patch.dict(os.environ, {**self.env, "SOLAR_COLLECTOR_MODE": "passive",
                                     "SOLAR_PASSIVE_STALE_SECONDS": "600"}):
            config = load_config()
            self.assertEqual((config.mode, config.passive_stale_seconds), ("passive", 600))
        for name, values in (("SOLAR_COLLECTOR_MODE", ("", "listen", "PASSIVE")),
                             ("SOLAR_PASSIVE_STALE_SECONDS", ("", "0", "29", "86401", "1.5"))):
            for value in values:
                with self.subTest(name=name, value=value), patch.dict(os.environ, {**self.env, name: value}):
                    with self.assertRaisesRegex(ValueError, name):
                        load_config()

    def test_each_required_variable_must_be_present(self):
        optional = {"SOLAR_RADIO_PORT", "SOLAR_INITIAL_ADDRESS"}
        for name in self.env.keys() - optional:
            settings = {key: value for key, value in self.env.items() if key != name}
            with self.subTest(name=name), patch.dict(os.environ, settings):
                with self.assertRaisesRegex(ValueError, name):
                    load_config()

    def test_partial_environment_never_uses_synthetic_radio_identities(self):
        with patch.dict(os.environ, {"SOLAR_RADIO_HOST": "radio.example.invalid"}):
            with self.assertRaisesRegex(ValueError, "Missing radio environment"):
                load_config()

    def test_configuration_uses_only_environment(self):
        self.path.write_text('{"host":"must-not-be-used.example.invalid"}')
        with chdir(self.directory.name):
            self.assertFalse(load_config().configured)
            with patch.dict(os.environ, self.env):
                self.assertEqual(load_config().host, "radio.example.invalid")

    def test_unsupported_setting_is_rejected_with_or_without_radio_settings(self):
        for settings in ({}, self.env):
            with self.subTest(configured=bool(settings)), patch.dict(os.environ, {**settings, "SOLAR_CONFIG": str(self.path)}):
                with self.assertRaisesRegex(ValueError, "Unsupported setting SOLAR_CONFIG"):
                    load_config()

    def test_invalid_environment_settings_are_rejected(self):
        cases = [(name, "") for name in self.env]
        cases += [("SOLAR_RADIO_CHANNEL", "27"), ("SOLAR_RADIO_CHANNEL", "10"),
                  ("SOLAR_RADIO_PORT", "0"), ("SOLAR_RADIO_PORT", "65536"),
                  ("SOLAR_RADIO_CHANNEL", "true"), ("SOLAR_RADIO_CHANNEL", "14.5"),
                  ("SOLAR_PAN_ID", "0xffff"), ("SOLAR_EXTENDED_PAN_ID", "0"),
                  ("SOLAR_COLLECTOR_EUI", "secret"), ("SOLAR_COLLECTOR_EUI", "0300000000000001"),
                  ("SOLAR_INVERTER_EUI", self.env["SOLAR_COLLECTOR_EUI"]),
                  ("SOLAR_INITIAL_ADDRESS", "0xfffc"), ("SOLAR_RADIO_HOST", "   ")]
        for name, value in cases:
            with self.subTest(variable=name, value=value), patch.dict(os.environ, {**self.env, name: value}):
                with self.assertRaises(ValueError):
                    load_config()

    def test_unconfigured_radio_fails_before_opening_socket(self):
        from collector.smlight_collector import RadioSession
        with patch("collector.config.CONFIG", RadioConfig()), patch("socket.create_connection") as connect:
            with self.assertRaisesRegex(ValueError, "radio environment variables"):
                RadioSession("radio.example.invalid", 6638)
            connect.assert_not_called()
            with self.assertRaises(ValueError):
                require_configured()

    def test_unfilled_env_template_does_not_enable_transmission(self):
        settings = dict(line.split("=", 1) for line in Path("config.example.env").read_text().splitlines()
                        if line and not line.startswith("#"))
        with patch.dict(os.environ, settings):
            with self.assertRaises(ValueError):
                load_config()
