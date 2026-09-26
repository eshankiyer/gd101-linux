"""J2534 boundary for the recovered VKWP path, using a user's installed driver.

This library does not select MDX session properties or run on import.
It is not compatible with a bare KKL serial cable without a J2534 provider.
"""
import ctypes as C
import os
from pathlib import Path

U32 = C.c_uint32  # Windows ULONG stays 32-bit, including on 64-bit Windows.


class Message(C.Structure):
    _fields_ = [(name, U32) for name in ('ProtocolID', 'RxStatus', 'TxFlags',
                'Timestamp', 'DataSize', 'ExtraDataIndex')] + [('Data', C.c_ubyte * 4128)]

    @classmethod
    def from_frame(cls, frame):
        if not 1 <= len(frame) <= 4128:
            raise ValueError('Invalid J2534 message length')
        msg = cls(ProtocolID=4, TxFlags=0x200, DataSize=len(frame),
                  ExtraDataIndex=len(frame)-1)
        msg.Data[:len(frame)] = frame
        return msg

    def frame(self):
        if self.ProtocolID != 4 or not 1 <= self.DataSize <= 4128:
            raise ValueError('Driver returned invalid VKWP message')
        return bytes(self.Data[:self.DataSize])


class DriverError(RuntimeError):
    def __init__(self, operation, status):
        self.status = status
        super().__init__(f'{operation} failed: J2534 status 0x{status:08x}')


class Adapter:
    def __init__(self, provider):
        self.provider = provider
        self.device = None
        self.channel = None

    @classmethod
    def load(cls, dll):
        if os.name != 'nt':
            raise RuntimeError('A Windows J2534 driver requires Windows and matching Python bitness')
        path = Path(dll).resolve(strict=True)
        provider = C.WinDLL(str(path))
        ptr = C.POINTER(U32)
        msgptr = C.POINTER(Message)
        signatures = {
            'PassThruOpen': [C.c_void_p, ptr], 'PassThruClose': [U32],
            'PassThruConnect': [U32, U32, U32, U32, ptr],
            'PassThruDisconnect': [U32],
            'PassThruIoctl': [U32, U32, C.c_void_p, C.c_void_p],
            'PassThruWriteMsgs': [U32, msgptr, ptr, U32],
            'PassThruReadMsgs': [U32, msgptr, ptr, U32],
            'PassThruReadVersion': [U32, C.c_char_p, C.c_char_p, C.c_char_p],
        }
        for name, args in signatures.items():
            function = getattr(provider, name)
            function.argtypes = args
            function.restype = U32
        return cls(provider)

    def versions(self):
        """Open only the interface and query versions, without a vehicle channel.

        This executes the installed provider DLL. It does not initialize K-line,
        connect a CAN channel, or transmit a diagnostic request.
        """
        if self.device is not None:
            raise RuntimeError('Adapter already open')
        device = U32()
        self.call('PassThruOpen', None, C.byref(device))
        self.device = device.value
        try:
            firmware, dll, api = (C.create_string_buffer(80) for _ in range(3))
            self.call('PassThruReadVersion', self.device, firmware, dll, api)
            return dict(zip(('firmware', 'dll', 'api'),
                            (v.value.decode('ascii', errors='replace')
                             for v in (firmware, dll, api))))
        finally:
            self.close()

    def call(self, name, *args):
        status = getattr(self.provider, name)(*args)
        if status:
            raise DriverError(name, status)

    def connect(self, *, baud, flags):
        """Caller must supply verified session settings. No initialization yet."""
        if self.device is not None:
            raise RuntimeError('Adapter already open')
        if not 1 <= baud <= 0xFFFFFFFF or not 0 <= flags <= 0xFFFFFFFF:
            raise ValueError('Invalid connection settings')
        device, channel = U32(), U32()
        self.call('PassThruOpen', None, C.byref(device))
        self.device = device.value
        try:
            self.call('PassThruConnect', self.device, 4, flags, baud, C.byref(channel))
        except Exception:
            self.close()
            raise
        self.channel = channel.value

    def require_channel(self):
        if self.channel is None:
            raise RuntimeError('No connected channel')

    def fast_init(self, frame):
        """Transmit caller-supplied wakeup through FAST_INIT, not plain UART."""
        self.require_channel()
        request, response = Message.from_frame(frame), Message()
        self.call('PassThruIoctl', self.channel, 5, C.byref(request), C.byref(response))
        return response.frame()

    def write(self, frame, *, timeout_ms):
        self.require_channel()
        self.validate_timeout(timeout_ms)
        request, count = Message.from_frame(frame), U32(1)
        self.call('PassThruWriteMsgs', self.channel, C.byref(request), C.byref(count), timeout_ms)
        if count.value != 1:
            raise RuntimeError('Driver did not accept exactly one message')

    def read(self, *, timeout_ms):
        self.require_channel()
        self.validate_timeout(timeout_ms)
        response, count = Message(), U32(1)
        status = self.provider.PassThruReadMsgs(self.channel, C.byref(response), C.byref(count), timeout_ms)
        if status in (9, 16) and count.value == 0:
            return None
        if status:
            raise DriverError('PassThruReadMsgs', status)
        if count.value != 1:
            raise RuntimeError('Unexpected message count')
        return {'frame': response.frame(), 'rx_status': response.RxStatus,
                'timestamp': response.Timestamp, 'extra_data_index': response.ExtraDataIndex}

    @staticmethod
    def validate_timeout(value):
        if not 0 <= value <= 10000:
            raise ValueError('Timeout must be between 0 and 10000 ms')

    def close(self):
        errors = []
        for name, field in [('PassThruDisconnect', 'channel'), ('PassThruClose', 'device')]:
            handle = getattr(self, field)
            if handle is not None:
                try:
                    self.call(name, handle)
                except DriverError as e:
                    errors.append(e)
                finally:
                    setattr(self, field, None)
        if errors:
            raise errors[0]
