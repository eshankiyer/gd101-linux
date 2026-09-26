import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider
from gd101_driver import GD101Error
from gd101_kline import KLINE_BAUD_RATES
from gd101_native import build_kline_open_payload
from test_gd101_transmit import session

class KLineRateTests(unittest.TestCase):
    def test_all_original_rates_open_and_readback(self):
        self.assertEqual(len(KLINE_BAUD_RATES),20)
        for protocol in (3,4):
            for rate in KLINE_BAUD_RATES:
                p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
                status,c=p.connect(1,protocol,0,rate)
                self.assertEqual(status,0)
                p.session.connect_kline.assert_called_once_with(rate)
                self.assertEqual(p.configure_timing(c,parameters=[1]),(0,[rate]))

    def test_native_change_ack_and_noop(self):
        d=session([]);d.kline_baudrate=10400;d.kline_loopback=0
        d.exchange=Mock(return_value=b'\x41\x01\0\0')
        d.configure_kline_runtime(baudrate=9600,loopback=1,timing=10)
        d.exchange.assert_called_once_with(build_kline_open_payload(9600)+b'\0',b'\x41\x01\0',timeout=2)
        self.assertEqual((d.kline_baudrate,d.kline_loopback,d._periodic_kline_timing),(9600,1,10))
        d.configure_kline_runtime(baudrate=9600)
        self.assertEqual(d.exchange.call_count,1)

    def test_failed_ack_keeps_settings_and_inhibits_send(self):
        for reply in (TimeoutError('lost ACK'),b'\x41\x01\x07\0',b'\x41\x01\0'):
            d=session([]);d.kline_baudrate=10400;d.kline_loopback=0
            d.exchange=Mock()
            if isinstance(reply,Exception):d.exchange.side_effect=reply
            else:d.exchange.return_value=reply
            with self.assertRaises(Exception):d.configure_kline_runtime(baudrate=9600,loopback=1)
            self.assertEqual((d.kline_baudrate,d.kline_loopback),(10400,0))
            self.assertTrue(d.tx_uncertain)
            with self.assertRaises(GD101Error):d.write_kline_wire(b'abc')
            with self.assertRaises(GD101Error):d.configure_kline_runtime(baudrate=9600)
            d.exchange.assert_called_once()

    def test_rate_change_waits_for_response_completion(self):
        import threading
        from gd101_response_window import KLineResponseWindow
        d=session([]);d.kline_baudrate=10400;d.exchange=Mock(return_value=b'\x41\x01\0\0')
        window=KLineResponseWindow(0);window.receive_state(True)
        d._kline_response_window=window
        entered=threading.Event();done=threading.Event();errors=[]
        def change():
            entered.set()
            try:d.configure_kline_runtime(baudrate=9600)
            except Exception as error:errors.append(error)
            finally:done.set()
        thread=threading.Thread(target=change);thread.start()
        try:
            self.assertTrue(entered.wait(1));self.assertFalse(done.wait(.03))
            d.exchange.assert_not_called();self.assertFalse(d.tx_uncertain)
            window.receive_state(False,completed=True)
            self.assertTrue(done.wait(1));thread.join(1)
            self.assertEqual(errors,[]);self.assertEqual(d.kline_baudrate,9600)
            d.exchange.assert_called_once()
        finally:window.receive_state(False,completed=True);thread.join(3)

    def test_failed_response_window_never_sends_or_mutates_settings(self):
        for failure in (TimeoutError('window expired'),RuntimeError('reader failed')):
            d=session([]);d.kline_baudrate=10400;d.kline_loopback=0
            d.exchange=Mock();d._kline_response_window=Mock()
            d._kline_response_window.wait.side_effect=failure
            with self.assertRaises(type(failure)):d.configure_kline_runtime(baudrate=9600,loopback=1)
            d.exchange.assert_not_called()
            self.assertFalse(d.tx_uncertain)
            self.assertEqual((d.kline_baudrate,d.kline_loopback),(10400,0))

    def test_rejected_open_never_publishes_channel(self):
        d=session([]);d.kline_open=False;d.can_open=False
        d.exchange=Mock(return_value=b'\x41\x01\x07\0')
        with self.assertRaises(GD101Error):d.connect_kline(9600)
        self.assertFalse(d.kline_open)

    def test_provider_mixed_validation_and_failed_publication(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,4,0,10400)
        self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(3,2)])[0],10)
        p.session.configure_kline_runtime.assert_not_called()
        p.session.configure_kline_runtime.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(3,1)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[10400,0]))
        p.session.configure_kline_runtime.side_effect=None
        self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(3,1)])[0],0)
        p.session.configure_kline_runtime.assert_called_with(baudrate=9600,loopback=1)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[9600,1]))

if __name__=='__main__':unittest.main()
