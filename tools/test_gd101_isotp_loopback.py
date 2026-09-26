import unittest
from gd101_isotp import ISOTPTransmitDone,ISOTPTransmitEcho,ISOTPReassembler,make_flow_entry
from gd101_receiver import PacketReceiver
import test_gd101_periodic_isotp as periodic_helpers
import test_gd101_isotp_config as config_helpers
from gd101_abi import LifecycleProvider,install
from test_gd101_abi_deadline import Exports
from unittest.mock import Mock

class LoopbackTests(unittest.TestCase):
    def test_configuration_boolean_and_filter_persistence(self):
        p=config_helpers.ISOConfigTests().provider();c=p.channel
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,0xffffffff)])[0],0)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
        self.assertEqual(p.start_flow_filter(c,6,0,b'\xff'*4,bytes.fromhex('000007e8'),bytes.fromhex('000007e0'))[0],0)
        self.assertEqual(p.session.configure_isotp_routes.call_args.kwargs['config'][4],1)
        for invalid in (-1,0x100000000):
            self.assertEqual(p.configure_timing(c,pairs=[(3,invalid)])[0],10)
        self.assertEqual(p.configure_timing(c,pairs=[(3,0)])[0],0)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))

    def test_host_only_update_preserves_incoming_multiframe_response(self):
        import time
        from gd101_isotp import FlowRoute
        from test_gd101_isotp_route_update import record
        d=periodic_helpers.PeriodicISOTests().session()
        d.receiver=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        r=d.receiver;now=time.monotonic()
        r._accept_isotp(record(0x7e8,b'\x10\x0aabcdef'),now)
        pending=r.isotp.pending[0]
        d.configure_isotp_loopback(True)
        self.assertIs(r.isotp.pending[0],pending)
        self.assertFalse(r.isotp_updating)
        self.assertEqual(d.flow_config[4],1)
        d.send_packet.assert_not_called()
        r._accept_isotp(record(0x7e8,b'\x21ghij'),now+.01)
        self.assertEqual(r.read_message(0).data,b'abcdefghij')

    def test_provider_loopback_uses_no_route_reprogramming_and_commits_after_success(self):
        p=config_helpers.ISOConfigTests().provider();c=p.channel
        p.session.configure_isotp_routes.reset_mock()
        p.session.configure_isotp_loopback.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(3,1)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[0]))
        p.session.configure_isotp_loopback.side_effect=None
        self.assertEqual(p.configure_timing(c,pairs=[(3,1)])[0],0)
        p.session.configure_isotp_routes.assert_not_called()
        p.session.configure_isotp_loopback.assert_called_with(True)
        self.assertEqual(p.configure_timing(c,parameters=[3]),(0,[1]))
        self.assertEqual(p.configure_timing(c,pairs=[(3,0),(30,8)])[0],0)
        self.assertEqual(p.session.configure_isotp_routes.call_count,1)
        self.assertEqual(p.session.configure_isotp_routes.call_args.kwargs['config'][4:6],bytes([0,8]))

    def test_ack_then_echo_all_address_modes_and_lengths(self):
        for extended in (False,True):
            for address in (None,0xbb):
                for size in (2,512,4095):
                    with self.subTest(extended=extended,address=address,size=size):
                        d=periodic_helpers.PeriodicISOTests().session()
                        tx=0x18da10f1 if extended else 0x7e0
                        d.flow_entries={0:make_flow_entry(tx,0x7e8,extended=extended,transmit_address=address,receive_address=address)}
                        cfg=bytearray(d.flow_config);cfg[4]=1;d.flow_config=bytes(cfg)
                        d.receiver=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({},timeout=1))
                        d.receive_packet.side_effect=None
                        d.receive_packet.return_value=bytes.fromhex('430a0000007856341200' if size>7 else '43000000007856341200')
                        payload=bytes(i%256 for i in range(size))
                        d.write_isotp_message(0,payload)
                        self.assertEqual(d.receiver.read_message(0),ISOTPTransmitDone(tx,extended,address,0x12345678))
                        echo=d.receiver.read_message(0)
                        self.assertEqual(echo,ISOTPTransmitEcho(tx,extended,address,0x12345678,payload))
                        self.assertEqual(echo.fields(),(tx.to_bytes(4,'big')+(bytes([address]) if address is not None else b'')+payload,1|(256 if extended else 0)|(128 if address is not None else 0),0x12345678))

    def test_echo_linux_abi_and_immutable_payload(self):
        data=bytearray(b'\x3e\x80');echo=ISOTPTransmitEcho(0x7e0,False,None,12,data);data[0]=0
        p=LifecycleProvider();p.channel=1;p.protocol=6;p.session=Mock();p.session.read_message.return_value=echo
        ffi=Exports();install(ffi,p);m=ffi.new('GD101_MESSAGE *');count=ffi.new('uint32_t *',1)
        self.assertEqual(ffi.functions['PassThruReadMsgs'](1,m,count,0),0)
        self.assertEqual((m.RxStatus,m.DataSize,m.Timestamp),(1,6,12))
        self.assertEqual(bytes(ffi.buffer(m.Data,6)),bytes.fromhex('000007e03e80'))

if __name__=='__main__':unittest.main()
