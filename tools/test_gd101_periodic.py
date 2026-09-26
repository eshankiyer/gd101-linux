import threading
import time
import unittest
from gd101_periodic import PeriodicScheduler,PeriodicMessage

class PeriodicTests(unittest.TestCase):
    def test_stop_waits_for_inflight_and_prevents_later_send(self):
        entered=threading.Event();release=threading.Event();stopped=threading.Event();sent=[]
        def send(message):
            sent.append(message);entered.set();release.wait(2)
        s=PeriodicScheduler(send)
        try:
            data=bytearray(b'abc');h=s.start(PeriodicMessage(5,0,data),5);data[0]=0
            self.assertTrue(entered.wait(1))
            t=threading.Thread(target=lambda:(s.stop(h),stopped.set()));t.start()
            self.assertFalse(stopped.wait(.03));release.set()
            self.assertTrue(stopped.wait(1));t.join()
            time.sleep(.02)
            self.assertEqual(sent,[PeriodicMessage(5,0,b'abc')])
        finally:release.set();s.close()

    def test_failed_send_is_not_retried(self):
        called=threading.Event();calls=[]
        def send(message):
            calls.append(message);called.set();raise TimeoutError('Uncertain transmission')
        s=PeriodicScheduler(send)
        try:
            h=s.start(PeriodicMessage(6,0,b'abc'),5)
            self.assertTrue(called.wait(1))
            s.clear()
            self.assertIsInstance(s.error(h),TimeoutError)
            time.sleep(.02);self.assertEqual(len(calls),1)
        finally:s.close()

    def test_capacity_clear_and_close(self):
        entered=threading.Event();release=threading.Event()
        def send(message):entered.set();release.wait(2)
        s=PeriodicScheduler(send)
        try:
            handles=[s.start(PeriodicMessage(5,0,b'x'),1000) for _ in range(10)]
            self.assertTrue(entered.wait(1))
            with self.assertRaises(OverflowError):s.start(PeriodicMessage(5,0,b'x'),1000)
            release.set();s.clear()
            with self.assertRaises(KeyError):s.stop(handles[0])
        finally:release.set();s.close()
        self.assertFalse(s._thread.is_alive())
        with self.assertRaises(RuntimeError):s.start(PeriodicMessage(5,0,b'x'),1000)

    def test_late_completion_skips_catchup(self):
        now=[0.0];sent=threading.Event();release=threading.Event()
        def send(message):sent.set();release.wait(2)
        s=PeriodicScheduler(send,clock=lambda:now[0])
        try:
            h=s.start(PeriodicMessage(5,0,b'x'),100)
            self.assertTrue(sent.wait(1));now[0]=.35;release.set()
            with s._condition:
                self.assertTrue(s._condition.wait_for(lambda:s._inflight is None,1))
                self.assertAlmostEqual(s._jobs[h].due,.4)
        finally:release.set();s.close()

if __name__=='__main__':unittest.main()
