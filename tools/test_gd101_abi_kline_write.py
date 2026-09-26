"""Protocol boundary tests without hardware or ECU initialization."""
import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider

class KlineWriteTests(unittest.TestCase):
    def provider(self,protocol=4,flags=0):
        p=LifecycleProvider();p.session=Mock();p.channel=42;p.protocol=protocol;p.connect_flags=flags
        return p

    def test_checksum_modes_and_input_preserved(self):
        for protocol in (3,4):
            for flags in (0,0x200):
                p=self.provider(protocol,flags);data=bytearray.fromhex('8146f081')
                self.assertEqual(p.write_message(42,protocol,0,data,123),0)
                expected=bytes(data)+(b'' if flags else b'\x38')
                p.session.write_kline_ordered.assert_called_once_with(expected,timeout=.123)
                self.assertEqual(data,bytes.fromhex('8146f081'))

    def test_validation_does_not_transmit(self):
        p=self.provider()
        for channel,protocol,flags,data,timeout,status in (
            (41,4,0,b'a',100,2),(42,5,0,b'a',100,21),
            (42,4,0x200,b'a',100,6),(42,4,0,b'',100,10),
            (42,4,0,bytes(4097),100,10),
            (42,4,0,b'a',25001,1)):
            self.assertEqual(p.write_message(channel,protocol,flags,data,timeout),status)
        p.session.write_kline_ordered.assert_not_called()

    def test_wire_limit_includes_checksum(self):
        for flags,size in ((0,999),(0x200,1000),(0,4096),(0x200,4096)):
            p=self.provider(flags=flags)
            self.assertEqual(p.write_message(42,4,0,bytes(size),100),0)
            self.assertEqual(len(p.session.write_kline_ordered.call_args.args[0]),size+(not flags))

    def test_timeout_not_retried(self):
        p=self.provider();p.session.write_kline_ordered.side_effect=TimeoutError('missing completion')
        self.assertEqual(p.write_message(42,4,0,b'a',100),9)
        p.session.write_kline_ordered.assert_called_once()

if __name__=='__main__':unittest.main()
