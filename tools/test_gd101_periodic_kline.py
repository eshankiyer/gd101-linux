import threading
import unittest
from unittest.mock import Mock
from test_gd101_transmit import session

class PeriodicKLineTests(unittest.TestCase):
    def test_checksum_modes_and_protocols(self):
        for protocol in (3,4):
            for no_checksum in (False,True):
                d=session([]);sent=threading.Event()
                d.write_kline_wire=Mock(side_effect=lambda *a,**k:sent.set())
                try:
                    h=d.start_periodic_kline(b'\x81\x33\xf1',100,protocol=protocol,no_checksum=no_checksum,timing=2)
                    self.assertTrue(sent.wait(1));d.stop_periodic(h)
                    call=d.write_kline_wire.call_args
                    self.assertEqual(call.args[0],b'\x81\x33\xf1'+(b'' if no_checksum else b'\xa5'))
                    self.assertEqual(call.kwargs['timing'],2)
                    self.assertEqual(d.write_kline_wire.call_count,1)
                finally:
                    if hasattr(d,'_periodic_scheduler'):d._periodic_scheduler.close()

    def test_minimum_and_invalid_payloads(self):
        d=session([])
        for data,interval,p2 in [(b'x',99,110),(b'x',199,400),(b'',100,110),(bytes(4097),100,110)]:
            with self.assertRaises(ValueError):d.start_periodic_kline(data,interval,p2_max=p2)
        self.assertFalse(hasattr(d,'_periodic_scheduler'));d.send_packet.assert_not_called()

    def test_wire_completion_and_disconnect(self):
        d=session([bytes.fromhex('430b0000000200000000')]);sent=threading.Event()
        d.send_packet.side_effect=lambda *a,**k:sent.set()
        d.exchange=Mock(return_value=bytes.fromhex('41010000'))
        try:
            d.start_periodic_kline(b'\x81',1000)
            self.assertTrue(sent.wait(1));d.disconnect_kline()
            self.assertFalse(d.kline_open);self.assertIsNone(d._periodic_scheduler)
            self.assertFalse(d.tx_uncertain);self.assertEqual(d.send_packet.call_count,1)
        finally:
            if getattr(d,'_periodic_scheduler',None):d._periodic_scheduler.close()

if __name__=='__main__':unittest.main()
