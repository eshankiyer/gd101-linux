import unittest
from unittest.mock import Mock
from gd101_response_window import KLineResponseWindow
from test_gd101_transmit import session

class ResponseWindowTests(unittest.TestCase):
    def test_timeout_before_send_keeps_delivery_certain(self):
        d=session([bytes.fromhex('430b0000000200000000')])
        d.configure_kline_runtime(p2_max=1000)
        d.write_kline_wire(b'\x81\x81')
        self.assertFalse(d.tx_uncertain)
        with self.assertRaisesRegex(TimeoutError,'nothing sent'):d.write_kline_wire(b'\x81\x81',timeout=.01)
        self.assertEqual(d.send_packet.call_count,1);self.assertEqual(d.tx_counter,1)
        self.assertFalse(d.tx_uncertain)

    def test_configuration_applies_to_next_completion(self):
        now=[10.0];w=KLineResponseWindow(.05,clock=lambda:now[0]);w.note_completion()
        self.assertAlmostEqual(w._until,10.05)
        w.configure(.1);self.assertAlmostEqual(w._until,10.05)
        now[0]=11;w.note_completion();self.assertAlmostEqual(w._until,11.1)
        now[0]=12;self.assertAlmostEqual(w.wait(.5),.5)
        with self.assertRaises(ValueError):w.configure(-1)
        self.assertEqual(w._delay,.1)

    def test_native_completion_noted_only_after_success(self):
        d=session([TimeoutError('No acknowledgement')]);w=Mock();w.wait.return_value=.1
        d._kline_response_window=w
        with self.assertRaises(TimeoutError):d.write_kline_wire(b'\x81',timeout=.2)
        w.wait.assert_called_once_with(.2);w.note_completion.assert_not_called()
        self.assertTrue(d.tx_uncertain)

if __name__=='__main__':unittest.main()
