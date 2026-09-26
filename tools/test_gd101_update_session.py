import struct
import unittest
from gd101_update_protocol import crc16
from gd101_update_session import UpdateSession


def reply(command, status=0):
    frame=struct.pack('<HHB',5,command,status)
    return (frame+struct.pack('<H',crc16(frame))).ljust(8,b'\0')


class Transport:
    def __init__(self, response, *, short_write=False):
        self.response=response;self.requests=[];self.short_write=short_write
    def write(self,data,*,timeout):
        self.requests.append(data)
        return len(data)-int(self.short_write)
    def read_exact(self,count,*,timeout):
        result,self.response=self.response[:count],self.response[count:]
        return result


class UpdateSessionTests(unittest.TestCase):
    def test_mode_is_data_not_error(self):
        transport=Transport(reply(0xf023,1))
        self.assertEqual(UpdateSession(transport).exchange(0xf023,bytes(6),timeout=1),1)
    def test_no_retry_or_reboot_after_failed_exchange(self):
        # Includes integrity failure, a stale reply, truncation, CRC failure,
        # and incomplete transmission. No follow-up request may escape.
        corrupt=bytearray(reply(0xf002));corrupt[5]^=1
        cases=[(0xf006,reply(0xf006,1),False),
               (0xf002,reply(0xf002,2),False),
               (0xf002,reply(0xf001),False),
               (0xf002,b'',False),
               (0xf002,reply(0xf002)[:-1],False),
               (0xf002,bytes(corrupt),False),
               (0xf002,reply(0xf002),True)]
        for command,response,short in cases:
            with self.subTest(command=command,response=response,short=short):
                transport=Transport(response,short_write=short)
                session=UpdateSession(transport)
                with self.assertRaises((IOError,ValueError)):
                    session.exchange(command,bytes(6),timeout=1)
                with self.assertRaises(RuntimeError):
                    session.exchange(0xf003,bytes(6),timeout=1)
                self.assertEqual(len(transport.requests),1)
    def test_successful_ack(self):
        session=UpdateSession(Transport(reply(0xf002)))
        self.assertEqual(session.exchange(0xf002,bytes(512),timeout=5),b'\0')
        self.assertFalse(session.failed)

if __name__=='__main__':unittest.main()
