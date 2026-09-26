import struct
import time
import unittest
from collections import deque
from gd101_driver import GD101,GD101Error
from gd101_receiver import PacketReceiver
from gd101_native import encode_short,decode_frame
from gd101_isotp import ISOTPReassembler,FlowRoute,make_flow_entry
from test_gd101_receiver import FakePort


def record(identifier,data):
    payload=b'\x43\x01\0'+struct.pack('<I',1)+bytes(2)+struct.pack('<I',identifier)+bytes([len(data)])+data
    return payload+bytes(len(payload)%2)

class RouteUpdateTests(unittest.TestCase):
    def setup_driver(self,reject=False):
        key=bytes(range(32))
        class Port(FakePort):
            def flush(self):pass
            def write(self,wire):
                request=decode_frame(wire,key)
                code=11 if request[0]==2 else 4
                reply=bytes((0x41,code,7 if reject else 0,0))
                self.inject(encode_short(record(0x7e8,b'\x03old'),key)+
                            encode_short(record(0x7e9,b'\x03new'),key)+encode_short(reply,key))
                return len(wire)
        port=Port();r=PacketReceiver(port,key,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=.5))
        now=time.monotonic()
        r._accept_isotp(record(0x7e8,b'\x03old'),now)
        r._accept_isotp(record(0x7e8,b'\x10\x0aabcdef'),now)
        d=object.__new__(GD101);d.port=port;d.tx_key=d.rx_key=key;d.receiver=r
        d.can_open=True;d.tx_uncertain=d.flow_uncertain=False
        d.flow_entries={0:make_flow_entry(0x7e0,0x7e8)};d.events=deque();d.record={}
        r.start()
        return d,r,port,key

    def test_acknowledged_update_preserves_queued_messages(self):
        d,r,port,key=self.setup_driver()
        try:
            self.assertIn(0,r.isotp.pending)
            entries={0:make_flow_entry(0x7e1,0x7e9)}
            d.configure_isotp_routes(entries,standard=[(0x7ff,0x7e9)])
            self.assertEqual(d.flow_entries,entries)
            self.assertFalse(d.flow_uncertain);self.assertFalse(r.isotp_updating)
            self.assertEqual(r.isotp_update_drops,4)
            self.assertEqual(r.read_message(0).data,b'old')
            self.assertEqual(r.isotp.routes,{0:FlowRoute(0x7e9)})
            self.assertEqual(r.isotp.timeout,.5);self.assertEqual(r.isotp.pending,{})
            port.inject(encode_short(record(0x7e9,b'\x03new'),key))
            self.assertEqual(r.read_message(1).data,b'new')
            self.assertTrue(r.thread.is_alive())
        finally:r.stop()

    def test_rejected_update_keeps_gate_and_uncertainty(self):
        d,r,port,key=self.setup_driver(reject=True)
        before=dict(d.flow_entries)
        try:
            with self.assertRaises(GD101Error):d.configure_isotp_routes({})
            self.assertTrue(d.flow_uncertain);self.assertTrue(r.isotp_updating)
            self.assertEqual(d.flow_entries,before)
            self.assertEqual(r.read_message(0).data,b'old')
            count=len(d.record['packets'])
            with self.assertRaises(GD101Error):d.configure_isotp_routes({})
            self.assertEqual(len(d.record['packets']),count)
        finally:r.stop()

    def test_validation_precedes_gate_and_io(self):
        d,r,port,key=self.setup_driver()
        try:
            with self.assertRaises(ValueError):d.configure_isotp_routes({},standard=[(0,0)]*30)
            self.assertFalse(r.isotp_updating);self.assertFalse(d.flow_uncertain)
            self.assertNotIn('packets',d.record)
        finally:r.stop()

if __name__=='__main__':unittest.main()
