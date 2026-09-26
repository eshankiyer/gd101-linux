"""GD101 update orchestration with injectable transport and recovery callbacks.

This module has no hardware entry point. Connection ownership, power checks,
firmware provenance and serial recovery must be established by its caller.
In particular, CRC validation is not firmware authentication.
"""
from dataclasses import dataclass
import struct
import time
from gd101_firmware import inspect_firmware


@dataclass(frozen=True)
class UpdateResult:
    version: str
    blocks_acknowledged: int
    integrity_verified: bool
    native_driver_verified: bool


class UpdateWorkflow:
    def __init__(self, session, *, reconnect, verify_native, sleep=time.sleep):
        self.sleep = sleep
        self.session = session
        self.reconnect = reconnect
        self.verify_native = verify_native
        self.started = False
        self.stage = 'not_started'
        self.blocks_acknowledged = 0
        self.integrity_verified = False
        self.error = None

    def run(self, firmware, *, expected_sha256, metadata=None):
        if self.started:
            raise RuntimeError('Update workflow is single-use; explicit recovery required')
        # Check the exact reviewed image, including its unauthenticated header,
        # before sending any command. A digest provided by the caller is a pin,
        # not a claim of manufacturer signature verification.
        firmware = bytes(firmware)
        info = inspect_firmware(firmware, metadata)
        if info.sha256 != expected_sha256:
            raise ValueError('Firmware differs from the reviewed image')
        if info.header_payload_offset != 512 or info.updater_base_address != 0x08008000:
            raise ValueError('Unverified firmware memory layout')
        self.started = True
        try:
            self.stage = 'query_mode'
            mode = self.session.exchange(0xf023, bytes(6), timeout=1)
            if mode not in (0, 1):
                raise RuntimeError('Unrecognized adapter mode')
            if mode == 0:
                self.stage = 'enter_bootloader'
                self.session.exchange(0xf003, b'\1'+bytes(5), timeout=3)
                self.stage = 'reconnect_bootloader'
                self.sleep(2)
                self.session = self.reconnect()
            # Unlike the vendor worker, verify the new mode before erasing.
            self.stage = 'verify_bootloader'
            if self.session.exchange(0xf023, bytes(6), timeout=1) != 1:
                raise RuntimeError('Bootloader mode was not confirmed')
            self.stage = 'erase'
            self.session.exchange(0xf000,struct.pack('<II',info.updater_base_address,info.size),timeout=3)
            self.stage = 'set_address'
            self.session.exchange(0xf001,struct.pack('<II',info.updater_base_address,512),timeout=3)
            self.stage = 'write'
            for offset in range(0,info.size,512):
                self.session.exchange(0xf002,firmware[offset:offset+512],timeout=5)
                self.blocks_acknowledged += 1
                self.sleep(0.01)
            self.stage = 'integrity'
            self.session.exchange(0xf006,bytes(6),timeout=3)
            self.integrity_verified = True
            self.stage = 'return_application'
            self.session.exchange(0xf003,bytes(6),timeout=3)
            self.stage = 'verify_native_driver'
            # Callback must independently authenticate and read the installed
            # version through the normal driver after resetting/reopening USB.
            observed = self.verify_native()
            if observed.get('version') != info.version or observed.get('driver_verified') is not True:
                raise RuntimeError('Post-update version/driver verification failed')
            self.stage = 'complete'
            return UpdateResult(info.version,self.blocks_acknowledged,True,True)
        except Exception as exc:
            # No automatic retries, reboots or further commands after uncertainty.
            # Retain the failed stage and acknowledged count for recovery.
            self.error = str(exc)
            raise
