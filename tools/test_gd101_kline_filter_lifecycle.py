"""Completed messages retain their arrival-time filter decision."""
import unittest
from unittest.mock import Mock
from gd101_receiver import PacketReceiver
from gd101_kline import KLineAssembler,KLineFilter,KLineByte

class FilterLifecycleTests(unittest.TestCase):
    def receiver(self,filters=(),capture=False):
        return PacketReceiver(Mock(),bytes(32),assembler=KLineAssembler(filters=filters),
                              capture_kline=capture)

    def message(self,r,data,start):
        wire=data+bytes([sum(data)&255])
        for i,b in enumerate(wire):r._accept_kline(start+i*.001,KLineByte(0,10+i,b))
        r._accept_kline(start+.030)

    def test_first_response_bypasses_filters_only_once(self):
        filters=(KLineFilter(1,b'',b''),KLineFilter(2,b'\xff',b'\x81'))
        r=self.receiver(filters,True)
        self.message(r,b'\x81',0)
        self.assertEqual(r.read_kline_initialization(0).data,b'\x81')
        self.assertEqual(r.kline_filters(),filters)
        self.message(r,b'\x81',.1)
        self.message(r,b'\x82',.2)
        self.assertEqual(r.read_message(0).data,b'\x82')
        with self.assertRaises(TimeoutError):r.read_message(0)

    def test_filter_changes_do_not_reclassify_completed_messages(self):
        r=self.receiver((KLineFilter(1,b'',b''),))
        self.message(r,b'\x81',0)
        r.set_kline_filters(())
        self.assertEqual(r.read_message(0).data,b'\x81')
        self.message(r,b'\x82',.1)
        r.set_kline_filters((KLineFilter(1,b'',b''),))
        with self.assertRaises(TimeoutError):r.read_message(0)

    def test_capture_timeout_restores_normal_filtering(self):
        r=self.receiver(capture=True)
        with self.assertRaises(TimeoutError):r.read_kline_initialization(0)
        self.message(r,b'\x81',0)
        with self.assertRaises(TimeoutError):r.read_message(0)

if __name__=='__main__':unittest.main()
