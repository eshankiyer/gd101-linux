"""GD101 native frame codec recovered from GODIAG_PT32.dll 2.3.

Importing this module performs no hardware I/O. This is not a complete driver.
Source addresses and limitations: research/hds/gd101-driver/native-framing.md.
"""

NATIVE_BAUD = 2534


def build_kline_open_payload(baud, selector=0, flags=0, *, parity=0):
    """Build operation 4 with the selected parity and remaining serial defaults.

    Offline building only. This is 11 bytes, not a complete wire frame. The
    caller must resolve session state, selector and explicit padding before
    sending. Numeric acceptance here does not establish device baud support.
    """
    if not isinstance(baud, int) or not 1 <= baud <= 0xffffffff:
        raise ValueError('Baud must be a positive uint32')
    if not isinstance(selector, int) or not 0 <= selector <= 255:
        raise ValueError('Selector must be one byte')
    if not isinstance(flags, int) or not 0 <= flags <= 0xffffffff:
        raise ValueError('Flags must be uint32')
    if parity not in (0,1,2):raise ValueError('Parity must be none, odd or even')
    return (bytes([1, 1, 1, selector, 0x70 if flags & 0x1000 else 0x7f])
            + baud.to_bytes(4, 'little') + bytes([1,6+parity]))


def parse_discovery_reply(payload):
    """Parse fields accepted by Windows routine 7ab78210, without guessing names.

    Input is an already CRC-validated, unencrypted payload. The last byte is
    not consumed by this routine. The short variant supplies default 64-byte
    fields; these are not established encryption keys or authentication data.
    """
    payload = bytes(payload)
    if len(payload) not in (28, 156) or payload[:3] != b'\x40\0\0':
        raise ValueError('Unsupported discovery reply length or prefix/status')
    return {
        'identifier': payload[3:11],
        'field_16': payload[11:27],
        'field_64_a': payload[27:91] if len(payload) == 156 else b'\x55' * 64,
        'field_64_b': payload[91:155] if len(payload) == 156 else b'\xaa' * 64,
    }


def prepare_serial(port):
    """Return a CLOSED pySerial port configured for the recovered native mode.

    Caller must explicitly open it. No discovery, startup or vehicle messages
    are sent. Startup has been tested separately on adapter MT000418.
    The 10 ms read timeout approximates the Windows first-byte wait; it is not
    an identical implementation of COMMTIMEOUTS. Windows retains some existing
    DCB flags; this uses pySerial's normal binary-port defaults for those flags.
    """
    import serial
    connection = serial.Serial(port=None, baudrate=NATIVE_BAUD, bytesize=8,
        parity='N', stopbits=1, timeout=0.01, write_timeout=1,
        xonxoff=False, rtscts=False, dsrdtr=False, exclusive=True)
    connection.dtr = True
    connection.rts = False
    connection.port = port
    return connection


class ShortFrameStream:
    """Incremental unencrypted short-frame decoder for fragmented USB reads.

    On a malformed frame the decoder becomes unusable, so callers cannot
    silently resynchronize to payload bytes. Construct a new instance only
    after resetting the surrounding session. No extended/encrypted support.
    """

    def __init__(self):
        self.pending = bytearray()
        self.failed = False

    def feed(self, data):
        if self.failed:
            raise ValueError('Decoder failed; reset the surrounding session')
        frames = []
        try:
            for byte in bytes(data):
                self.pending.append(byte)
                header = self.pending[0]
                if not 1 <= header <= 120:
                    raise ValueError('Invalid, extended, or encrypted short header')
                size = 2 * header + 2
                if len(self.pending) == size:
                    frames.append(decode_short(self.pending))
                    self.pending.clear()
        except ValueError:
            self.failed = True
            self.pending.clear()
            raise
        return frames


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0x07 if value & 0x80 else 0)) & 0xff
    return value


def encode_short(payload, xor_key=None):
    """Encode short framing, optionally with a 32-byte wire-order XOR key.

    Native zero-command messages remain clear even when a key is supplied.
    Key negotiation and session lifecycle are the caller's responsibility.
    """
    payload = bytes(payload)
    if not 2 <= len(payload) <= 240 or len(payload) % 2:
        raise ValueError('Requires 2..240 even payload bytes; padding is caller-supplied')
    header = len(payload) // 2
    if xor_key is not None:
        xor_key = bytes(xor_key)
        if len(xor_key) != 32:
            raise ValueError('Framing XOR key must contain 32 bytes in wire order')
        if payload[0] != 0:
            header |= 0x80
            payload = bytes(x ^ xor_key[i % 32] for i, x in enumerate(payload))
    body = bytes([header]) + payload
    return body + bytes([crc8(body)])


