import unittest
from unittest.mock import Mock
from gd101_isotp import ISOTPReassembler,FlowRoute
from gd101_receiver import PacketReceiver
from gd101_can import build_can_open_payload,default_can_descriptor
from gd101_driver import GD101Error
import test_gd101_periodic_isotp as helpers
import test_gd101_isotp_config as config_helpers
from test_gd101_isotp_route_update import record

class ISORateTests(unittest.TestCase):
    def test_deferred_timing_survives_until_rate_update(self):
        d=self.session([b'\x41\0\0\0',b'\x41\x0b\0\0',b'\x41\x04\0\0'])
        reader=d.receiver
        d.configure_isotp_loopback(False,timing=(75,20))
        d.send_packet.assert_not_called();self.assertIs(d.receiver,reader)
        d.configure_isotp_routes(d.flow_entries,config=d.flow_config,bitrate=250000)
        descriptor=bytearray(default_can_descriptor(250000));descriptor[8:10]=bytes([75,20])
        self.assertEqual(d.send_packet.call_args_list[0].args[0],build_can_open_payload(descriptor))
        self.assertEqual(d.can_timing,(75,20))
        d.receive_packet.side_effect=TimeoutError('lost ACK')
        with self.assertRaises(TimeoutError):
            d.configure_isotp_routes(d.flow_entries,bitrate=125000,timing=(90,10))
        self.assertEqual(d.can_timing,(75,20))

    def test_provider_timing_validation_and_atomicity(self):
        p=config_helpers.ISOConfigTests().provider();c=p.channel
        p.session.configure_isotp_routes.reset_mock()
        self.assertEqual(p.configure_timing(c,pairs=[(23,101),(23,331),(24,276)])[0],0)
        p.session.configure_isotp_routes.assert_not_called()
        p.session.configure_isotp_loopback.assert_called_once_with(False,timing=(75,20))
        self.assertEqual(p.configure_timing(c,parameters=[24,1,23]),(0,[20,500000,75]))
        self.assertEqual(p.configure_timing(c,pairs=[(23,101),(1,250000)])[0],10)
        p.session.configure_isotp_routes.assert_not_called()
        p.session.configure_isotp_routes.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(23,90)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[1,23,24]),(0,[500000,75,20]))

    def session(self,replies):
        d=helpers.PeriodicISOTests().session();d.can_bitrate=500000
        d.receiver=PacketReceiver(None,bytes(32),isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        d.receive_packet.side_effect=replies
        return d

    def test_rate_then_tables_publish_together(self):
        d=self.session([b'\x41\0\0\0',b'\x41\x0b\0\0',b'\x41\x04\0\0'])
        d.receiver._accept_isotp(record(0x7e8,b'\x01A'),0)
        d.receiver._accept_isotp(record(0x7e8,b'\x10\x0aabcdef'),0)
        d.configure_isotp_routes(d.flow_entries,standard=[(0x7ff,0x7e8)],config=d.flow_config,bitrate=250000)
        calls=d.send_packet.call_args_list
        self.assertEqual(calls[0].args[0],build_can_open_payload(default_can_descriptor(250000)))
        self.assertEqual([c.kwargs['extended'] for c in calls],[False,True,False])
        self.assertEqual(d.can_bitrate,250000);self.assertFalse(d.flow_uncertain)
        self.assertFalse(d.receiver.isotp_updating);self.assertFalse(d.receiver.isotp.pending)
        self.assertEqual(d.receiver.read_message(0).data,b'A')
        self.assertEqual(d.receiver.isotp.routes,{0:FlowRoute(0x7e8)})

    def test_encrypted_transport_and_reader_across_rate_transition(self):
        from test_gd101_receiver import FakePort
        from gd101_native import encode_short,decode_frame
        from gd101_driver import GD101
        from collections import deque
        key=bytes(range(32));requests=[]
        class Port(FakePort):
            def flush(self):pass
            def write(self,wire):
                payload=decode_frame(wire,key);requests.append(payload)
                if len(requests)==1:
                    self_rate=int.from_bytes(payload[5:9],'little')
                    if self_rate!=250000:raise AssertionError('Wrong rate on wire')
                    reply=b'\x41\0\0\0'
                else:reply=bytes([0x41,11 if payload[0]==2 else 4,0,0])
                self.inject(encode_short(record(0x7e8,b'\x01B'),key)+encode_short(reply,key))
                return len(wire)
        d=self.session([]);d.port=Port();d.tx_key=d.rx_key=key
        d.record={};d.events=deque()
        d.receiver=PacketReceiver(d.port,key,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        d.send_packet=GD101.send_packet.__get__(d);d.receive_packet=GD101.receive_packet.__get__(d)
        d.receiver.start()
        try:
            d.configure_isotp_routes(d.flow_entries,standard=[(0x7ff,0x7e8)],config=d.flow_config,bitrate=250000)
            self.assertEqual(len(requests),3)
            self.assertEqual(d.receiver.isotp_update_drops,3)
            self.assertIsNone(d.receiver.error);self.assertTrue(d.receiver.thread.is_alive())
            d.port.inject(encode_short(record(0x7e8,b'\x01C'),key))
            self.assertEqual(d.receiver.read_message(1).data,b'C')
        finally:d.receiver.stop()

    def test_failed_stage_keeps_uncertainty_and_blocks_send(self):
        good=[b'\x41\0\0\0',b'\x41\x0b\0\0',b'\x41\x04\0\0']
        for stage in range(3):
            d=self.session(good[:stage]+[TimeoutError('lost ACK')]);before=d.flow_config
            with self.assertRaises(TimeoutError):d.configure_isotp_routes(d.flow_entries,bitrate=250000)
            self.assertTrue(d.flow_uncertain);self.assertTrue(d.receiver.isotp_updating)
            self.assertEqual(d.can_bitrate,500000);self.assertEqual(d.flow_config,before)
            with self.assertRaises(GD101Error):d.write_isotp_message(0,b'abc')
            self.assertEqual(d.send_packet.call_count,stage+1)

    def test_provider_mixed_validation_and_failure(self):
        p=config_helpers.ISOConfigTests().provider();c=p.channel
        p.session.configure_isotp_routes.reset_mock()
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(30,0x100000000)])[0],10)
        p.session.configure_isotp_routes.assert_not_called()
        p.session.configure_isotp_routes.side_effect=TimeoutError()
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(3,1)])[0],9)
        self.assertEqual(p.configure_timing(c,parameters=[1,3]),(0,[500000,0]))
        p.session.configure_isotp_routes.side_effect=None
        self.assertEqual(p.configure_timing(c,pairs=[(1,250000),(3,1),(30,8)])[0],0)
        options=p.session.configure_isotp_routes.call_args.kwargs
        self.assertEqual(options['bitrate'],250000);self.assertEqual(options['config'][4:6],b'\x01\x08')
        self.assertEqual(p.configure_timing(c,parameters=[1,3,30]),(0,[250000,1,8]))

if __name__=='__main__':unittest.main()
