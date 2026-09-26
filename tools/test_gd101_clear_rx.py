"""Receive clearing must not erase command replies, filters or partial input."""
from collections import deque
import unittest
from unittest.mock import Mock
from gd101_receiver import PacketReceiver,ReceivedPacket
from gd101_kline import KLineAssembler,KLineByte,KLineFilter
from gd101_driver import GD101
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class ClearReceiveTests(unittest.TestCase):
    def test_can_preserves_command_and_completion_packets(self):
        r=PacketReceiver(Mock(),bytes(32))
        packets=[ReceivedPacket(b'',p,0) for p in (b'\x43\x01\0',b'\x43\x00\0',b'\x41\0\0\0')]
        r.packets.extend(packets);r.decoder.pending.extend(b'\x84')
        r.clear_receive(5)
        self.assertEqual(list(r.packets),packets[1:])
        self.assertEqual(r.decoder.pending,b'\x84')

    def test_kline_preserves_partial_message_and_filters(self):
        filters=(KLineFilter(1,b'',b''),)
        r=PacketReceiver(Mock(),bytes(32),assembler=KLineAssembler(filters=filters))
        for i,b in enumerate(b'\x81\x81'):r._accept_kline(i*.001,KLineByte(0,i,b))
        r._accept_kline(.03)
        r._accept_kline(.1,KLineByte(0,10,0x82))
        r.clear_receive(4)
        with self.assertRaises(TimeoutError):r.read_message(0)
        self.assertEqual(r.kline_filters(),filters)
        r._accept_kline(.101,KLineByte(0,11,0x82));r._accept_kline(.13)
        self.assertEqual(r.read_message(0).data,b'\x82')

    def test_native_deferred_events_preserve_completions(self):
        d=object.__new__(GD101);d.can_open=True;d.kline_open=False;d.receiver=Mock()
        d.events=deque((b'\x43\x01\0',b'\x43\x00\0'))
        d.tx_uncertain=True;d.clear_rx_buffer()
        self.assertEqual(list(d.events),[b'\x43\x00\0'])
        d.receiver.clear_receive.assert_called_once_with(5)
        self.assertTrue(d.tx_uncertain)

    def test_linux_ioctl_validates_handle(self):
        ffi=Exports();p=LifecycleProvider();p.channel=9;p.session=Mock();install(ffi,p)
        self.assertEqual(ffi.functions['PassThruIoctl'](8,8,ffi.NULL,ffi.NULL),2)
        p.session.clear_rx_buffer.assert_not_called()
        self.assertEqual(ffi.functions['PassThruIoctl'](9,8,ffi.NULL,ffi.NULL),0)
        p.session.clear_rx_buffer.assert_called_once_with()

if __name__=='__main__':unittest.main()
