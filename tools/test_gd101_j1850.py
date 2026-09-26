import unittest
from gd101_j1850 import build_open,build_close,build_transmit,decode_receive


class J1850PayloadTests(unittest.TestCase):
    def test_receive_rejects_truncated_lengths(self):
        for protocol in (1,2):
            prefix=bytes([0x43,9 if protocol==1 else 8])+bytes(4)
            for raw in (prefix,prefix+b'\0'+bytes(4),prefix+b'\xff'+bytes(3)):
                with self.assertRaises(ValueError):decode_receive(protocol,raw)
        with self.assertRaises(ValueError):decode_receive(1,b'\x43\x08'+bytes(20))

    def test_pwm_extra_data_skips_message_crc(self):
        raw=b'\x43\x08'+bytes([1,0,0,0])+b'\x42abc\xeeXY'
        message=decode_receive(2,raw)
        self.assertEqual(message.data,b'abcXY')
        self.assertEqual(message.extra_data_index,3)
        self.assertEqual(message.timestamp,1)

    def test_rejects_bad_inputs(self):
        for protocol in (0,3,6):
            with self.assertRaises(ValueError):build_close(protocol)
        for size in (0,7,9):
            with self.assertRaises(ValueError):build_open(1,bytes(size))
        for protocol,size in ((1,17),(2,256)):
            with self.assertRaises(ValueError):build_transmit(protocol,bytes(size))
        for counter in (-1,0x100000000,1.5):
            with self.assertRaises(ValueError):build_transmit(1,b'abc',counter=counter)

    def test_message_input_is_snapshotted(self):
        data=bytearray(b'abc');packet=build_transmit(1,data,counter=240)
        data[:]=b'xyz'
        self.assertEqual(packet,b'\x03\x05\0\x03abc')


if __name__=='__main__':unittest.main()
