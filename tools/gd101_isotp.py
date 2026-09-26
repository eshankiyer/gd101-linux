"""Native ISO-TP input routing recovered from protocol-6 decoder 7ab6e1d0.

This is the CAN input stage, not ISO-TP reassembly or a complete channel API.
"""
from dataclasses import dataclass
from gd101_can import decode_can_receive_record,CANFrame


@dataclass(frozen=True)
class ISOTPTransmitDone:
    arbitration_id: int
    extended: bool
    address: int | None
    timestamp: int

    def __post_init__(self):
        if not 0<=self.arbitration_id<=(0x1fffffff if self.extended else 0x7ff):
            raise ValueError('Invalid completion CAN identifier')
        if self.address is not None and not 0<=self.address<=255:raise ValueError('Invalid completion address')
        if not 0<=self.timestamp<=0xffffffff:raise ValueError('Invalid completion timestamp')

    def fields(self):
        header=self.arbitration_id.to_bytes(4,'big')
        if self.address is not None:header+=bytes([self.address])
        return header,9|(0x100 if self.extended else 0)|(0x80 if self.address is not None else 0),self.timestamp


@dataclass(frozen=True)
class ISOTPTransmitEcho(ISOTPTransmitDone):
    data: bytes

    def __post_init__(self):
        super().__post_init__()
        payload=bytes(self.data)
        if not 1<=len(payload)<=4095:raise ValueError('Invalid ISO-TP echo payload length')
        object.__setattr__(self,'data',payload)

    def fields(self):
        header,status,stamp=super().fields()
        return header+self.data,status&~8,stamp


@dataclass(frozen=True)
class ISOTPStartOfMessage:
    arbitration_id: int
    extended: bool
    address: int | None
    timestamp: int

    def fields(self):
        header,status,stamp=ISOTPTransmitDone(self.arbitration_id,self.extended,self.address,self.timestamp).fields()
        return header,(status&~9)|2,stamp


@dataclass(frozen=True)
class FlowRoute:
    receive_id: int
    address: int | None = None
    enabled: bool = True

    def __post_init__(self):
        if not 0 <= self.receive_id <= 0x1fffffff:
            raise ValueError('Invalid flow route CAN identifier')
        if self.address is not None and not 0 <= self.address <= 255:
            raise ValueError('Extended address must be one byte')


@dataclass(frozen=True)
class RoutedCANFrame:
    frame: CANFrame
    filter_index: int


def decode_isotp_input(record, routes, *, selector=0):
    """Return routed raw CAN input, or None when original matching rejects it.

    A table index 0..63 must name an enabled route. The original permits 64..127
    and 255 without a table lookup and rejects 128..254. Retain that distinction;
    do not infer a reassembled payload or a trusted ECU identity from the index.
    """
    record=bytes(record)
    if len(record)<14:
        raise ValueError('Truncated ISO-TP CAN input')
    if record[2]!=selector:
        return None
    index=record[8]
    if index<64:
        route=routes.get(index)
        if route is None or not route.enabled:
            return None
        if int.from_bytes(record[9:13],'little')!=route.receive_id:
            return None
        if route.address is not None:
            if len(record)<15:
                raise ValueError('Missing extended-address byte')
            if record[14]!=route.address:
                return None
    elif 128<=index<255:
        return None
    # Raw-CAN decoder requires 15 bytes; original ISO-TP path permits 14 for
    # zero DLC. Supply padding only to that decoder, never to the data payload.
    frame=decode_can_receive_record(record+bytes(max(0,15-len(record))))
    return RoutedCANFrame(frame,index)


def build_flow_table_packet(entries, config, *, selector=0):
    """Port 7ab6d9d0, returning the unframed extended-command payload.

    entries maps slot indexes to the original 32-byte table records. config is
    the 32-byte a7f0..a80f configuration region. Named configuration/route creation
    and acknowledgment handling are separate work; this API performs no I/O.
    """
    config=bytes(config)
    if len(config)!=32 or not 0<=selector<=255 or len(entries)>64:
        raise ValueError('Invalid flow-table configuration or selector')
    records=[]
    for slot,raw in sorted(entries.items()):
        raw=bytes(raw)
        if not 0<=slot<64 or len(raw)!=32 or raw[0]==0:
            raise ValueError('Flow entries require unique slots 0..63 and active 32-byte records')
        records.append(bytes([slot])+raw[1:2]+raw[4:10]+raw[12:17])
    header=(bytes([2,selector])+config[14:16]+config[18:20]+config[12:13]+
            b'\x01\x03'+config[26:27]+b'\x08'+config[5:7]+config[8:12]+bytes([len(records)]))
    return header+b''.join(records)


