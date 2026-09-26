import unittest
from gd101_receiver import PacketReceiver
from gd101_kline import KLineAssembler,KLineTransmitEcho
from gd101_abi import LifecycleProvider
from test_gd101_transmit import session
from unittest.mock import Mock

class KLineLoopbackTests(unittest.TestCase):
    def test_native_short_and_byte_mode_echo_once_after_final_ack(self):
        for size in (2,1001,4097):
            for no_checksum in (False,True):
                d=session([])
                d.receiver=PacketReceiver(None,bytes(32),assembler=KLineAssembler(no_checksum=no_checksum))
                def reply(timeout):
                    self.assertEqual(len(d.receiver.messages),0)
                    packet=d.send_packet.call_args.args[0]
                    return bytes((0x43,2 if size>1000 else 11,0,packet[3] if size>1000 else packet[2],0,123,0,0,0,0))
                d.receive_packet=Mock(side_effect=reply)
                d.configure_kline_runtime(loopback=1)
                wire=bytes(i%256 for i in range(size))
                d.write_kline_wire(wire,timing=0,timeout=10)
                echo=d.receiver.read_message(0)
                self.assertEqual(echo,KLineTransmitEcho(wire if no_checksum else wire[:-1],123))
                with self.assertRaises(TimeoutError):d.receiver.read_message(0)

    def test_failed_send_has_no_echo(self):
        d=session([TimeoutError('missing completion')])
        d.receiver=PacketReceiver(None,bytes(32),assembler=KLineAssembler())
        d.configure_kline_runtime(loopback=1)
        with self.assertRaises(TimeoutError):d.write_kline_wire(b'abc')
        self.assertFalse(d.receiver.messages)

    def test_overflow_does_not_turn_success_into_retry(self):
        d=session([bytes.fromhex('430b0000007b00000000')])
        d.receiver=PacketReceiver(None,bytes(32),assembler=KLineAssembler(),limit=1)
        d.receiver.publish_kline_echo(b'old!',1)
        d.configure_kline_runtime(loopback=1)
        self.assertEqual(d.write_kline_wire(b'abc').timestamp,123)
        self.assertFalse(d.tx_uncertain);d.send_packet.assert_called_once()
        with self.assertRaises(BufferError):d.receiver.read_message(0)
        self.assertEqual(d.receiver.read_message(0),KLineTransmitEcho(b'old',1))

    def test_failed_runtime_change_and_reconnect_defaults(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,4,0,10400)
        p.session.configure_kline_runtime.side_effect=RuntimeError('failed runtime operation')
        with self.assertRaises(RuntimeError):p.configure_timing(c,pairs=[(3,1)])
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))
        p.session.configure_kline_runtime.side_effect=None
        self.assertEqual(p.configure_timing(c,pairs=[(3,1)])[0],0)
        self.assertEqual(p.disconnect(c),0)
        _,new=p.connect(1,3,0,10400)
        self.assertEqual(p.configure_timing(new,parameters=[3]),(0,[0]))
        d=session([]);d.can_open=False;d.kline_open=False;d.kline_loopback=255
        d.exchange=Mock(return_value=bytes.fromhex('41010000'))
        d.connect_kline()
        self.assertEqual(d.kline_loopback,0)

    def test_final_loopback_byte_validation_precedes_all_runtime_changes(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,4,0,10400)
        for value in (2,255,258,0xffffffff):
            self.assertEqual(p.configure_timing(c,pairs=[(12,20),(3,value)])[0],10)
            p.session.configure_kline_runtime.assert_not_called()
            self.assertEqual(p.configure_timing(c,parameters=[12,3]),(0,[10,0]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,2),(3,1)])[0],0)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,0),(3,2)])[0],10)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
        d=session([])
        d.configure_kline_runtime(loopback=1,timing=5)
        with self.assertRaises(ValueError):d.configure_kline_runtime(loopback=2,timing=10)
        self.assertEqual(d.kline_loopback,1);self.assertEqual(d._periodic_kline_timing,5)

    def test_configuration_byte_readback_and_echo_fields(self):
        for protocol in (3,4):
            p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
            _,c=p.connect(1,protocol,0,10400)
            self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))
            self.assertEqual(p.configure_timing(c,pairs=[(3,0x101)])[0],0)
            p.session.configure_kline_runtime.assert_called_with(loopback=1)
            self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
            self.assertEqual(p.received_fields(KLineTransmitEcho(b'abc',123)),(b'abc',1,123))
            self.assertEqual(p.configure_timing(c,pairs=[(3,256)])[0],0)
            self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))

if __name__=='__main__':unittest.main()
