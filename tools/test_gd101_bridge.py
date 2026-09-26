import struct
import unittest
from gd101_bridge import dispatch, REQUEST, RESPONSE, MAGIC
from gd101_abi import LifecycleProvider

class Session:
    def __init__(self):
        from gd101_can import CANFilters
        self.record={};self.closed=False;self.filters=CANFilters()
        self.writes=[];self.queued=[]
        self.receiver=None;self.events=[];self.frames=[]
    def __enter__(self):return self
    def close(self):self.closed=True
    def read_version(self):return bytes.fromhex('4003000e014d54303030343138360217531e50424758383930300b4511f3')
    def connect_can(self,baud):self.baud=baud
    def connect_kline(self,baud):self.baud=baud
    def disconnect_kline(self):pass
    def start_receiver(self,**kwargs):
        if 'assembler' in kwargs:
            from unittest.mock import Mock
            self.receiver=Mock()
    def disconnect_can(self):pass
    def start_can_filter(self,*args,**kwargs):return self.filters.add(*args,**kwargs)
    def stop_can_filter(self,handle):self.filters.remove(handle)
    def clear_can_filters(self):self.filters.clear()
    def queue_can(self,identifier,data,**kwargs):self.queued.append((identifier,data,kwargs));return len(self.queued)
    def write_can_ordered(self,identifier,data,**kwargs):self.writes.append((identifier,data,kwargs))
    def read_can_message(self,timeout):
        if self.frames:return self.frames.pop(0)
        raise TimeoutError('Empty simulated receive queue')

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.session=Session();self.provider=LifecycleProvider(lambda:self.session)
    def call(self,operation,*args,extra=b''):
        values=[*args,*([0]*(4-len(args))),len(extra)]
        packet=REQUEST.pack(MAGIC,1,123,7,operation,*values)+extra
        response=dispatch(self.provider,packet)
        magic,sequence,status,length=RESPONSE.unpack_from(response)
        self.assertEqual((magic,sequence,length),(MAGIC,7,len(response)-16))
        return status,response[16:]
    def test_lifecycle_and_version(self):
        status,data=self.call(1);self.assertEqual(status,0)
        device,=struct.unpack('<I',data)
        status,data=self.call(5,device);self.assertEqual(status,0)
        self.assertEqual(len(data),240)
        self.assertEqual(data[:80].rstrip(b'\0'),b'SN:MT000418,FW:1.14')
        self.assertEqual(self.call(2,device)[0],0)
        self.assertTrue(self.session.closed)
        self.assertEqual(self.call(5,device)[0],26)
    def test_invalid_requests_do_not_open(self):
        for packet in (b'',b'0'*40,REQUEST.pack(MAGIC,2,123,7,1,0,0,0,0,0)):
            with self.assertRaises(ValueError):dispatch(self.provider,packet)
        self.assertIsNone(self.provider.session)
    def test_double_open_and_invalid_handle(self):
        self.assertEqual(self.call(1)[0],0)
        self.assertEqual(self.call(1)[0],14)
        self.assertEqual(self.call(2,999)[0],26)
        self.assertFalse(self.session.closed)
    def test_backend_exception(self):
        def failing():raise OSError('missing adapter')
        self.provider.factory=failing
        self.assertEqual(self.call(1)[0],7)
        self.assertIn('missing adapter',self.provider.last_error)
        self.assertIsNone(self.provider.session)
    def test_connect_rejects_unsupported(self):
        _,data=self.call(1);device,=struct.unpack('<I',data)
        self.assertEqual(self.call(3,device,5,0,12345)[0],25)
        self.assertEqual(self.call(3,device,7,0,500000)[0],1)
        status,data=self.call(3,device,5,0,500000)
        self.assertEqual(status,0);channel,=struct.unpack('<I',data)
        self.assertEqual(self.call(4,channel)[0],0)
    def test_unsupported_operation(self):
        self.assertEqual(self.call(999)[0],1)

    def test_can_filter_lifecycle_and_id_width(self):
        from gd101_can import CANFrame
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,5,0x800,500000);channel,=struct.unpack('<I',data)
        mask=bytes.fromhex('000007ff');pattern=bytes.fromhex('000007e8')
        status,data=self.call(7,channel,1,4,0,extra=mask+pattern)
        self.assertEqual(status,0);handle,=struct.unpack('<I',data)
        self.assertTrue(self.session.filters.accepts(CANFrame(0x7e8,b'abc',False,0)))
        self.assertFalse(self.session.filters.accepts(CANFrame(0x7e8,b'abc',True,0)))
        self.assertEqual(self.call(8,channel,handle)[0],0)
        self.assertEqual(self.call(8,channel,handle)[0],22)
        self.assertEqual(self.call(7,channel,1,4,0x100,extra=mask+pattern)[0],0)
        self.assertTrue(self.session.filters.accepts(CANFrame(0x7e8,b'abc',True,0)))
        self.assertEqual(self.call(9,channel)[0],0)
        self.assertFalse(self.session.filters.accepts(CANFrame(0x7e8,b'abc',True,0)))

    def test_filter_rejections(self):
        self.assertEqual(self.call(7,999,1,4,0,extra=b'\0'*8)[0],2)
        self.assertEqual(self.call(7,999,1,5,0,extra=b'\0'*8)[0],10)
        with self.assertRaises(ValueError):self.call(1,extra=b'1234')

    def test_can_write_validation_and_mapping(self):
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,5,0,500000);channel,=struct.unpack('<I',data)
        frame=bytes.fromhex('000007df02010c0000000000')
        self.assertEqual(self.call(10,channel,5,0,100,extra=frame)[0],0)
        self.assertEqual(self.session.writes,[(0x7df,frame[4:],{'extended':False,'timeout':.1})])
        for protocol,flags,timeout,data,status in (
            (4,0,100,frame,21),(5,1,100,frame,6),(5,0,0,frame,0),
            (5,0,100,b'\0'*3,10),(5,0,100,b'\0'*13,10),
            (5,0,100,bytes.fromhex('00000800'),10),
            (5,0x100,100,bytes.fromhex('20000000'),10)):
            self.assertEqual(self.call(10,channel,protocol,flags,timeout,extra=data)[0],status)
        self.assertEqual(len(self.session.writes),1)
        self.assertEqual(self.session.queued,[(0x7df,frame[4:],{'extended':False})])
        self.assertEqual(self.call(10,channel,5,0x100,100,extra=bytes.fromhex('18da10f1'))[0],0)
        self.assertTrue(self.session.writes[-1][2]['extended'])

    def test_can_write_timeout_is_reported_without_retry(self):
        from unittest.mock import Mock
        from gd101_driver import GD101Timeout
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,5,0,500000);channel,=struct.unpack('<I',data)
        self.session.write_can_ordered=Mock(side_effect=GD101Timeout('uncertain'))
        self.assertEqual(self.call(10,channel,5,0,100,extra=bytes.fromhex('000007df'))[0],9)
        self.session.write_can_ordered.assert_called_once()

    def test_can_receive_layout_and_empty_status(self):
        from gd101_can import CANFrame
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,5,0,500000);channel,=struct.unpack('<I',data)
        self.session.frames=[CANFrame(0x18daf110,b'\x62\x01',True,0xfffffff0)]
        status,data=self.call(11,channel,0)
        self.assertEqual(status,0)
        self.assertEqual(struct.unpack_from('<6I',data),(5,0x100,0,0xfffffff0,6,6))
        self.assertEqual(data[24:30],bytes.fromhex('18daf1106201'))
        self.assertEqual(self.call(11,channel,0)[0],16)
        self.assertEqual(self.call(11,channel,5)[0],9)
        self.assertEqual(self.call(11,999,0)[0],2)

    def test_reader_start_failure_closes_unpublished_channel(self):
        from unittest.mock import Mock
        _,data=self.call(1);device,=struct.unpack('<I',data)
        self.session.start_receiver=Mock(side_effect=RuntimeError('reader failed'))
        self.session.disconnect_can=Mock()
        self.assertEqual(self.call(3,device,5,0,500000)[0],7)
        self.assertIsNone(self.provider.channel)
        self.session.disconnect_can.assert_called_once()

    def test_fast_init_flags_response_and_timeout(self):
        from unittest.mock import Mock
        from gd101_kline import KLineMessage
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,4,0x200,10400);channel,=struct.unpack('<I',data)
        response=bytes.fromhex('83f046c10808')
        self.session.initialize_kline=Mock(return_value=KLineMessage(0,123,130,response))
        request=bytes.fromhex('8146f08138')
        status,data=self.call(12,channel,4,0,1,extra=request)
        self.assertEqual(status,0);self.assertEqual(len(data),4152)
        self.assertEqual(struct.unpack_from('<6I',data),(4,0,0,123,len(response),len(response)))
        self.assertEqual(data[24:30],response)
        self.session.initialize_kline.assert_called_once_with('fast',request,flags=0x200,
            capture_response=True,response_timeout=.355,no_checksum=True)
        self.session.initialize_kline.side_effect=TimeoutError('ECU missing')
        self.assertEqual(self.call(12,channel,4,0,1,extra=request)[0],9)

    def test_fast_init_rejects_oversize_before_hardware(self):
        from unittest.mock import Mock
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,4,0,10400);channel,=struct.unpack('<I',data)
        self.session.initialize_kline=Mock()
        self.assertEqual(self.call(12,channel,4,0,1,extra=bytes(228))[0],10)
        self.assertEqual(self.call(12,channel,5,0,1,extra=b'a')[0],21)
        self.assertEqual(self.call(12,channel,4,1,1,extra=b'a')[0],6)
        self.assertEqual(self.call(12,channel,4,0x200,1,extra=b'a')[0],6)
        self.session.initialize_kline.assert_not_called()

    def test_fast_init_checksum_comes_from_channel(self):
        from unittest.mock import Mock
        _,data=self.call(1);device,=struct.unpack('<I',data)
        _,data=self.call(3,device,4,0,10400);channel,=struct.unpack('<I',data)
        self.session.initialize_kline=Mock(return_value=None)
        self.assertEqual(self.call(12,channel,4,0,0,extra=b'\x81')[0],0)
        self.session.initialize_kline.assert_called_once_with('fast',b'\x81',flags=0,
            capture_response=False,response_timeout=None,no_checksum=False)

if __name__=='__main__':unittest.main()
