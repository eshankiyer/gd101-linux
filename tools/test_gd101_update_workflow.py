import hashlib
import struct
import unittest
import zlib
from gd101_update_workflow import UpdateWorkflow


def image():
    header=bytearray(512);header[8:10]=b'\x0f\x01'
    payload=bytes(range(256))*2
    struct.pack_into('<I',header,0x0c,512)
    struct.pack_into('<I',header,0x14,zlib.crc32(payload))
    struct.pack_into('<I',header,0x1c,0x08008200)
    return bytes(header)+payload


class Session:
    def __init__(self, *, mode=0, fail_at=None):
        self.mode=mode;self.calls=[];self.fail_at=fail_at
    def exchange(self, command, payload, *, timeout):
        self.calls.append((command,payload))
        if len(self.calls)==self.fail_at:raise TimeoutError('Injected timeout')
        if command==0xf023:return self.mode
        if command==0xf003:self.mode=int.from_bytes(payload[:2],'little')
        return b'\0'


class WorkflowTests(unittest.TestCase):
    def setup_workflow(self,session=None,verification=None):
        session=session or Session()
        workflow=UpdateWorkflow(session,reconnect=lambda:session,sleep=lambda _:None,
            verify_native=lambda:verification if verification is not None else {'version':'1.15','driver_verified':True})
        return session,workflow
    def run_image(self,workflow,data=None):
        data=image() if data is None else data
        return workflow.run(data,expected_sha256=hashlib.sha256(data).hexdigest())
    def test_complete_requires_both_integrity_and_native_verification(self):
        session,workflow=self.setup_workflow()
        result=self.run_image(workflow)
        self.assertEqual(result.version,'1.15')
        self.assertEqual(result.blocks_acknowledged,2)
        self.assertTrue(result.integrity_verified and result.native_driver_verified)
        self.assertEqual([c for c,_ in session.calls],[0xf023,0xf003,0xf023,0xf000,0xf001,0xf002,0xf002,0xf006,0xf003])
        self.assertEqual(b''.join(d for c,d in session.calls if c==0xf002),image())
    def test_each_timeout_stops_without_cleanup_commands(self):
        for fail_at in range(1,10):
            with self.subTest(fail_at=fail_at):
                session,workflow=self.setup_workflow(Session(fail_at=fail_at))
                with self.assertRaises(TimeoutError):self.run_image(workflow)
                self.assertEqual(len(session.calls),fail_at)
                with self.assertRaises(RuntimeError):self.run_image(workflow)
                self.assertEqual(len(session.calls),fail_at)
                self.assertNotEqual(workflow.stage,'complete')
    def test_incorrect_version_or_unverified_driver_is_not_success(self):
        for result in [{'version':'1.14','driver_verified':True},{'version':'1.15','driver_verified':False},{}]:
            with self.subTest(result=result):
                _,workflow=self.setup_workflow(verification=result)
                with self.assertRaisesRegex(RuntimeError,'Post-update'):self.run_image(workflow)
                self.assertEqual(workflow.stage,'verify_native_driver')
    def test_no_erase_if_bootloader_transition_did_not_occur(self):
        session,workflow=self.setup_workflow()
        failed_reconnect=Session(mode=0)
        workflow.reconnect=lambda:failed_reconnect
        with self.assertRaisesRegex(RuntimeError,'Bootloader'):self.run_image(workflow)
        self.assertEqual([c for c,_ in failed_reconnect.calls],[0xf023])
    def test_unknown_mode_is_not_reset_or_erased(self):
        session,workflow=self.setup_workflow(Session(mode=2))
        with self.assertRaisesRegex(RuntimeError,'Unrecognized'):self.run_image(workflow)
        self.assertEqual(len(session.calls),1)
    def test_image_pin_checks_header_before_io(self):
        session,workflow=self.setup_workflow()
        data=bytearray(image());data[0]^=1
        with self.assertRaisesRegex(ValueError,'reviewed'):
            workflow.run(data,expected_sha256=hashlib.sha256(image()).hexdigest())
        self.assertEqual(session.calls,[])
    def test_wrong_memory_layout_rejected_before_io(self):
        session,workflow=self.setup_workflow()
        data=bytearray(image());struct.pack_into('<I',data,0x1c,0x08000200)
        with self.assertRaisesRegex(ValueError,'memory layout'):self.run_image(workflow,data)
        self.assertEqual(session.calls,[])
    def test_already_bootloader_does_not_reset_before_erasing(self):
        session,workflow=self.setup_workflow(Session(mode=1))
        self.run_image(workflow)
        self.assertEqual([c for c,_ in session.calls[:3]],[0xf023,0xf023,0xf000])

if __name__=='__main__':unittest.main()
