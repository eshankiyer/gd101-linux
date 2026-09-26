import hashlib
from pathlib import Path
import unittest
from gd101_native import crc8, encode_short, decode_short, ShortFrameStream, prepare_serial


class NativeCodecTests(unittest.TestCase):
    def test_fragmentation_and_coalescing(self):
        payloads = [b'\x40\x01', bytes(range(240)), b'\x40\x03']
        wire = b''.join(encode_short(p) for p in payloads)
        for cut in range(len(wire) + 1):
            decoder = ShortFrameStream()
            self.assertEqual(decoder.feed(wire[:cut]) + decoder.feed(wire[cut:]), payloads)
        decoder = ShortFrameStream()
        self.assertEqual([p for byte in wire for p in decoder.feed(bytes([byte]))], payloads)

    def test_corruption_latches_failure(self):
        for wire in (b'\xff', b'\0', b'\x01\0\0\xff'):
            decoder = ShortFrameStream()
            with self.assertRaises(ValueError): decoder.feed(wire)
            with self.assertRaises(ValueError): decoder.feed(encode_short(b'\x40\x01'))
            self.assertEqual(decoder.pending, b'')

    def test_preparation_does_not_open_a_device(self):
        port = prepare_serial('/nonexistent/offline-test-port')
        self.assertFalse(port.is_open)
        self.assertEqual(port.baudrate, 2534)
        self.assertTrue(port.dtr)
        self.assertFalse(port.rts)

    def test_crc_matches_every_entry_of_recovered_driver_table(self):
        path = Path(__file__).resolve().parents[1] / 'research/hds/gd101-driver/unpacked-code.bin'
        if not path.exists():
            self.skipTest('Optional vendor reference binary is not distributed')
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         '91f8b6cc82ff4e063b51b96a6b6d5c0ff4d41f4b2720e4cf7e18acf9e6dfb22d')
        offset = 0x7ab7d998 - 0x7ab51000
        self.assertEqual(bytes(crc8(bytes([v])) for v in range(256)),
                         data[offset:offset+256])

    def test_crc_standard_vector(self):
        self.assertEqual(crc8(b'123456789'), 0xf4)

    def test_boundaries_and_corruption(self):
        for payload in [b'\0\x03', bytes(range(240))]:
            frame = encode_short(payload)
            self.assertEqual(frame[0], len(payload)//2)
            self.assertEqual(decode_short(frame), payload)
            with self.assertRaises(ValueError):
                decode_short(frame[:-1]+bytes([frame[-1]^1]))
        for payload in [b'', b'\0', bytes(241), bytes(242)]:
            with self.assertRaises(ValueError): encode_short(payload)
        for frame in [b'', b'\0\0\0\0', b'\x81\0\0\0', b'\x02\0\0\0']:
            with self.assertRaises(ValueError): decode_short(frame)


if __name__ == '__main__':
    unittest.main()
