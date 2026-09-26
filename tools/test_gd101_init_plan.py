import unittest
from gd101_kline import kline_init_plan


class InitPlanTests(unittest.TestCase):
    def test_fast_sequence_has_separate_handshake(self):
        steps=kline_init_plan('fast',bytes.fromhex('8110f181'))
        self.assertEqual([s.operation for s in steps],[0x1d,10])
        self.assertEqual(steps[0].payload[-5:],bytes.fromhex('8110f18103'))
        self.assertEqual(steps[1].payload,b'\x03\x03\0')
        self.assertEqual([s.timeout_ms for s in steps],[2000,475])
        self.assertEqual(kline_init_plan('fast')[1].timeout_ms,450)

    def test_five_baud_sequence(self):
        steps=kline_init_plan('five_baud',b'\x33')
        self.assertEqual([s.operation for s in steps],[0x1e,9])
        self.assertEqual(steps[0].payload,bytes.fromhex('0102002c012c010833000000'))
        self.assertEqual(steps[1].payload,bytes.fromhex('030200001400140032003200'))
        self.assertEqual(steps[1].timeout_ms,2790)

    def test_missing_address_and_bad_timing_rejected(self):
        with self.assertRaises(ValueError): kline_init_plan('five_baud')
        with self.assertRaises(ValueError): kline_init_plan('other')
        with self.assertRaises(ValueError): kline_init_plan('fast',timing_words=(1,2))


if __name__=='__main__': unittest.main()
