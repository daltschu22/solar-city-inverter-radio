from contextlib import chdir
import json
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
        self.data = {"host": "radio.example.invalid", "port": 12345, "channel": 20,
                     "pan_id": "0x4321", "extended_pan_id": "0x8877665544332211",
                     "collector_eui": "0200000000000011", "inverter_eui": "0200000000000022",
                     "initial_address": "0x6789"}
        self.env = {"SOLAR_RADIO_HOST": "radio.example.invalid", "SOLAR_RADIO_PORT": "12345",
                    "SOLAR_RADIO_CHANNEL": "20", "SOLAR_PAN_ID": "0x4321",
                    "SOLAR_EXTENDED_PAN_ID": "0x8877665544332211",
                    "SOLAR_COLLECTOR_EUI": "0200000000000011",
                    "SOLAR_INVERTER_EUI": "0200000000000022",
                    "SOLAR_INITIAL_ADDRESS": "0x6789"}

    def save(self, data=None):
        self.path.write_text(json.dumps(self.data if data is None else data))

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

    def test_configuration_controls_wire_identities_beacon_and_address_seed(self):
        self.save()
        self.assert_wire_configuration({"SOLAR_CONFIG": str(self.path)})

    def test_environment_only_controls_wire_identities_beacon_and_address_seed(self):
        self.assert_wire_configuration(self.env)

    def test_environment_overrides_selected_json_fields(self):
        self.save()
        with patch.dict(os.environ, {"SOLAR_RADIO_HOST": "other.example.invalid",
                                    "SOLAR_RADIO_CHANNEL": "0x19", "SOLAR_PAN_ID": "4660"}):
            config = load_config(self.path)
        self.assertEqual((config.host, config.channel, config.pan_id),
                         ("other.example.invalid", 25, 0x1234))
        self.assertEqual((config.port, config.collector_eui, config.initial_address),
                         (12345, self.data["collector_eui"], 0x6789))

    def test_environment_can_complete_partial_json(self):
        self.save({"host": "radio.example.invalid"})
        with patch.dict(os.environ, self.env):
            self.assertTrue(load_config(self.path).configured)

    def test_optional_environment_settings_use_defaults(self):
        settings = {key: value for key, value in self.env.items()
                    if key not in {"SOLAR_RADIO_PORT", "SOLAR_INITIAL_ADDRESS"}}
        with chdir(self.directory.name), patch.dict(os.environ, settings):
            config = load_config()
        self.assertTrue(config.configured)
        self.assertEqual((config.port, config.initial_address), (6638, 0x2345))

    def test_partial_environment_never_uses_synthetic_radio_identities(self):
        with chdir(self.directory.name), patch.dict(os.environ, {"SOLAR_RADIO_HOST": "radio.example.invalid"}):
            with self.assertRaisesRegex(ValueError, "missing fields"):
                load_config()

    def test_missing_implicit_file_without_environment_stays_unconfigured(self):
        with chdir(self.directory.name):
            self.assertFalse(load_config().configured)

    def test_invalid_environment_settings_are_rejected_even_with_valid_json(self):
        self.save()
        for name, value in (("SOLAR_RADIO_HOST", ""), ("SOLAR_RADIO_CHANNEL", ""),
                            ("SOLAR_RADIO_CHANNEL", "27"), ("SOLAR_RADIO_PORT", "0"),
                            ("SOLAR_PAN_ID", "0xffff"), ("SOLAR_EXTENDED_PAN_ID", "0"),
                            ("SOLAR_COLLECTOR_EUI", "secret"),
                            ("SOLAR_COLLECTOR_EUI", "0300000000000001"),
                            ("SOLAR_INVERTER_EUI", self.data["collector_eui"]),
                            ("SOLAR_INITIAL_ADDRESS", "0xfffc")):
            with self.subTest(variable=name, value=value), patch.dict(os.environ, {name: value}):
                with self.assertRaises(ValueError):
                    load_config(self.path)

    def test_environment_does_not_hide_a_bad_explicit_file(self):
        with patch.dict(os.environ, self.env):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                load_config(self.path)
            self.path.write_text("not JSON")
            with self.assertRaisesRegex(ValueError, "Cannot read"):
                load_config(self.path)
            self.save({**self.data, "unknown": 3})
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                load_config(self.path)

    def test_invalid_or_incomplete_settings_are_rejected(self):
        for field, value in (("channel", 27), ("channel", True), ("pan_id", "0xffff"),
                             ("extended_pan_id", 0), ("collector_eui", "secret"),
                             ("collector_eui", "0300000000000001"),
                             ("inverter_eui", self.data["collector_eui"]),
                             ("initial_address", "0xfffc"), ("host", ""),
                             ("port", 0), ("unknown", 3)):
            with self.subTest(field=field, value=value):
                self.save({**self.data, field: value})
                with self.assertRaises(ValueError):
                    load_config(self.path)
        self.save({"host": "radio.example.invalid"})
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_unconfigured_radio_fails_before_opening_socket(self):
        from collector.smlight_collector import RadioSession
        with patch("collector.config.CONFIG", RadioConfig()), patch("socket.create_connection") as connect:
            with self.assertRaisesRegex(ValueError, "radio.local.json"):
                RadioSession("radio.example.invalid", 6638)
            connect.assert_not_called()
            with self.assertRaises(ValueError):
                require_configured()

    def test_template_and_missing_explicit_file_do_not_enable_transmission(self):
        with self.assertRaises(ValueError):
            load_config("config.example.json")
        with self.assertRaises(ValueError):
            load_config(self.path)
