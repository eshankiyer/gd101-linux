import unittest
from unittest.mock import Mock
from gd101_can import CANFilters,CANTransmitEcho
from gd101_receiver import PacketReceiver
from gd101_abi import LifecycleProvider,install
from test_gd101_can_transmit import driver
from test_gd101_abi_deadline import Exports

class CANLoopbackTests(unittest.TestCase):
    def test_echo_after_ack_and_bypasses_receive_filters(self):
        for extended in (False,True):
            for data in (b'',b'12345678'):
                d=driver([bytes.fromhex('43000000000200000000')])
                d.receiver=PacketReceiver(None,bytes(32),can_filters=CANFilters())
                d.configure_can_loopback(True)
                identifier=0x18daf110 if extended else 0x7df
                d.write_can_frame(identifier,data,extended=extended)
                echo=d.receiver.read_message(0)
                self.assertEqual(echo,CANTransmitEcho(identifier,data,extended,2))
                p=LifecycleProvider();p.protocol=5
                self.assertEqual(p.received_fields(echo),(identifier.to_bytes(4,'big')+data,0x101 if extended else 1,2))

    def test_disabled_rejected_and_overflow(self):
        for enabled,rejected in ((False,False),(True,True)):
            d=driver([bytes.fromhex('43000000070200000000' if rejected else '43000000000200000000')])
            d.receiver=PacketReceiver(None,bytes(32),can_filters=CANFilters())
            d.configure_can_loopback(enabled)
            if rejected:
                with self.assertRaises(Exception):d.write_can_frame(1,b'')
            else:d.write_can_frame(1,b'')
            self.assertEqual(len(d.receiver.messages),0)
        d=driver([bytes.fromhex('43000000000200000000')])
        d.receiver=PacketReceiver(None,bytes(32),can_filters=CANFilters(),limit=1)
        d.receiver.publish_can_echo(CANTransmitEcho(1,b'',False,1))
        d.configure_can_loopback(True)
        self.assertEqual(d.write_can_frame(1,b'').timestamp,2)
        d.send_packet.assert_called_once()
        with self.assertRaises(BufferError):d.receiver.read_message(0)
        self.assertEqual(d.receiver.read_message(0).timestamp,1)

    def test_background_writers_publish_after_ack(self):
        import test_gd101_periodic_native as helpers
        for periodic in (False,True):
            with self.subTest(periodic=periodic):
                d=helpers.NativePeriodicTests().session()
                d.receiver=PacketReceiver(None,bytes(32),can_filters=CANFilters())
                d.receive_packet.side_effect=None
                d.receive_packet.return_value=bytes.fromhex('43000000000200000000')
                d.configure_can_loopback(True)
                try:
                    if periodic:handle=d.start_periodic_can(0x7df,b'abc',1000)
                    else:d.queue_can(0x7df,b'abc')
                    self.assertEqual(d.receiver.read_message(1),CANTransmitEcho(0x7df,b'abc',False,2))
                    if periodic:d.stop_periodic(handle)
                    else:
                        d.clear_tx_queue()
                        self.assertEqual([status for _,status,_ in d.queued_results()],['completed'])
                    self.assertEqual(d.send_packet.call_count,1)
                finally:d.disconnect_can()

    def test_configuration_and_linux_echo(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,5,0,500000)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[500000,0]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,0xffffffff)])[0],0)
        p.session.configure_can_loopback.assert_called_once_with(True)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,0),(99,0)])[0],1)
        self.assertTrue(p.can_loopback)
        p.session.read_can_message.return_value=CANTransmitEcho(0x18daf110,b'abc',True,12)
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](c,m,count,0),0)
        self.assertEqual((m.RxStatus,m.DataSize,m.Timestamp),(0x101,7,12))
        self.assertEqual(bytes(ffi.buffer(m.Data,7)),bytes.fromhex('18daf110')+b'abc')
        p.disconnect(c);p.connect(1,5,0,250000)
        self.assertFalse(p.can_loopback)

if __name__=='__main__':unittest.main()
