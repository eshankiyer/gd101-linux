import unittest
import errno
import json
import tempfile
from pathlib import Path
from serial import SerialException
from unittest.mock import Mock,MagicMock,patch
from gd101_abi import LifecycleProvider,install
from gd101_devices import GD101NotConnected, open_serial_port
from test_gd101_abi_deadline import Exports
import test_gd101_bridge as bridge_helpers

class DeviceOpenErrorTests(unittest.TestCase):
    def test_serial_open_errors_clean_up_and_leave_handle_available(self):
        from gd101_driver import GD101
        cases=[(errno.ENOENT,8,'disappeared'),(errno.ENODEV,8,'disappeared'),
               (errno.ENXIO,8,'disappeared'),(errno.EBUSY,14,'busy'),
               (errno.EAGAIN,14,'busy'),(errno.EACCES,7,'Permission denied'),
               (errno.EPERM,7,'Permission denied')]
        for number,status,message in cases:
            with self.subTest(errno=number),tempfile.TemporaryDirectory() as temp:
                path=Path(temp);auth=path/'auth.json'
                auth.write_text(json.dumps({'adapter_serial':'MT000418'}))
                port=Mock();port.open.side_effect=SerialException(number,'test open failure')
                with patch('gd101_devices.select_gd101_port',return_value='/dev/test-gd101'), \
                     patch('gd101_driver.prepare_serial',return_value=port), \
                     patch('gd101_driver.authenticate_native') as authenticate:
                    session=GD101(path/'capture.json',auth_file=auth)
                    p=LifecycleProvider(lambda:session)
                    ffi=Exports();install(ffi,p);handle=ffi.new('uint32_t *',99)
                    self.assertEqual(ffi.functions['PassThruOpen'](ffi.NULL,handle),status)
                    self.assertEqual(handle[0],0)
                    self.assertIsNone(p.device);self.assertIsNone(p.session)
                    self.assertEqual(p.next_id,1)
                    self.assertIn(message,p.last_error)
                    authenticate.assert_not_called()
                    port.open.assert_called_once();port.close.assert_called_once()
                    port.read.assert_not_called();port.write.assert_not_called()
                    self.assertIsNone(session.capture)
                    self.assertIsNone(session.tx_key);self.assertIsNone(session.rx_key)
                    self.assertIn(message,json.loads((path/'capture.json').read_text())['error'])
                    replacement=MagicMock();p.factory=lambda:replacement
                    self.assertEqual(p.open(),(0,1))

    def test_unclassified_open_errors_are_preserved(self):
        for error in (SerialException('Port is already open'),OSError(errno.EIO,'I/O failure'),ValueError('bad config')):
            with self.subTest(error=error):
                port=Mock();port.open.side_effect=error
                with self.assertRaises(type(error)) as caught:open_serial_port(port)
                self.assertIs(caught.exception,error)
                port.open.assert_called_once()

    def test_missing_device_does_not_publish_or_consume_handle(self):
        session=MagicMock();factory=Mock(side_effect=[GD101NotConnected('Configured adapter absent'),session])
        p=LifecycleProvider(factory)
        self.assertEqual(p.open(),(8,0))
        self.assertIsNone(p.device);self.assertIsNone(p.session);self.assertEqual(p.next_id,1)
        self.assertIn('absent',p.last_error)
        self.assertEqual(p.open(),(0,1))
        session.__enter__.assert_called_once()

    def test_linux_open_error_and_last_error_output(self):
        p=LifecycleProvider(Mock(side_effect=GD101NotConnected('Configured GODIAG adapter is not connected')))
        ffi=Exports();install(ffi,p);handle=ffi.new('uint32_t *',99)
        self.assertEqual(ffi.functions['PassThruOpen'](ffi.NULL,handle),8)
        self.assertEqual(handle[0],0)
        text=ffi.new('char[80]')
        self.assertEqual(ffi.functions['PassThruGetLastError'](text),0)
        self.assertIn(b'not connected',ffi.string(text))

    def test_rpc_retains_disconnected_status(self):
        helper=bridge_helpers.BridgeTests();helper.setUp()
        helper.provider.factory=Mock(side_effect=GD101NotConnected('Configured adapter absent'))
        status,payload=helper.call(1)
        self.assertEqual(status,8)
        self.assertEqual(payload.rstrip(b'\0'),b'Configured adapter absent')

    def test_missing_device_does_not_prepare_any_serial_port(self):
        from gd101_driver import GD101
        import tempfile,json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);auth=path/'auth.json';auth.write_text(json.dumps({'adapter_serial':'MT000418'}))
            p=LifecycleProvider(lambda:GD101(path/'capture.json',auth_file=auth))
            with patch('serial.tools.list_ports.comports',return_value=[]),patch('gd101_driver.prepare_serial') as prepare:
                self.assertEqual(p.open(),(8,0))
                prepare.assert_not_called()
            self.assertFalse((path/'capture.json').exists())

if __name__=='__main__':unittest.main()
