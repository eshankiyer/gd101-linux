import threading
import time
import unittest
from unittest.mock import Mock
from gd101_can import CANFilters
from gd101_driver import GD101Error
from test_gd101_can_transmit import driver

class NativePeriodicTests(unittest.TestCase):
    def session(self):
        d=driver([]);d.flow_uncertain=False;d.can_filters=CANFilters();d.flow_entries={}
        d.exchange=Mock(return_value=bytes.fromhex('41000000'))
        return d

    def test_native_can_packet_and_stop(self):
        d=self.session();sent=threading.Event()
        d.send_packet.side_effect=lambda payload:sent.set()
        d.receive_packet.side_effect=None
        d.receive_packet.return_value=bytes.fromhex('43000000000200000000')
        try:
            h=d.start_periodic_can(0x7df,b'\x01',1000)
            self.assertTrue(sent.wait(1));d.stop_periodic(h)
            d.send_packet.assert_called_once_with(bytes.fromhex('0300000000df070000010100'))
            self.assertFalse(d.tx_uncertain)
            with self.assertRaises(KeyError):d.stop_periodic(h)
        finally:d.disconnect_can()

    def test_timeout_deactivates_and_retains_uncertainty(self):
        d=self.session();called=threading.Event()
        def receive(timeout):called.set();raise TimeoutError('Uncertain send')
        d.receive_packet.side_effect=receive
        try:
            h=d.start_periodic_can(0x7df,b'',5)
            self.assertTrue(called.wait(1));d.clear_periodic()
            self.assertIsInstance(d.periodic_error(h),TimeoutError)
            self.assertTrue(d.tx_uncertain)
            with self.assertRaises(GD101Error):d.start_periodic_can(0x7df,b'',5)
            time.sleep(.02);self.assertEqual(d.send_packet.call_count,1)
        finally:d.disconnect_can()

    def test_disconnect_waits_without_lock_cycle(self):
        d=self.session();entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        def receive(timeout):entered.set();release.wait(2);return bytes.fromhex('43000000000200000000')
        d.receive_packet.side_effect=receive
        t=None
        try:
            d.start_periodic_can(1,b'',5);scheduler=d._periodic_scheduler
            self.assertTrue(entered.wait(1))
            def disconnect():
                try:d.disconnect_can()
                except Exception as exc:errors.append(exc)
                finally:done.set()
            t=threading.Thread(target=disconnect);t.start()
            self.assertFalse(done.wait(.03));d.exchange.assert_not_called()
            release.set();self.assertTrue(done.wait(1));t.join()
            self.assertEqual(errors,[]);self.assertFalse(d.can_open)
            self.assertIsNone(d._periodic_scheduler);self.assertFalse(scheduler._thread.is_alive())
            self.assertEqual(d.send_packet.call_count,1)
        finally:
            release.set()
            if t is not None:t.join(2)
            d.disconnect_can()

    def test_invalid_registration_does_not_create_worker(self):
        d=self.session()
        for identifier,data,interval in [(0x800,b'',100),(1,bytes(9),100),(1,b'',0)]:
            with self.assertRaises(ValueError):d.start_periodic_can(identifier,data,interval)
        self.assertFalse(hasattr(d,'_periodic_scheduler'));d.send_packet.assert_not_called()

if __name__=='__main__':unittest.main()
