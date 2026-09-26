import unittest
from gd101_baseline import supported_pid_responses


class ResponseTests(unittest.TestCase):
    def test_real_failure_capture_and_prompt_are_not_vehicle_replies(self):
        for response in ['NO DATA\r\r>', '\r>', 'OK\r>', '?\r>', '0100\r>']:
            self.assertEqual(supported_pid_responses(response), [])

    def test_synthetic_padded_and_unpadded_responses(self):
        for response in ['7E8 06 41 00 BE 3F A8 13\r>',
                         '7E8064100BE3FA81300\r>']:
            self.assertEqual(supported_pid_responses(response), [
                {'can_id': '7E8', 'supported_pids_01_20_mask': 'be3fa813'}])

    def test_wrong_service_pid_length_or_transmit_echo(self):
        for response in ['7DF 06 41 00 BE 3F A8 13',
                         '7E8 06 41 20 BE 3F A8 13',
                         '7E8 06 49 00 BE 3F A8 13',
                         '7E8 06 41 00 BE 3F A8',
                         '7E8 07 41 00 BE 3F A8 13']:
            self.assertEqual(supported_pid_responses(response), [])


if __name__ == '__main__':
    unittest.main()
