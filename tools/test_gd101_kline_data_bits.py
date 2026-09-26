import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider

class DataBitsTests(unittest.TestCase):
    def test_original_fixed_data_bits_and_no_hardware_changes(self):
        for protocol in (3,4):
            p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
            _,c=p.connect(1,protocol,0,10400)
            self.assertEqual(p.configure_timing(c,parameters=[1,22,32]),(0,[10400,0,0]))
            for value in (0,256,65536):
                self.assertEqual(p.configure_timing(c,pairs=[(32,value)])[0],0)
                self.assertEqual(p.configure_timing(c,parameters=[32]),(0,[0]))
            p.session.configure_kline_runtime.assert_not_called()
            self.assertEqual(p.initialization_timings(),{})

    def test_invalid_final_value_rejects_whole_list(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        _,c=p.connect(1,4,0,10400)
        for value in (1,7,8,255,257,0xffffffff):
            self.assertEqual(p.configure_timing(c,pairs=[(1,9600),(22,2),(32,value)])[0],10)
            p.session.configure_kline_runtime.assert_not_called()
            self.assertEqual(p.configure_timing(c,parameters=[1,22,32]),(0,[10400,0,0]))
        self.assertEqual(p.configure_timing(c,pairs=[(32,1),(32,0)])[0],0)

if __name__=='__main__':unittest.main()
