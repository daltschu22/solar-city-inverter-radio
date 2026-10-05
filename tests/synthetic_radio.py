"""Entirely generated exchanges with fictional identities, times, and readings.

The frame layout matches the supported legacy protocol. No captured data is
included. The CRC implementation here is independent of the decoder under test.
"""

import struct


def crc(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return struct.pack("<H", value)


def records():
    result = []
    transactions = [
        (40360, [5000, 5000, 0x8000, 0x8000, 0xFFFF]),
        (40380, [1, 57920] + [0] * 15),
        (40360, [7500, 7500, 0x8000, 0x8000, 0xFFFF]),
    ]
    for sweep, (start, values) in enumerate(transactions, 1):
        request = struct.pack(">BBHH", 1, 3, start, len(values))
        response = bytes([1, 3, len(values) * 2]) + struct.pack(">" + "H" * len(values), *values)
        for reply, data in enumerate((request, response)):
            source, destination = (0x2345, 0) if reply else (0, 0x2345)
            source_eui, destination_eui = ((0x0200000000000002, 0x0200000000000001)
                                         if reply else (0x0200000000000001, 0x0200000000000002))
            mac = struct.pack("<HBHHH", 0x8861, sweep * 2 + reply, 0x1234, destination, source)
            nwk = struct.pack("<HHHBBQQ", 0x1808, destination, source, 30,
                              sweep * 2 + reply, destination_eui, source_eui)
            aps = struct.pack("<BBHHBB", 0x40, 0xE8, 0x11, 0xC105, 0xE8, sweep * 2 + reply)
            result.append({"raw": (mac + nwk + aps + data + crc(data)).hex(),
                           "type": "data", "channel": 14, "rssi": -60,
                           "capture_sweep": sweep, "timestamp": sweep * 1000 + reply,
                           "last_seen": 1700000000 + sweep * 60 + reply})
    return result
