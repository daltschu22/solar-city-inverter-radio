import json
import contextlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.discover_radio import analyze, beacon_fields, load_records, mac_payload_offset, main
from synthetic_radio import records

ROOT = Path(__file__).resolve().parents[1]
INVERTER = '0200000000000002'
COLLECTOR = '0200000000000001'
EXTENDED_PAN = 0x1122334455667788


def beacon(source=0x2345, extended_pan=EXTENDED_PAN, variable=False):
    mac = struct.pack('<HBHH', 0x8000, 1, 0x1234, source)
    # Superframe, one optional GTS descriptor, and pending short/long addresses.
    fields = struct.pack('<H', 0xcfff)
    fields += b'\x01\x00\x01\x00\x11' if variable else b'\0'
    fields += b'\x11' + bytes(10) if variable else b'\0'
    payload = b'\0\x20\x84' + struct.pack('<Q', extended_pan) + b'\xff\xff\xff\0'
    return {'raw': (mac + fields + payload).hex(), 'channel': 14}


def startup():
    source = bytes.fromhex(records()[1]['raw'])
    return {'raw': (source[:41] + bytes.fromhex('f400010101')).hex(), 'channel': 14}


def candidate(report):
    return report['networks'][0]['inverter_candidates'][0]


class DiscoveryTests(unittest.TestCase):
    def test_recovers_all_settings_from_inverter_originated_frames(self):
        # Only inverter responses and its beacon are supplied, with no site config.
        frames = [f for i, f in enumerate(records()) if i % 2] + [beacon()]
        own = candidate(analyze(frames))['inverter_only']
        expected = {'channel': 14, 'pan_id': '0x1234',
                    'extended_pan_id': '0x1122334455667788',
                    'inverter_eui': INVERTER, 'collector_eui': COLLECTOR}
        self.assertEqual({k: v['value'] for k, v in own.items()}, expected)
        self.assertTrue(all(v['status'] == 'observed' for v in own.values()))
        self.assertEqual(own['extended_pan_id']['evidence'][0]['sources'], [INVERTER])

    def test_startup_alone_exposes_pair_but_not_extended_pan(self):
        c = candidate(analyze([startup()]))
        self.assertEqual(c['reasons'], ['startup_f4'])
        self.assertEqual(c['inverter_only']['collector_eui']['value'], COLLECTOR)
        self.assertEqual(c['inverter_only']['extended_pan_id']['status'], 'unknown')

    def test_coordinator_beacon_is_not_inverter_only_evidence(self):
        c = candidate(analyze([startup(), beacon(source=0)]))
        self.assertEqual(c['whole_network']['extended_pan_id']['status'], 'observed')
        self.assertEqual(c['inverter_only']['extended_pan_id']['status'], 'unknown')

    def test_anonymous_search_does_not_invent_network_or_identity(self):
        report = analyze([{'raw': '030801ffffffff07', 'channel': 18}])
        self.assertEqual(report['anonymous_beacon_requests'], {'18': 1})
        self.assertEqual(report['networks'], [])

    def test_conflicting_extended_pans_are_reported_not_guessed(self):
        c = candidate(analyze([startup(), beacon(), beacon(extended_pan=123)]))
        self.assertEqual(c['inverter_only']['extended_pan_id']['status'], 'conflict')
        self.assertIsNone(c['inverter_only']['extended_pan_id']['value'])

    def test_inverter_short_address_ambiguity_prevents_beacon_attribution(self):
        first = startup()
        second = dict(first, raw=first['raw'].replace('0200000000000002', '9900000000000002'))
        report = analyze([first, second, beacon()])
        self.assertEqual(report['networks'][0]['ambiguous_short_addresses'], ['0x2345'])
        self.assertEqual(len(report['networks'][0]['inverter_candidates']), 2)
        for c in report['networks'][0]['inverter_candidates']:
            self.assertEqual(c['inverter_only']['extended_pan_id']['status'], 'unknown')

    def test_beacon_variable_sections_are_skipped(self):
        for variable in (False, True):
            raw = bytes.fromhex(beacon(variable=variable)['raw'])
            self.assertEqual(beacon_fields(raw, mac_payload_offset(raw)),
                             {'extended_pan_id': '0x1122334455667788', 'stack_profile': 0})

    def test_bad_frames_and_decoded_metadata_cannot_supply_identity(self):
        bad = [dict(startup(), bad_fcs=True), dict(startup(), receive_error=1),
               {'raw': 'not hex', 'channel': 14}, {'raw': '00', 'channel': 14},
               dict(startup(), channel=27)]
        self.assertEqual(analyze(bad)['networks'], [])
        cached = dict(startup(), network_source_ieee='0200000000000099',
                      network_destination_ieee='0200000000000088', source_pan_id='0x9999')
        own = candidate(analyze([cached]))['inverter_only']
        self.assertEqual(own['inverter_eui']['value'], INVERTER)
        self.assertEqual(own['collector_eui']['value'], COLLECTOR)

    def test_bad_modbus_crc_and_relayed_frames_do_not_identify_inverter(self):
        response = records()[1]
        raw = bytearray.fromhex(response['raw'])
        raw[-1] ^= 1
        self.assertEqual(analyze([dict(response, raw=raw.hex())])['networks'][0]['inverter_candidates'], [])
        raw = bytearray.fromhex(startup()['raw'])
        raw[7:9] = b'\x11\x11'  # Direct MAC source no longer matches NWK source.
        self.assertEqual(analyze([dict(startup(), raw=raw.hex())])['networks'][0]['inverter_candidates'], [])

    def test_capture_import_does_not_require_private_config(self):
        subprocess.run([sys.executable, '-c',
                        'import tools.smlight_capture; import tools.discover_radio'], cwd=ROOT,
                       env={**os.environ, 'SOLAR_RADIO_CHANNEL': 'invalid'}, check=True)

    def test_cli_is_offline_ignores_config_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frames.json'
            output = Path(directory) / 'report.json'
            path.write_text(json.dumps([startup(), beacon()]))
            args = [sys.executable, str(ROOT / 'tools/discover_radio.py'),
                    '--input', str(path), '--output', str(output)]
            env = {**os.environ, 'SOLAR_RADIO_CHANNEL': 'invalid'}
            with patch('socket.create_connection', side_effect=AssertionError('No sockets allowed')):
                self.assertEqual(candidate(analyze(load_records(path)))['inverter_eui'], INVERTER)
            subprocess.run(args, cwd=directory, env=env, check=True, capture_output=True)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            original = output.read_bytes()
            self.assertNotEqual(subprocess.run(args, cwd=directory, env=env, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), original)

    def test_live_capture_requires_explicit_exclusive_ownership(self):
        run = subprocess.run([sys.executable, str(ROOT / 'tools/discover_radio.py'),
                              '--host', 'radio.example.invalid', '--output', 'unused.json'],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--exclusive-radio', run.stderr)

    def test_live_wrapper_is_passive_and_refuses_existing_report_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'discovery.json'
            args = ['discover_radio.py', '--host', 'radio.example.invalid', '--exclusive-radio',
                    '--channels', '18', '--seconds', '30', '--output', str(output)]

            def capture(options):
                self.assertFalse(options.beacon_request)
                self.assertEqual(options.channels, [18])
                self.assertEqual(options.seconds, 30)
                Path(f'{options.output}.json').write_text(json.dumps([startup(), beacon()]))

            old_mask = os.umask(0o077)
            try:
                with patch.object(sys, 'argv', args), patch('tools.smlight_capture.capture', side_effect=capture) as gather:
                    with contextlib.redirect_stdout(io.StringIO()):
                        main()
                    self.assertEqual(gather.call_count, 1)
                    self.assertEqual(candidate(json.loads(output.read_text()))['inverter_eui'], INVERTER)
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        main()
                    self.assertEqual(gather.call_count, 1)
            finally:
                os.umask(old_mask)

    def test_jsonl_and_wrapped_capture_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'capture.json'
            source = [startup(), beacon()]
            for contents in (json.dumps(source), json.dumps({'frames': source}),
                             json.dumps({'received_frames': source}),
                             '\n'.join(json.dumps(row) for row in source)):
                p.write_text(contents)
                self.assertEqual(load_records(p), source)
