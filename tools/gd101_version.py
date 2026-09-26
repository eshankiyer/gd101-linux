"""Firmware text mapping from original parser 7ab780b0."""


def firmware_version_text(payload):
    payload=bytes(payload)
    if len(payload)<29 or payload[:3]!=b'\x40\x03\0':
        raise ValueError('Invalid GD101 version response')
    serial=payload[5:13]
    if any(value<32 or value>126 for value in serial):
        raise ValueError('Invalid GD101 version serial text')
    return f'SN:{serial.decode("ascii")},FW:{payload[4]}.{payload[3]}'
