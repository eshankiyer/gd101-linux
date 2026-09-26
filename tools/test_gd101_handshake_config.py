import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider
from test_gd101_initialization import driver

class HandshakeConfigTests(unittest.TestCase):
    def test_protocol_specific_timing_field_and_validation(self):
        for protocol,parameter,wrong in ((3,18,25),(4,25,18)):
            p=LifecycleProvider();p.protocol=protocol;p.channel=1;p.session=Mock()
            values=[(parameter,401),(14,402),(15,11),(16,12),(17,13),(33,2)]
            self.assertEqual(p.configure_timing(1,pairs=values)[0],0)
            self.assertEqual(p.configure_timing(1,parameters=[k for k,v in values]),(0,[v for k,v in values]))
            before=dict(p.timing_config)
            self.assertEqual(p.configure_timing(1,pairs=[(14,400),(wrong,5)])[0],1)
            self.assertEqual(p.configure_timing(1,pairs=[(33,256)])[0],10)
            self.assertEqual(p.timing_config,before)
            p.session.kline_baudrate=10400;p.session.initialize_kline.return_value=b'\x08\x08'
            self.assertEqual(p.five_baud_init(1,b'\x33')[0],0)
            self.assertEqual(p.session.initialize_kline.call_args.kwargs,
                {'protocol':protocol,'no_checksum':False,'timing_words':(5,300,25,50,401,402,11,12,13),'option':2})

    def test_native_five_baud_packet_fields_and_timeout(self):
        d=driver([bytes.fromhex('41020000'),bytes.fromhex('43050000a02800000808')])
        self.assertEqual(d.initialize_kline('five_baud',b'\x33',
            timing_words=(5,300,25,50,401,402,11,12,13),option=2),b'\x08\x08')
        self.assertEqual(d.exchange.call_args_list[0].args[0],bytes.fromhex('010200910192010833000000'))
        self.assertEqual(d.exchange.call_args_list[1].args[0],bytes.fromhex('030200020b000c000d000d00'))
        self.assertEqual(d.exchange.call_args_list[1].kwargs['timeout'],2.939)

if __name__=='__main__':unittest.main()
