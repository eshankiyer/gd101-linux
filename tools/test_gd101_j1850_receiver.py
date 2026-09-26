import unittest
from gd101_receiver import PacketReceiver
from gd101_native import encode_short
from gd101_kline import KLineFilter,KLineAssembler
from test_gd101_receiver import FakePort


def record(protocol,data=b'abc',extra=b''):
    count=len(data)+1 if protocol==1 else ((len(data)+1)<<4)|len(extra)
    raw=bytes([0x43,9 if protocol==1 else 8])+b'\x78\x56\x34\x12'+bytes([count])+data+b'\xee'+extra
    return raw+bytes(len(raw)%2)


class J1850ReceiverTests(unittest.TestCase):
    def test_encrypted_receive_filters_and_separate_acknowledgments(self):
        key=bytes(range(32))
        for protocol in (1,2):
            port=FakePort();r=PacketReceiver(port,key,j1850_protocol=protocol)
            r.set_j1850_filters([KLineFilter(1,b'\xff',b'a'),KLineFilter(2,b'\xff\xff',b'ax')])
            ack=bytes([0x41,8 if protocol==1 else 7,0,0])
            extra=b'XY' if protocol==2 else b''
            r.start()
            try:
                packets=[record(protocol,b'xyz'),record(protocol,b'axz'),record(protocol,extra=extra),ack]
                port.inject(b''.join(encode_short(p,key) for p in packets))
                self.assertEqual(r.read_packet(1).payload,ack)
                message=r.read_message(1)
                self.assertEqual(message.data,b'abc'+extra)
                self.assertEqual(message.extra_data_index,3)
                self.assertEqual(message.timestamp,0x12345678)
                with self.assertRaises(TimeoutError):r.read_message(0)
                self.assertIsNone(r.error)
            finally:r.stop()

    def test_bad_content_and_overflow_do_not_lose_command_reply(self):
        port=FakePort();r=PacketReceiver(port,None,j1850_protocol=1,limit=1)
        r.set_j1850_filters([KLineFilter(1,b'',b'')]);r.start()
        ack=b'\x41\x08\0\0'
        try:
            port.inject(b''.join(encode_short(p) for p in (b'\x43\x09'+bytes(8),record(1),record(1),ack)))
            self.assertEqual(r.read_packet(1).payload,ack)
            self.assertEqual(r.j1850_error_count,1)
            with self.assertRaises(BufferError):r.read_message(0)
            self.assertEqual(r.read_message(0).data,b'abc')
            self.assertIsNone(r.error)
            r.clear_receive(1)
            self.assertEqual(len(r.j1850_filters),1)
        finally:r.stop()

    def test_empty_filters_discard_and_mode_validation(self):
        r=PacketReceiver(None,None,j1850_protocol=2)
        r._accept_j1850(record(2))
        with self.assertRaises(TimeoutError):r.read_message(0)
        with self.assertRaises(ValueError):r.set_j1850_filters([object()])
        with self.assertRaises(ValueError):PacketReceiver(None,None,j1850_protocol=3)
        with self.assertRaises(ValueError):PacketReceiver(None,None,j1850_protocol=1,assembler=KLineAssembler())


if __name__=='__main__':unittest.main()
