"""Sub-millisecond bookkeeping must not discard a one-millisecond deadline."""
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from cffi import FFI
from gd101_abi import LifecycleProvider,install
from gd101_can import CANFrame
from gd101_kline import KLineMessage

class Exports:
    def __init__(self):
        self.raw=FFI();self.functions={}
        header=(Path(__file__).resolve().parents[1]/'driver/gd101.h').read_text()
        declarations='\n'.join(line for line in header.splitlines()
            if not line.startswith('#') and line not in ('extern "C" {','}'))
        self.raw.cdef('typedef unsigned int uint32_t;\n'+declarations)
    def __getattr__(self,name):return getattr(self.raw,name)
    def def_extern(self,*,name,error):
        def save(function):self.functions[name]=function;return function
        return save

class DeadlineTests(unittest.TestCase):
    def test_fast_init_output_and_failure_preservation(self):
        ffi=Exports();provider=LifecycleProvider()
        provider.fast_init=Mock(return_value=(0,KLineMessage(0,1234,1250,b'\x83\xf1\x10\xc1')))
        install(ffi,provider)
        request=ffi.new('GD101_MESSAGE *');response=ffi.new('GD101_MESSAGE *')
        request.ProtocolID=4;request.TxFlags=0;request.DataSize=1;request.Data[0]=0x81
        ioctl=ffi.functions['PassThruIoctl']
        self.assertEqual(ioctl(99,5,request,response),0)
        provider.fast_init.assert_called_once_with(99,4,0,b'\x81',True)
        self.assertEqual((response.ProtocolID,response.Timestamp,response.DataSize,response.ExtraDataIndex),(4,1234,4,4))
        self.assertEqual(bytes(ffi.buffer(response.Data,4)),b'\x83\xf1\x10\xc1')
        provider.fast_init.return_value=(9,None)
        snapshot=bytes(ffi.buffer(response))
        self.assertEqual(ioctl(99,5,request,response),9)
        self.assertEqual(bytes(ffi.buffer(response)),snapshot)
        provider.fast_init.return_value=(0,None)
        self.assertEqual(ioctl(99,5,ffi.NULL,ffi.NULL),0)
        provider.fast_init.assert_called_with(99,0,0,b'',False)

    def test_kline_write_through_linux_boundary(self):
        ffi=Exports();provider=LifecycleProvider()
        provider.channel=99;provider.protocol=4;provider.session=Mock()
        install(ffi,provider)
        msg=ffi.new('GD101_MESSAGE *');msg.ProtocolID=4;msg.DataSize=512
        data=bytes(range(256))*2;ffi.memmove(msg.Data,data,len(data))
        count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruWriteMsgs'](99,msg,count,1000),0)
        self.assertEqual(count[0],1)
        self.assertEqual(provider.session.write_kline_ordered.call_args.args[0],data+b'\0')
        self.assertEqual(bytes(ffi.buffer(msg.Data,msg.DataSize)),data)

    def test_kline_read_and_filters_through_linux_boundary(self):
        ffi=Exports();p=LifecycleProvider();p.channel=99;p.protocol=3;p.session=Mock()
        data=bytes(range(256))*2
        p.session.read_message.side_effect=[KLineMessage(0,4321,4400,data),TimeoutError()]
        install(ffi,p)
        messages=ffi.new('GD101_MESSAGE[2]');messages[1].DataSize=0xaabbccdd
        count=ffi.new('uint32_t *',2)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](99,messages,count,0),0)
        self.assertEqual(count[0],1)
        self.assertEqual((messages[0].ProtocolID,messages[0].Timestamp,messages[0].DataSize),(3,4321,512))
        self.assertEqual(bytes(ffi.buffer(messages[0].Data,512)),data)
        self.assertEqual(messages[1].DataSize,0xaabbccdd)
        mask=ffi.new('GD101_MESSAGE *');pattern=ffi.new('GD101_MESSAGE *');handle=ffi.new('uint32_t *')
        mask.ProtocolID=pattern.ProtocolID=3
        self.assertEqual(ffi.functions['PassThruStartMsgFilter'](99,1,mask,pattern,ffi.NULL,handle),0)
        saved=handle[0]
        mask.ProtocolID=pattern.ProtocolID=4
        self.assertEqual(ffi.functions['PassThruStartMsgFilter'](99,1,mask,pattern,ffi.NULL,handle),21)
        self.assertEqual(handle[0],0)
        self.assertEqual(ffi.functions['PassThruStopMsgFilter'](99,saved),0)
        self.assertEqual(ffi.functions['PassThruStopMsgFilter'](99,saved),22)

    def test_one_millisecond_survives_fractional_overhead(self):
        ffi=Exports();provider=LifecycleProvider()
        provider.write_message=Mock(return_value=0)
        provider.protocol=5
        provider.read_can=Mock(return_value=(0,CANFrame(0x7e8,b'\x41',False,123)))
        install(ffi,provider)
        msg=ffi.new('GD101_MESSAGE *');msg.ProtocolID=5;msg.DataSize=4
        count=ffi.new('uint32_t *',1)
        with patch('gd101_abi.time.monotonic',side_effect=[100,100.0004]):
            self.assertEqual(ffi.functions['PassThruWriteMsgs'](99,msg,count,1),0)
        provider.write_message.assert_called_once_with(99,5,0,b'\0'*4,1)
        count[0]=1
        with patch('gd101_abi.time.monotonic',side_effect=[100,100.0004]):
            self.assertEqual(ffi.functions['PassThruReadMsgs'](99,msg,count,1),0)
        provider.read_can.assert_called_once_with(99,1)
        self.assertEqual(count[0],1)
        self.assertEqual(bytes(ffi.buffer(msg.Data,msg.DataSize)),bytes.fromhex('000007e841'))

if __name__=='__main__':unittest.main()
