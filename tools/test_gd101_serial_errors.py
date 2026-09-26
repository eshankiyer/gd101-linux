import unittest
from unittest.mock import Mock
from serial import SerialException,SerialTimeoutException
from gd101_devices import GD101NotConnected,GD101SerialTimeout,serial_transport_call,device_error_status
from gd101_driver import GD101,read_frame
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
from test_gd101_transmit import session

class SerialErrorsTests(unittest.TestCase):
    def test_direct_read_classifies_transport_failure(self):
        for error,kind,status in ((SerialException('lost'),GD101NotConnected,8),(SerialTimeoutException('timeout'),GD101SerialTimeout,9)):
            port=Mock();port.read.side_effect=error
            with self.assertRaises(kind) as raised:read_frame(port)
            self.assertEqual(device_error_status(raised.exception),status)
            port.read.assert_called_once()

    def test_transmit_failure_does_not_retry_or_clear_uncertainty(self):
        for stage in ('write','flush'):
            for error,kind in ((SerialException('lost'),GD101NotConnected),(SerialTimeoutException('late'),GD101SerialTimeout)):
                d=session([]);d.receiver=None;d.port=Mock();d.tx_key=bytes(32);d.record={}
                d.port.write.side_effect=lambda wire:len(wire)
                getattr(d.port,stage).side_effect=error
                d.send_packet=GD101.send_packet.__get__(d)
                with self.assertRaises(kind):d.write_kline_wire(b'abc')
                self.assertTrue(d.tx_uncertain)
                d.port.write.assert_called_once();d.receive_packet.assert_not_called()
                p=LifecycleProvider();p.channel=1;p.protocol=4;p.session=Mock()
                p.session.write_kline_ordered.side_effect=kind('synthetic serial failure')
                ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');m.ProtocolID=4;m.DataSize=1
                count=ffi.new('uint32_t *',1)
                self.assertEqual(ffi.functions['PassThruWriteMsgs'](1,m,count,100),8 if kind is GD101NotConnected else 9)
                self.assertEqual(count[0],0)

    def test_background_serial_timeout_is_not_disconnect(self):
        from gd101_receiver import PacketReceiver
        from gd101_can import CANFilters
        port=Mock();port.in_waiting=1;port.read.side_effect=SerialTimeoutException('timed out')
        r=PacketReceiver(port,bytes(32),can_filters=CANFilters());r.start()
        try:
            self.assertTrue(r.stop_event.wait(1))
            with self.assertRaises(RuntimeError) as raised:r.read_message(0)
            self.assertEqual(device_error_status(raised.exception),9)
            self.assertIsInstance(r.error,GD101SerialTimeout)
        finally:r.stop()

    def test_nonserial_errors_and_results_are_preserved(self):
        self.assertEqual(serial_transport_call(lambda x:x,123),123)
        error=ValueError('framing issue');call=Mock(side_effect=error)
        with self.assertRaises(ValueError) as raised:serial_transport_call(call)
        self.assertIs(raised.exception,error)
        self.assertEqual(device_error_status(error),7)

if __name__=='__main__':unittest.main()
