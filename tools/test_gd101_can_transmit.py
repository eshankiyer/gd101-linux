import unittest
from collections import deque
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error


def driver(replies):
    d=object.__new__(GD101)
    d.can_open=True
    d.tx_uncertain=False
    d.tx_counter=0
    d.events=deque()
    d.send_packet=Mock()
    d.receive_packet=Mock(side_effect=replies)
    return d


class CANTransmitTests(unittest.TestCase):
    def test_completion_preserves_received_frame(self):
        incoming=bytes.fromhex('430100010000000000df070000015500')
        d=driver([incoming,bytes.fromhex('43000000000200000000')])
        ack=d.write_can_frame(0x7df,b'\x01')
        self.assertEqual(ack.timestamp,2)
        self.assertEqual(list(d.events),[incoming])
        d.send_packet.assert_called_once_with(bytes.fromhex('0300000000df070000010100'))
        self.assertFalse(d.tx_uncertain)

    def test_timeout_latches_and_never_retries(self):
        d=driver([TimeoutError('no completion')])
        with self.assertRaises(TimeoutError): d.write_can_frame(0x7df,b'')
        with self.assertRaises(GD101Error): d.write_can_frame(0x7df,b'')
        d.send_packet.assert_called_once()

    def test_rejection_and_wrong_tag(self):
        wrong=bytes.fromhex('43000001000100000000')
        d=driver([wrong,bytes.fromhex('43000000070200000000')])
        with self.assertRaisesRegex(GD101Error,'status 7'): d.write_can_frame(0x7df,b'')
        self.assertEqual(list(d.events),[wrong])
        self.assertFalse(d.tx_uncertain)

    def test_invalid_identifier_cannot_send(self):
        d=driver([])
        with self.assertRaises(ValueError): d.write_can_frame(0x800,b'')
        with self.assertRaises(ValueError): d.write_can_frame(1,bytes(9))
        d.send_packet.assert_not_called()
        self.assertFalse(d.tx_uncertain)


if __name__=='__main__': unittest.main()
