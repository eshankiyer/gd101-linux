import unittest
from collections import deque
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error
from gd101_isotp import make_flow_entry,build_isotp_transmit,default_flow_config


def driver(replies):
    d=object.__new__(GD101)
    d.can_open=True;d.tx_uncertain=d.flow_uncertain=False;d.tx_counter=0
    d.flow_entries={0:make_flow_entry(0x7e0,0x7e8)}
    d.flow_config=default_flow_config()
    d.events=deque();d.send_packet=Mock();d.receive_packet=Mock(side_effect=replies)
    return d


class ISOTPTransmitTests(unittest.TestCase):
    def test_single_frame_layout_and_padding(self):
        entry=make_flow_entry(0x7e0,0x7e8)
        payload,large=build_isotp_transmit(entry,0,b'\x22\xf1\x90')
        self.assertFalse(large)
        self.assertEqual(payload.hex(),'0300000000e0070000040322f190')
        entry=make_flow_entry(0x7e0,0x7e8,pad=True,transmit_address=0xaa,receive_address=0xbb)
        payload,large=build_isotp_transmit(entry,0,b'\x22',padding_byte=0xcc)
        self.assertEqual(payload[-8:],bytes.fromhex('aa0122cccccccccc'))
        self.assertEqual(payload[4],0x18)

    def test_ack_types_and_timeout_latch(self):
        for size,ack in ((3,'43000000000100000000'),(8,'430a0000000200000000')):
            d=driver([bytes.fromhex(ack)])
            self.assertEqual(d.write_isotp_message(0,bytes(size)),1 if size==3 else 2)
            self.assertEqual(d.send_packet.call_args.kwargs['extended'],size==8)
            self.assertFalse(d.tx_uncertain)
        d=driver([TimeoutError('no response')])
        with self.assertRaises(TimeoutError):d.write_isotp_message(0,bytes(8))
        with self.assertRaises(GD101Error):d.write_isotp_message(0,bytes(8))
        d.send_packet.assert_called_once()

    def test_missing_route_and_bad_size_never_send(self):
        d=driver([])
        with self.assertRaises(GD101Error):d.write_isotp_message(1,b'a')
        with self.assertRaises(ValueError):d.write_isotp_message(0,bytes(4096))
        d.send_packet.assert_not_called()


if __name__=='__main__':unittest.main()
