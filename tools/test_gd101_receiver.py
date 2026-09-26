import threading
import unittest
from gd101_receiver import FrameBuffer, PacketReceiver
from gd101_native import encode_short, encode_extended
from gd101_kline import KLineAssembler, KLineFilter


class FakePort:
    def __init__(self):
        self.data=bytearray()
        self.condition=threading.Condition()

    @property
    def in_waiting(self):
        with self.condition: return len(self.data)

    def inject(self,data):
        with self.condition:
            self.data.extend(data)
            self.condition.notify_all()

    def read(self,count):
        with self.condition:
            if not self.data: self.condition.wait(.005)
            chunk=bytes(self.data[:min(count,3)])
            del self.data[:len(chunk)]
            return chunk


class ReceiverTests(unittest.TestCase):
    def test_fragmentation_all_boundaries_and_failure_latch(self):
        key=bytes(range(32))
        payloads=[b'\x41\x01\0\0',bytes(range(255)),b'\0\x03']
        wire=encode_short(payloads[0],key)+encode_extended(payloads[1],key)+encode_short(payloads[2])
        for cut in range(len(wire)+1):
            decoder=FrameBuffer(key)
            output=decoder.feed(wire[:cut])+decoder.feed(wire[cut:])
            self.assertEqual([p for _,p in output],payloads)
            self.assertFalse(decoder.pending)
        decoder=FrameBuffer(key)
        with self.assertRaises(ValueError): decoder.feed(b'\0')
        with self.assertRaises(ValueError): decoder.feed(encode_short(b'\0\x03'))

    def test_background_idle_flush_and_command_reply(self):
        port=FakePort()
        assembler=KLineAssembler(filters=[KLineFilter(1,b'',b'')])
        receiver=PacketReceiver(port,None,assembler=assembler)
        receiver.start()
        try:
            # A complete checksummed message and a command reply in one stream.
            port.inject(encode_short(bytes.fromhex('4303000100000055'))+
                        encode_short(bytes.fromhex('4303000200000055'))+
                        encode_short(bytes.fromhex('41010000')))
            self.assertEqual(receiver.read_packet(1).payload,bytes.fromhex('41010000'))
            message=receiver.read_message(1)
            self.assertEqual(message.data,b'\x55')
            self.assertEqual(message.last_timestamp,2)
            with self.assertRaises(TimeoutError): receiver.read_message(.01)
        finally:
            receiver.stop()
        self.assertFalse(receiver.thread.is_alive())

    def test_crc_failure_reaches_waiting_caller(self):
        port=FakePort()
        receiver=PacketReceiver(port,None)
        receiver.start()
        try:
            port.inject(b'\x01\0\x03\xff')
            with self.assertRaisesRegex(RuntimeError,'reader failed'):
                receiver.read_packet(1)
            self.assertIsInstance(receiver.error,ValueError)
        finally: receiver.stop()

    def test_queue_overflow_fails_explicitly(self):
        port=FakePort()
        receiver=PacketReceiver(port,None,limit=1)
        receiver.start()
        try:
            port.inject(encode_short(b'\0\x03')*2)
            self.assertTrue(receiver.stop_event.wait(1))
            self.assertIn('overflow',str(receiver.error))
        finally: receiver.stop()

    def test_stop_wakes_waiting_consumer(self):
        port=FakePort()
        receiver=PacketReceiver(port,None)
        receiver.start()
        receiver.stop()
        with self.assertRaisesRegex(RuntimeError,'stopped'):
            receiver.read_packet(1)


if __name__=='__main__': unittest.main()
