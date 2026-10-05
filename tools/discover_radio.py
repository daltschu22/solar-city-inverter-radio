#!/usr/bin/env python3
"""Gather candidate radio settings without a site config or original collector.

Analyze JSON/JSONL offline, or capture passively on an exclusively owned SMLIGHT.
Radio identity is evidence, not proof of inverter model or commissioning success.
"""

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.radio_protocol import decode_ieee802154_frame, decode_modbus_message

FIELDS = ('channel', 'pan_id', 'extended_pan_id', 'inverter_eui', 'collector_eui')


def eui(value):
    if not isinstance(value, str):
        return None
    value = value.removeprefix('0x').lower()
    if len(value) != 16 or any(c not in '0123456789abcdef' for c in value):
        return None
    return value if int(value, 16) not in (0, 0xffffffffffffffff) else None


def mac_payload_offset(raw):
    """Locate a legacy 802.15.4 payload; reject unsupported header layouts."""
    if len(raw) < 3:
        raise ValueError('truncated MAC header')
    control = int.from_bytes(raw[:2], 'little')
    if (control >> 12) & 3 > 1 or control & 0x0300:
        raise ValueError('unsupported MAC header layout')
    destination, source = (control >> 10) & 3, (control >> 14) & 3
    if destination == 1 or source == 1 or (control & 0x40 and not destination):
        raise ValueError('invalid legacy addressing')
    offset = 3
    if destination:
        offset += 2 + (2 if destination == 2 else 8)
    if source:
        offset += (0 if control & 0x40 else 2) + (2 if source == 2 else 8)
    if offset > len(raw):
        raise ValueError('truncated MAC addresses')
    return offset


def beacon_fields(raw, offset):
    """Skip variable GTS/pending-address sections before the Zigbee descriptor."""
    if len(raw) < offset + 4:
        raise ValueError('truncated beacon')
    offset += 2  # Superframe specification.
    count = raw[offset] & 7
    offset += 1 + (1 + 3 * count if count else 0)
    if offset >= len(raw):
        raise ValueError('truncated beacon GTS fields')
    pending = raw[offset]
    offset += 1 + 2 * (pending & 7) + 8 * ((pending >> 4) & 7)
    payload = raw[offset:]
    if len(payload) < 15 or payload[0] != 0 or payload[1] >> 4 != 2:
        raise ValueError('not a complete supported Zigbee beacon')
    return {'extended_pan_id': f'0x{int.from_bytes(payload[3:11], "little"):016x}',
            'stack_profile': payload[1] & 15}


def load_records(path):
    content = path.read_text(encoding='utf-8')
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in content.splitlines() if line.strip()]
    if isinstance(data, dict):
        if 'raw' in data:
            data = [data]
        else:
            data = next((data[key] for key in ('frames', 'received_frames', 'packets')
                         if isinstance(data.get(key), list)), None)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError('Expected frame records as a JSON list, frames object, or JSONL')
    return data


