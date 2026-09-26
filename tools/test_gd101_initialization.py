import unittest
from collections import deque
from unittest.mock import Mock
from gd101_driver import GD101,GD101Error
from gd101_kline import parse_kline_init_reply


def driver(replies):
    d=object.__new__(GD101)
    d.kline_open=True
    d.tx_uncertain=d.init_uncertain=False
    d.receiver=None
    d.events=deque()
    d.port=Mock()
    d.exchange=Mock(side_effect=replies)
    return d


class InitializationTests(unittest.TestCase):
    def test_five_baud_encrypted_replies_preserve_receiver_filters(self):
        from test_gd101_receiver import FakePort
        from gd101_native import encode_short,decode_frame
        from gd101_kline import KLineAssembler,KLineFilter
        key=bytes(range(32));requests=[]
        class InitPort(FakePort):
            def reset_input_buffer(self):
                with self.condition:self.data.clear()
            def flush(self):pass
            def write(self,wire):
                request=decode_frame(wire,key);requests.append(request)
                if request[:2]==b'\x01\x02':reply=bytes.fromhex('41020000')
                elif request[:2]==b'\x03\x02':reply=bytes.fromhex('43050000a02800000808')
                else:raise AssertionError('Unexpected five-baud command')
                self.inject(encode_short(reply,key));return len(wire)
        d=driver([]);d.port=InitPort();d.tx_key=d.rx_key=key
        d.record={'exchanges':[]};d.can_open=False;d.exchange=GD101.exchange.__get__(d)
        filters=(KLineFilter(1,b'\xff',b'\x83'),)
        d.start_receiver(assembler=KLineAssembler(filters=filters,no_checksum=True))
        d.receiver.set_kline_gap(.007)
        previous=d.receiver
        try:
            self.assertEqual(d.initialize_kline('five_baud',b'\x33',no_checksum=True),b'\x08\x08')
            self.assertEqual([p[:2] for p in requests],[b'\x01\x02',b'\x03\x02'])
            self.assertFalse(previous.thread.is_alive())
            self.assertTrue(d.receiver.thread.is_alive())
            self.assertEqual(d.receiver.kline_filters(),filters)
            self.assertEqual(d.receiver.kline_gap(),.007)
            self.assertTrue(d.receiver.assembler.no_checksum)
            self.assertFalse(d.init_uncertain)
        finally:
            if d.receiver is not None:d.receiver.stop()

    def test_response_bytes_before_handshake_ack_are_not_lost(self):
        from test_gd101_receiver import FakePort
        from gd101_native import encode_short,decode_frame
        from gd101_kline import KLineAssembler,KLineFilter
        key=bytes(range(32))
        response=bytes.fromhex('83f046c10808')
        class InitPort(FakePort):
            def reset_input_buffer(self):
                with self.condition:self.data.clear()
            def flush(self):pass
            def write(self,wire):
                request=decode_frame(wire,key)
                if request[:2]==b'\x01\x03':
                    self.inject(encode_short(bytes.fromhex('41030000'),key))
                elif request[:2]==b'\x03\x03':
                    frames=[]
                    for index,value in enumerate(response+bytes([sum(response)&255])):
                        event=b'\x43\x03\0'+(index+1).to_bytes(4,'little')+bytes([value])
                        frames.append(encode_short(event,key))
                    # ECU bytes deliberately precede the adapter's handshake ACK.
                    self.inject(b''.join(frames)+encode_short(bytes.fromhex('43040000'),key))
                else:raise AssertionError('Unexpected initialization command')
                return len(wire)
        d=driver([]);d.port=InitPort();d.tx_key=d.rx_key=key
        d.record={'exchanges':[]};d.can_open=False
        d.exchange=GD101.exchange.__get__(d)
        # Existing application filters reject all traffic. Initialization must
        # still return its response without weakening normal receive filtering.
        normal_filters=(KLineFilter(2,b'',b''),)
        d.start_receiver(assembler=KLineAssembler(filters=normal_filters))
        try:
            result=d.initialize_kline('fast',bytes.fromhex('8146f08138'),flags=0x200,
                capture_response=True,response_timeout=1)
            self.assertEqual(result.data,response)
            self.assertEqual((result.first_timestamp,result.last_timestamp),(1,7))
            self.assertFalse(d.init_uncertain)
            self.assertEqual(len(d.record['exchanges']),2)
            self.assertEqual(d.receiver.kline_filters(),normal_filters)
            with self.assertRaises(TimeoutError):d.read_message(0)
            # A second initialization must stop/join the prior reader and create
            # a new one, rather than leaving two threads consuming USB replies.
            prior=d.receiver
            again=d.initialize_kline('fast',bytes.fromhex('8146f08138'),flags=0x200,
                capture_response=True,response_timeout=1)
            self.assertEqual(again.data,response)
            self.assertFalse(prior.thread.is_alive())
            self.assertEqual(d.receiver.kline_filters(),normal_filters)
            self.assertEqual(len(d.record['exchanges']),4)
        finally:
            if d.receiver is not None:d.receiver.stop()

    def test_fast_response_reader_starts_before_handshake(self):
        from gd101_kline import KLineMessage
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        order=[]
        def start(**kwargs):
            order.append('reader');self.assertTrue(kwargs['assembler'].no_checksum)
        d.start_receiver=Mock(side_effect=start)
        replies=iter([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        d.exchange=Mock(side_effect=lambda *a,**kw:(order.append('handshake'),next(replies))[1])
        response=KLineMessage(0,1,2,bytes.fromhex('83f010c1ea8f'))
        d.read_kline_initialization=Mock(return_value=response)
        self.assertEqual(d.initialize_kline('fast',bytes.fromhex('8110f18163'),flags=0x200,
            capture_response=True,response_timeout=.4,no_checksum=True),response)
        self.assertEqual(order,['reader','handshake','handshake'])
        d.read_kline_initialization.assert_called_once_with(.4)
        self.assertFalse(d.init_uncertain)

    def test_fast_ack_without_ecu_response_remains_uncertain(self):
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        d.start_receiver=Mock();d.read_kline_initialization=Mock(side_effect=TimeoutError('no ECU'))
        with self.assertRaises(TimeoutError):
            d.initialize_kline('fast',b'\x81',capture_response=True,response_timeout=.4)
        self.assertTrue(d.init_uncertain)

    def test_failed_handshake_cancels_capture_without_clearing_uncertainty(self):
        d=driver([bytes.fromhex('41030007')])
        receiver=Mock()
        def start(**kwargs):d.receiver=receiver
        d.start_receiver=Mock(side_effect=start)
        with self.assertRaises(GD101Error):
            d.initialize_kline('fast',b'\x81',capture_response=True,response_timeout=.4)
        receiver.cancel_kline_initialization.assert_called_once_with()
        self.assertTrue(d.init_uncertain)

    def test_no_checksum_flag_does_not_append_second_checksum(self):
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        wire=bytes.fromhex('8146f08138')
        d.initialize_kline('fast',wire,flags=0x200)
        payload=d.exchange.call_args_list[0].args[0]
        self.assertEqual(payload[11],len(wire))
        self.assertEqual(payload[12:12+len(wire)],wire)

    def test_five_baud_keywords_and_sequence(self):
        d=driver([bytes.fromhex('41020000'),bytes.fromhex('43050000a02800000808')])
        self.assertEqual(d.initialize_kline('five_baud',b'\x33'),b'\x08\x08')
        self.assertEqual([c.args[1] for c in d.exchange.call_args_list],
                         [b'\x41\x02\0',b'\x43\x05\0'])
        self.assertFalse(d.init_uncertain)

    def test_fast_sequence_padding(self):
        d=driver([bytes.fromhex('41030000'),bytes.fromhex('43040000')])
        self.assertIsNone(d.initialize_kline('fast',bytes.fromhex('8110f181')))
        calls=d.exchange.call_args_list
        self.assertEqual(calls[1].args,(bytes.fromhex('03030000'),b'\x43\x04\0'))
        self.assertEqual(len(calls[0].args[0])%2,0)

    def test_first_stage_failure_stops_handshake_and_latches(self):
        d=driver([bytes.fromhex('41020007')])
        with self.assertRaises(GD101Error): d.initialize_kline('five_baud',b'\x33')
        self.assertEqual(d.exchange.call_count,1)
        with self.assertRaises(GD101Error): d.initialize_kline('five_baud',b'\x33')
        self.assertEqual(d.exchange.call_count,1)

    def test_wrong_selector_and_truncated_keywords(self):
        for payload in (bytes.fromhex('43050100a02800000808'),bytes.fromhex('43050000')):
            with self.assertRaises(ValueError): parse_kline_init_reply(9,payload)


if __name__=='__main__': unittest.main()
