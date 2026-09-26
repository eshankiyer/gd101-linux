import threading
import unittest
import test_gd101_periodic_isotp as iso_tests
from gd101_transactions import operation_lock
from gd101_isotp import make_flow_entry

class AsyncISOTests(unittest.TestCase):
    def test_large_payload_native_completion(self):
        d=iso_tests.PeriodicISOTests().session();sent=threading.Event();release=threading.Event()
        def ack(timeout):
            sent.set();release.wait(2)
            packet=d.send_packet.call_args.args[0]
            return bytes([0x43,0x0a,0,packet[2],0,1,0,0,0,0])
        d.receive_packet.side_effect=ack
        try:
            data=bytearray(bytes(range(256))*2)
            h=d.queue_isotp(0,data);self.assertTrue(sent.wait(1));data[0]=99
            release.set();d.clear_tx_queue()
            self.assertEqual(d.queued_results(),[(h,'completed',None)])
            packet=d.send_packet.call_args.args[0]
            self.assertEqual(packet[-512:],bytes(range(256))*2)
            self.assertTrue(d.send_packet.call_args.kwargs['extended'])
            self.assertFalse(d.tx_uncertain)
        finally:release.set();d.disconnect_can()

    def test_replaced_route_fails_before_send(self):
        d=iso_tests.PeriodicISOTests().session()
        try:
            with operation_lock(d):
                h=d.queue_isotp(0,b'\x3e\x80')
                d.flow_entries[0]=make_flow_entry(0x7e1,0x7e9)
            d._async_writer._thread.join(1)
            self.assertFalse(d._async_writer._thread.is_alive())
            result=d.queued_results()
            self.assertEqual(result[0][:2],(h,'failed'))
            self.assertIn('route changed',str(result[0][2]));d.send_packet.assert_not_called()
        finally:d.disconnect_can()

    def test_invalid_payload_does_not_start_queue(self):
        d=iso_tests.PeriodicISOTests().session()
        for slot,data in [(1,b'\x3e'),(0,b''),(0,bytes(4096))]:
            with self.assertRaises(ValueError):d.queue_isotp(slot,data)
        self.assertFalse(hasattr(d,'_async_writer'));d.send_packet.assert_not_called()

if __name__=='__main__':unittest.main()
