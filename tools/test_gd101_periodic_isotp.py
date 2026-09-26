import threading
import unittest
from unittest.mock import Mock
from gd101_driver import GD101Error
from gd101_periodic import PeriodicMessage
from gd101_transactions import operation_lock
from gd101_isotp import make_flow_entry,default_flow_config
import test_gd101_periodic_native as native_tests

class PeriodicISOTests(unittest.TestCase):
    def session(self):
        d=native_tests.NativePeriodicTests().session()
        d.flow_entries={0:make_flow_entry(0x7e0,0x7e8)};d.flow_config=default_flow_config()
        return d

    def test_native_single_frame_packet(self):
        d=self.session();sent=threading.Event()
        d.send_packet.side_effect=lambda *a,**k:sent.set()
        d.receive_packet.side_effect=None
        d.receive_packet.return_value=bytes.fromhex('43000000000200000000')
        try:
            h=d.start_periodic_isotp(0,b'\x3e\x80',1000)
            self.assertTrue(sent.wait(1));d.stop_periodic(h)
            payload=d.send_packet.call_args.args[0]
            self.assertEqual(payload[5:9],bytes.fromhex('e0070000'))
            self.assertEqual(payload[9:13],bytes.fromhex('03023e80'))
            self.assertFalse(d.tx_uncertain)
        finally:d.disconnect_can()

    def test_changed_route_never_redirects_job(self):
        d=self.session()
        job=PeriodicMessage(6,0,b'\0\x3e\x80',d.flow_entries[0])
        with operation_lock(d):d.flow_entries[0]=make_flow_entry(0x7e1,0x7e9)
        with self.assertRaisesRegex(GD101Error,'route changed'):d._send_periodic_message(job)
        d.send_packet.assert_not_called()
        del d.flow_entries[0]
        with self.assertRaisesRegex(GD101Error,'route changed'):d._send_periodic_message(job)
        d.send_packet.assert_not_called()

    def test_invalid_payload_and_minimum_do_not_create_jobs(self):
        d=self.session()
        for slot,data,interval in [(0,b'\x3e',999),(1,b'\x3e',1000),(0,b'',1000),(0,bytes(4096),1000)]:
            with self.assertRaises(ValueError):d.start_periodic_isotp(slot,data,interval)
        self.assertFalse(hasattr(d,'_periodic_scheduler'));d.send_packet.assert_not_called()

if __name__=='__main__':unittest.main()
