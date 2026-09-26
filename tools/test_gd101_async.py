import threading
import unittest
from gd101_async import AsyncWriter
from gd101_periodic import PeriodicMessage

class AsyncTests(unittest.TestCase):
    def test_fifo_snapshot_capacity_and_completion(self):
        entered=threading.Event();release=threading.Event();second=threading.Event();seen=[]
        def send(m):
            seen.append(m.data)
            if len(seen)==1:entered.set();release.wait(2)
            else:second.set()
        w=AsyncWriter(send,capacity=2)
        try:
            one=w.enqueue(PeriodicMessage(5,0,b'one'));self.assertTrue(entered.wait(1))
            buf=bytearray(b'two');two=w.enqueue(PeriodicMessage(5,0,buf));buf[0]=0
            with self.assertRaises(OverflowError):w.enqueue(PeriodicMessage(5,0,b'three'))
            release.set();self.assertTrue(second.wait(1));w.clear()
            self.assertEqual(seen,[b'one',b'two'])
            self.assertEqual(w.results(),[(one,'completed',None),(two,'completed',None)])
        finally:release.set();w.close()

    def test_failure_cancels_rest_without_retry(self):
        entered=threading.Event();release=threading.Event();error=TimeoutError('Uncertain send')
        def send(m):entered.set();release.wait(2);raise error
        w=AsyncWriter(send)
        try:
            one=w.enqueue(PeriodicMessage(5,0,b'one'));self.assertTrue(entered.wait(1))
            two=w.enqueue(PeriodicMessage(5,0,b'two'));release.set()
            w._thread.join(1);self.assertFalse(w._thread.is_alive())
            self.assertEqual(w.results(),[(one,'failed',error),(two,'cancelled',None)])
            with self.assertRaises(RuntimeError):w.enqueue(PeriodicMessage(5,0,b'other'))
        finally:release.set();w.close()

    def test_clear_cancels_pending_and_waits_for_inflight(self):
        entered=threading.Event();release=threading.Event();done=threading.Event();seen=[]
        def send(m):seen.append(m.data);entered.set();release.wait(2)
        w=AsyncWriter(send);t=None
        try:
            w.enqueue(PeriodicMessage(5,0,b'one'));self.assertTrue(entered.wait(1))
            w.enqueue(PeriodicMessage(5,0,b'two'))
            t=threading.Thread(target=lambda:(w.clear(),done.set()));t.start()
            self.assertFalse(done.wait(.02));release.set();self.assertTrue(done.wait(1));t.join()
            self.assertEqual(seen,[b'one'])
            self.assertEqual([s for _,s,_ in w.results()],['cancelled','completed'])
        finally:
            release.set();w.close()
            if t:t.join(2)

if __name__=='__main__':unittest.main()
