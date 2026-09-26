import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class PeriodicKLineABITests(unittest.TestCase):
    def provider(self,protocol=4,flags=0):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        p.connect(1,protocol,flags,10400)
        p.session.start_periodic_kline.return_value=42
        return p

    def test_linux_channel_modes_and_timing(self):
        for protocol in (3,4):
            for flags in (0,0x200):
                p=self.provider(protocol,flags);ffi=Exports();install(ffi,p)
                p.configure_timing(p.channel,pairs=[(10,400),(12,6)])
                m=ffi.new('GD101_MESSAGE *');m.ProtocolID=protocol;m.DataSize=512
                data=bytes(range(256))*2;ffi.memmove(m.Data,data,len(data))
                handle=ffi.new('uint32_t *')
                self.assertEqual(ffi.functions['PassThruStartPeriodicMsg'](p.channel,m,handle,200),0)
                p.session.start_periodic_kline.assert_called_once_with(data,200,protocol=protocol,
                    no_checksum=bool(flags),timing=3,p2_max=400)
                self.assertEqual(ffi.functions['PassThruIoctl'](p.channel,9,ffi.NULL,ffi.NULL),0)
                self.assertEqual(ffi.functions['PassThruStopPeriodicMsg'](p.channel,handle[0]),13)

    def test_invalid_registration_never_calls_native(self):
        p=self.provider();p.configure_timing(p.channel,pairs=[(10,600)])
        for flags,data,interval in [(0,b'x',299),(1,b'x',300),(0,b'',300),(0,bytes(4097),300)]:
            status,_=p.start_periodic(p.channel,4,flags,data,interval)
            self.assertNotEqual(status,0)
        p.session.start_periodic_kline.assert_not_called()

if __name__=='__main__':unittest.main()
