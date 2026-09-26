import struct
import unittest
from gd101_receiver import PacketReceiver
from gd101_isotp import ISOTPReassembler,FlowRoute,ISOTPStartOfMessage
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

def packet(data,stamp=1):
    return b'\x43\x01\0'+struct.pack('<I',stamp)+bytes(2)+struct.pack('<I',0x7e8)+bytes([len(data)])+data

class StartIndicationTests(unittest.TestCase):
    def receiver(self):return PacketReceiver(None,None,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1),isotp_indications=True)
    def test_start_precedes_completion(self):
        r=self.receiver();r._accept_isotp(packet(b'\x10\x0aabcdef'),1)
        self.assertEqual(r.read_message(0),ISOTPStartOfMessage(0x7e8,False,None,1))
        r._accept_isotp(packet(b'\x21ghij\0\0\0',2),1.01)
        self.assertEqual(r.read_message(0).data,b'abcdefghij')
        with self.assertRaises(TimeoutError):r.read_message(0)

    def test_invalid_first_frame_has_no_start(self):
        r=self.receiver();r._accept_isotp(packet(b'\x10\x0aabc'),1)
        with self.assertRaises(TimeoutError):r.read_message(0)
        self.assertEqual(r.isotp_error_count,1)

    def test_linux_extended_header(self):
        from unittest.mock import Mock
        p=LifecycleProvider();p.channel=1;p.protocol=6;p.session=Mock()
        p.session.read_message.return_value=ISOTPStartOfMessage(0x18daf110,True,0xaa,44)
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),0)
        self.assertEqual((m.RxStatus,m.DataSize,m.Timestamp),(0x182,5,44))

if __name__=='__main__':unittest.main()
