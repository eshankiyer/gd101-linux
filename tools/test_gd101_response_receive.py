import threading
import unittest
from gd101_response_window import KLineResponseWindow
from gd101_receiver import PacketReceiver
from gd101_kline import KLineAssembler,KLineByte

class ReceiveWindowTests(unittest.TestCase):
    def test_pending_and_filtered_completion_extend_wait(self):
        now=[1.0];w=KLineResponseWindow(.055,clock=lambda:now[0])
        r=PacketReceiver(None,bytes(32),assembler=KLineAssembler(),response_window=w)
        r._accept_kline(1,KLineByte(0,1,0x81))
        self.assertTrue(w._receiving)
        r._accept_kline(1.001,KLineByte(0,2,0x81))
        now[0]=1.03;r._accept_kline(1.03)
        self.assertFalse(w._receiving);self.assertAlmostEqual(w._until,1.085)
        self.assertEqual(len(r.messages),0) # No pass filter, but bus timing still applies.

    def test_new_byte_after_gap_keeps_window_closed(self):
        now=[1.0];w=KLineResponseWindow(clock=lambda:now[0])
        r=PacketReceiver(None,bytes(32),assembler=KLineAssembler(no_checksum=True),response_window=w)
        r._accept_kline(1,KLineByte(0,1,1))
        now[0]=1.03;r._accept_kline(1.03,KLineByte(0,2,2))
        self.assertTrue(w._receiving);self.assertAlmostEqual(w._until,1.085)

    def test_receiving_and_failure_prevent_send(self):
        w=KLineResponseWindow(0);w.receive_state(True)
        with self.assertRaises(TimeoutError):w.wait(.01)
        w.receive_state(False,completed=True);self.assertGreater(w.wait(.1),0)
        w.fail(ValueError('Lost receive framing'))
        with self.assertRaisesRegex(RuntimeError,'receiver failed'):w.wait(.1)

if __name__=='__main__':unittest.main()
