import unittest
from gd101_isotp import ISOTPTransmitDone,ISOTPReassembler,make_flow_entry
from gd101_receiver import PacketReceiver
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
import test_gd101_periodic_isotp as helpers

class IndicationTests(unittest.TestCase):
    def test_native_success_publishes_completion(self):
        d=helpers.PeriodicISOTests().session()
        d.receiver=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({},timeout=1))
        d.receive_packet.side_effect=None;d.receive_packet.return_value=bytes.fromhex('43000000007856341200')
        self.assertEqual(d.write_isotp_message(0,b'\x3e\x80'),0x12345678)
        self.assertEqual(d.receiver.read_message(0),ISOTPTransmitDone(0x7e0,False,None,0x12345678))
        d.receive_packet.return_value=bytes.fromhex('43000001077856341200')
        with self.assertRaises(Exception):d.write_isotp_message(0,b'\x3e\x80')
        self.assertEqual(len(d.receiver.messages),0)

    def test_linux_completion_without_payload(self):
        from unittest.mock import Mock
        p=LifecycleProvider();p.channel=1;p.protocol=6;p.session=Mock()
        p.session.read_message.return_value=ISOTPTransmitDone(0x18da10f1,True,0xbb,123)
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),0)
        self.assertEqual((m.RxStatus,m.DataSize,m.Timestamp),(0x189,5,123))
        self.assertEqual(bytes(ffi.buffer(m.Data,5)),bytes.fromhex('18da10f1bb'))

if __name__=='__main__':unittest.main()
