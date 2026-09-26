import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
from test_gd101_initialization import driver
from gd101_kline import build_kline_wire_transmit

HDS=[(10,80),(12,2),(19,30),(20,70),(21,210)]

class TimingTests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.channel=2;p.protocol=4;p.session=Mock()
        return p

    def test_hds_timings_reach_send_and_init(self):
        p=self.provider();self.assertEqual(p.configure_timing(2,pairs=HDS)[0],0)
        self.assertEqual(p.write_message(2,4,0,b'\x81',100),0)
        p.session.write_kline_ordered.assert_called_once_with(b'\x81\x81',timeout=.1,timing=1)
        p.session.kline_baudrate=10400;p.session.initialize_kline.return_value=b'\x08\x08'
        self.assertEqual(p.five_baud_init(2,b'\x33')[0],0)
        self.assertEqual(p.session.initialize_kline.call_args.kwargs['timing_words'],(1,30,70,210,300,300,20,20,50))
        self.assertEqual(p.fast_init(2,4,0,b'\x81',True)[0],0)
        self.assertEqual(p.session.initialize_kline.call_args.kwargs['response_timeout'],.070)

    def test_atomic_validation_and_readback(self):
        p=self.provider();p.configure_timing(2,pairs=HDS)
        self.assertEqual(p.configure_timing(2,pairs=[(19,90),(12,2000)])[0],10)
        self.assertEqual(p.configure_timing(2,parameters=[19,12]),(0,[30,2]))
        self.assertEqual(p.configure_timing(2,pairs=[(99,0)])[0],1)
        self.assertEqual(p.configure_timing(3,pairs=[])[0],2)
        p.configure_timing(2,pairs=[(10,81),(12,3)])
        self.assertEqual(p.configure_timing(2,parameters=[10,12]),(0,[80,2]))

    def test_linux_config_buffers(self):
        p=self.provider();ffi=Exports();install(ffi,p);ioctl=ffi.functions['PassThruIoctl']
        entries=ffi.new('GD101_CONFIG[]',[{'Parameter':k,'Value':v} for k,v in HDS])
        config=ffi.new('GD101_CONFIG_LIST *',{'NumOfParams':5,'ConfigPtr':entries})
        self.assertEqual(ioctl(2,2,config,ffi.NULL),0)
        for e in entries:e.Value=0xdeadbeef
        self.assertEqual(ioctl(2,1,config,ffi.NULL),0)
        self.assertEqual([e.Value for e in entries],[v for k,v in HDS])
        entries[4].Parameter=99;before=bytes(ffi.buffer(entries))
        self.assertEqual(ioctl(2,1,config,ffi.NULL),1)
        self.assertEqual(bytes(ffi.buffer(entries)),before)
        config.NumOfParams=65
        self.assertEqual(ioctl(2,2,config,ffi.NULL),10)
        config.NumOfParams=1;config.ConfigPtr=ffi.NULL
        self.assertEqual(ioctl(2,2,config,ffi.NULL),4)

    def test_native_init_uses_configured_words(self):
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        d.initialize_kline('fast',b'\x81',timing_words=(1,30,70,210,300,300,20,20,50))
        first=d.exchange.call_args_list[0].args[0]
        self.assertEqual(first,bytes.fromhex('0103001e004600d2000100028181'))
        self.assertEqual(d.exchange.call_args_list[1].kwargs['timeout'],.342)
        self.assertEqual(build_kline_wire_transmit(b'\x81',counter=0,timing=999)[3],999&255)

if __name__=='__main__':unittest.main()
