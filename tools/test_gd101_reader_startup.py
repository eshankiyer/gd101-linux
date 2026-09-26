import unittest
from unittest.mock import Mock,patch
from gd101_driver import GD101
from gd101_receiver import PacketReceiver

class ReaderStartupTests(unittest.TestCase):
    def session(self):
        d=object.__new__(GD101);d.receiver=None;d.rx_key=bytes(32);d.port=Mock();d.record={}
        return d

    def test_failed_start_is_cleaned_without_publication(self):
        d=self.session();candidate=Mock();candidate.start.side_effect=RuntimeError('Thread creation failed')
        with patch('gd101_receiver.PacketReceiver',return_value=candidate):
            with self.assertRaisesRegex(RuntimeError,'Thread creation failed'):d.start_receiver()
        candidate.stop.assert_called_once();self.assertIsNone(d.receiver)

    def test_uncertain_cleanup_retains_reader_for_shutdown(self):
        d=self.session();candidate=Mock()
        candidate.start.side_effect=RuntimeError('Startup failed')
        candidate.stop.side_effect=RuntimeError('Still running')
        with patch('gd101_receiver.PacketReceiver',return_value=candidate):
            with self.assertRaisesRegex(RuntimeError,'Startup failed'):d.start_receiver()
        self.assertIs(d.receiver,candidate)
        self.assertEqual(d.record['receiver_start_cleanup_error'],'Still running')

    def test_publish_only_after_start_returns(self):
        d=self.session();candidate=Mock()
        candidate.start.side_effect=lambda:self.assertIsNone(d.receiver)
        with patch('gd101_receiver.PacketReceiver',return_value=candidate):d.start_receiver()
        self.assertIs(d.receiver,candidate);candidate.stop.assert_not_called()

    def test_unstarted_reader_can_be_stopped(self):
        r=PacketReceiver(None,bytes(32));r.stop();r.stop()
        self.assertTrue(r.stop_event.is_set());self.assertFalse(r.thread.is_alive())

if __name__=='__main__':unittest.main()