def decode_short(frame, xor_key=None):
    """Validate transmitted CRC and decode optional native XOR short framing."""
    frame = bytes(frame)
    if len(frame) < 4:
        raise ValueError('Truncated frame')
    encrypted = bool(frame[0] & 0x80)
    if encrypted and xor_key is None:
        raise ValueError('Encrypted payload requires a framing XOR key')
    if xor_key is not None:
        xor_key = bytes(xor_key)
        if len(xor_key) != 32:
            raise ValueError('Framing XOR key must contain 32 bytes in wire order')
    size = (frame[0] & 0x7f) * 2
    if not 2 <= size <= 240 or len(frame) != size + 2:
        raise ValueError('Invalid short-frame length')
    if crc8(frame[:-1]) != frame[-1]:
        raise ValueError('CRC mismatch')
    payload = frame[1:-1]
    if encrypted:
        payload = bytes(x ^ xor_key[i % 32] for i, x in enumerate(payload))
    return payload


MAX_EXTENDED_PAYLOAD = 0x1005


def encode_extended(payload, xor_key=None):
    """Recovered signed-length writer path (7ab75740), no implicit padding."""
    payload = bytes(payload)
    if not 1 <= len(payload) <= MAX_EXTENDED_PAYLOAD:
        raise ValueError('Extended payload must contain 1..4101 bytes')
    header = 0x7f
    if xor_key is not None:
        xor_key = bytes(xor_key)
        if len(xor_key) != 32:
            raise ValueError('Framing XOR key must contain 32 bytes')
        header = 0xff
        payload = bytes(x ^ xor_key[i % 32] for i, x in enumerate(payload))
    body = bytes([header]) + len(payload).to_bytes(2, 'little') + payload
    return body + bytes([crc8(body)])


def decode_frame(frame, xor_key=None):
    """Validate a short or extended wire frame using the receive-direction key."""
    frame = bytes(frame)
    if not frame or frame[0] & 0x7f != 0x7f:
        return decode_short(frame, xor_key)
    if len(frame) < 5:
        raise ValueError('Truncated extended frame')
    size = int.from_bytes(frame[1:3], 'little')
    if not 1 <= size <= MAX_EXTENDED_PAYLOAD or len(frame) != size + 4:
        raise ValueError('Invalid extended frame length')
    if crc8(frame[:-1]) != frame[-1]:
        raise ValueError('CRC mismatch')
    payload = frame[3:-1]
    if frame[0] & 0x80:
        if xor_key is None or len(xor_key) != 32:
            raise ValueError('Encrypted frame requires a 32-byte key')
        payload = bytes(x ^ xor_key[i % 32] for i, x in enumerate(payload))
    return payload


def build_kline_init_payload(operation, data=b'', *, selector=0,
                             timing_words=(5, 300, 25, 50, 300, 300), flags=0):
    """Offline port of builder operations 0x1d/0x1e at 7ab60670.

    These are candidate fast/five-baud initialization operations. The timing
    words are the six uint16 fields at object offsets a808..a813. This builder
    does not establish IOCTL dispatch semantics or parse an ECU init response.
    """
    data = bytes(data)
    if not 0 <= selector <= 255 or not 0 <= flags <= 0xffffffff:
        raise ValueError('Invalid selector or flags')
    if len(timing_words) != 6 or any(not 0 <= x <= 65535 for x in timing_words):
        raise ValueError('Requires six uint16 timing words')
    timing = b''.join(x.to_bytes(2, 'little') for x in timing_words)
    if operation == 0x1e:
        if len(data) != 1:
            raise ValueError('Operation 0x1e requires one address byte')
        return bytes([1, 2, selector]) + timing[8:12] + bytes([8]) + data + bytes(3)
    if operation != 0x1d:
        raise ValueError('Unsupported initialization operation')
    if data and not flags & 0x200:
        data += bytes([sum(data) & 255])
    # This command is emitted with short framing by the original builder.
    if len(data) > 228:
        raise ValueError('Initialization exceeds the supported short-frame limit')
    return bytes([1, 3, selector]) + timing[2:8] + timing[:2] + bytes([len(data)]) + data


def build_kline_handshake_payload(operation, *, selector=0,
                                  timing_words=(20,20,50), option=0):
    """Second-stage packets from 7ab60670: operations 9 and 10."""
    if not 0 <= selector <= 255 or not 0 <= option <= 255:
        raise ValueError('Selector and option must be bytes')
    if operation == 10:
        return bytes([3,3,selector])
    if operation != 9:
        raise ValueError('Unsupported handshake operation')
    if len(timing_words)!=3 or any(not 0 <= x <= 65535 for x in timing_words):
        raise ValueError('Requires three uint16 handshake timing words')
    timing=b''.join(x.to_bytes(2,'little') for x in timing_words)
    return bytes([3,2,selector,option])+timing+timing[4:6]