def analyze(records):
    networks = {}
    rejected = Counter()
    anonymous = Counter()
    accepted = 0

    def network(channel, pan):
        key = (channel, pan)
        if key not in networks:
            networks[key] = {'events': [], 'bindings': defaultdict(set),
                             'nodes': defaultdict(lambda: {'shorts': set(), 'frames': []}),
                             'candidates': set(), 'security': Counter()}
        return networks[key]

    for index, record in enumerate(records, 1):
        try:
            if record.get('bad_fcs') or record.get('receive_error'):
                raise ValueError('capture reported an invalid frame')
            channel = record.get('channel')
            if type(channel) is not int or channel not in range(11, 27):
                raise ValueError('missing or invalid channel')
            raw = bytes.fromhex(record['raw'])
            if not 3 <= len(raw) <= 125:  # Input convention excludes two FCS bytes.
                raise ValueError('invalid frame length or FCS not stripped')
            control = int.from_bytes(raw[:2], 'little')
            offset = mac_payload_offset(raw)
            kind = {0: 'beacon', 1: 'data', 2: 'ack', 3: 'command'}.get(control & 7)
            if kind is None:
                raise ValueError('unsupported MAC frame type')
            # Re-decode raw bytes. Never trust cached decoded identity fields.
            frame = decode_ieee802154_frame({'raw': raw.hex(), 'type': kind, 'channel': channel})
            if control & 8:
                raise ValueError('MAC security not supported')
            if kind == 'command' and frame.get('mac_command_id') == 7:
                if offset + 1 != len(raw):
                    raise ValueError('malformed beacon request')
                if frame.get('mac_source') is None:
                    anonymous[channel] += 1
            pan = frame.get('source_pan_id')
            if pan in (None, '0xffff'):
                pan = frame.get('destination_pan_id')
            if pan in (None, '0xffff'):
                accepted += 1
                continue
            descriptor = beacon_fields(raw, offset) if kind == 'beacon' else {}
            if kind == 'data' and frame.get('protocol') != 'zigbee':
                raise ValueError('not a supported Zigbee network frame')
        except (ValueError, KeyError, TypeError, IndexError, struct.error) as exc:
            rejected[str(exc)] += 1
            continue
        accepted += 1
        net = network(channel, pan)
        short = frame.get('mac_source')
        if not isinstance(short, str) or len(short) != 6:
            short = None
        direct = frame.get('network_source') == short and short is not None
        source = (eui(frame.get('network_source_ieee')) if direct else None)
        source = source or eui(frame.get('mac_source'))
        secured = bool(frame.get('network_secured') or frame.get('aps_secured'))
        if kind == 'data':
            net['security']['secured' if secured else 'unsecured'] += 1
        payload = bytes.fromhex(frame.get('application_payload', ''))
        if (not secured and direct and frame.get('profile_id') == '0x0000'
                and frame.get('cluster_id') == '0x0013'
                and frame.get('source_endpoint') == frame.get('destination_endpoint') == 0
                and 'aps_fragmentation' not in frame and len(payload) == 12
                and int.from_bytes(payload[1:3], 'little') == int(short, 16)):
            announced = eui(payload[3:11][::-1].hex())
            if source is None or source == announced:
                source = announced
        if source:
            node = net['nodes'][source]
            if len(node['frames']) < 5:
                node['frames'].append(index)
            if short:
                node['shorts'].add(short)
                net['bindings'][short].add(source)
        event = {'frame': index, 'source_eui': source, 'source_short': short,
                 'kind': kind, 'fields': {'channel': channel, 'pan_id': pan, **descriptor}}
        if direct and short == '0x0000' and source:
            event['fields']['collector_eui'] = source
        serial = (not secured and direct and short != '0x0000'
                  and frame.get('network_destination') == frame.get('mac_destination') == '0x0000'
                  and frame.get('profile_id') == '0xc105' and frame.get('cluster_id') == '0x0011'
                  and frame.get('source_endpoint') == frame.get('destination_endpoint') == 0xe8
                  and frame.get('aps_frame_type') == 'data' and 'aps_fragmentation' not in frame)
        if serial:
            message = decode_modbus_message(payload)
            reason = ('startup_f4' if payload == bytes.fromhex('f400010101') else
                      'modbus_read_response' if message.get('crc_valid') and
                      message.get('kind') == 'response' and message.get('unit_id') == 1 else None)
            if reason:
                event['candidate_reason'] = reason
                destination = eui(frame.get('network_destination_ieee'))
                if destination:
                    event['fields']['collector_eui'] = destination
        net['events'].append(event)

    reports = []
    for (channel, pan), net in sorted(networks.items()):
        for event in net['events']:
            if event['source_eui'] is None:
                matches = net['bindings'].get(event['source_short'], set())
                if len(matches) == 1:
                    event['source_eui'] = next(iter(matches))
            if event.get('candidate_reason') and event['source_eui']:
                net['candidates'].add(event['source_eui'])
                event['fields']['inverter_eui'] = event['source_eui']
        candidates = []
        for inverter in sorted(net['candidates']):
            own = [event for event in net['events'] if event['source_eui'] == inverter]
            # Network facts may come from other devices. Pair identity must be
            # supported by this candidate or the coordinator, not another inverter.
            relevant = [event for event in net['events']
                        if event['source_eui'] == inverter or event['source_short'] == '0x0000'
                        or event['kind'] == 'beacon']
            candidates.append({'inverter_eui': inverter,
                               'reasons': sorted({e['candidate_reason'] for e in own if e.get('candidate_reason')}),
                               'inverter_only': evidence(own),
                               'whole_network': evidence(relevant)})
        reports.append({'channel': channel, 'pan_id': pan,
                        'network_fields': evidence(net['events'], fields=('extended_pan_id', 'stack_profile')),
                        'security': dict(net['security']),
                        'nodes': [{'eui': address, 'short_addresses': sorted(node['shorts']),
                                   'example_frames': node['frames']} for address, node in sorted(net['nodes'].items())],
                        'ambiguous_short_addresses': sorted(short for short, eu in net['bindings'].items() if len(eu) > 1),
                        'inverter_candidates': candidates})
    return {'schema_version': 1, 'input_frames': len(records), 'accepted_frames': accepted,
            'rejected_frames': dict(rejected),
            'anonymous_beacon_requests': {str(k): v for k, v in sorted(anonymous.items())},
            'networks': reports,
            'limitations': ['Candidate identity does not confirm inverter model or compatibility.',
                            'Absent fields remain unknown; no installation config or default identities are read.',
                            'Inverter-only evidence excludes coordinator-originated fields, but the capture may have occurred on an active network.',
                            'A complete observation is not proof of cold commissioning without the original collector.']}


