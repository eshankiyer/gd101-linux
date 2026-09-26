import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class AsyncABITests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.channel=1;p.protocol=5;p.session=Mock()
        return p

    def test_zero_timeout_partial_batch_and_clear(self):
        p=self.provider();ffi=Exports();install(ffi,p)
        messages=ffi.new('GD101_MESSAGE[]',2)
        for m in messages:
            m.ProtocolID=5;m.DataSize=5;ffi.memmove(m.Data,bytes.fromhex('000007df01'),5)
        p.session.queue_can.side_effect=[1,OverflowError('Full')]
        count=ffi.new('uint32_t *',2)
        self.assertEqual(ffi.functions['PassThruWriteMsgs'](1,messages,count,0),17)
        self.assertEqual(count[0],1)
        p.session.write_can_ordered.assert_not_called()
        self.assertEqual(ffi.functions['PassThruIoctl'](1,7,ffi.NULL,ffi.NULL),0)
        p.session.clear_tx_queue.assert_called_once()
        self.assertEqual(ffi.functions['PassThruIoctl'](99,7,ffi.NULL,ffi.NULL),2)
        self.assertEqual(p.session.clear_tx_queue.call_count,1)

    def test_invalid_queue_request_never_reaches_native(self):
        p=self.provider()
        for flags,data,status in [(1,bytes(4),6),(0,b'123',10),(0,bytes(13),10)]:
            self.assertEqual(p.write_message(1,5,flags,data,0),status)
        p.session.queue_can.assert_not_called()
        p.protocol=4
        self.assertEqual(p.write_message(1,4,0,b'\x81',0),0)
        p.session.queue_kline.assert_called_once_with(b'\x81',protocol=4,no_checksum=False,timing=5)

if __name__=='__main__':unittest.main()