def make_flow_entry(transmit_id, receive_id, *, extended=False,
                    transmit_address=None, receive_address=None, pad=False):
    """Construct a 32-byte route from flow-filter creation code 7ab70530.

    This helper uses the same CAN-ID width and addressing mode in both directions.
    The original supports further flag combinations not exposed here yet.
    """
    limit=0x1fffffff if extended else 0x7ff
    if not 0<=transmit_id<=limit or not 0<=receive_id<=limit:
        raise ValueError('Flow identifier exceeds selected CAN width')
    addressed=transmit_address is not None
    if addressed != (receive_address is not None):
        raise ValueError('Both directions must use the same addressing mode')
    if addressed and (not 0<=transmit_address<=255 or not 0<=receive_address<=255):
        raise ValueError('Flow addresses must be bytes')
    flags=5|(2 if extended else 0)|(8 if addressed else 0)|(0x30 if pad else 0)
    entry=bytearray(32)
    entry[0]=1
    entry[1]=entry[9]=flags
    entry[4:8]=transmit_id.to_bytes(4,'little')
    entry[12:16]=receive_id.to_bytes(4,'little')
    if addressed:
        entry[8]=transmit_address
        entry[16]=receive_address
    return bytes(entry)


def default_flow_config():
    """Original protocol-6 constructor region a7f0..a80f."""
    config=bytearray(32)
    config[8:12]=b'\xff'*4
    for offset in (14,16,18,24):
        config[offset:offset+2]=(1000).to_bytes(2,'little')
    return bytes(config)


def build_acceptance_table(standard=(), extended=(), *, selector=0):
    """01 04 table emitted after flow setup by 7ab70530.

    Each group contains (mask, pattern) integer pairs in original on-wire LE32
    ordering. Standard/extended grouping follows the original filter flag.
    """
    standard,extended=tuple(standard),tuple(extended)
    if not 0<=selector<=255 or len(standard)+len(extended)>29:
        raise ValueError('Acceptance table exceeds supported short-frame capacity')
    output=bytearray([1,4,selector,len(standard),len(extended)])
    for group in (standard,extended):
        for mask,pattern in group:
            if not 0<=mask<=0xffffffff or not 0<=pattern<=0xffffffff:
                raise ValueError('Acceptance masks/patterns must be uint32')
            output+=mask.to_bytes(4,'little')+pattern.to_bytes(4,'little')
    return bytes(output)


def validate_flow_ack(payload, command):
    """Require the observed reply ID and zero status; ignore padding bytes."""
    if command not in (0x0b,4) or len(payload)<3 or payload[:2]!=bytes([0x41,command]):
        raise ValueError('Unexpected ISO-TP configuration acknowledgment')
    if payload[2]:
        raise ValueError(f'ISO-TP configuration rejected: status {payload[2]}')


@dataclass(frozen=True)
class ISOTPMessage:
    arbitration_id: int
    data: bytes
    extended: bool
    address: int | None
    first_timestamp: int
    last_timestamp: int
    filter_index: int


class ISOTPFrameError(ValueError):
    """Malformed CAN transport content; the USB session can continue."""


