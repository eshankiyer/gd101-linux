"""Exercise real Linux serial I/O over a PTY, never the physical adapter."""
import os
import hashlib
import zlib
import pty
import select
import struct
import threading
import time
import unittest
from unittest.mock import patch
from gd101_update_serial import UpdateSerial
from gd101_update_session import UpdateSession
from gd101_update_workflow import UpdateWorkflow
from gd101_update_protocol import crc16,read_mode_command
from gd101_devices import GD101DeviceInUse


class SerialTransportTests(unittest.TestCase):
    def setUp(self):
        self.master,self.slave=pty.openpty()
        self.path=os.ttyname(self.slave)
        self.selector=patch('gd101_update_serial.select_gd101_port',return_value=self.path)
        self.mock_selector=self.selector.start()
    def tearDown(self):
        self.selector.stop()
        os.close(self.master);os.close(self.slave)
    def test_fragmented_roundtrip_and_exclusive_open(self):
        errors=[];received=[]
        reply=struct.pack('<HHB',5,0xf023,1)
        reply=(reply+struct.pack('<H',crc16(reply))).ljust(8,b'\0')
        def peer():
            try:
                data=bytearray();deadline=time.monotonic()+2
                while len(data)<16:
                    ready,_,_=select.select([self.master],[],[],max(0,deadline-time.monotonic()))
                    if not ready:raise TimeoutError('No updater request')
                    data.extend(os.read(self.master,16-len(data)))
                received.append(bytes(data))
                for block in (reply[:1],reply[1:3],reply[3:5],reply[5:]):
                    os.write(self.master,block)
                    time.sleep(.005)
            except BaseException as exc:errors.append(exc)
        with UpdateSerial('MT000418') as port:
            # No commands or hidden startup bytes are sent merely by opening.
            self.assertEqual(select.select([self.master],[],[],.02)[0],[])
            with self.assertRaises(GD101DeviceInUse):
                with UpdateSerial('MT000418'):pass
            worker=threading.Thread(target=peer);worker.start()
            try:
                self.assertEqual(UpdateSession(port).exchange(0xf023,bytes(6),timeout=1),1)
            finally:worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(received,[read_mode_command()])
        self.mock_selector.assert_called_with('MT000418',requested=None)
    def test_partial_reply_times_out_and_session_prohibits_more_io(self):
        with UpdateSerial('MT000418') as port:
            session=UpdateSession(port)
            os.write(self.master,b'\x05\x00')
            started=time.monotonic()
            with self.assertRaises(TimeoutError):session.exchange(0xf023,bytes(6),timeout=.05)
            self.assertLess(time.monotonic()-started,.5)
            self.assertEqual(os.read(self.master,4096),read_mode_command())
            with self.assertRaises(RuntimeError):session.exchange(0xf003,bytes(6),timeout=.05)
            self.assertEqual(select.select([self.master],[],[],.02)[0],[])
    def test_full_workflow_over_linux_serial(self):
        header=bytearray(512);header[8:10]=b'\x0f\x01'
        payload=bytes(range(256))*4
        struct.pack_into('<I',header,12,512)
        struct.pack_into('<I',header,20,zlib.crc32(payload))
        struct.pack_into('<I',header,28,0x08008200)
        firmware=bytes(header)+payload
        errors=[];received=[];written=[]
        def peer():
            mode=0
            try:
                def read_exact(count):
                    data=bytearray();deadline=time.monotonic()+2
                    while len(data)<count:
                        if not select.select([self.master],[],[],max(0,deadline-time.monotonic()))[0]:
                            raise TimeoutError('Peer deadline')
                        data.extend(os.read(self.master,count-len(data)))
                    return bytes(data)
                while True:
                    prefix=read_exact(4)
                    size,command=struct.unpack('<HH',prefix)
                    frame=prefix+read_exact(((size+9)&~7)-4)
                    self.assertEqual(struct.unpack_from('>H',frame,size)[0],crc16(frame[:size]))
                    data=frame[4:size];received.append(command)
                    if command==0xf003:mode=int.from_bytes(data[:2],'little')
                    if command==0xf002:written.append(data)
                    status=mode if command==0xf023 else 0
                    response=struct.pack('<HHB',5,command,status)
                    response=(response+struct.pack('<H',crc16(response))).ljust(8,b'\0')
                    os.write(self.master,response[:3]);os.write(self.master,response[3:])
                    if command==0xf003 and mode==0:return
            except BaseException as exc:errors.append(exc)
        with UpdateSerial('MT000418') as port:
            worker=threading.Thread(target=peer);worker.start()
            session=UpdateSession(port)
            workflow=UpdateWorkflow(session,reconnect=lambda:UpdateSession(port),sleep=lambda _:None,
                verify_native=lambda:{'version':'1.15','driver_verified':True})
            try:
                result=workflow.run(firmware,expected_sha256=hashlib.sha256(firmware).hexdigest())
            finally:worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(b''.join(written),firmware)
        self.assertEqual(result.blocks_acknowledged,3)
        self.assertEqual(received,[0xf023,0xf003,0xf023,0xf000,0xf001,0xf002,0xf002,0xf002,0xf006,0xf003])

    def test_closed_transport_and_invalid_deadlines(self):
        port=UpdateSerial('MT000418')
        with self.assertRaises(RuntimeError):port.read_exact(4,timeout=1)
        with port:
            for timeout in (0,-1,float('nan'),float('inf')):
                with self.subTest(timeout=timeout),self.assertRaises(ValueError):
                    UpdateSession(port).exchange(0xf023,bytes(6),timeout=timeout)
            self.assertEqual(select.select([self.master],[],[],.02)[0],[])
        self.assertIsNone(port.port)

if __name__=='__main__':unittest.main()
