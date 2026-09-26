"""Raw CAN primitives recovered from GD101 driver protocol-5 routines.

Used by GD101 raw CAN lifecycle/reception; transmit and filtering remain pending.
"""
from dataclasses import dataclass
from threading import RLock


def build_can_open_payload(descriptor):
    """Original 7ab5bde0 op4 mapping from a 22-byte internal descriptor.

    Descriptor interpretation/defaults are not a public configuration API yet.
    """
    d=bytes(descriptor)
    if len(d)!=22:
        raise ValueError('CAN descriptor must contain 22 bytes')
    return (b'\x01\0\x01'+d[:1]+bytes([((d[3]<<4)|(d[2]&15))&255])+
            d[4:11]+d[12:13]+d[11:12]+d[16:22])


def build_can_close_payload(selector=0):
    if not 0 <= selector <= 255:
        raise ValueError('Selector must be one byte')
    return bytes([1,0,0,selector])


@dataclass(frozen=True)
class CANFrame:
    arbitration_id: int
    data: bytes
    extended: bool
    timestamp: int


@dataclass(frozen=True)
class CANTransmitEcho(CANFrame):
    """Acknowledged transmit returned when host LOOPBACK is enabled."""


def decode_can_receive_record(record):
    """Decode original descriptor-type-0 record at 7ab5c730.

    Caller must route/validate the packet identifier separately. The original
    routine receives a record from its router, not an arbitrary USB payload.
    Timestamp units are unverified. Only classic CAN lengths are accepted.
    """
    record=bytes(record)
    if len(record)<15:
        raise ValueError('Truncated CAN receive record')
    size=record[13]
    if size>8 or len(record)<14+size:
        raise ValueError('Invalid or truncated classic CAN payload')
    extended=bool(record[7]&0x10)
    identifier=int.from_bytes(record[9:13],'little')
    if identifier > (0x1fffffff if extended else 0x7ff):
        raise ValueError('CAN identifier exceeds selected width')
    return CANFrame(identifier,record[14:14+size],extended,
                    int.from_bytes(record[3:7],'little'))


def default_can_descriptor(bitrate=500000):
    """Protocol-5 constructor defaults at a7d8, on selector 0/pins 6 and 14.

    Bitrate set is recovered from 7ab5de30. Other fields retain constructor
    values; listen-only and alternate pin modes are not established here.
    """
    if bitrate not in (125000,200000,250000,500000,1000000):
        raise ValueError('Unsupported original-driver CAN bitrate')
    d=bytearray(22)
    d[2:4]=bytes.fromhex('0e06')
    d[4:8]=bitrate.to_bytes(4,'little')
    d[8:12]=bytes.fromhex('500f0100')
    return bytes(d)


def build_can_transmit(arbitration_id, data, *, extended=False, counter=0, selector=0):
    """Normal classic-CAN transmit block 7ab5b9d5, unpadded short payload."""
    data=bytes(data)
    if not 0 <= arbitration_id <= (0x1fffffff if extended else 0x7ff):
        raise ValueError('CAN identifier exceeds selected width')
    if len(data)>8 or not 0 <= selector <= 255 or not 0 <= counter <= 0xffffffff:
        raise ValueError('Invalid CAN payload, selector or counter')
    tag=(counter+16*(counter//240))&255
    return bytes([3,0,selector,tag,4 if extended else 0])+arbitration_id.to_bytes(4,'little')+bytes([len(data)])+data


@dataclass(frozen=True)
class CANTransmitAck:
    selector: int
    tag: int
    status: int
    timestamp: int


def decode_can_transmit_ack(payload):
    """Completion fields from 7ab5cc80, without claiming timestamp units."""
    if len(payload)<9 or int.from_bytes(payload[:2],'big')&0x3fff != 0x300:
        raise ValueError('Invalid CAN completion record')
    return CANTransmitAck(payload[2],payload[3],payload[4],int.from_bytes(payload[5:9],'little'))


class CANFilters:
    """Host-side pass/block filters from receive router 7ab5cc80.

    Masks cover the four-byte big-endian arbitration ID followed by data.
    No pass match means discard; a block match overrides a pass. This covers
    raw CAN filters, not ISO-TP flow-control filters or controller acceptance.
    """
    def __init__(self):
        self._filters={}
        self._lock=RLock()
        self._next_id=1

    def add(self, kind, mask, pattern, *, extended=None):
        # The same comparison and twelve-byte limit apply to K-line and CAN.
        from gd101_kline import KLineFilter
        entry=KLineFilter(kind,mask,pattern)
        if extended is not None and type(extended) is not bool:
            raise ValueError('Extended-ID selection must be bool or None')
        with self._lock:
            if len(self._filters)>=10:
                raise OverflowError('CAN channel supports at most ten filters')
            identifier=self._next_id
            self._next_id+=1
            self._filters[identifier]=(entry,extended)
            return identifier

    def remove(self, identifier):
        with self._lock:
            if identifier not in self._filters:
                raise ValueError('Unknown CAN filter handle')
            del self._filters[identifier]

    def clear(self):
        with self._lock:self._filters.clear()

    def accepts(self, frame):
        data=frame.arbitration_id.to_bytes(4,'big')+frame.data
        with self._lock:
            filters=tuple(f for f,extended in self._filters.values()
                          if extended is None or extended==frame.extended)
        return (any(f.kind==1 and f.matches(data) for f in filters) and
                not any(f.kind==2 and f.matches(data) for f in filters))
