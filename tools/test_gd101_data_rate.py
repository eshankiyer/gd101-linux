import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class DataRateTests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        return p

    def test_read_actual_rate_for_all_supported_channels(self):
        for protocol in (3,4,5,6):
            for rate in ((10400,) if protocol in (3,4) else (125000,200000,250000,500000,1000000)):
                with self.subTest(protocol=protocol,rate=rate):
                    p=self.provider();status,c=p.connect(1,protocol,0,rate)
                    self.assertEqual(status,0)
                    self.assertEqual(p.configure_timing(c,parameters=[1,1]),(0,[rate,rate]))
                    other=7 if protocol in (3,4) else 3
                    if protocol!=5:
                        value=40 if protocol in (3,4) else 0
                        self.assertEqual(p.configure_timing(c,parameters=[1,other,1]),(0,[rate,value,rate]))
                    self.assertEqual(p.disconnect(c),0)
                    self.assertIsNone(p.baudrate)
                    self.assertEqual(p.configure_timing(c,parameters=[1])[0],2)

    def test_failed_connect_does_not_publish_requested_rate(self):
        p=self.provider();p.session.start_receiver.side_effect=RuntimeError('reader failure')
        with self.assertRaises(RuntimeError):p.connect(1,5,0,500000)
        self.assertIsNone(p.baudrate);self.assertIsNone(p.channel)

    def test_linux_get_config_and_invalid_mixed_request(self):
        p=self.provider();_,c=p.connect(1,6,0,250000)
        ffi=Exports();install(ffi,p)
        entries=ffi.new('GD101_CONFIG[]',[{'Parameter':1,'Value':99},{'Parameter':3,'Value':99}])
        config=ffi.new('GD101_CONFIG_LIST *',{'NumOfParams':2,'ConfigPtr':entries})
        ioctl=ffi.functions['PassThruIoctl']
        self.assertEqual(ioctl(c,1,config,ffi.NULL),0)
        self.assertEqual([e.Value for e in entries],[250000,0])
        entries[0].Value=99;entries[1].Parameter=999;entries[1].Value=99
        self.assertEqual(ioctl(c,1,config,ffi.NULL),1)
        self.assertEqual([e.Value for e in entries],[99,99])

if __name__=='__main__':unittest.main()
