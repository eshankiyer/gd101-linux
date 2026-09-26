import unittest
from gd101_abi import LifecycleProvider
from gd101_driver import GD101Error
from test_gd101_initialization import driver

class DetectedRateTests(unittest.TestCase):
    def test_only_iso14230_updates_from_successful_handshake(self):
        for protocol in (None,3,4):
            d=driver([bytes.fromhex('41020000'),bytes.fromhex('43050000802500000808')])
            d.kline_baudrate=10400;d.kline_parity=2
            self.assertEqual(d.initialize_kline('five_baud',b'\x33',protocol=protocol),b'\x08\x08')
            self.assertEqual(d.kline_baudrate,9600 if protocol==4 else 10400)
            self.assertEqual(d.kline_parity,2)

    def test_failed_or_truncated_reply_preserves_reported_rate(self):
        for reply in (bytes.fromhex('43050007802500000808'),bytes.fromhex('4305000080250000')):
            d=driver([bytes.fromhex('41020000'),reply]);d.kline_baudrate=10400
            with self.assertRaises(GD101Error):d.initialize_kline('five_baud',b'\x33',protocol=4)
            self.assertEqual(d.kline_baudrate,10400);self.assertTrue(d.init_uncertain)

    def test_provider_readback_tracks_detected_rate(self):
        d=driver([bytes.fromhex('41020000'),bytes.fromhex('43050000802500000808')]);d.kline_baudrate=10400
        p=LifecycleProvider();p.session=d;p.channel=1;p.protocol=4;p.baudrate=10400
        self.assertEqual(p.five_baud_init(1,b'\x33'),(0,b'\x08\x08'))
        self.assertEqual(p.configure_timing(1,parameters=[1]),(0,[9600]))

if __name__=='__main__':unittest.main()
