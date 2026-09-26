import threading
import unittest
from unittest.mock import Mock
from gd101_driver import GD101
from gd101_periodic import PeriodicMessage
from gd101_abi import LifecycleProvider

class PeriodicTimingTests(unittest.TestCase):
    def test_update_waits_for_send_and_next_uses_new_value(self):
        d=object.__new__(GD101);entered=threading.Event();release=threading.Event();updated=threading.Event();values=[]
        def send(data,**kw):
            values.append(kw['timing'])
            if len(values)==1:entered.set();release.wait(2)
        d.write_kline_wire=send
        job=PeriodicMessage(4,5,b'\x81\x81')
        t=threading.Thread(target=lambda:d._send_periodic_message(job));t.start()
        u=None
        try:
            self.assertTrue(entered.wait(1))
            u=threading.Thread(target=lambda:(d.configure_kline_runtime(timing=0),updated.set()));u.start()
            self.assertFalse(updated.wait(.03));release.set()
            self.assertTrue(updated.wait(1));t.join();u.join()
            d._send_periodic_message(job)
            self.assertEqual(values,[5,0])
        finally:
            release.set();t.join(2)
            if u:u.join(2)

    def test_invalid_combined_update_preserves_runtime_and_provider(self):
        d=object.__new__(GD101);d._periodic_kline_timing=5;d.receiver=Mock()
        d.receiver.set_kline_gap.side_effect=ValueError('Synthetic failure')
        p=LifecycleProvider();p.session=d;p.channel=1;p.protocol=4
        with self.assertRaises(ValueError):p.configure_timing(1,pairs=[(7,8),(12,2)])
        self.assertEqual(p.timing_config,{});self.assertEqual(d._periodic_kline_timing,5)
        d.receiver.set_kline_gap.reset_mock()
        with self.assertRaises(ValueError):d.configure_kline_runtime(timing=-1,gap=.004)
        d.receiver.set_kline_gap.assert_not_called()

if __name__=='__main__':unittest.main()
