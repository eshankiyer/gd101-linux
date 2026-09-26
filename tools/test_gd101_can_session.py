import unittest
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error
from gd101_can import default_can_descriptor


def session(replies):
    d=object.__new__(GD101)
    from gd101_can import CANFilters
    d.can_filters=CANFilters()
    d.can_open=d.kline_open=False
    d.exchange=Mock(side_effect=replies)
    return d


class CANSessionTests(unittest.TestCase):
    def test_open_close_and_defaults(self):
        d=session([b'\x41\0\0\0']*2)
        d.connect_can()
        self.assertTrue(d.can_open)
        self.assertEqual(d.exchange.call_args.args[0].hex(),'010001006e20a10700500f010000000000000000')
        d.disconnect_can()
        self.assertFalse(d.can_open)
        self.assertEqual(d.exchange.call_args.args[0],b'\x01\0\0\0')

    def test_error_status_and_exclusion(self):
        d=session([b'\x41\0\0\x07'])
        with self.assertRaises(GD101Error): d.connect_can()
        self.assertFalse(d.can_open)
        d.kline_open=True
        with self.assertRaises(GD101Error): d.connect_can()
        self.assertEqual(d.exchange.call_count,1)

    def test_bitrate_rejected_before_write(self):
        d=session([])
        with self.assertRaises(ValueError): d.connect_can(12345)
        d.exchange.assert_not_called()
        self.assertEqual(int.from_bytes(default_can_descriptor(125000)[4:8],'little'),125000)



class CANReceiveTests(unittest.TestCase):
    def test_queued_frame_preserves_completion(self):
        from collections import deque
        d=session([])
        d.can_open=True
        complete=bytes.fromhex('43000000000000000000')
        frame=bytes.fromhex('430100010000000000df070000015500')
        d.events=deque([complete,frame])
        result=d.read_can_frame()
        self.assertEqual(result.arbitration_id,0x7df)
        self.assertEqual(result.data,b'\x55')
        self.assertEqual(list(d.events),[complete])

if __name__=='__main__': unittest.main()
