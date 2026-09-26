import unittest
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports

class PinControlTests(unittest.TestCase):
    def test_active_modes_and_output_tracking(self):
        d=object.__new__(GD101);d.exchange=Mock(return_value=b'\x41\x05\0\0')
        for pin,value,wire in ((9,0xfffffffe,b'\xfe\xff'),(12,12000,b'\xe0\x2e'),(13,18000,b'PF')):
            d.set_programming_voltage(pin,value)
            d.exchange.assert_called_with(bytes([1,5,pin])+wire+b'\0',b'\x41\x05',timeout=2)
        self.assertEqual(d.active_programming_pins,{9,12,13})
        d.disable_programming_voltage(12)
        self.assertEqual(d.active_programming_pins,{9,13})
        d.exchange.return_value=b'\x41\x05\x01'
        with self.assertRaises(GD101Error):d.set_programming_voltage(12,12000)
        self.assertEqual(d.active_programming_pins,{9,13})

    def test_shutdown_disables_acknowledged_outputs(self):
        from test_gd101_close_failures import session
        d=session();d.exchange=Mock(return_value=b'\x41\x05\0\0')
        d.set_programming_voltage(12,12000);d.set_programming_voltage(9,0xfffffffe)
        d.close()
        self.assertEqual(d.active_programming_pins,set())
        self.assertEqual([c.args[0] for c in d.exchange.call_args_list[-2:]],
                         [bytes([1,5,9,0,0,0]),bytes([1,5,12,0,0,0])])

    def test_uncertain_output_shutdown_reports_failure_without_retry(self):
        from test_gd101_close_failures import session
        d=session();d.exchange=Mock(side_effect=TimeoutError('lost ACK'))
        with self.assertRaises(TimeoutError):d.set_programming_voltage(12,12000)
        p=LifecycleProvider();p.device=1;p.session=d
        self.assertEqual(p.close(1),7)
        self.assertEqual(d.active_programming_pins,{12})
        d.exchange.assert_called_once()
        self.assertIn('pin_close_error',d.record)

    def test_native_command_and_ack(self):
        d=object.__new__(GD101);d.exchange=Mock(return_value=b'\x41\x05\0\0')
        for pin in (9,12,13):
            d.disable_programming_voltage(pin)
            d.exchange.assert_called_with(bytes((1,5,pin,0,0,0)),b'\x41\x05',timeout=2)
            self.assertFalse(d.pin_uncertain)
        with self.assertRaises(ValueError):d.disable_programming_voltage(7)
        self.assertEqual(d.exchange.call_count,3)

    def test_uncertain_failure_does_not_retry(self):
        for failure in (TimeoutError(),GD101Error('Bad framing')):
            d=object.__new__(GD101);d.exchange=Mock(side_effect=failure)
            with self.assertRaises(type(failure)):d.disable_programming_voltage(9)
            with self.assertRaises(GD101Error):d.disable_programming_voltage(9)
            self.assertEqual(d.exchange.call_count,1)
            self.assertTrue(d.pin_uncertain)
        d=object.__new__(GD101);d.exchange=Mock(return_value=b'\x41\x05')
        with self.assertRaises(GD101Error):d.disable_programming_voltage(9)
        self.assertTrue(d.pin_uncertain)

    def test_rejection_is_not_success(self):
        d=object.__new__(GD101);d.exchange=Mock(return_value=b'\x41\x05\x01\0')
        with self.assertRaises(GD101Error):d.disable_programming_voltage(9)
        self.assertFalse(d.pin_uncertain)

    def test_abi_validation(self):
        ffi=Exports();p=LifecycleProvider();p.device=1;p.session=Mock();install(ffi,p)
        call=ffi.functions['PassThruSetProgrammingVoltage']
        self.assertEqual(call(2,9,0xffffffff),26)
        self.assertEqual(call(1,7,0xffffffff),19)
        self.assertEqual(call(1,9,12000),19)
        self.assertEqual(call(1,12,0xfffffffe),19)
        self.assertEqual(call(1,9,0xfffffffe),0)
        p.session.set_programming_voltage.assert_called_with(9,0xfffffffe)
        self.assertEqual(call(1,12,12000),0)
        p.session.set_programming_voltage.assert_called_with(12,12000)
        p.session.disable_programming_voltage.assert_not_called()
        self.assertEqual(call(1,9,0xffffffff),0)
        p.session.disable_programming_voltage.assert_called_once_with(9)
        p.session.disable_programming_voltage.side_effect=TimeoutError()
        self.assertEqual(call(1,9,0xffffffff),9)
        p.session.disable_programming_voltage.side_effect=GD101Error('Rejected')
        self.assertEqual(call(1,9,0xffffffff),7)

if __name__=='__main__':unittest.main()
