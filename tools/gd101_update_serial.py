"""Exclusive Linux serial transport for the experimental GD101 updater.

Opening this transport sends no updater command. There is no flashing CLI.
The caller must verify power/recovery and own the adapter before using it.
The recovered updater uses baud 2534 and enables CTS/RTS flow control; these
settings still require physical updater/recovery validation on Linux.
"""
import math
import time
import serial
from gd101_devices import select_gd101_port, open_serial_port, serial_transport_call


def positive_timeout(timeout):
    if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be positive and finite')
    return float(timeout)


class UpdateSerial:
    def __init__(self, expected_serial, *, requested=None):
        self.expected_serial = expected_serial
        self.requested = requested
        self.port = None

    def __enter__(self):
        if self.port is not None:
            raise RuntimeError('Updater serial transport is already open')
        # Resolve USB identity each time, including after re-enumeration.
        path=select_gd101_port(self.expected_serial,requested=self.requested)
        port=serial.Serial(port=None,baudrate=2534,bytesize=8,parity='N',
            stopbits=1,timeout=0,write_timeout=1,xonxoff=False,rtscts=True,
            dsrdtr=False,exclusive=True)
        port.dtr=True
        port.port=path
        try:
            open_serial_port(port)
        except BaseException:
            port.close()
            raise
        self.port=port
        return self

    def close(self):
        port,self.port=self.port,None
        if port is not None:
            port.close()

    def __exit__(self,*args):
        self.close()

    def _require_port(self):
        if self.port is None or not self.port.is_open:
            raise RuntimeError('Updater serial transport is closed')
        return self.port

    def write(self,data,*,timeout):
        timeout=positive_timeout(timeout)
        data=bytes(data)
        if not 8 <= len(data) <= 4104 or len(data)%8:
            raise ValueError('Expected one aligned updater frame')
        port=self._require_port()
        port.write_timeout=timeout
        # Do not flush: tcdrain can block beyond the exchange deadline. Reading
        # an acknowledgement establishes delivery; any timeout is uncertain.
        return serial_transport_call(port.write,data)

    def read_exact(self,count,*,timeout):
        timeout=positive_timeout(timeout)
        if not isinstance(count,int) or not 0 <= count <= 4104:
            raise ValueError('Invalid bounded updater read size')
        port=self._require_port()
        deadline=time.monotonic()+timeout
        result=bytearray()
        while len(result)<count:
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Updater serial reply deadline exceeded')
            port.timeout=remaining
            block=serial_transport_call(port.read,count-len(result))
            if not block:
                raise TimeoutError('Updater serial reply deadline exceeded')
            result.extend(block)
        return bytes(result)
