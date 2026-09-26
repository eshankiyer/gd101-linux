"""Offline J1850 payload builders from original VPW/PWM protocol handlers.

These build adapter payloads, not complete serial frames. No session, checksum
policy, receive assembly or ABI protocol support is implied.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class J1850Message:
    protocol: int
    data: bytes
    timestamp: int
    extra_data_index: int
    rx_status: int = 0


def _command(protocol):
    if protocol not in (1,2):raise ValueError('J1850 protocol must be VPW (1) or PWM (2)')
    return 8 if protocol==1 else 7


def build_open(protocol,descriptor):
    command=_command(protocol);descriptor=bytes(descriptor)
    if len(descriptor)!=8:raise ValueError('J1850 descriptor must contain eight bytes')
    result=bytes([1,command,1])+descriptor[:4]+descriptor[5:6]
    return result+(descriptor[7:8] if protocol==2 else b'')


def build_close(protocol):
    return bytes([1,_command(protocol),0])


def build_transmit(protocol,data,*,counter=0):
    """Original op5 copy, with raw message bytes supplied by the caller.

    VPW messages longer than 16 bytes use a different original operation.
    PWM's byte-sized length is bounded here; this is not its public API limit.
    """
    _command(protocol);data=bytes(data)
    if not isinstance(counter,int) or not 0<=counter<=0xffffffff:
        raise ValueError('J1850 counter must be uint32')
    if len(data)>(16 if protocol==1 else 255):raise ValueError('J1850 op5 payload too long')
    tag=(counter+(counter//240)*16)&255
    return bytes([3,5 if protocol==1 else 4,tag,len(data)])+data


def decode_receive(protocol,record):
    """Decode original type-0 VPW/PWM records, excluding message CRC.

    PWM IFR bytes follow the skipped CRC and remain after extra_data_index.
    Timestamp units and CRC validation are separate unresolved work.
    """
    _command(protocol);record=bytes(record)
    expected=bytes([0x43,9 if protocol==1 else 8])
    if len(record)<10 or record[:2]!=expected:
        raise ValueError('Truncated or unexpected J1850 receive record')
    length=record[6]
    count=length if protocol==1 else length>>4
    extra=0 if protocol==1 else length&15
    if count==0 or len(record)<7+count+extra:
        raise ValueError('Invalid J1850 receive lengths')
    data=record[7:7+count-1]+record[7+count:7+count+extra]
    return J1850Message(protocol,data,int.from_bytes(record[2:6],'little'),count-1)
