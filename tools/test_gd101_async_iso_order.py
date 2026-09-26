import threading
import unittest
import test_gd101_periodic_isotp as helpers

class ISOOrderingTests(unittest.TestCase):
    def test_queued_then_blocking_order(self):
        d=helpers.PeriodicISOTests().session();entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        def ack(timeout):
            packet=d.send_packet.call_args.args[0]
            if packet[3]==0:entered.set();release.wait(2)
            return bytes([0x43,0,0,packet[3],0,1,0,0,0,0])
        d.receive_packet.side_effect=ack;t=None
        try:
            d.queue_isotp(0,b'\x3e\x01');self.assertTrue(entered.wait(1))
            d.queue_isotp(0,b'\x3e\x02')
            def write():
                try:d.write_isotp_ordered(0,b'\x3e\x03',timeout=1)
                except Exception as error:errors.append(error)
                finally:done.set()
            t=threading.Thread(target=write);t.start();self.assertFalse(done.wait(.02))
            release.set();self.assertTrue(done.wait(1));t.join();self.assertEqual(errors,[])
            self.assertEqual([c.args[0][10:13] for c in d.send_packet.call_args_list],
                             [b'\x02\x3e\x01',b'\x02\x3e\x02',b'\x02\x3e\x03'])
        finally:
            release.set()
            if t:t.join(2)
            d.disconnect_can()

if __name__=='__main__':unittest.main()
