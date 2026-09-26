"""Recovered K-line receive primitives; no I/O or assumed ECU services.

Sources: 7ab61350 (byte decoder), 7ab61ec0/7ab65af0 (routing).
"""
from dataclasses import dataclass

# Original ISO9141/ISO14230 SET_CONFIG 7ab63180 / 7ab665b0.
KLINE_BAUD_RATES = frozenset((1200,2400,4800,9600,0x258f,0x2648,10000,
    0x28a0,0x28b0,0x2a76,0x2e81,0x30d4,0x3366,0x3641,0x3972,0x3d09,
    19200,38400,57600,115200))

KLINE_EVENT_IDS = frozenset((0x0302, 0x0303, 0x030b))


def packet_id(payload):
    if len(payload) < 2:
        raise ValueError('Truncated native packet identifier')
    return int.from_bytes(payload[:2], 'big') & 0x3fff


@dataclass(frozen=True)
class KLineByte:
    selector: int
    timestamp: int
    value: int


def decode_kline_byte(payload):
    """Decode a 0303 receive-byte event; timestamp units remain unverified."""
    if len(payload) != 8 or packet_id(payload) != 0x0303:
        raise ValueError('Requires an eight-byte K-line receive event')
    return KLineByte(payload[2], int.from_bytes(payload[3:7], 'little'), payload[7])


@dataclass(frozen=True)
class KLineFilter:
    """Original host-side pass (1) or block (2) mask/pattern filter."""
    kind: int
    mask: bytes
    pattern: bytes

    def __post_init__(self):
        object.__setattr__(self, 'mask', bytes(self.mask))
        object.__setattr__(self, 'pattern', bytes(self.pattern))
        if self.kind not in (1, 2):
            raise ValueError('K-line supports pass and block filters here')
        # Captured worker stores twelve-byte mask/pattern regions.
        if len(self.mask) != len(self.pattern) or len(self.mask) > 12:
            raise ValueError('Filter mask and pattern require equal lengths <=12')

    def matches(self, data):
        return len(data) >= len(self.mask) and all(
            data[i] & mask == self.pattern[i] for i, mask in enumerate(self.mask))


def finish_kline_message(wire_data, filters, *, no_checksum=False):
    """Port of 7ab62e80 checksum/filter decisions; None means discarded.

    A matching pass filter is required. A matching block filter wins over all
    passes. The original compares masked data to the unmodified pattern.
    """
    data = bytes(wire_data)
    if not data:
        return None
    if not no_checksum:
        if len(data) < 2 or sum(data[:-1]) & 255 != data[-1]:
            return None
        data = data[:-1]
    filters = tuple(filters)
    if not any(f.kind == 1 and f.matches(data) for f in filters):
        return None
    if any(f.kind == 2 and f.matches(data) for f in filters):
        return None
    return data


@dataclass(frozen=True)
class KLineMessage:
    selector: int
    first_timestamp: int
    last_timestamp: int
    data: bytes


@dataclass(frozen=True)
class KLineTransmitEcho:
    data: bytes
    timestamp: int

    @classmethod
    def from_wire(cls, data, timestamp, *, no_checksum=False):
        wire=bytes(data)
        if not 1<=len(wire)<=4097:raise ValueError('Invalid echo wire length')
        if not 0<=timestamp<=0xffffffff:raise ValueError('Invalid echo timestamp')
        return cls(wire if no_checksum else wire[:-1],timestamp)


class KLineAssembler:
    """Host-time receive assembly with explicit inter-byte timeout.

    Feed events with monotonic host arrival times, then call expire even when
    no further events arrive. Adapter timestamps are preserved, not used as
    elapsed-time values because their units remain unverified. Pass this component to GD101.start_receiver to assemble events as
    the background USB reader receives them.
    """
    def __init__(self, *, selector=0, gap_seconds=0.020, filters=(), no_checksum=False):
        import math
        if not 0 <= selector <= 255 or not math.isfinite(gap_seconds) or gap_seconds < 0:
            raise ValueError('Invalid selector or inter-byte timeout')
        self.selector = selector
        self.gap_seconds = gap_seconds
        self.filters = tuple(filters)
        self.no_checksum = no_checksum
        self.pending = bytearray()
        self.last_arrival = None
        self.first_timestamp = self.last_timestamp = None
        self.clock = None
        self.discarded = 0

    def _check_clock(self, now):
        import math
        if not math.isfinite(now) or (self.clock is not None and now < self.clock):
            raise ValueError('Arrival times must be finite and monotonic')
        self.clock = now

    def expire(self, now):
        self._check_clock(now)
        if not self.pending or now < self.last_arrival + self.gap_seconds:
            return None
        data = finish_kline_message(self.pending, self.filters, no_checksum=self.no_checksum)
        self.pending.clear()
        if data is None:
            self.discarded += 1
            return None
        return KLineMessage(self.selector,self.first_timestamp,self.last_timestamp,data)

    def feed(self, event, now):
        if event.selector != self.selector:
            raise ValueError('Receive event belongs to another K-line channel')
        if not 0 <= event.value <= 255 or not 0 <= event.timestamp <= 0xffffffff:
            raise ValueError('Invalid byte event')
        completed = self.expire(now)
        if len(self.pending) >= 0x1020:
            self.pending.clear()
            self.discarded += 1
            raise ValueError('K-line receive message exceeds original buffer capacity')
        if not self.pending:
            self.first_timestamp = event.timestamp
        self.pending.append(event.value)
        self.last_timestamp = event.timestamp
        self.last_arrival = now
        return completed


