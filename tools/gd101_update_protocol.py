"""Offline GD101 firmware-updater framing. Does not open serial hardware.

Matches the uranus_p1.dll short-address form with Curanus identity -1.
The normal J2534 transport uses a different framing protocol.
"""
import struct


def crc16(data):
    value = 0
    for byte in bytes(data):
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value


def encode_command(command, data):
    if not isinstance(command, int) or not 0xf000 <= command <= 0xf0ff:
        raise ValueError('Expected updater command identifier')
    data = bytes(data)
    size = 4 + len(data)
    if size > 4096:
        raise ValueError('Updater frame exceeds bounded buffer')
    frame = struct.pack('<HH', size, command) + data
    frame += struct.pack('>H', crc16(frame))
    return frame.ljust((len(frame) + 7) & ~7, b'\0')


def read_mode_command():
    return encode_command(0xf023, bytes(6))


def decode_reply(frame, *, expected_command=None):
    frame = bytes(frame)
    if len(frame) < 8:
        raise ValueError('Truncated updater reply')
    size, command = struct.unpack_from('<HH', frame)
    if not 5 <= size <= 4096 or len(frame) != ((size + 9) & ~7):
        raise ValueError('Invalid updater reply size or alignment')
    # Live mode-query reply verifies that reply CRC is little-endian, unlike TX.
    expected_crc = struct.unpack_from('<H', frame, size)[0]
    if crc16(frame[:size]) != expected_crc:
        raise ValueError('Updater reply CRC mismatch')
    if expected_command is not None and command != expected_command:
        raise ValueError('Unexpected updater reply command')
    return command, frame[4:size]
