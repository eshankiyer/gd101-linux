"""FIVE_BAUD_INIT byte-array boundaries; never opens hardware."""
import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class FiveBaudTests(unittest.TestCase):
    def setUp(self):
        self.ffi=Exports();self.p=LifecycleProvider()
        self.p.channel=1;self.p.protocol=3;self.p.connect_flags=0x200
        self.p.session=Mock();self.p.session.initialize_kline.return_value=b'\x08\x08'
        install(self.ffi,self.p);self.ioctl=self.ffi.functions['PassThruIoctl']
        self.input_bytes=self.ffi.new('unsigned char[]',b'\x33')
        self.output_bytes=self.ffi.new('unsigned char[]',b'\xaa\xbb\xcc\xdd')
        self.source=self.ffi.new('GD101_BYTE_ARRAY *',{'NumOfBytes':1,'BytePtr':self.input_bytes})
        self.target=self.ffi.new('GD101_BYTE_ARRAY *',{'NumOfBytes':4,'BytePtr':self.output_bytes})

    def test_keywords_and_canary(self):
        self.assertEqual(self.ioctl(1,4,self.source,self.target),0)
        self.assertEqual(self.target.NumOfBytes,2)
        self.assertEqual(bytes(self.ffi.buffer(self.output_bytes,4)),b'\x08\x08\xcc\xdd')
        self.assertEqual(self.input_bytes[0],0x33)
        self.p.session.initialize_kline.assert_called_once_with('five_baud',b'\x33',protocol=3,no_checksum=True)

    def test_invalid_buffers_do_not_initialize(self):
        self.assertEqual(self.ioctl(1,4,self.ffi.NULL,self.target),4)
        self.assertEqual(self.ioctl(1,4,self.source,self.ffi.NULL),4)
        for field in (self.source,self.target):
            saved=field.BytePtr;field.BytePtr=self.ffi.NULL
            self.assertEqual(self.ioctl(1,4,self.source,self.target),4)
            field.BytePtr=saved
        self.source.NumOfBytes=2
        self.assertEqual(self.ioctl(1,4,self.source,self.target),10)
        self.source.NumOfBytes=1;self.target.NumOfBytes=1
        self.assertEqual(self.ioctl(1,4,self.source,self.target),18)
        self.p.session.initialize_kline.assert_not_called()

    def test_timeout_and_malformed_result_preserve_output(self):
        self.p.session.initialize_kline.side_effect=TimeoutError('No keyword reply')
        self.assertEqual(self.ioctl(1,4,self.source,self.target),9)
        self.p.session.initialize_kline.side_effect=None
        self.p.session.initialize_kline.return_value=b'\x08'
        self.assertEqual(self.ioctl(1,4,self.source,self.target),7)
        self.assertEqual(self.target.NumOfBytes,4)
        self.assertEqual(bytes(self.ffi.buffer(self.output_bytes,4)),b'\xaa\xbb\xcc\xdd')

    def test_channel_and_protocol_validation(self):
        self.assertEqual(self.ioctl(2,4,self.source,self.target),2)
        self.p.protocol=5
        self.assertEqual(self.ioctl(1,4,self.source,self.target),1)
        self.p.session.initialize_kline.assert_not_called()

if __name__=='__main__':unittest.main()
