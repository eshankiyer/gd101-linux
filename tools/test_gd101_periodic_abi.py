import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class PeriodicABITests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.channel=2;p.protocol=5;p.session=Mock()
        p.session.start_periodic_can.return_value=42
        return p

    def test_linux_lifecycle_and_clear(self):
        p=self.provider();ffi=Exports();install(ffi,p)
        message=ffi.new('GD101_MESSAGE *');message.ProtocolID=5;message.DataSize=5
        ffi.memmove(message.Data,bytes.fromhex('000007df01'),5)
        handle=ffi.new('uint32_t *',999)
        start=ffi.functions['PassThruStartPeriodicMsg'];stop=ffi.functions['PassThruStopPeriodicMsg']
        self.assertEqual(start(2,message,handle,1000),0)
        p.session.start_periodic_can.assert_called_once_with(0x7df,b'\x01',1000,extended=False)
        old=handle[0];self.assertEqual(stop(2,old),0)
        p.session.stop_periodic.assert_called_once_with(42)
        self.assertEqual(stop(2,old),13)
        self.assertEqual(start(2,message,handle,1000),0);self.assertNotEqual(handle[0],old)
        self.assertEqual(ffi.functions['PassThruIoctl'](2,9,ffi.NULL,ffi.NULL),0)
        p.session.clear_periodic.assert_called_once()
        self.assertEqual(stop(2,handle[0]),13)
        handle[0]=999
        self.assertEqual(start(2,ffi.NULL,handle,1000),4);self.assertEqual(handle[0],0)

    def test_invalid_parameters_and_capacity(self):
        p=self.provider()
        for c,proto,flags,data,interval,status in [(3,5,0,b'1234',100,2),(2,6,0,b'1234',100,21),
              (2,5,1,b'1234',100,6),(2,5,0,b'123',100,10),(2,5,0,b'1234',0,10)]:
            self.assertEqual(p.start_periodic(c,proto,flags,data,interval)[0],status)
        p.session.start_periodic_can.assert_not_called()
        p.session.start_periodic_can.side_effect=OverflowError('Full')
        before=p.next_id
        self.assertEqual(p.start_periodic(2,5,0,bytes(4),100),(12,0))
        self.assertEqual(p.next_id,before);self.assertEqual(p.periodic_handles,{})

    def test_failed_native_job_reports_error(self):
        p=self.provider();_,h=p.start_periodic(2,5,0,bytes(4),100)
        p.session.stop_periodic.side_effect=KeyError(42)
        p.session.periodic_error.return_value=TimeoutError('Uncertain delivery')
        self.assertEqual(p.stop_periodic(2,h),7)
        self.assertIn('Uncertain',p.last_error)
        self.assertEqual(p.stop_periodic(2,h),13)

if __name__=='__main__':unittest.main()
