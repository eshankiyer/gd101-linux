import threading
import unittest
from unittest.mock import Mock
from test_gd101_transmit import session

class KLineOrderingTests(unittest.TestCase):
    def test_blocking_follows_queued_wire(self):
        d=session([]);entered=threading.Event();release=threading.Event();done=threading.Event();errors=[]
        def ack(timeout):
            packet=d.send_packet.call_args.args[0]
            if packet[2]==0:entered.set();release.wait(2)
            return bytes([0x43,0x0b,0,packet[2],0,1,0,0,0,0])
        d.receive_packet.side_effect=ack;t=None
        try:
            d.queue_kline(b'\x81');self.assertTrue(entered.wait(1));d.queue_kline(b'\x82')
            def write():
                try:d.write_kline_ordered(b'\x83\x83',timeout=1)
                except Exception as error:errors.append(error)
                finally:done.set()
            t=threading.Thread(target=write);t.start();self.assertFalse(done.wait(.02))
            release.set();self.assertTrue(done.wait(1));t.join();self.assertEqual(errors,[])
            self.assertEqual([c.args[0][-2:] for c in d.send_packet.call_args_list],[b'\x81\x81',b'\x82\x82',b'\x83\x83'])
        finally:
            release.set()
            if t:t.join(2)
            d._async_writer.close()

if __name__=='__main__':unittest.main()
