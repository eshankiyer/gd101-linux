import unittest
from concurrent.futures import ThreadPoolExecutor
from gd101_can import CANFilters,CANFrame
import test_gd101_bridge as bridge_helpers


class CANFilterLimitsTests(unittest.TestCase):
    def test_empty_and_partial_filters(self):
        filters=CANFilters()
        handle=filters.add(1,b'',b'',extended=False)
        self.assertTrue(filters.accepts(CANFrame(0x123,b'',False,0)))
        self.assertFalse(filters.accepts(CANFrame(0x123,b'',True,0)))
        filters.remove(handle)
        filters.add(1,b'\xff',b'\x18',extended=True)
        self.assertTrue(filters.accepts(CANFrame(0x18daf110,b'X',True,0)))
        self.assertFalse(filters.accepts(CANFrame(0x19daf110,b'X',True,0)))
        filters.add(2,b'',b'',extended=True)
        self.assertFalse(filters.accepts(CANFrame(0x18daf110,b'X',True,0)))

    def test_capacity_is_atomic_and_slots_can_be_reused(self):
        filters=CANFilters()
        def add(_):
            try:return filters.add(1,b'',b'')
            except OverflowError:return None
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(add,range(25)))
        handles=[h for h in results if h is not None]
        self.assertEqual(len(handles),10);self.assertEqual(len(set(handles)),10)
        filters.remove(handles[0]);new=filters.add(1,b'',b'')
        self.assertNotIn(new,handles)
        with self.assertRaises(ValueError):filters.remove(handles[0])
        filters.clear()
        for _ in range(10):filters.add(1,b'',b'')

    def test_rpc_short_filters_and_limit_error(self):
        import struct
        h=bridge_helpers.BridgeTests();h.setUp()
        _,data=h.call(1);device,=struct.unpack('<I',data)
        _,data=h.call(3,device,5,0,500000);channel,=struct.unpack('<I',data)
        handles=[]
        for length in (0,1,2,3,4,5,6,7,8,12):
            status,data=h.call(7,channel,1,length,0,extra=bytes(length*2))
            self.assertEqual(status,0);handles.append(struct.unpack('<I',data)[0])
        status,data=h.call(7,channel,1,0,0)
        self.assertEqual(status,12);self.assertIn(b'ten filters',data)
        self.assertEqual(h.call(8,channel,handles[0])[0],0)
        self.assertEqual(h.call(7,channel,1,0,0)[0],0)


if __name__=='__main__':unittest.main()
