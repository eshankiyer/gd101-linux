"""Offline inspection of GD101 updater metadata and firmware containers.

No hardware or network access. CRC32 detects payload corruption, not authenticity
or compatibility. The original uranus_do rounds length down to 512 bytes; this
inspector rejects unaligned input rather than silently ignoring its tail.
"""
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import struct
import zlib


def parse_metadata(data):
    data = bytes(data)
    if not data:
        raise ValueError('Empty updater metadata')
    count, offset, fields = data[0], 1, {}
    def field():
        nonlocal offset
        if offset >= len(data):
            raise ValueError('Truncated metadata length')
        size = data[offset]
        offset += 1
        if not size or size > len(data) - offset:
            raise ValueError('Empty or truncated metadata field')
        raw = data[offset:offset + size]
        offset += size
        try:
            return raw.decode('ascii')
        except UnicodeDecodeError as exc:
            raise ValueError('Metadata field is not ASCII') from exc
    for _ in range(count):
        key, value = field(), field()
        if key in fields:
            raise ValueError('Duplicate metadata field')
        fields[key] = value
    if offset != len(data):
        raise ValueError('Trailing metadata bytes')
    if set(fields) != {'name', 'version', 'hash'}:
        raise ValueError('Unexpected updater metadata fields')
    return fields


@dataclass(frozen=True)
class FirmwareInspection:
    size: int
    blocks: int
    version: str
    payload_crc32: str
    sha256: str
    header_payload_offset: int
    header_load_address: int
    updater_base_address: int
    authenticity_verified: bool = False
    hardware_compatibility_verified: bool = False


def inspect_firmware(data, metadata=None):
    data = bytes(data)
    if len(data) < 1024 or len(data) % 512:
        raise ValueError('Expected complete 512-byte header and aligned payload')
    expected = struct.unpack_from('<I', data, 0x14)[0]
    actual = zlib.crc32(data[512:])
    if actual != expected:
        raise ValueError('Firmware payload CRC32 mismatch')
    minor, major = data[8:10]
    version = f'{major}.{minor}'
    offset = struct.unpack_from('<I', data, 0x0c)[0]
    load = struct.unpack_from('<I', data, 0x1c)[0]
    base = 0x08008000 if load == 0xffffffff else (load - offset) & 0xffffffff
    if metadata is not None:
        fields = parse_metadata(metadata)
        if fields['name'] != 'nano_fw' or fields['version'] != version:
            raise ValueError('Firmware and metadata identification disagree')
    return FirmwareInspection(len(data), len(data)//512, version, f'{actual:08x}',
                              hashlib.sha256(data).hexdigest(), offset, load, base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--metadata', type=Path)
    args = parser.parse_args()
    metadata = args.metadata.read_bytes() if args.metadata else None
    result = inspect_firmware(args.firmware.read_bytes(), metadata)
    print(json.dumps(asdict(result), indent=2))


if __name__ == '__main__':
    main()
