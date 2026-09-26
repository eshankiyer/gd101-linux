import unittest
from collections import deque
from unittest.mock import Mock,patch
from gd101_driver import GD101,GD101Error


def session(replies):
    driver=object.__new__(GD101)
    driver.kline_open=True
    driver.tx_uncertain=False
    driver.init_uncertain=False
    driver.tx_counter=0
    driver.events=deque()
    driver.send_packet=Mock()
    driver.receive_packet=Mock(side_effect=replies)
    return driver


class TransmitTests(unittest.TestCase):
    def test_long_message_encrypted_usb_roundtrip(self):
        from test_gd101_receiver import FakePort
        from gd101_native import decode_frame,encode_short
        key=bytes(range(32));received=[]
        class Port(FakePort):
            def flush(self):pass
            def write(self,wire):
                request=decode_frame(wire,key)
                if request[:3]!=b'\x03\x01\0' or len(request)!=6 or request[5]!=0:
                    raise AssertionError('Unexpected byte-transmit wire format')
                received.append(request[4])
                reply=bytes((0x43,2,0,request[3],0,1,0,0,0,0))
                self.inject(encode_short(reply,key));return len(wire)
        d=session([]);d.port=Port();d.tx_key=d.rx_key=key;d.record={};d.receiver=None
        d.send_packet=GD101.send_packet.__get__(d);d.receive_packet=GD101.receive_packet.__get__(d)
        data=bytes(i%256 for i in range(1001))
        d.write_kline_wire(data,timing=0,timeout=10)
        self.assertEqual(bytes(received),data)
        self.assertFalse(d.tx_uncertain)

    def test_long_message_byte_commands_and_counter_wrap(self):
        for size in (1001,4097):
            d=session([]);d.tx_counter=239
            def reply(timeout):
                payload=d.send_packet.call_args.args[0]
                return bytes((0x43,2,0,payload[3],0,1,0,0,0,0))
            d.receive_packet=Mock(side_effect=reply)
            data=bytes(i%256 for i in range(size))
            d.write_kline_wire(data,timing=0,timeout=10)
            calls=d.send_packet.call_args_list
            self.assertEqual(len(calls),size)
            self.assertEqual(bytes(c.args[0][4] for c in calls),data)
            self.assertTrue(all(c.args[0][:3]==b'\x03\x01\0' and c.args[0][5:]==b'\0'
                                and c.kwargs=={'extended':False} for c in calls))
            self.assertEqual([c.args[0][3] for c in calls[:3]],[239,0,1])
            self.assertFalse(d.tx_uncertain)

    def test_long_partial_rejection_stops_and_latches(self):
        d=session([bytes.fromhex('43020000000100000000'),bytes.fromhex('43020001070100000000')])
        with self.assertRaisesRegex(GD101Error,'status 7'):
            d.write_kline_wire(bytes(1001),timing=0)
        self.assertEqual(d.send_packet.call_count,2)
        self.assertTrue(d.tx_uncertain)
        with self.assertRaises(GD101Error):d.write_kline_wire(b'a')
        self.assertEqual(d.send_packet.call_count,2)

    def test_long_message_uses_one_deadline(self):
        d=session([]);clock=[0.0]
        d.send_packet.side_effect=lambda *a,**kw:clock.__setitem__(0,clock[0]+.004)
        d.receive_packet=Mock(side_effect=lambda timeout:bytes((0x43,2,0,d.send_packet.call_args.args[0][3],0,0,0,0,0,0)))
        with patch('gd101_driver.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TimeoutError):d.write_kline_wire(bytes(1001),timing=0,timeout=.01)
        self.assertEqual(d.send_packet.call_count,3)
        self.assertEqual(d.receive_packet.call_count,2)
        self.assertTrue(d.tx_uncertain)

    def test_long_message_interbyte_pacing(self):
        d=session([]);clock=[0.0];sent_at=[]
        d.send_packet.side_effect=lambda *a,**kw:sent_at.append(clock[0])
        d.receive_packet=Mock(side_effect=lambda timeout:bytes((0x43,2,0,d.send_packet.call_args.args[0][3],0,0,0,0,0,0)))
        def sleep(seconds):clock[0]+=seconds
        with patch('gd101_driver.time.monotonic',side_effect=lambda:clock[0]),patch('gd101_driver.time.sleep',side_effect=sleep) as wait:
            d.write_kline_wire(bytes(1001),timing=5,timeout=10)
        self.assertEqual(wait.call_count,1000)
        self.assertEqual(len(sent_at),1001)
        for before,after in zip(sent_at,sent_at[1:]):self.assertAlmostEqual(after-before,.005)
        self.assertAlmostEqual(clock[0],5.0)

    def test_matching_completion_and_interleaved_events(self):
        event=bytes.fromhex('4303000100000055')
        wrong=bytes.fromhex('430b0001000200000000')
        good=bytes.fromhex('430b0000000300000000')
        driver=session([event,wrong,good])
        result=driver.write_kline_wire(b'\x55\x55')
        self.assertEqual(result.timestamp,3)
        self.assertFalse(driver.tx_uncertain)
        self.assertEqual(list(driver.events),[event,wrong])
        driver.send_packet.assert_called_once_with(b'\x01\0\0\x05\x55\x55',extended=True)

    def test_timeout_never_retransmits(self):
        driver=session([TimeoutError('simulated wait timeout')])
        with self.assertRaises(TimeoutError): driver.write_kline_wire(b'\x55\x55')
        with self.assertRaises(GD101Error): driver.write_kline_wire(b'\x55\x55')
        driver.send_packet.assert_called_once()
        self.assertTrue(driver.tx_uncertain)

    def test_rejection_is_reported(self):
        driver=session([bytes.fromhex('430b0000070300000000')])
        with self.assertRaisesRegex(GD101Error,'status 7'):
            driver.write_kline_wire(b'\x55\x55')
        self.assertFalse(driver.tx_uncertain)
        driver.send_packet.assert_called_once()

    def test_invalid_timeout_never_sends(self):
        for timeout in (0,-1,float('inf'),float('nan')):
            driver=session([])
            with self.assertRaises(ValueError): driver.write_kline_wire(b'a',timeout=timeout)
            driver.send_packet.assert_not_called()


if __name__=='__main__': unittest.main()
