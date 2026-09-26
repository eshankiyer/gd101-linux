import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from gd101_devices import select_gd101_port
from gd101_driver import GD101,authenticate_native,GD101Error


def port(device,serial='MT000418',vid=0xe327,pid=0x2534):
    return SimpleNamespace(device=device,serial_number=serial,vid=vid,pid=pid)

class DeviceTests(unittest.TestCase):
    def test_matching_identity_only(self):
        ports=[port('/dev/other',vid=1),port('/dev/second',serial='MT999999'),port('/dev/ttyACM7')]
        self.assertEqual(select_gd101_port('MT000418',ports=ports),'/dev/ttyACM7')
        for requested in ('/dev/other','/dev/second','/dev/missing'):
            with self.assertRaises(RuntimeError):select_gd101_port('MT000418',ports=ports,requested=requested)
        with self.assertRaises(RuntimeError):select_gd101_port('MT000418',ports=[])
        with self.assertRaises(ValueError):select_gd101_port('bad',ports=ports)

    def test_ambiguity_and_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);device=path/'device';device.touch();alias=path/'alias';alias.symlink_to(device)
            ports=[port(str(device)),port(str(alias))]
            self.assertEqual(select_gd101_port('MT000418',ports=ports,requested=alias),str(alias))
            select_gd101_port('MT000418',ports=ports)
            ports.append(port(str(path/'second')))
            with self.assertRaises(RuntimeError):select_gd101_port('MT000418',ports=ports)
            self.assertEqual(select_gd101_port('MT000418',ports=ports,requested=device),str(device))

    def test_session_uses_selected_path_without_opening_port(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);auth=path/'auth.json';auth.write_text(json.dumps({'adapter_serial':'MT123456'}))
            with patch('gd101_devices.select_gd101_port',return_value='/dev/ttyACM7') as select,patch('gd101_driver.prepare_serial') as prepare:
                d=GD101(path/'capture.json',auth_file=auth,port_path='/dev/chosen')
                select.assert_called_once_with('MT123456',requested='/dev/chosen')
                prepare.assert_called_once_with('/dev/ttyACM7')
                prepare.return_value.open.assert_not_called()
                self.assertEqual(d.record['port'],'/dev/ttyACM7')
                self.assertEqual(d.adapter_serial,'MT123456')

    def test_auth_identity_change_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            auth=Path(temp)/'auth.json';auth.write_text(json.dumps({'adapter_serial':'MT123456'}))
            serial=Mock()
            with self.assertRaises(GD101Error):authenticate_native(serial,{},auth,expected_serial='MT000418')
            serial.write.assert_not_called()

if __name__=='__main__':unittest.main()