class ISOTPReassembler:
    """Passive classic-CAN reassembly based on 7ab6e6e0.

    No flow-control frames are emitted here. The adapter's configured native
    flow table is responsible for that part, which needs live validation.
    Caller supplies monotonic arrival times and an explicit timeout in seconds;
    original timer tick units have not been verified. Malformed sequences abort.
    """
    def __init__(self, routes, *, timeout):
        import math
        if not math.isfinite(timeout) or timeout<=0 or len(routes)>64:
            raise ValueError('Invalid reassembly timeout or route count')
        if any(not 0<=slot<64 for slot in routes):
            raise ValueError('Invalid reassembly route index')
        self.routes=dict(routes)
        self.timeout=timeout
        self.pending={}
        self.clock=None

    def expire(self, now):
        import math
        if not math.isfinite(now) or (self.clock is not None and now<self.clock):
            raise ValueError('Reassembly time must be finite and monotonic')
        self.clock=now
        expired=[slot for slot,state in self.pending.items() if now>=state['deadline']]
        for slot in expired: del self.pending[slot]
        return expired

    def feed(self, routed, now):
        self.expire(now)
        try:return self._feed(routed,now)
        except ISOTPFrameError:
            self.pending.pop(routed.filter_index,None)
            raise

    def _feed(self, routed, now):
        slot=routed.filter_index
        route=self.routes.get(slot)
        frame=routed.frame
        if route is None or not route.enabled or frame.arbitration_id!=route.receive_id:
            return None
        offset=int(route.address is not None)
        raw=frame.data
        if len(raw)>8 or len(raw)<=offset or (offset and raw[0]!=route.address):
            raise ISOTPFrameError('Malformed ISO-TP address or PCI')
        pci=raw[offset]
        kind=pci>>4
        body=raw[offset+1:]
        if kind==0:
            length=pci&15
            self.pending.pop(slot,None)
            if not 1<=length<=7-offset or length>len(body):
                raise ISOTPFrameError('Invalid ISO-TP single-frame length')
            return ISOTPMessage(frame.arbitration_id,body[:length],frame.extended,route.address,
                                frame.timestamp,frame.timestamp,slot)
        if kind==1:
            self.pending.pop(slot,None)
            if len(raw)!=8:
                raise ISOTPFrameError('ISO-TP first frame requires eight CAN bytes')
            length=((pci&15)<<8)|body[0]
            if not 7-offset<length<=4095:
                raise ISOTPFrameError('Invalid ISO-TP first-frame length')
            self.pending[slot]={'length':length,'data':bytearray(body[1:]),'next':1,
                'deadline':now+self.timeout,'first':frame.timestamp,'extended':frame.extended}
            return None
        if kind!=2:
            return None  # Flow-control and reserved types belong to other handlers.
        state=self.pending.get(slot)
        if state is None:
            return None
        if pci&15!=state['next'] or frame.extended!=state['extended'] or not body:
            del self.pending[slot]
            raise ISOTPFrameError('ISO-TP consecutive-frame sequence mismatch')
        remaining=state['length']-len(state['data'])
        if len(body)<min(remaining,7-offset):
            del self.pending[slot]
            raise ISOTPFrameError('Truncated ISO-TP consecutive frame')
        state['data'].extend(body[:remaining])
        state['next']=(state['next']+1)&15
        state['deadline']=now+self.timeout
        if len(state['data'])<state['length']:
            return None
        del self.pending[slot]
        return ISOTPMessage(frame.arbitration_id,bytes(state['data']),frame.extended,route.address,
                            state['first'],frame.timestamp,slot)


def build_isotp_transmit(entry, slot, data, *, counter=0, selector=0, padding_byte=0):
    """Normal worker paths in 7ab6cc90; returns (payload, extended_framing).

    Short messages carry their single CAN frame; longer messages reference a
    native flow route and let the adapter segment them. No I/O is performed.
    """
    entry,data=bytes(entry),bytes(data)
    if len(entry)!=32 or not entry[0] or not 0<=slot<64:
        raise ValueError('An active ISO-TP flow route is required')
    if not 1<=len(data)<=4095 or not 0<=counter<=0xffffffff or not 0<=selector<=255:
        raise ValueError('Invalid ISO-TP size, counter or selector')
    if not 0<=padding_byte<=255:
        raise ValueError('Padding byte must be uint8')
    flags=entry[1]
    addressed=bool(flags&8)
    padded=bool(flags&0x30)
    tag=(counter+16*(counter//240))&255
    if len(data)>7-int(addressed):
        return bytes([0,selector,tag,4 if padded else 0,slot])+data,True
    body=(bytes([entry[8]]) if addressed else b'')+bytes([len(data)])+data
    if padded:
        body+=bytes([padding_byte])*(8-len(body))
    wire_flags=(4 if flags&2 else 0)|(8 if addressed else 0)|(16 if padded else 0)
    return bytes([3,0,selector,tag,wire_flags])+entry[4:8]+bytes([len(body)])+body,False
