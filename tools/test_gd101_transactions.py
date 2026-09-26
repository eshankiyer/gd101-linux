import threading
import time
import unittest
from unittest.mock import Mock,patch
from collections import deque
from gd101_driver import GD101
from gd101_transactions import operation_lock

class TransactionTests(unittest.TestCase):
    def session(self):
        d=object.__new__(GD101);d.record={'exchanges':[]};d.events=deque();d.can_open=False
        return d

    def test_exchange_keeps_send_and_ack_together(self):
        d=self.session();entered=threading.Event();release=threading.Event();order=[]
        d.send_packet=lambda data:order.append(data) or data
        def receive(timeout):
            if len(order)==1:entered.set();release.wait(2)
            return b'ack'
        d.receive_packet=receive
        threads=[threading.Thread(target=lambda data=data:d.exchange(data,b'ack')) for data in (b'one',b'two')]
        try:
            threads[0].start();self.assertTrue(entered.wait(1));threads[1].start()
            time.sleep(.03);self.assertEqual(order,[b'one'])
            release.set()
            for t in threads:t.join(1);self.assertFalse(t.is_alive())
            self.assertEqual(order,[b'one',b'two'])
        finally:
            release.set()
            for t in threads:
                if t.ident:t.join(2)

    def test_contended_timeout_sends_nothing(self):
        d=self.session();d.send_packet=Mock();errors=[]
        lock=operation_lock(d)
        with lock:
            def run():
                try:d.exchange(b'x',b'ack',timeout=.02)
                except Exception as error:errors.append(error)
            t=threading.Thread(target=run);t.start();t.join(1)
            self.assertFalse(t.is_alive())
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],TimeoutError)
        d.send_packet.assert_not_called()

    def test_waiting_message_reader_does_not_own_transaction(self):
        d=self.session();entered=threading.Event();release=threading.Event()
        d.receiver=Mock()
        d.receiver.read_message.side_effect=lambda timeout:(entered.set(),release.wait(2))[-1]
        t=threading.Thread(target=d.read_message);t.start()
        try:
            self.assertTrue(entered.wait(1));lock=operation_lock(d)
            self.assertTrue(lock.acquire(timeout=.02));lock.release()
        finally:release.set();t.join(1)

    def test_nested_version_transaction_is_reentrant(self):
        d=self.session();reply=b'\x40\x03\0'+bytes(29)
        d.send_packet=Mock(return_value=b'frame');d.receive_packet=Mock(return_value=reply)
        self.assertEqual(d.read_version(),reply)
        self.assertEqual(d.send_packet.call_count,1)

    def test_lock_wait_is_deducted_from_timeout(self):
        from gd101_transactions import serialized_operation
        class Session:
            @serialized_operation
            def operation(self,timeout=4):return timeout
        s=Session();s._operation_lock=Mock()
        s._operation_lock.acquire.side_effect=[False,True]
        with patch('gd101_transactions.time.monotonic',side_effect=[10,10.25]):
            self.assertEqual(s.operation(timeout=1),.75)
        s._operation_lock.release.assert_called_once()

if __name__=='__main__':unittest.main()
