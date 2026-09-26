import struct
import unittest
from gd101_isotp import FlowRoute,decode_isotp_input


def record(index=0,identifier=0x7e8,data=b'\xaa\x03',selector=0):
    return b'\x43\x01'+bytes([selector])+struct.pack('<I',1)+bytes([0,index])+struct.pack('<I',identifier)+bytes([len(data)])+data


class ISOTPInputTests(unittest.TestCase):
    def test_wrong_id_selector_and_disabled_route(self):
        routes={0:FlowRoute(0x7e8)}
        self.assertIsNone(decode_isotp_input(record(identifier=0x7e9),routes))
        self.assertIsNone(decode_isotp_input(record(selector=1),routes))
        self.assertIsNone(decode_isotp_input(record(),{0:FlowRoute(0x7e8,enabled=False)}))

    def test_extended_address_and_bad_lengths(self):
        self.assertIsNotNone(decode_isotp_input(record(),{0:FlowRoute(0x7e8,0xaa)}))
        self.assertIsNone(decode_isotp_input(record(),{0:FlowRoute(0x7e8,0xbb)}))
        with self.assertRaises(ValueError): decode_isotp_input(record(index=255,data=bytes(9)),{})
        with self.assertRaises(ValueError): decode_isotp_input(record()[:-1],{0:FlowRoute(0x7e8)})

    def test_zero_dlc_passthrough(self):
        decoded=decode_isotp_input(record(index=255,data=b''),{})
        self.assertEqual(decoded.frame.data,b'')
        self.assertEqual(decoded.filter_index,255)



class FlowTableTests(unittest.TestCase):
    def test_route_layout_and_input_matching(self):
        from gd101_isotp import make_flow_entry,build_flow_table_packet
        entry=make_flow_entry(0x7e0,0x7e8,transmit_address=0xf1,receive_address=0x10)
        self.assertEqual(entry[1],13)
        self.assertEqual(entry[4:10],bytes.fromhex('e0070000f10d'))
        self.assertEqual(entry[12:17],bytes.fromhex('e807000010'))
        payload=build_flow_table_packet({63:entry},bytes(32))
        self.assertEqual(len(payload),31)
        self.assertEqual(payload[17],1)
        self.assertEqual(payload[18:],bytes.fromhex('3f0de0070000f10de807000010'))

    def test_table_bounds_and_asymmetric_addresses_rejected(self):
        from gd101_isotp import make_flow_entry,build_flow_table_packet
        with self.assertRaises(ValueError): make_flow_entry(0x800,0x7e8)
        with self.assertRaises(ValueError): make_flow_entry(0x7e0,0x7e8,transmit_address=1)
        with self.assertRaises(ValueError): build_flow_table_packet({64:bytes([1])*32},bytes(32))
        with self.assertRaises(ValueError): build_flow_table_packet({0:bytes(32)},bytes(32))

if __name__=='__main__': unittest.main()
