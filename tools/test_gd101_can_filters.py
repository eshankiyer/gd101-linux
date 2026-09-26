import unittest
from unittest.mock import Mock
from gd101_can import CANFilters,CANFrame
from gd101_driver import GD101


def frame(identifier=0x7e8,data=b'\x03\x41\x0c'):
    return CANFrame(identifier,data,False,1)


class CANFilterTests(unittest.TestCase):
    def test_id_and_payload_masks_and_block_precedence(self):
        filters=CANFilters()
        self.assertFalse(filters.accepts(frame()))
        handle=filters.add(1,bytes.fromhex('fffffff8'),bytes.fromhex('000007e8'))
        self.assertTrue(filters.accepts(frame()))
        self.assertTrue(filters.accepts(frame(0x7ef)))
        self.assertFalse(filters.accepts(frame(0x7e0)))
        block=filters.add(2,bytes.fromhex('ffffffff00ff'),bytes.fromhex('000007e80041'))
        self.assertFalse(filters.accepts(frame()))
        self.assertTrue(filters.accepts(frame(data=b'\x03\x42\x0c')))
        filters.remove(block)
        self.assertTrue(filters.accepts(frame()))
        filters.remove(handle)
        self.assertFalse(filters.accepts(frame()))

    def test_patterns_are_not_silently_masked(self):
        filters=CANFilters()
        filters.add(1,b'\0',b'\x01')
        self.assertFalse(filters.accepts(frame()))

    def test_handle_lifecycle_and_validation(self):
        filters=CANFilters()
        first=filters.add(1,b'',b'')
        filters.clear()
        second=filters.add(1,b'',b'')
        self.assertNotEqual(first,second)
        with self.assertRaises(ValueError): filters.remove(first)
        with self.assertRaises(ValueError): filters.add(3,b'',b'')
        with self.assertRaises(ValueError): filters.add(1,bytes(13),bytes(13))

    def test_filtered_api_skips_nonmatching_frames(self):
        d=object.__new__(GD101)
        d.can_open=True
        d.can_filters=CANFilters()
        d.start_can_filter(1,bytes.fromhex('ffffffff'),bytes.fromhex('000007e8'))
        wanted=frame()
        d.read_can_frame=Mock(side_effect=[frame(0x123),wanted])
        self.assertEqual(d.read_can_message(),wanted)
        self.assertEqual(d.read_can_frame.call_count,2)
        timeouts=[c.args[0] for c in d.read_can_frame.call_args_list]
        self.assertLessEqual(timeouts[1],timeouts[0])

    def test_nonblocking_filter_drain_and_empty(self):
        d=object.__new__(GD101);d.can_open=True;d.can_filters=CANFilters()
        d.start_can_filter(1,bytes.fromhex('ffffffff'),bytes.fromhex('000007e8'))
        d.read_can_frame=Mock(side_effect=[frame(0x123),frame(),TimeoutError('empty')])
        self.assertEqual(d.read_can_message(0),frame())
        with self.assertRaises(TimeoutError):d.read_can_message(0)
        self.assertTrue(all(c.args==(0,) for c in d.read_can_frame.call_args_list))


if __name__=='__main__': unittest.main()
