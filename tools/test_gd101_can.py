import unittest
from gd101_can import decode_can_receive_record,build_can_open_payload


class CANValidationTests(unittest.TestCase):
    def test_rejects_short_payload_and_bad_identifier(self):
        for record in (bytes(14), bytes(13)+b'\x09'+bytes(9),
                       bytes(9)+b'\xff\xff\xff\xff'+b'\x01\0',
                       bytes(13)+b'\x08\0'):
            with self.assertRaises(ValueError): decode_can_receive_record(record)
        with self.assertRaises(ValueError): build_can_open_payload(bytes(21))


if __name__=='__main__': unittest.main()
