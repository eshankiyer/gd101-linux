import unittest
from gd101_can import CANFrame
from gd101_isotp import ISOTPReassembler,FlowRoute,RoutedCANFrame


def frame(data,stamp=1,slot=0,identifier=0x7e8):
    return RoutedCANFrame(CANFrame(identifier,bytes(data),False,stamp),slot)


class ReassemblyTests(unittest.TestCase):
    def test_malformed_transfer_does_not_abort_other_route(self):
        r=ISOTPReassembler({0:FlowRoute(0x7e8),1:FlowRoute(0x7e9)},timeout=1)
        r.feed(frame(b'\x10\x0aabcdef'),0)
        r.feed(frame(b'\x10\x0aABCDEF',slot=1,identifier=0x7e9),.01)
        with self.assertRaises(ValueError):r.feed(frame(b''),.02)
        self.assertNotIn(0,r.pending)
        self.assertIn(1,r.pending)
        message=r.feed(frame(b'\x21GHIJ',slot=1,identifier=0x7e9),.03)
        self.assertEqual(message.data,b'ABCDEFGHIJ')

    def test_single_and_multiframe_padding(self):
        r=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1)
        self.assertEqual(r.feed(frame(b'\x03abc\xff\xff'),0).data,b'abc')
        self.assertIsNone(r.feed(frame(b'\x10\x0aabcdef',2),.1))
        result=r.feed(frame(b'\x21ghij\0\0\0',3),.2)
        self.assertEqual(result.data,b'abcdefghij')
        self.assertEqual((result.first_timestamp,result.last_timestamp),(2,3))

    def test_extended_address(self):
        r=ISOTPReassembler({0:FlowRoute(0x7e8,0xaa)},timeout=1)
        self.assertEqual(r.feed(frame(b'\xaa\x03abc'),0).data,b'abc')
        r.feed(frame(b'\xaa\x10\x09abcde'),.1)
        result=r.feed(frame(b'\xaa\x21fghi\0\0'),.2)
        self.assertEqual(result.data,b'abcdefghi')
        self.assertEqual(result.address,0xaa)

    def test_sequence_wrap(self):
        data=bytes(range(130))
        r=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=1)
        r.feed(frame(b'\x10\x82'+data[:6]),0)
        result=None
        for n,start in enumerate(range(6,len(data),7),1):
            result=r.feed(frame(bytes([0x20|(n&15)])+data[start:start+7]),n*.01)
        self.assertEqual(result.data,data)

    def test_timeout_sequence_error_and_truncated_frame(self):
        r=ISOTPReassembler({0:FlowRoute(0x7e8)},timeout=.5)
        r.feed(frame(b'\x10\x0aabcdef'),0)
        self.assertEqual(r.expire(.5),[0])
        self.assertIsNone(r.feed(frame(b'\x21ghij'),.6))
        r.feed(frame(b'\x10\x0aabcdef'),1)
        with self.assertRaises(ValueError): r.feed(frame(b'\x22ghij'),1.1)
        self.assertFalse(r.pending)
        with self.assertRaises(ValueError): r.feed(frame(b'\x07abc'),1.2)


if __name__=='__main__': unittest.main()