def evidence(events, fields=FIELDS):
    result = {}
    for field in fields:
        values = {}
        for event in events:
            if field not in event['fields']:
                continue
            value = event['fields'][field]
            key = str(value)
            entry = values.setdefault(key, {'value': value, 'count': 0, 'frames': [], 'sources': set()})
            entry['count'] += 1
            if len(entry['frames']) < 5:
                entry['frames'].append(event['frame'])
            if event['source_eui']:
                entry['sources'].add(event['source_eui'])
        entries = []
        for key in sorted(values):
            entry = values[key]
            entries.append({**entry, 'sources': sorted(entry['sources'])})
        result[field] = {'status': 'observed' if len(entries) == 1 else 'conflict' if entries else 'unknown',
                         'value': entries[0]['value'] if len(entries) == 1 else None, 'evidence': entries}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, nargs='+', help='JSON or JSONL frame captures (raw excludes FCS)')
    source.add_argument('--host', help='SMLIGHT host for a new passive capture')
    parser.add_argument('--port', type=int, default=6638)
    parser.add_argument('--channels', type=int, nargs='+', default=list(range(11, 27)))
    parser.add_argument('--seconds', type=float, default=20, help='Capture dwell time per channel')
    parser.add_argument('--beacon-request', action='store_true',
                        help='Optionally transmit a standard discovery request on each channel (requires Scapy)')
    parser.add_argument('--exclusive-radio', action='store_true',
                        help='Confirm that no collector or other program owns this bridge')
    parser.add_argument('--output', type=Path, required=True, help='New private report path; use captures/')
    args = parser.parse_args()
    os.umask(0o077)
    if args.host and not args.exclusive_radio:
        parser.error('--host requires --exclusive-radio; use a spare receiver or stop its current owner')
    if args.beacon_request and not args.host:
        parser.error('--beacon-request requires --host')
    if (not 1 <= args.port <= 65535 or not 0 < args.seconds <= 3600
            or any(channel not in range(11, 27) for channel in args.channels)):
        parser.error('Use a valid port, channels 11-26, and a dwell time in (0, 3600] seconds')
    try:
        if args.output.exists():
            raise FileExistsError('Choose a new output path; existing reports are not overwritten')
        if args.host:
            if args.beacon_request:
                try:
                    import scapy.layers.dot15d4  # Check optional dependency before touching the bridge.
                except ImportError as exc:
                    raise ValueError('--beacon-request requires pip install scapy==2.7.0') from exc
            from tools.smlight_capture import capture
            prefix = args.output.parent / (args.output.stem + '-frames')
            capture(argparse.Namespace(host=args.host, port=args.port, channels=args.channels,
                                       seconds=args.seconds, rounds=1,
                                       beacon_request=args.beacon_request, output=prefix))
            records = load_records(Path(f'{prefix}.json'))
        else:
            records = [record for path in args.input for record in load_records(path)]
        result = analyze(records)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Discovery failed: {exc}\n')
    print(f"Analyzed {len(records)} frames across {len(result['networks'])} networks")
    for net in result['networks']:
        print(f"Channel {net['channel']}, PAN {net['pan_id']}: {len(net['nodes'])} identified nodes")
        for candidate in net['inverter_candidates']:
            own = candidate['inverter_only']
            known = sum(own[field]['status'] == 'observed' for field in FIELDS)
            missing = ', '.join(field for field in FIELDS if own[field]['status'] != 'observed') or 'none'
            print(f"  Candidate {candidate['inverter_eui']}: {known}/5 settings from its own frames; unresolved: {missing}")
    print(f'Private report saved to {args.output}. Review identities before sharing.')


if __name__ == '__main__':
    main()
