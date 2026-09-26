import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider


class CANConnectFlagsTests(unittest.TestCase):
    def test_channel_flags_control_filter_width(self):
        for flags in (0,0x100,0x800,0x900):
            p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
            p.session.start_can_filter.return_value=42
            status,c=p.connect(1,5,flags,500000)
            self.assertEqual(status,0)
            for extended in (False,True):
                p.session.start_can_filter.reset_mock()
                allowed=bool(flags&0x800) or extended==bool(flags&0x100)
                result=p.start_filter(c,1,b'\xff'*4,b'\0\0\x07\xe8',0x100 if extended else 0)
                self.assertEqual(result,(0,42) if allowed else (10,0))
                if not allowed:p.session.start_can_filter.assert_not_called()

    def test_unknown_flags_do_not_open_hardware(self):
        p=LifecycleProvider();p.device=1;p.session=Mock()
        self.assertEqual(p.connect(1,5,0x200,500000),(6,0))
        p.session.connect_can.assert_not_called()


if __name__=='__main__':unittest.main()
