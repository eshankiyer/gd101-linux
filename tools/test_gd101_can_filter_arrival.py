import unittest
from collections import deque
from unittest.mock import Mock
from gd101_can import CANFilters
from gd101_driver import GD101,GD101Error
from gd101_receiver import PacketReceiver,ReceivedPacket
from gd101_native import encode_short
from gd101_abi import LifecycleProvider
from test_gd101_receiver import FakePort

FRAME=bytes.fromhex('430100010000000000df070000015500')
ACK=bytes.fromhex('43000000000000000000')

class CANArrivalTests(unittest.TestCase):
    def test_filter_changes_do_not_reclassify_queued_frames(self):
        key=bytes(range(32));port=FakePort();filters=CANFilters()
        r=PacketReceiver(port,key,can_filters=filters);r.start()
        try:
            port.inject(encode_short(FRAME,key)+encode_short(ACK,key))
            self.assertEqual(r.read_packet(1).payload,ACK)
            handle=filters.add(1,bytes(4),bytes(4),extended=False)
            with self.assertRaises(TimeoutError):r.read_message(0)
            port.inject(encode_short(FRAME,key)+encode_short(ACK,key))
            self.assertEqual(r.read_packet(1).payload,ACK)
            filters.remove(handle)
            self.assertEqual(r.read_message(0).arbitration_id,0x7df)
            port.inject(encode_short(FRAME,key)+encode_short(ACK,key))
            self.assertEqual(r.read_packet(1).payload,ACK)
            with self.assertRaises(TimeoutError):r.read_message(0)
        finally:r.stop()

    def test_native_read_uses_arrival_queue_and_clear_preserves_ack(self):
        filters=CANFilters();filters.add(1,bytes(4),bytes(4))
        r=PacketReceiver(None,bytes(32),can_filters=filters)
        d=object.__new__(GD101);d.receiver=r;d.can_open=True;d.can_filters=filters;d.events=deque()
        r._accept_can(FRAME);filters.clear()
        self.assertEqual(d.read_can_message(0).data,b'\x55')
        with self.assertRaises(GD101Error):d.read_can_frame(0)
        filters.add(1,bytes(4),bytes(4));r._accept_can(FRAME)
        ack=ReceivedPacket(b'',ACK,0);r.packets.append(ack);d.clear_rx_buffer()
        self.assertEqual(list(r.packets),[ack])
        with self.assertRaises(TimeoutError):d.read_can_message(0)

    def test_api_requests_filtered_reader(self):
        p=LifecycleProvider();p.device=1;p.session=Mock()
        self.assertEqual(p.connect(1,5,0,500000)[0],0)
        p.session.start_receiver.assert_called_once_with(filter_can=True)

    def test_selector_validation_and_block_precedence(self):
        filters=CANFilters();filters.add(1,bytes(4),bytes(4))
        filters.add(2,b'\xff'*4,bytes.fromhex('000007df'))
        r=PacketReceiver(None,bytes(32),can_filters=filters)
        r._accept_can(FRAME)
        with self.assertRaises(TimeoutError):r.read_message(0)
        with self.assertRaises(ValueError):r._accept_can(FRAME[:2]+b'\x01'+FRAME[3:])

if __name__=='__main__':unittest.main()
