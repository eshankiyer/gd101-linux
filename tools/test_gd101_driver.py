import unittest
from gd101_driver import read_frame, GD101Error
from gd101_native import encode_short, encode_extended, decode_frame


class FragmentedPort:
    def __init__(self, data):
        self.data = bytearray(data)

    def read(self, count):
        # A real serial read may deliver fewer bytes than requested.
        count = min(count, 2)
        result = bytes(self.data[:count])
        del self.data[:count]
        return result


class FrameReaderTests(unittest.TestCase):
    def test_fragmented_encrypted_frame_keeps_following_frame(self):
        first = encode_short(b'\x41\x01\0\0', bytes(range(32)))
        second = encode_short(b'\0\x03')
        port = FragmentedPort(first+second)
        self.assertEqual(read_frame(port),first)
        self.assertEqual(read_frame(port),second)
        self.assertEqual(port.data,b'')

    def test_extended_fragmentation_and_coalescing(self):
        key = bytes(range(32))
        payload = bytes(range(256))*16
        first = encode_extended(payload,key)
        second = encode_short(b'\0\x03')
        port = FragmentedPort(first+second)
        self.assertEqual(decode_frame(read_frame(port),key),payload)
        self.assertEqual(read_frame(port),second)

    def test_extended_bad_length_and_crc(self):
        for header in (b'\x7f\0\0', b'\xff\xff\xff'):
            with self.assertRaises(GD101Error):
                read_frame(FragmentedPort(header))
        frame = bytearray(encode_extended(b'abc'))
        frame[-1] ^= 1
        with self.assertRaises(ValueError):
            decode_frame(frame)

    def test_timeout_and_invalid_header(self):
        with self.assertRaises(GD101Error):
            read_frame(FragmentedPort(b''),timeout=0)
        with self.assertRaises(GD101Error):
            read_frame(FragmentedPort(b'\x79'))


if __name__ == '__main__':
    unittest.main()
