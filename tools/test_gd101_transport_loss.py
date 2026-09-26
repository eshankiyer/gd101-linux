import unittest
from unittest.mock import Mock
from serial import SerialException
from gd101_devices import GD101NotConnected,device_error_status
from gd101_receiver import PacketReceiver
from gd101_can import CANFilters
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
import test_gd101_bridge as bridge_helpers

class TransportLossTests(unittest.TestCase):
    def failed_reader(self,error):
        port=Mock();port.in_waiting=1;port.read.side_effect=error
        receiver=PacketReceiver(port,bytes(32),can_filters=CANFilters())
        receiver.start();self.assertTrue(receiver.stop_event.wait(1));receiver.stop()
        return receiver,port

    def test_reader_disconnect_reaches_linux_and_rpc_without_retry(self):
        receiver,port=self.failed_reader(SerialException('device reports readiness but returned no data'))
        self.assertIsInstance(receiver.error,GD101NotConnected)
        p=LifecycleProvider();p.channel=1;p.protocol=5;p.session=Mock()
        p.session.read_can_message.side_effect=receiver.read_message
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');m.ProtocolID=99
        count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),8)
        self.assertEqual(count[0],0);self.assertEqual(m.ProtocolID,99)
        helper=bridge_helpers.BridgeTests();helper.provider=p
        status,payload=helper.call(13,1,0)
        self.assertEqual(status,8);self.assertIn(b'USB serial transport',payload)
        self.assertEqual(port.read.call_count,1)

    def test_protocol_failure_is_not_reported_as_disconnect(self):
        receiver,port=self.failed_reader(ValueError('bad native frame'))
        with self.assertRaises(RuntimeError) as result:receiver.read_message(0)
        self.assertEqual(device_error_status(result.exception),7)
        self.assertNotIsInstance(receiver.error,GD101NotConnected)

    def test_wrapped_disconnect_and_cyclic_errors(self):
        disconnected=GD101NotConnected('gone');wrapper=RuntimeError('worker failed')
        wrapper.__cause__=disconnected
        self.assertEqual(device_error_status(wrapper),8)
        cycle=RuntimeError('other');cycle.__cause__=cycle
        self.assertEqual(device_error_status(cycle),7)

if __name__=='__main__':unittest.main()
