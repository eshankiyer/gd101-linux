import unittest
from gd101_kline import KLineFilter, KLineByte, KLineAssembler, finish_kline_message

PASS_ALL=KLineFilter(1,b'',b'')


class MessageTests(unittest.TestCase):
    def test_checksum_and_filter_policy(self):
        wire=bytes.fromhex('486b41100004')  # additive checksum wraps to 04
        self.assertEqual(finish_kline_message(wire,[PASS_ALL]),wire[:-1])
        self.assertIsNone(finish_kline_message(wire,[]))
        block=KLineFilter(2,b'\xf0',b'\x40')
        self.assertIsNone(finish_kline_message(wire,[PASS_ALL,block]))
        # Pattern bits outside the mask are not normalized by the driver.
        self.assertEqual(finish_kline_message(wire,[PASS_ALL,KLineFilter(2,b'\xf0',b'\x48')]),wire[:-1])
        self.assertIsNone(finish_kline_message(wire[:-1]+b'\x05',[PASS_ALL]))
        self.assertIsNone(finish_kline_message(b'\0',[PASS_ALL]))
        self.assertEqual(finish_kline_message(b'\0',[PASS_ALL],no_checksum=True),b'\0')

    def test_idle_flush_and_adapter_timestamp_wrap(self):
        assembler=KLineAssembler(filters=[PASS_ALL])
        self.assertIsNone(assembler.feed(KLineByte(0,0xffffffff,0x55),1.0))
        self.assertIsNone(assembler.feed(KLineByte(0,0,0x55),1.001))
        self.assertIsNone(assembler.expire(1.010))
        message=assembler.expire(1.030)
        self.assertEqual(message.data,b'\x55')
        self.assertEqual((message.first_timestamp,message.last_timestamp),(0xffffffff,0))
        self.assertIsNone(assembler.expire(1.1))

    def test_next_message_does_not_merge_after_gap(self):
        assembler=KLineAssembler(filters=[PASS_ALL],no_checksum=True)
        assembler.feed(KLineByte(0,1,0xaa),1.0)
        result=assembler.feed(KLineByte(0,2,0xbb),1.1)
        self.assertEqual(result.data,b'\xaa')
        self.assertEqual(assembler.expire(1.2).data,b'\xbb')

    def test_invalid_clock_and_channel(self):
        assembler=KLineAssembler()
        assembler.feed(KLineByte(0,0,1),5)
        with self.assertRaises(ValueError): assembler.expire(4)
        with self.assertRaises(ValueError): assembler.expire(float('nan'))
        with self.assertRaises(ValueError): assembler.feed(KLineByte(1,0,1),6)
        self.assertEqual(assembler.pending,b'\x01')


if __name__=='__main__': unittest.main()
