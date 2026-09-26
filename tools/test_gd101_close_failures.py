import io
import unittest
from unittest.mock import Mock

from gd101_driver import GD101
from gd101_abi import LifecycleProvider
from gd101_can import CANFilters


def session():
    d=GD101.__new__(GD101)
    d.port=Mock();d.receiver=None;d.record={}
    d.tx_key=d.rx_key=bytes(32)
    d.kline_open=d.can_open=False
    d.can_filters=CANFilters();d.flow_entries={1:'old route'}
    d.capture=io.StringIO();d.errors=io.StringIO()
    return d


class CloseFailureTests(unittest.TestCase):
    def test_serial_close_failure_still_releases_state_and_logs(self):
        d=session();capture=d.capture;errors=d.errors
        d.port.close.side_effect=OSError('device vanished')
        p=LifecycleProvider();p.device=1;p.session=d;p.channel=2;p.protocol=5;p.baudrate=500000
        self.assertEqual(p.close(1),7)
        self.assertIn('device vanished',d.record['serial_close_error'])
        self.assertTrue(capture.closed);self.assertTrue(errors.closed)
        self.assertIsNone(d.capture);self.assertIsNone(d.errors)
        self.assertIsNone(d.tx_key);self.assertIsNone(d.rx_key)
        self.assertEqual(d.flow_entries,{})
        self.assertIsNone(p.device);self.assertIsNone(p.channel);self.assertIsNone(p.session)
        self.assertIsNone(p.protocol);self.assertIsNone(p.baudrate)
        d.port.close.assert_called_once()

    def test_each_log_failure_preserves_other_cleanup(self):
        for stage in ('write','close','errors'):
            with self.subTest(stage=stage):
                d=session();capture=Mock();errors=Mock();d.capture=capture;d.errors=errors
                target=errors.close if stage=='errors' else getattr(capture,stage)
                target.side_effect=OSError('disk error')
                p=LifecycleProvider();p.device=1;p.session=d
                self.assertEqual(p.close(1),7)
                capture.close.assert_called_once();errors.close.assert_called_once()
                d.port.close.assert_called_once()
                self.assertIsNone(d.capture);self.assertIsNone(d.errors)
                self.assertIsNone(d.tx_key);self.assertIsNone(p.session)

    def test_failed_open_preserves_original_error_despite_close_failure(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from gd101_devices import GD101NotConnected
        import errno
        with TemporaryDirectory() as root:
            d=session();d.capture.close();d.errors.close();d.errors=None
            d.capture_path=Path(root)/'capture.json'
            d.port.open.side_effect=OSError(errno.ENODEV,'gone')
            d.port.close.side_effect=OSError('close also failed')
            with self.assertRaises(GD101NotConnected):d.__enter__()
            self.assertIsNone(d.capture)
            self.assertIn('close also failed',d.record['serial_close_error'])


if __name__=='__main__':unittest.main()
