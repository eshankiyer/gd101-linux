import threading
import unittest
import test_gd101_periodic_native as native_tests

class NativeAsyncTests(unittest.TestCase):
    def test_accepts_while_sending_and_preserves_native_order(self):
        d=native_tests.NativePeriodicTests().session();entered=threading.Event();release=threading.Event();second=threading.Event()
        def ack(timeout):
            payload=d.send_packet.call_args.args[0]
            if payload[3]==0:entered.set();release.wait(2)
            else:second.set()
            return bytes([0x43,0,0,payload[3],0,1,0,0,0,0])
        d.receive_packet.side_effect=ack
        try:
            first=d.queue_can(0x7df,b'\x01');self.assertTrue(entered.wait(1))
            self.assertTrue(d.tx_uncertain)
            next_id=d.queue_can(0x7e0,b'\x02');self.assertNotEqual(first,next_id)
            release.set();self.assertTrue(second.wait(1));d.clear_tx_queue()
            results=d.queued_results();self.assertEqual([s for _,s,_ in results],['completed','completed'])
            packets=[c.args[0] for c in d.send_packet.call_args_list]
            self.assertEqual([int.from_bytes(p[5:9],'little') for p in packets],[0x7df,0x7e0])
            self.assertFalse(d.tx_uncertain)
        finally:release.set();d.disconnect_can()
        self.assertIsNone(d._async_writer)

if __name__=='__main__':unittest.main()
