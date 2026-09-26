import unittest
from collections import deque
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error
from gd101_isotp import default_flow_config,build_flow_table_packet,make_flow_entry,build_acceptance_table


def driver(replies):
    d=object.__new__(GD101)
    d.can_open=True
    d.tx_uncertain=d.flow_uncertain=False
    d.flow_entries={}
    d.events=deque()
    d.send_packet=Mock()
    d.receive_packet=Mock(side_effect=replies)
    return d


class FlowSetupTests(unittest.TestCase):
    def test_observed_empty_table_replies_and_commit(self):
        d=driver([bytes.fromhex('410b0093'),bytes.fromhex('41040000')])
        entries={0:make_flow_entry(0x7e0,0x7e8)}
        d.configure_isotp_routes(entries)
        self.assertEqual(d.flow_entries,entries)
        self.assertFalse(d.flow_uncertain)
        self.assertEqual(d.send_packet.call_args_list[0].args[0],build_flow_table_packet(entries,default_flow_config()))
        self.assertTrue(d.send_packet.call_args_list[0].kwargs['extended'])
        self.assertEqual(d.send_packet.call_args_list[1].args[0],bytes.fromhex('010400000000'))

    def test_failure_does_not_commit_or_retry(self):
        d=driver([bytes.fromhex('410b07')])
        with self.assertRaises(GD101Error): d.configure_isotp_routes({})
        self.assertTrue(d.flow_uncertain)
        with self.assertRaises(GD101Error): d.configure_isotp_routes({})
        self.assertEqual(d.send_packet.call_count,1)

    def test_acceptance_validation_before_first_write(self):
        d=driver([])
        with self.assertRaises(ValueError): d.configure_isotp_routes({},standard=[(0,0)]*30)
        d.send_packet.assert_not_called()
        self.assertFalse(d.flow_uncertain)

    def test_acceptance_group_layout(self):
        table=build_acceptance_table([(0x7ff,0x7e8)],[(0x1fffffff,0x18daf110)])
        self.assertEqual(table.hex(),'0104000101ff070000e8070000ffffff1f10f1da18')


if __name__=='__main__': unittest.main()
