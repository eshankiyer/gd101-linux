"""Live Linux ABI check: open, version, voltage, close. Opens no vehicle channel."""
import ctypes as C
import json
from pathlib import Path
import uuid


def main():
    root=Path(__file__).resolve().parents[1]
    lib=C.CDLL(str(root/'driver/build/libgd101.so'))
    signatures={
        'PassThruOpen':[C.c_void_p,C.POINTER(C.c_uint32)],
        'PassThruReadVersion':[C.c_uint32,C.c_void_p,C.c_void_p,C.c_void_p],
        'PassThruIoctl':[C.c_uint32,C.c_uint32,C.c_void_p,C.c_void_p],
        'PassThruClose':[C.c_uint32],
        'PassThruGetLastError':[C.c_void_p],
    }
    for name,args in signatures.items():
        function=getattr(lib,name);function.argtypes=args;function.restype=C.c_uint32
    result={'kind':'live-linux-abi-identity-and-voltage','vehicle_channels_opened':False}
    device=C.c_uint32()
    def call(name,*args):
        status=getattr(lib,name)(*args)
        result[name]=int(status)
        if status:
            error=C.create_string_buffer(80);lib.PassThruGetLastError(error)
            result[name+'_error']=error.value.decode('ascii','replace')
        return status
    if call('PassThruOpen',None,C.byref(device))==0:
        try:
            buffers=[C.create_string_buffer(80) for _ in range(3)]
            if call('PassThruReadVersion',device.value,*buffers)==0:
                result.update(zip(('firmware','driver','api'),(b.value.decode('ascii','replace') for b in buffers)))
            voltage=C.c_uint32()
            if call('PassThruIoctl',device.value,3,None,C.byref(voltage))==0:
                result['supply_mv']=voltage.value
        finally:call('PassThruClose',device.value)
    directory=root/'.local/adapter-checks';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    report=directory/(uuid.uuid4().hex+'-abi.json')
    report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({**result,'report':str(report)}))
    return int(any(result.get(name,1)!=0 for name in ('PassThruOpen','PassThruReadVersion','PassThruIoctl','PassThruClose')))


if __name__=='__main__':raise SystemExit(main())
