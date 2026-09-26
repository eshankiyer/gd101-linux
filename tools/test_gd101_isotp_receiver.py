import struct
import unittest
from collections import deque
from unittest.mock import Mock
from gd101_receiver import PacketReceiver
from gd101_driver import GD101,GD101Error
from gd101_native import encode_short
from gd101_isotp import ISOTPReassembler,FlowRoute,make_flow_entry
from test_gd101_receiver import FakePort


def wire(data,stamp=1):
    payload=b'\x43\x01\0'+struct.pack('<I',stamp)+bytes(2)+struct.pack('<I',0x7e8)+bytes([len(data)])+data
    return encode_short(payload+bytes(len(payload)%2))


class ISOReceiverTests(unittest.TestCase):
    def test_fragmented_wire_to_complete_message_and_separate_reply(self):
        port=FakePort()
        receiver=PacketReceiver(port,None,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        receiver.start()
        try:
            port.inject(wire(b'\x10\x0aabcdef')+encode_short(bytes.fromhex('41000000'))+wire(b'\x21ghij\0\0\0',2))
            self.assertEqual(receiver.read_message(1).data,b'abcdefghij')
            self.assertEqual(receiver.read_packet(1).payload,bytes.fromhex('41000000'))
        finally: receiver.stop()

    def test_bad_sequence_drops_transfer_but_reader_recovers(self):
        port=FakePort()
        receiver=PacketReceiver(port,None,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        receiver.start()
        try:
            port.inject(wire(b'\x10\x0aabcdef')+wire(b'\x22ghij\0\0\0')+wire(b'\x03xyz'))
            self.assertEqual(receiver.read_message(1).data,b'xyz')
            self.assertEqual(receiver.isotp_error_count,1)
            self.assertIn('sequence mismatch',receiver.isotp_errors[0])
            self.assertIsNone(receiver.error)
            self.assertTrue(receiver.thread.is_alive())
        finally: receiver.stop()

    def test_error_history_is_bounded_and_completions_survive(self):
        port=FakePort()
        receiver=PacketReceiver(port,None,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        receiver.start()
        try:
            bad=wire(b'\x10\x0aabcdef')+wire(b'\x22ghij\0\0\0')
            ack=bytes.fromhex('43000000000000000000')
            port.inject(bad*40+encode_short(ack)+wire(b'\x03xyz'))
            self.assertEqual(receiver.read_packet(2).payload,ack)
            self.assertEqual(receiver.read_message(1).data,b'xyz')
            self.assertEqual(receiver.isotp_error_count,40)
            self.assertEqual(len(receiver.isotp_errors),32)
        finally:receiver.stop()

    def test_transport_crc_error_still_stops_reader(self):
        port=FakePort()
        receiver=PacketReceiver(port,None,isotp=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1))
        receiver.start()
        try:
            corrupt=bytearray(wire(b'\x03abc'));corrupt[-1]^=1;port.inject(corrupt)
            with self.assertRaisesRegex(RuntimeError,'reader failed'):receiver.read_message(1)
            self.assertEqual(receiver.isotp_error_count,0)
        finally:receiver.stop()

    def test_api_route_snapshot_and_preconditions(self):
        d=object.__new__(GD101)
        d.can_open=True;d.flow_uncertain=False;d.receiver=None;d.events=deque()
        d.flow_entries={2:make_flow_entry(0x7e0,0x7e8,transmit_address=1,receive_address=2)}
        d.start_receiver=Mock()
        d.start_isotp_receiver(timeout=.5)
        processor=d.start_receiver.call_args.kwargs['isotp']
        self.assertEqual(processor.routes[2],FlowRoute(0x7e8,2))
        self.assertEqual(processor.timeout,.5)
        d.flow_uncertain=True
        with self.assertRaises(GD101Error): d.start_isotp_receiver()


if __name__=='__main__': unittest.main()
