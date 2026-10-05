import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from config import RadioConfig, load_config, require_configured


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "radio.local.json"
        self.data = {"host": "radio.example.invalid", "port": 12345, "channel": 20,
                     "pan_id": "0x4321", "extended_pan_id": "0x8877665544332211",
                     "collector_eui": "0200000000000011", "inverter_eui": "0200000000000022",
                     "initial_address": "0x6789"}

    def save(self, data=None):
        self.path.write_text(json.dumps(self.data if data is None else data))

    def test_configuration_controls_wire_identities_beacon_and_address_seed(self):
        self.save()
        code = '''
from config import CONFIG
from coordinator import Coordinator, beacon
from server import decode_ieee802154_frame
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
                       env={**os.environ, "SOLAR_CONFIG": str(self.path)})

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
        from smlight_collector import RadioSession
        with patch("config.CONFIG", RadioConfig()), patch("socket.create_connection") as connect:
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
