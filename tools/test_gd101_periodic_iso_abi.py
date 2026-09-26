import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class PeriodicISOABITests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        p.connect(1,6,0x100,500000)
        p.start_flow_filter(p.channel,6,0x180,b'\xff'*5,bytes.fromhex('18daf110aa'),bytes.fromhex('18da10f1bb'))
        p.session.start_periodic_isotp.return_value=44
        return p

    def test_extended_address_large_payload_linux(self):
        p=self.provider();ffi=Exports();install(ffi,p)
        m=ffi.new('GD101_MESSAGE *');m.ProtocolID=6;m.TxFlags=0x180
        data=bytes.fromhex('18da10f1bb')+bytes(range(256))*2
        m.DataSize=len(data);ffi.memmove(m.Data,data,len(data));handle=ffi.new('uint32_t *')
        self.assertEqual(ffi.functions['PassThruStartPeriodicMsg'](p.channel,m,handle,1000),0)
        p.session.start_periodic_isotp.assert_called_once_with(0,data[5:],1000)
        self.assertEqual(ffi.functions['PassThruIoctl'](p.channel,9,ffi.NULL,ffi.NULL),0)
        self.assertEqual(ffi.functions['PassThruStopPeriodicMsg'](p.channel,handle[0]),13)

    def test_unmatched_invalid_and_short_interval(self):
        p=self.provider();data=bytes.fromhex('18da10f1bb3e80')
        for flags,payload,interval,status in [(0x180,data,999,10),(0x180,data[:5],1000,10),
                (0x180,data[:5]+bytes(4096),1000,10),(0x80,data,1000,23),
                (0x380,data,1000,6),(0x180,bytes(7),1000,23)]:
            self.assertEqual(p.start_periodic(p.channel,6,flags,payload,interval)[0],status)
        p.session.start_periodic_isotp.assert_not_called()

if __name__=='__main__':unittest.main()
