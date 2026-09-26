import unittest
from gd101_abi import install
from test_gd101_abi_deadline import Exports
import test_gd101_periodic_iso_abi as helpers

class QueuedISOABITests(unittest.TestCase):
    def test_extended_address_zero_timeout(self):
        p=helpers.PeriodicISOABITests().provider();ffi=Exports();install(ffi,p)
        m=ffi.new('GD101_MESSAGE *');m.ProtocolID=6;m.TxFlags=0x180
        payload=bytes(range(256))*2;data=bytes.fromhex('18da10f1bb')+payload
        m.DataSize=len(data);ffi.memmove(m.Data,data,len(data))
        count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruWriteMsgs'](p.channel,m,count,0),0)
        self.assertEqual(count[0],1);p.session.queue_isotp.assert_called_once_with(0,payload)
        p.session.write_isotp_ordered.assert_not_called()
        m.TxFlags=0x80;count[0]=1
        self.assertEqual(ffi.functions['PassThruWriteMsgs'](p.channel,m,count,0),23)
        self.assertEqual(count[0],0)

if __name__=='__main__':unittest.main()
