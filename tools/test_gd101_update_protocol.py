import unittest
from gd101_update_protocol import crc16,encode_command,read_mode_command,decode_reply


class UpdateProtocolTests(unittest.TestCase):
    def test_standard_crc_vector(self):
        self.assertEqual(crc16(b'123456789'),0xbb3d)
    def test_observed_mode_request(self):
        self.assertEqual(read_mode_command().hex(),'0a0023f0000000000000229200000000')
    def test_observed_mode_response(self):
        command,payload=decode_reply(bytes.fromhex('060023f00095cbbe'),expected_command=0xf023)
        self.assertEqual(command,0xf023)
        self.assertEqual(payload[0]&15,0)
    def test_observed_reset_response(self):
        command,payload=decode_reply(bytes.fromhex('080003f000000ebec590806631383602'),expected_command=0xf003)
        self.assertEqual(command,0xf003)
        self.assertEqual(payload,bytes.fromhex('00000ebe'))
    def test_reject_incomplete_reply(self):
        for raw in ['','060023','060023f00095cb','080003f000000ebec5908066313836']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):decode_reply(bytes.fromhex(raw))
    def test_reject_corruption_and_wrong_command(self):
        with self.assertRaisesRegex(ValueError,'CRC'):decode_reply(bytes.fromhex('060023f00195cbbe'))
        with self.assertRaisesRegex(ValueError,'command'):decode_reply(bytes.fromhex('060023f00095cbbe'),expected_command=0xf003)
    def test_command_limits(self):
        for command,data in [(0,b''),(0xf100,b''),(0xf002,bytes(4093))]:
            with self.subTest(command=command),self.assertRaises(ValueError):encode_command(command,data)
        result=encode_command(0xf002,bytes(512))
        self.assertEqual(len(result),520)
        self.assertEqual(int.from_bytes(result[:2],'little'),516)


if __name__=='__main__':unittest.main()
