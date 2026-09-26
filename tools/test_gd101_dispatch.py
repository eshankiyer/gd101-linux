import unittest
from collections import deque
from unittest.mock import Mock
from gd101_driver import GD101, GD101Error
from gd101_kline import decode_kline_byte


def session(packets):
    # No constructor, serial port, authentication material or hardware needed.
    adapter=object.__new__(GD101)
    adapter.events=deque()
    adapter.record={'exchanges':[]}
    adapter.send_packet=Mock(return_value=b'wire')
    adapter.receive_packet=Mock(side_effect=packets)
    return adapter


class DispatchTests(unittest.TestCase):
    def test_interleaved_events_survive_ack(self):
        events=[bytes.fromhex('4303007856341255'),bytes.fromhex('43030079563412aa')]
        ack=bytes.fromhex('41010000')
        adapter=session(events+[ack])
        self.assertEqual(adapter.exchange(b'command',b'\x41\x01\0'),ack)
        for event in events:
            self.assertEqual(adapter.receive_event(),event)
        self.assertEqual(adapter.receive_packet.call_count,3)
        self.assertEqual(decode_kline_byte(events[0]).timestamp,0x12345678)
        adapter.send_packet.assert_called_once()

    def test_error_ack_is_not_queued_or_retried(self):
        adapter=session([bytes.fromhex('41010700')])
        with self.assertRaises(GD101Error):
            adapter.exchange(b'command',b'\x41\x01\0')
        self.assertFalse(adapter.events)
        adapter.send_packet.assert_called_once()

    def test_queue_overflow_is_explicit(self):
        event=bytes.fromhex('4303007856341255')
        adapter=session([event])
        adapter.events.extend([event]*4096)
        with self.assertRaisesRegex(GD101Error,'overflow'):
            adapter.exchange(b'command',b'\x41\x01\0')

    def test_malformed_receive_event_is_rejected(self):
        for data in (b'', bytes(8), bytes.fromhex('43030000')):
            with self.assertRaises(ValueError):
                decode_kline_byte(data)


if __name__=='__main__':
    unittest.main()
