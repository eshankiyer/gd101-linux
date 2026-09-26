import unittest
from unittest.mock import Mock,patch
from collections import deque
from gd101_driver import GD101,GD101Error,GD101Timeout
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class CommandTimeoutTests(unittest.TestCase):
    def session(self):
        d=object.__new__(GD101);d.events=deque();d.record={'exchanges':[]}
        d.send_packet=Mock(return_value=b'frame')
        d.receive_packet=Mock(return_value=b'\x43\x03\0\1\0\0\0\x81')
        return d

    def test_interleaved_receive_does_not_restart_command_deadline(self):
        d=self.session()
        with patch('gd101_driver.time.monotonic',side_effect=[0,.1,.2,.3,.4,1]):
            with self.assertRaises(GD101Timeout):d.exchange(b'\x02\0\x10\0',b'\x42\0',timeout=.5)
        self.assertEqual(d.send_packet.call_count,1)
        self.assertEqual(len(d.events),4)
        self.assertEqual([round(c.args[0],3) for c in d.receive_packet.call_args_list],[.4,.3,.2,.1])

    def test_ack_timeout_keeps_pin_uncertainty(self):
        d=self.session()
        with patch('gd101_driver.time.monotonic',side_effect=[0,3]):
            with self.assertRaises(GD101Timeout):d.disable_programming_voltage(9)
        self.assertTrue(d.pin_uncertain)
        with self.assertRaises(GD101Error):d.disable_programming_voltage(9)
        self.assertEqual(d.send_packet.call_count,1)

    def test_zero_response_wait_polls_without_rejecting_configuration(self):
        from test_gd101_initialization import driver
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        d.start_receiver=Mock()
        d.read_kline_initialization=Mock(side_effect=TimeoutError('No queued response'))
        with self.assertRaises(TimeoutError):
            d.initialize_kline('fast',b'\x81',capture_response=True,response_timeout=0)
        d.read_kline_initialization.assert_called_once_with(0)
        self.assertTrue(d.init_uncertain)
        self.assertEqual(d.exchange.call_count,2)

    def test_version_timeout_preserves_abi_output_and_distinguishes_bad_packet(self):
        ffi=Exports();p=LifecycleProvider();p.device=1;p.session=Mock();install(ffi,p)
        buffers=[ffi.new('char[80]',b'x'*79) for _ in range(3)]
        snapshots=[bytes(ffi.buffer(b)) for b in buffers]
        p.session.read_version.side_effect=GD101Timeout('No reply')
        self.assertEqual(ffi.functions['PassThruReadVersion'](1,*buffers),9)
        self.assertEqual([bytes(ffi.buffer(b)) for b in buffers],snapshots)
        p.session.read_version.side_effect=GD101Error('Unexpected reply')
        self.assertEqual(ffi.functions['PassThruReadVersion'](1,*buffers),7)
        self.assertEqual([bytes(ffi.buffer(b)) for b in buffers],snapshots)

if __name__=='__main__':unittest.main()
