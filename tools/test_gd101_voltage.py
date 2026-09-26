import unittest
from unittest.mock import Mock
from collections import deque
from gd101_driver import GD101, GD101Error
from gd101_native import encode_short, decode_frame
from gd101_abi import LifecycleProvider, install
from test_gd101_abi_deadline import Exports
from test_gd101_receiver import FakePort

class VoltageTests(unittest.TestCase):
    def test_encrypted_native_read(self):
        key=bytes(range(32))
        class Port(FakePort):
            def flush(self):pass
            def write(self,wire):
                assert decode_frame(wire,key)==b'\x02\x00\x10\x00'
                self.inject(encode_short(b'\x42\x00\x00\x10'+(12345).to_bytes(4,'little'),key))
                return len(wire)
        d=object.__new__(GD101);d.port=Port();d.tx_key=d.rx_key=key
        d.receiver=None;d.events=deque();d.record={'exchanges':[]}
        self.assertEqual(d.read_battery_voltage(),12345)

    def test_truncation(self):
        d=object.__new__(GD101);d.exchange=Mock(return_value=b'\x42\0\0\x10\x01\x02')
        with self.assertRaises(GD101Error):d.read_battery_voltage()

    def test_abi_bounds_and_failure_preservation(self):
        ffi=Exports();p=LifecycleProvider();p.device=7;p.session=Mock()
        p.session.read_battery_voltage.return_value=12345
        install(ffi,p);ioctl=ffi.functions['PassThruIoctl']
        result=ffi.new('uint32_t[3]',[0x12345678,99,0x87654321])
        self.assertEqual(ioctl(7,3,ffi.NULL,result+1),0)
        self.assertEqual(list(result),[0x12345678,12345,0x87654321])
        self.assertEqual(ioctl(7,3,ffi.NULL,ffi.NULL),4)
        self.assertEqual(ioctl(8,3,ffi.NULL,result+1),26)
        p.session.read_battery_voltage.side_effect=TimeoutError()
        self.assertEqual(ioctl(7,3,ffi.NULL,result+1),9)
        self.assertEqual(result[1],12345)
        p.session.read_battery_voltage.side_effect=RuntimeError('bad packet')
        self.assertEqual(ioctl(7,3,ffi.NULL,result+1),7)
        self.assertEqual(result[1],12345)

if __name__=='__main__':unittest.main()
