"""Local file-mailbox RPC from an isolated Windows DLL to the native driver.

No TCP listener. Hardware is opened only by an explicit PassThruOpen request.
The mailbox is private, size bounded, and requests are processed once. This
bridge supports lifecycle/version, raw CAN messaging and pass/block filters,
K-line writes and FAST_INIT. Other J2534 features remain unfinished.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import struct
import time
from gd101_abi import LifecycleProvider

MAGIC = 0x31444742
REQUEST = struct.Struct('<10I')
RESPONSE = struct.Struct('<4I')
MAX_INPUT = 4128

def dispatch(provider, packet):
    if not REQUEST.size <= len(packet) <= REQUEST.size+MAX_INPUT:
        raise ValueError('Incorrect RPC request size')
    magic, version, pid, sequence, operation, *args = REQUEST.unpack_from(packet)
    extra = packet[REQUEST.size:]
    if args[4] != len(extra) or (extra and operation not in (7,10,12,14,15,19,20,21,22)):
        raise ValueError('Invalid RPC payload length or operation')
    if magic != MAGIC or version != 1 or not pid or not sequence:
        raise ValueError('Invalid RPC envelope')
    payload = b''
    try:
        if operation == 22:
            status,handle=provider.start_periodic(args[0],args[1],args[2],extra,args[3])
            if not status:payload=struct.pack('<I',handle)
        elif operation == 23:status=provider.stop_periodic(args[0],args[1])
        elif operation == 24:status=provider.clear_periodic(args[0])
        elif operation == 25:status=provider.clear_tx(args[0])
        elif operation == 1:
            status, handle = provider.open()
            payload = struct.pack('<I', handle)
        elif operation == 2:
            status = provider.close(args[0])
        elif operation == 3:
            status, handle = provider.connect(*args[:4])
            payload = struct.pack('<I', handle)
        elif operation == 4:
            status = provider.disconnect(args[0])
        elif operation == 5:
            status,fields=provider.read_version(args[0])
            if not status:
                payload = b''.join(s.encode('ascii')[:79].ljust(80,b'\0') for s in fields)
        elif operation == 6:
            payload = provider.last_error.encode('ascii','replace')[:79].ljust(80,b'\0')
            status = 0
        elif operation == 7:
            size=args[2]
            if not 0 <= size <= 12 or len(extra) != 2*size:
                status=provider.failure(10,'Malformed filter data')
            else:
                status,handle=provider.start_filter(args[0],args[1],extra[:size],extra[size:],args[3])
                payload=struct.pack('<I',handle)
        elif operation == 8:
            status=provider.stop_filter(args[0],args[1])
        elif operation == 9:
            status=provider.clear_filters(args[0])
        elif operation == 10:
            status=provider.write_message(args[0],args[1],args[2],extra,args[3])
        elif operation == 11:
            status,frame=provider.read_can(args[0],args[1])
            if not status:
                data,rx_status,timestamp=provider.received_fields(frame)
                payload=struct.pack('<6I',5,rx_status,0,
                    timestamp,len(data),len(data))+data.ljust(12,b'\0')
        elif operation == 13:
            kline=provider.protocol in (3,4)
            read=provider.read_kline if kline else (provider.read_isotp if provider.protocol==6 else provider.read_can)
            status,result=read(args[0],args[1])
            if not status:
                data,rx_status,timestamp=provider.received_fields(result)
                if len(data)>4128:status=provider.failure(18,'Receive message exceeds ABI capacity')
                else:
                    payload=struct.pack('<6I',provider.protocol,
                        rx_status,0,timestamp,
                        len(data),len(data))+data.ljust(4128,b'\0')
        elif operation == 21:
            size=len(extra)//3
            if len(extra)%3 or size not in (4,5):status=provider.failure(10,'Malformed flow filter')
            else:
                status,handle=provider.start_flow_filter(args[0],args[1],args[2],extra[:size],extra[size:2*size],extra[2*size:])
                if not status:payload=struct.pack('<I',handle)
        elif operation == 14:
            size=len(extra)//2
            if len(extra)%2 or size>12:status=provider.failure(10,'Malformed filter data')
            else:
                status,handle=provider.start_filter(args[0],args[1],extra[:size],extra[size:],
                    args[3],protocol=args[2])
                payload=struct.pack('<I',handle)
        elif operation == 16:
            status=provider.clear_rx(args[0])
        elif operation == 17:
            status,value=provider.read_battery_voltage(args[0])
            if not status:payload=struct.pack('<I',value)
        elif operation == 18:
            status=provider.set_programming_voltage(*args[:3])
        elif operation in (19,20):
            width=8 if operation==19 else 4
            if len(extra)%width or len(extra)>64*width:
                status=provider.failure(10,'Malformed configuration entries')
            elif operation==19:
                status,_=provider.configure_timing(args[0],pairs=list(struct.iter_unpack('<II',extra)))
            else:
                status,values=provider.configure_timing(args[0],parameters=[p[0] for p in struct.iter_unpack('<I',extra)])
                if not status:payload=struct.pack('<'+'I'*len(values),*values)
        elif operation == 15:
            status,result=provider.five_baud_init(args[0],extra)
            if not status:payload=result
        elif operation == 12:
            status,result=provider.fast_init(args[0],args[1],args[2],extra,bool(args[3]))
            if not status and args[3]:
                if len(result.data)>4128:
                    status=provider.failure(18,'FAST_INIT response exceeds output capacity')
                else:
                    payload=struct.pack('<6I',4,0,0,result.first_timestamp,
                        len(result.data),len(result.data))+result.data.ljust(4128,b'\0')
        else:
            status = provider.failure(1,'RPC operation not implemented')
    except Exception as error:
        from gd101_devices import device_error_status
        status = provider.failure(device_error_status(error), f'{type(error).__name__}: {error}')
    if status:
        payload=provider.last_error.encode('ascii','replace')[:79].ljust(80,b'\0')
    return RESPONSE.pack(MAGIC, sequence, status, len(payload)) + payload

def serve(mailbox, provider=None):
    mailbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    if mailbox.stat().st_uid != os.getuid() or mailbox.stat().st_mode & 0o077:
        raise RuntimeError('Mailbox must be owner-only')
    # Lock prevents two brokers from processing a physical adapter concurrently.
    import fcntl
    lock = os.fdopen(os.open(mailbox/'broker.lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600),'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    provider = provider or LifecycleProvider()
    stop = False
    def terminate(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        with (mailbox/'operations.jsonl').open('a', buffering=1) as log:
            while not stop:
                for path in sorted(mailbox.glob('*.req')):
                    try:
                        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    except (FileNotFoundError,OSError):
                        continue
                    with os.fdopen(descriptor,'rb') as stream:
                        info=os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        # Bounded read even if the sender changes the file.
                        packet=stream.read(REQUEST.size+MAX_INPUT+1)
                    try:path.unlink()
                    except FileNotFoundError:continue
                    # Never execute an abandoned request after the client deadline.
                    if not REQUEST.size <= len(packet) <= REQUEST.size+MAX_INPUT or time.time()-info.st_mtime > 25:
                        continue
                    try:
                        result = dispatch(provider, packet)
                    except ValueError:
                        continue
                    reply = path.with_suffix('.res')
                    temp = path.with_suffix('.out')
                    # The Wine side can mutate the mailbox. Do not follow a
                    # planted output symlink into an unrelated host file.
                    try:
                        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    except FileExistsError:
                        continue
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(result)
                    temp.replace(reply)
                    fields = REQUEST.unpack_from(packet)
                    log.write(json.dumps({'time':time.time(),'pid':fields[2],
                        'sequence':fields[3],'operation':fields[4],
                        'status':RESPONSE.unpack_from(result)[2]})+'\n')
                time.sleep(.005)
    finally:
        if provider.session is not None:
            provider.close(provider.device)
        lock.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mailbox', type=Path)
    serve(parser.parse_args().mailbox.resolve())
