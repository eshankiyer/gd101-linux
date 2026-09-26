import unittest
from unittest.mock import Mock
from gd101_receiver import PacketReceiver
from gd101_native import encode_short
from gd101_can import CANFilters
from gd101_isotp import ISOTPReassembler,FlowRoute
from gd101_kline import KLineAssembler,KLineFilter,KLineByte
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
from test_gd101_receiver import FakePort
from test_gd101_can_filter_arrival import FRAME,ACK
from test_gd101_isotp_route_update import record

class ReceiveOverflowTests(unittest.TestCase):
    def test_can_overflow_keeps_reader_and_command_ack_alive(self):
        key=bytes(32);port=FakePort();filters=CANFilters();filters.add(1,bytes(4),bytes(4))
        r=PacketReceiver(port,key,can_filters=filters,limit=1);r.start()
        try:
            port.inject(encode_short(FRAME,key)*3+encode_short(ACK,key))
            self.assertEqual(r.read_packet(1).payload,ACK)
            self.assertTrue(r.thread.is_alive());self.assertIsNone(r.error)
            self.assertEqual(r.indication_overflow_total,2)
            with self.assertRaises(BufferError):r.read_message(0)
            self.assertEqual(r.read_message(0).data,b'\x55')
            port.inject(encode_short(FRAME,key)+encode_short(ACK,key))
            self.assertEqual(r.read_packet(1).payload,ACK)
            self.assertEqual(r.read_message(0).data,b'\x55')
        finally:r.stop()

    def test_isotp_and_kline_completed_message_loss_is_recoverable(self):
        iso=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1),limit=1)
        kline=PacketReceiver(None,bytes(32),assembler=KLineAssembler(no_checksum=True,filters=[KLineFilter(1,b'',b'')]),limit=1)
        for index in range(3):
            iso._accept_isotp(record(0x7e8,b'\x01'+bytes([index])),index)
            kline._accept_kline(index,KLineByte(0,index,index))
            kline._accept_kline(index+.1)
        for r in (iso,kline):
            self.assertEqual(r.indication_overflow_total,2)
            with self.assertRaises(BufferError):r.read_message(0)
            self.assertEqual(r.read_message(0).data,b'\0')
            self.assertIsNone(r.error)

    def test_all_protocols_return_buffer_overflow_without_output_mutation(self):
        for protocol in (3,4,5,6):
            with self.subTest(protocol=protocol):
                p=LifecycleProvider();p.channel=1;p.protocol=protocol;p.session=Mock()
                p.session.read_can_message.side_effect=BufferError('lost')
                p.session.read_message.side_effect=BufferError('lost')
                ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');m.ProtocolID=99
                count=ffi.new('uint32_t *',1)
                self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),18)
                self.assertEqual(count[0],0);self.assertEqual(m.ProtocolID,99)

if __name__=='__main__':unittest.main()
