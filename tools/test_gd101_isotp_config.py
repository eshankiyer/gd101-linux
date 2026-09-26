import unittest
from unittest.mock import Mock
from gd101_abi import LifecycleProvider,install
from gd101_isotp import default_flow_config,build_flow_table_packet
from test_gd101_abi_deadline import Exports

class ISOConfigTests(unittest.TestCase):
    def provider(self):
        p=LifecycleProvider();p.device=1;p.session=Mock();p.session.receiver=None
        self.assertEqual(p.connect(1,6,0,500000)[0],0)
        return p

    def test_settings_reach_packet_and_survive_filter_changes(self):
        p=self.provider();c=p.channel
        self.assertEqual(p.configure_timing(c,parameters=[30,31,43]),(0,[0,0,0]))
        self.assertEqual(p.configure_timing(c,pairs=[(30,8),(31,0xf1),(43,0xaa)])[0],0)
        config=p.session.configure_isotp_routes.call_args.kwargs['config']
        packet=build_flow_table_packet({},config)
        self.assertEqual(packet[11:13],bytes([8,0xf1]))
        self.assertEqual(packet[9],0xaa)
        status,handle=p.start_flow_filter(c,6,0x40,b'\xff'*4,bytes.fromhex('000007e8'),bytes.fromhex('000007e0'))
        self.assertEqual(status,0)
        self.assertEqual(p.session.configure_isotp_routes.call_args.kwargs['config'],config)
        self.assertEqual(p.stop_filter(c,handle),0)
        self.assertEqual(p.session.configure_isotp_routes.call_args.kwargs['config'],config)

    def test_validation_and_timeout_do_not_publish(self):
        p=self.provider();c=p.channel
        for pairs,status in [([(30,8),(31,0x100000000)],10), ([(30,8),(99,0)],1)]:
            p.session.configure_isotp_routes.reset_mock()
            self.assertEqual(p.configure_timing(c,pairs=pairs)[0],status)
            p.session.configure_isotp_routes.assert_not_called()
        p.session.configure_isotp_routes.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(30,8)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[30]),(0,[0]))
        self.assertIsNone(p.isotp_config)

    def test_linux_config_and_reconnect_defaults(self):
        p=self.provider();ffi=Exports();install(ffi,p)
        entries=ffi.new('GD101_CONFIG[]',[{'Parameter':30,'Value':16},{'Parameter':43,'Value':85}])
        config=ffi.new('GD101_CONFIG_LIST *',{'NumOfParams':2,'ConfigPtr':entries})
        ioctl=ffi.functions['PassThruIoctl']
        self.assertEqual(ioctl(p.channel,2,config,ffi.NULL),0)
        entries[0].Value=entries[1].Value=0
        self.assertEqual(ioctl(p.channel,1,config,ffi.NULL),0)
        self.assertEqual([e.Value for e in entries],[16,85])
        self.assertEqual(p.disconnect(p.channel),0)
        self.assertEqual(p.connect(1,6,0,500000)[0],0)
        self.assertEqual(p.configure_timing(p.channel,parameters=[30,43]),(0,[0,0]))

    def test_word_settings_native_layout_and_validation(self):
        p=self.provider();c=p.channel
        parameters=[34,35,37,46,49]
        self.assertEqual(p.configure_timing(c,parameters=parameters),(0,[65535,65535,0,1000,1000]))
        values=[0x1234,0x5678,9,0x2345,0x6789]
        self.assertEqual(p.configure_timing(c,pairs=list(zip(parameters,values)))[0],0)
        config=p.session.configure_isotp_routes.call_args.kwargs['config']
        packet=build_flow_table_packet({},config)
        self.assertEqual(packet[2:7],bytes.fromhex('8967452309'))
        self.assertEqual(packet[13:17],bytes.fromhex('34127856'))
        self.assertEqual(p.configure_timing(c,parameters=parameters),(0,values))
        for parameter in parameters:
            before=p.isotp_config;p.session.configure_isotp_routes.reset_mock()
            invalid=0x100000000
            self.assertEqual(p.configure_timing(c,pairs=[(30,1),(parameter,invalid)])[0],10)
            self.assertEqual(p.isotp_config,before)
            p.session.configure_isotp_routes.assert_not_called()

    def test_uint32_values_are_truncated_to_original_field_widths(self):
        p=self.provider();c=p.channel
        fields=[(30,1),(31,1),(43,1),(34,2),(35,2),(37,1),(46,2),(49,2)]
        for value in (256,65536,0x12345678,0xffffffff):
            pairs=[(parameter,value) for parameter,width in fields]
            self.assertEqual(p.configure_timing(c,pairs=pairs)[0],0)
            expected=[value&((1<<(8*width))-1) for parameter,width in fields]
            self.assertEqual(p.configure_timing(c,parameters=[p for p,w in fields]),(0,expected))
        for value in (-1,0x100000000):
            before=p.isotp_config;p.session.configure_isotp_routes.reset_mock()
            self.assertEqual(p.configure_timing(c,pairs=[(30,1),(49,value)])[0],10)
            self.assertEqual(p.isotp_config,before)
            p.session.configure_isotp_routes.assert_not_called()

if __name__=='__main__':unittest.main()
