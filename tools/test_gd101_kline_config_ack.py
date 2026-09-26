import unittest
from unittest.mock import Mock
from gd101_kline import validate_kline_config_ack
from gd101_driver import GD101Error
from test_gd101_transmit import session

class ConfigAckTests(unittest.TestCase):
    def test_status_and_trailing_byte_are_distinct(self):
        for closing in (False,True):
            validate_kline_config_ack(b'\x41\x01\0\xff',closing=closing)
            with self.assertRaises(ValueError):validate_kline_config_ack(b'\x41\x01\x07\0',closing=closing)
            with self.assertRaises(ValueError):validate_kline_config_ack(b'\x41\x02\0\0',closing=closing)
        validate_kline_config_ack(b'\x41\x01\0',closing=True)
        with self.assertRaises(ValueError):validate_kline_config_ack(b'\x41\x01\0')

    def test_open_and_rate_ignore_nonstatus_trailing_byte(self):
        d=session([]);d.kline_open=False;d.can_open=False
        d.exchange=Mock(return_value=b'\x41\x01\0\xff')
        d.connect_kline(10400)
        self.assertTrue(d.kline_open)
        d.configure_kline_runtime(baudrate=9600)
        self.assertEqual(d.kline_baudrate,9600);self.assertFalse(d.tx_uncertain)
        d.disconnect_kline();self.assertFalse(d.kline_open)

    def test_failed_close_preserves_open_state(self):
        d=session([]);d.exchange=Mock(return_value=b'\x41\x01\x07\0')
        with self.assertRaises(GD101Error):d.disconnect_kline()
        self.assertTrue(d.kline_open)

if __name__=='__main__':unittest.main()
