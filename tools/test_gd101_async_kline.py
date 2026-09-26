import threading
import unittest
from unittest.mock import Mock
from test_gd101_transmit import session
from gd101_transactions import operation_lock

class QueuedKLineTests(unittest.TestCase):
    def test_checksum_snapshot_and_modes(self):
        for proto in (3,4):
            for no_checksum in (False,True):
                d=session([]);sent=threading.Event()
                d.write_kline_wire=Mock(side_effect=lambda *a,**k:sent.set())
                try:
                    with operation_lock(d):
                        data=bytearray(b'\x81\x33\xf1')
                        h=d.queue_kline(data,protocol=proto,no_checksum=no_checksum,timing=2)
                        data[0]=0
                    self.assertTrue(sent.wait(1));d.clear_tx_queue()
                    call=d.write_kline_wire.call_args
                    self.assertEqual(call.args[0],b'\x81\x33\xf1'+(b'' if no_checksum else b'\xa5'))
                    self.assertEqual(call.kwargs['timing'],2)
                    self.assertEqual(d.queued_results(),[(h,'completed',None)])
                finally:d._async_writer.close()

    def test_native_completion_and_receive_window(self):
        d=session([bytes.fromhex('430b0000000200000000')]);sent=threading.Event()
        d.configure_kline_runtime(p2_max=400)
        d.send_packet.side_effect=lambda *a,**k:sent.set()
        try:
            h=d.queue_kline(b'\x81')
            self.assertTrue(sent.wait(1));d.clear_tx_queue()
            self.assertEqual(d.queued_results(),[(h,'completed',None)])
            self.assertFalse(d.tx_uncertain)
            self.assertGreater(d._kline_response_window._until,0)
            packet=d.send_packet.call_args.args[0]
            self.assertEqual(packet[-2:],b'\x81\x81')
        finally:d._async_writer.close()

    def test_invalid_queue_input_starts_no_worker(self):
        d=session([])
        for data,proto,timing in [(b'',4,5),(bytes(4097),4,5),(b'x',5,5),(b'x',4,-1)]:
            with self.assertRaises(ValueError):d.queue_kline(data,protocol=proto,timing=timing)
        self.assertFalse(hasattr(d,'_async_writer'));d.send_packet.assert_not_called()

if __name__=='__main__':unittest.main()
