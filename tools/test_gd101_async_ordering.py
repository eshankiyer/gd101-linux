import threading
import unittest
from unittest.mock import Mock
import test_gd101_periodic_native as native_tests

class OrderingTests(unittest.TestCase):
    def test_blocking_follows_accepted_queue(self):
        d=native_tests.NativePeriodicTests().session();entered=threading.Event();release=threading.Event();finished=threading.Event();errors=[]
        def ack(timeout):
            packet=d.send_packet.call_args.args[0]
            if packet[3]==0:entered.set();release.wait(2)
            return bytes([0x43,0,0,packet[3],0,1,0,0,0,0])
        d.receive_packet.side_effect=ack;t=None
        try:
            d.queue_can(1,b'a');self.assertTrue(entered.wait(1));d.queue_can(2,b'b')
            def blocking():
                try:d.write_can_ordered(3,b'c',timeout=1)
                except Exception as error:errors.append(error)
                finally:finished.set()
            t=threading.Thread(target=blocking);t.start();self.assertFalse(finished.wait(.02))
            release.set();self.assertTrue(finished.wait(1));t.join()
            self.assertEqual(errors,[])
            self.assertEqual([int.from_bytes(c.args[0][5:9],'little') for c in d.send_packet.call_args_list],[1,2,3])
        finally:
            release.set()
            if t:t.join(2)
            d.disconnect_can()

    def test_drain_timeout_does_not_send_blocking_message(self):
        d=native_tests.NativePeriodicTests().session();entered=threading.Event();release=threading.Event()
        def ack(timeout):entered.set();release.wait(2);return bytes.fromhex('43000000000100000000')
        d.receive_packet.side_effect=ack
        try:
            d.queue_can(1,b'a');self.assertTrue(entered.wait(1))
            with self.assertRaises(TimeoutError):d.write_can_ordered(2,b'b',timeout=.01)
            self.assertEqual(d.send_packet.call_count,1)
            release.set();d.clear_tx_queue()
            self.assertEqual([s for _,s,_ in d.queued_results()],['completed'])
        finally:release.set();d.disconnect_can()

    def test_prior_failure_prevents_later_blocking_send(self):
        d=native_tests.NativePeriodicTests().session();d.receive_packet.side_effect=TimeoutError('Lost ACK')
        try:
            d.queue_can(1,b'a');d._async_writer._thread.join(1)
            with self.assertRaisesRegex(RuntimeError,'Earlier queued'):d.write_can_ordered(2,b'b')
            self.assertEqual(d.send_packet.call_count,1)
        finally:d.disconnect_can()

if __name__=='__main__':unittest.main()