def build_kline_wire_transmit(data, *, counter, selector=0, timing=5):
    """Port normal extended transmit branch 7ab60301..7ab60377.

    Data is already prepared wire data, including any required checksum.
    Does not add addressing, checksum, ECU initialization or a keepalive.
    """
    data=bytes(data)
    if not data or len(data)>4097:
        raise ValueError('Wire data must contain 1..4097 bytes')
    if not 0 <= counter <= 0xffffffff or not 0 <= selector <= 255 or not 0 <= timing <= 65535:
        raise ValueError('Invalid transmit counter, selector or timing')
    tag=(counter+(counter//240)*16)&255
    return bytes([1,selector,tag,timing&255])+data


@dataclass(frozen=True)
class KLineTransmitAck:
    selector: int
    tag: int
    status: int
    timestamp: int


def build_kline_byte_transmit(value, *, counter, selector=0):
    """Short per-byte branch 7ab6025f..7ab602c1; caller pads odd USB payloads."""
    if not 0<=value<=255 or not 0<=counter<=0xffffffff or not 0<=selector<=255:
        raise ValueError('Invalid K-line byte transmit parameters')
    tag=(counter+(counter//240)*16)&255
    return bytes((3,1,selector,tag,value))


def decode_kline_transmit_ack(payload, *, byte_mode=False):
    """0302/030b acknowledgment fields read by 7ab61ec0 and 7ab65af0."""
    if len(payload)<9 or packet_id(payload)!=(0x0302 if byte_mode else 0x030b):
        raise ValueError('Truncated or incorrect K-line transmit acknowledgment')
    return KLineTransmitAck(payload[2],payload[3],payload[4],
                            int.from_bytes(payload[5:9],'little'))


@dataclass(frozen=True)
class KLineInitStep:
    operation: int
    payload: bytes
    timeout_ms: int


def kline_init_plan(kind, data=b'', *, selector=0, flags=0,
                    timing_words=(5,300,25,50,300,300,20,20,50), option=0):
    """Offline two-stage sequence recovered from 7ab61470/7ab62680.

    Does not implement queue clearing, response matching or the optional final
    fast-init message read. Payloads are unpadded, not complete USB frames.
    """
    from gd101_native import build_kline_init_payload,build_kline_handshake_payload
    data=bytes(data)
    if len(timing_words)!=9 or any(not 0 <= x <= 65535 for x in timing_words):
        raise ValueError('Requires nine uint16 initialization timing words')
    t=timing_words
    if kind=='fast':
        first=build_kline_init_payload(0x1d,data,selector=selector,flags=flags,timing_words=t[:6])
        second=build_kline_handshake_payload(10,selector=selector)
        # Original null input does not include the extra byte's transmit delay.
        wait=t[3]+t[1]+100+(t[0]*(len(data)+1) if data else 0)
        return (KLineInitStep(0x1d,first,2000),KLineInitStep(10,second,wait))
    if kind=='five_baud':
        first=build_kline_init_payload(0x1e,data,selector=selector,flags=flags,timing_words=t[:6])
        second=build_kline_handshake_payload(9,selector=selector,timing_words=t[6:],option=option)
        return (KLineInitStep(0x1e,first,2000),
                KLineInitStep(9,second,sum(t[4:])+2100))
    raise ValueError('Initialization must be fast or five_baud')


def kline_command_reply_id(payload):
    """Non-transmit short-command mapping from 7ab5fcd0."""
    if len(payload)<3:
        raise ValueError('K-line command is truncated')
    request=int.from_bytes(payload[:2],'big')
    if request not in (0x101,0x102,0x103,0x302,0x303):
        raise ValueError('Unsupported K-line control command')
    return {0x302:0x305,0x303:0x304}.get(request,request)


def validate_kline_config_ack(payload, *, closing=False):
    """Original op4/op12 decode: status is byte 2; byte 3 is ignored."""
    if len(payload)<(3 if closing else 4) or packet_id(payload)!=0x101:
        raise ValueError('Truncated or mismatched K-line configuration reply')
    if payload[2]:raise ValueError(f'K-line configuration rejected: status {payload[2]}')


def parse_kline_init_reply(operation, payload, selector=0):
    """Validate init acknowledgment; operation 9 additionally returns keywords."""
    expected={0x1d:0x103,0x1e:0x102,9:0x305,10:0x304}
    if operation not in expected or len(payload)<4:
        raise ValueError('Unsupported or truncated initialization reply')
    if packet_id(payload)!=expected[operation] or payload[2]!=selector:
        raise ValueError('Initialization reply command/channel mismatch')
    if payload[3]:
        raise ValueError(f'Initialization failed with adapter status {payload[3]}')
    if operation==9:
        if len(payload)<10:
            raise ValueError('Five-baud reply is missing its keyword bytes')
        return bytes(payload[8:10])
    return None
