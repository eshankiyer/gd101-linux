import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider
from gd101_native import build_kline_open_payload
from test_gd101_transmit import session

class ParityTests(unittest.TestCase):
    def test_native_parity_and_rate_preserve_each_other(self):
        d=session([]);d.kline_baudrate=10400;d.kline_parity=0
        d.exchange=Mock(return_value=b'\x41\x01\0\0')
        for parity in (1,2,0):
            d.configure_kline_runtime(parity=parity)
            self.assertEqual(d.exchange.call_args.args[0],build_kline_open_payload(10400,parity=parity)+b'\0')
        d.configure_kline_runtime(parity=2,baudrate=9600)
        d.configure_kline_runtime(baudrate=10400)
        self.assertEqual(d.exchange.call_args.args[0][-2],8)
        self.assertEqual((d.kline_baudrate,d.kline_parity),(10400,2))
        d.exchange.reset_mock();d.configure_kline_runtime(parity=2)
        d.exchange.assert_not_called()

    def test_rejected_change_keeps_parity_and_host_settings(self):
        d=session([]);d.kline_baudrate=10400;d.kline_parity=0;d.kline_loopback=0
        d.exchange=Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):d.configure_kline_runtime(parity=2,loopback=1)
        self.assertEqual((d.kline_parity,d.kline_loopback),(0,0));self.assertTrue(d.tx_uncertain)

    def test_parity_and_loopback_do_not_add_initialization_timing_overrides(self):
        p=LifecycleProvider();p.protocol=4;p.timing_config={22:2,3:1}
        self.assertEqual(p.initialization_timings(),{})
        p.timing_config[20]=70
        self.assertEqual(p.initialization_timings()['timing_words'][2],70)

    def test_provider_validation_readback_and_mixed_updates(self):
        for protocol in (3,4):
            p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
            _,c=p.connect(1,protocol,0,10400)
            self.assertEqual(p.configure_timing(c,parameters=[22]),(0,[0]))
            self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(22,3),(3,1)])[0],10)
            p.session.configure_kline_runtime.assert_not_called()
            self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(22,0x102),(3,1)])[0],0)
            p.session.configure_kline_runtime.assert_called_with(baudrate=9600,parity=2,loopback=1)
            self.assertEqual(p.configure_timing(c,parameters=[1,22,3]),(0,[9600,2,1]))

if __name__=='__main__':unittest.main()
