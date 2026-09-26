"""Build the 32-bit Windows DLL without redistributing vendor code."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'build'
EXPORTS={'PassThruOpen':8,'PassThruClose':4,'PassThruConnect':20,
         'PassThruDisconnect':4,'PassThruReadVersion':16,'PassThruGetLastError':4,
         'PassThruReadMsgs':16,'PassThruWriteMsgs':16,'PassThruIoctl':16,
         'PassThruStartMsgFilter':24,'PassThruStopMsgFilter':8,
         'PassThruStartPeriodicMsg':16,'PassThruStopPeriodicMsg':8,
         'PassThruSetProgrammingVoltage':12}
IMPORTS={'CreateFileA':28,'ReadFile':20,'WriteFile':20,'CloseHandle':4,
         'MoveFileA':8,'DeleteFileA':4,'GetTickCount':0,'GetCurrentProcessId':0,
         'Sleep':4,'CreateMutexA':12,'WaitForSingleObject':8,'ReleaseMutex':4}
IMPORTS.update({'LoadLibraryA':4,'GetProcAddress':8,'GetStdHandle':4,'ExitProcess':4})
IMPORTS.update({'LocalAlloc':8,'LocalFree':4})
IMPORTS.update({'OutputDebugStringA':4})

def build():
    OUT.mkdir(exist_ok=True)
    (OUT/'bridge-kernel32.def').write_text('LIBRARY KERNEL32.dll\nEXPORTS\n'+
        '\n'.join(f'{name}@{n}' for name,n in IMPORTS.items())+'\n')
    (OUT/'bridge.def').write_text('LIBRARY GD101_NATIVE.dll\nEXPORTS\n'+
        '\n'.join(f'{name}=_{name}@{n}' for name,n in EXPORTS.items())+'\n')
    subprocess.run(['llvm-dlltool','-m','i386','-k','-d',str(OUT/'bridge-kernel32.def'),
                    '-l',str(OUT/'bridge-kernel32.lib')],check=True)
    subprocess.run(['clang','--target=i686-pc-windows-msvc','-O2','-ffreestanding',
                    '-c',str(ROOT/'wine_bridge.c'),'-o',str(OUT/'wine_bridge.obj')],check=True)
    subprocess.run(['lld-link','/dll','/noentry','/nodefaultlib','/machine:x86',
                    '/def:'+str(OUT/'bridge.def'),'/out:'+str(OUT/'GD101_NATIVE.dll'),
                    str(OUT/'wine_bridge.obj'),str(OUT/'bridge-kernel32.lib')],check=True)
    for probe in ('probe_wine_bridge','probe_wine_write'):
        subprocess.run(['clang','--target=i686-pc-windows-msvc','-O2','-ffreestanding',
                        '-c',str(ROOT/(probe+'.c')),'-o',str(OUT/(probe+'.obj'))],check=True)
        subprocess.run(['lld-link','/entry:mainCRTStartup','/subsystem:console','/nodefaultlib','/machine:x86',
                        '/out:'+str(OUT/(probe+'.exe')),str(OUT/(probe+'.obj')),
                        str(OUT/'bridge-kernel32.lib')],check=True)

if __name__=='__main__':build()
