import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider
from gd101_driver import GD101
from gd101_receiver import PacketReceiver
from gd101_kline import KLineAssembler,KLineFilter,KLineByte

class ReceiveTimingTests(unittest.TestCase):
    def reader(self):
        return PacketReceiver(None,bytes(32),assembler=KLineAssembler(
            filters=(KLineFilter(1,b'',b''),),no_checksum=True))

    def test_gap_changes_actual_message_boundary_and_preserves_pending(self):
        r=self.reader();p=LifecycleProvider();p.channel=1;p.protocol=4;p.session=Mock(receiver=r)
        p.session.configure_kline_runtime=GD101.configure_kline_runtime.__get__(p.session)
        r._accept_kline(0,KLineByte(0,1,0x81))
        self.assertEqual(p.configure_timing(1,pairs=[(7,8)])[0],0)
        self.assertEqual(r.kline_gap(),.004)
        r._accept_kline(.005,KLineByte(0,2,0x82))
        self.assertEqual(r.read_message(0).data,b'\x81')
        r._accept_kline(.010)
        self.assertEqual(r.read_message(0).data,b'\x82')
        self.assertEqual(p.configure_timing(1,parameters=[7]),(0,[8]))

    def test_validation_does_not_change_reader_or_config(self):
        r=self.reader();p=LifecycleProvider();p.channel=1;p.protocol=4;p.session=Mock(receiver=r)
        p.session.configure_kline_runtime=GD101.configure_kline_runtime.__get__(p.session)
        self.assertEqual(p.configure_timing(1,pairs=[(7,8),(19,8000)])[0],10)
        self.assertEqual(r.kline_gap(),.020);self.assertEqual(p.timing_config,{})
        p.session.receiver=None
        self.assertEqual(p.configure_timing(1,pairs=[(7,8)])[0],7)
        self.assertEqual(p.timing_config,{})
        for gap in (-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):r.set_kline_gap(gap)

    def test_zero_gap_completes_on_next_expiry(self):
        r=self.reader();r.set_kline_gap(0)
        r._accept_kline(1,KLineByte(0,1,0x81));r._accept_kline(1)
        self.assertEqual(r.read_message(0).data,b'\x81')

if __name__=='__main__':unittest.main()
