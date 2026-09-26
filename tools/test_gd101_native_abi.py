"""Opt-in compiled Linux ABI checks; never opens hardware."""
import ctypes as C
import os
from pathlib import Path
import subprocess
import sys
import unittest
from hds_j2534 import Message

@unittest.skipUnless(os.environ.get('GD101_RUN_ABI_TESTS')=='1','Opt-in compiled native ABI checks')
class NativeABITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable,str(root/'driver/build.py')],check=True,
                       capture_output=True,text=True,timeout=60)
        cls.dll=C.CDLL(str(root/'driver/build/libgd101.so'))
        cls.dll.PassThruWriteMsgs.argtypes=[C.c_uint32,C.POINTER(Message),C.POINTER(C.c_uint32),C.c_uint32]
        cls.dll.PassThruWriteMsgs.restype=C.c_uint32
        cls.dll.PassThruReadMsgs.argtypes=cls.dll.PassThruWriteMsgs.argtypes
        cls.dll.PassThruReadMsgs.restype=C.c_uint32
        cls.dll.PassThruGetLastError.argtypes=[C.c_void_p]
        cls.dll.PassThruGetLastError.restype=C.c_uint32
        cls.dll.PassThruStartMsgFilter.argtypes=[C.c_uint32,C.c_uint32,C.POINTER(Message),C.POINTER(Message),C.POINTER(Message),C.POINTER(C.c_uint32)]
        cls.dll.PassThruStartMsgFilter.restype=C.c_uint32
        cls.dll.PassThruReadVersion.argtypes=[C.c_uint32,C.c_void_p,C.c_void_p,C.c_void_p]
        cls.dll.PassThruReadVersion.restype=C.c_uint32

    def test_read_version_export_and_invalid_handle(self):
        buffers=[(C.c_ubyte*82)(*([0xaa]*82)) for _ in range(3)]
        self.assertEqual(self.dll.PassThruReadVersion(999,*[C.byref(b,1) for b in buffers]),26)
        self.assertTrue(all(bytes(b)==b'\xaa'*82 for b in buffers))
        self.assertEqual(self.dll.PassThruReadVersion(999,None,None,None),4)

    def test_null_count_and_invalid_channel(self):
        message=Message(ProtocolID=5,DataSize=4)
        self.assertEqual(self.dll.PassThruWriteMsgs(999,C.byref(message),None,100),4)
        count=C.c_uint32(1)
        self.assertEqual(self.dll.PassThruWriteMsgs(999,C.byref(message),C.byref(count),100),2)
        self.assertEqual(count.value,0)
        self.assertEqual(C.sizeof(Message),4152)

    def test_error_text_preserves_canary(self):
        self.dll.PassThruWriteMsgs(0,None,None,100)
        buffer=(C.c_ubyte*82)(*([0xaa]*82))
        self.assertEqual(self.dll.PassThruGetLastError(C.byref(buffer,1)),0)
        self.assertEqual((buffer[0],buffer[81]),(0xaa,0xaa))
        self.assertIn(b'null',bytes(buffer[1:81]).split(b'\0')[0].lower())

    def test_filter_null_mask_clears_output(self):
        output=C.c_uint32(123)
        self.assertEqual(self.dll.PassThruStartMsgFilter(0,1,None,None,None,C.byref(output)),4)
        self.assertEqual(output.value,0)

    def test_read_invalid_channel_does_not_modify_message(self):
        message=Message(ProtocolID=0xaabbccdd,DataSize=0x11223344)
        count=C.c_uint32(1)
        self.assertEqual(self.dll.PassThruReadMsgs(999,C.byref(message),C.byref(count),0),2)
        self.assertEqual(count.value,0)
        self.assertEqual((message.ProtocolID,message.DataSize),(0xaabbccdd,0x11223344))

if __name__=='__main__':unittest.main()
