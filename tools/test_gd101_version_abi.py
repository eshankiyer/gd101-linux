import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class VersionTests(unittest.TestCase):
    def test_version_output_bounded_and_terminated(self):
        ffi=Exports();p=LifecycleProvider();p.device=1;p.session=Mock()
        p.session.read_version.return_value=bytes.fromhex('4003000e014d54303030343138360217531e50424758383930300b4511f3')
        install(ffi,p);buffers=[ffi.new('char[]',b'\xaa'*82) for _ in range(3)]
        outputs=[b+1 for b in buffers]
        self.assertEqual(ffi.functions['PassThruReadVersion'](1,*outputs),0)
        self.assertEqual(ffi.string(outputs[0]),b'SN:MT000418,FW:1.14')
        self.assertEqual(ffi.string(outputs[1]),b'GD101 native experimental 0.1')
        self.assertEqual(ffi.string(outputs[2]),b'04.04')
        for b in buffers:
            self.assertEqual((b[0],b[81]),(b'\xaa',b'\xaa'))
            self.assertEqual(b[80],b'\0')

    def test_null_output_does_not_contact_adapter(self):
        ffi=Exports();p=LifecycleProvider();p.device=1;p.session=Mock();install(ffi,p)
        buffer=ffi.new('char[80]')
        for index in range(3):
            args=[buffer,buffer,buffer];args[index]=ffi.NULL
            self.assertEqual(ffi.functions['PassThruReadVersion'](1,*args),4)
        p.session.read_version.assert_not_called()

    def test_malformed_reply_leaves_output_unchanged(self):
        ffi=Exports();p=LifecycleProvider();p.device=1;p.session=Mock();install(ffi,p)
        for raw in (bytes(28),bytes(30),b'\x40\x03\0'+bytes(27)):
            p.session.read_version.return_value=raw
            outputs=[ffi.new('char[]',b'X'*80) for _ in range(3)]
            self.assertEqual(ffi.functions['PassThruReadVersion'](1,*outputs),7)
            self.assertTrue(all(ffi.string(b)==b'X'*80 for b in outputs))

if __name__=='__main__':unittest.main()
