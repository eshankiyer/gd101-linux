import struct
import unittest
import zlib
from gd101_firmware import inspect_firmware, parse_metadata


def metadata(version='1.15'):
    fields={'name':'nano_fw','version':version,'hash':'0123456789abcdef'*2}
    out=bytearray([len(fields)])
    for key,value in fields.items():
        for text in [key,value]:out.extend(bytes([len(text)])+text.encode('ascii'))
    return bytes(out)


def firmware():
    header=bytearray(512);header[8:10]=bytes([15,1])
    payload=bytes(range(256))*2
    struct.pack_into('<I',header,0x0c,512)
    struct.pack_into('<I',header,0x14,zlib.crc32(payload))
    struct.pack_into('<I',header,0x1c,0x08008200)
    return bytes(header)+payload


class FirmwareInspectionTests(unittest.TestCase):
    def test_matching_metadata_and_crc(self):
        result=inspect_firmware(firmware(),metadata())
        self.assertEqual((result.version,result.size,result.blocks),('1.15',1024,2))
        self.assertEqual(result.updater_base_address,0x08008000)
        self.assertFalse(result.authenticity_verified)
        self.assertFalse(result.hardware_compatibility_verified)
    def test_corrupted_payload_rejected(self):
        data=bytearray(firmware());data[-1]^=1
        with self.assertRaisesRegex(ValueError,'CRC32'):inspect_firmware(data)
    def test_partial_or_extra_block_rejected(self):
        for data in [b'',bytes(512),firmware()[:-1],firmware()+b'x']:
            with self.subTest(size=len(data)),self.assertRaises(ValueError):inspect_firmware(data)
    def test_wrong_version_rejected(self):
        with self.assertRaisesRegex(ValueError,'disagree'):inspect_firmware(firmware(),metadata('1.14'))
    def test_header_is_not_authenticated_by_payload_crc(self):
        data=bytearray(firmware());data[0]^=1
        self.assertEqual(inspect_firmware(data).payload_crc32,inspect_firmware(firmware()).payload_crc32)
        self.assertFalse(inspect_firmware(data).authenticity_verified)
    def test_original_default_load_address(self):
        data=bytearray(firmware());struct.pack_into('<I',data,0x1c,0xffffffff)
        self.assertEqual(inspect_firmware(data).updater_base_address,0x08008000)
    def test_metadata_fields(self):
        self.assertEqual(parse_metadata(metadata())['version'],'1.15')
    def test_bad_metadata(self):
        for data in [b'',b'\1',b'\1\0',metadata()[:-1],metadata()+b'x',b'\1\1\xff\1a',b'\2\1a\1b\1a\1c']:
            with self.subTest(data=data),self.assertRaises(ValueError):parse_metadata(data)


if __name__=='__main__':unittest.main()
