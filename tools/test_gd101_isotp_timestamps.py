import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from gd101_isotp import ISOTPStartOfMessage,ISOTPMessage
from test_gd101_abi_deadline import Exports

class ISOTPTimestampTests(unittest.TestCase):
    def test_start_and_completion_preserve_native_timestamps_across_wrap(self):
        p=LifecycleProvider();p.channel=1;p.protocol=6;p.session=Mock()
        p.session.read_message.side_effect=[
            ISOTPStartOfMessage(0x7e8,False,None,0xfffffffe),
            ISOTPMessage(0x7e8,b'abcdefghij',False,None,0xfffffffe,2,0)]
        ffi=Exports();install(ffi,p);messages=ffi.new('GD101_MESSAGE[]',2)
        count=ffi.new('uint32_t *',2)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,messages,count,0),0)
        self.assertEqual(count[0],2)
        self.assertEqual((messages[0].RxStatus,messages[0].Timestamp),(2,0xfffffffe))
        self.assertEqual((messages[1].RxStatus,messages[1].Timestamp),(0,2))

if __name__=='__main__':unittest.main()
