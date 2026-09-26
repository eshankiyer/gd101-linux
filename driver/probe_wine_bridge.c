/* Laptop-only bridge test: CAN/K-line lifecycle, filters and empty reads.
 * Sends no CAN data frames, K-line initialization or diagnostic requests. */
typedef unsigned long U32;
typedef void *HANDLE;
typedef struct {U32 protocol,rx,tx,time,size,extra;unsigned char data[4128];} MSG;
static MSG mask,pattern;
__declspec(dllimport) HANDLE __stdcall LoadLibraryA(const char*);
__declspec(dllimport) void* __stdcall GetProcAddress(HANDLE,const char*);
__declspec(dllimport) HANDLE __stdcall GetStdHandle(U32);
__declspec(dllimport) int __stdcall WriteFile(HANDLE,const void*,U32,U32*,void*);
__declspec(dllimport) void __stdcall ExitProcess(U32);
static void text(const char*s){U32 n=0,w=0;while(s[n])n++;WriteFile(GetStdHandle(-11),s,n,&w,0);}
static void hex(U32 v){char s[11]="00000000\r\n";for(int i=0;i<8;i++)s[7-i]="0123456789abcdef"[(v>>(4*i))&15];text(s);}
void mainCRTStartup(void){
    HANDLE h=LoadLibraryA("GD101_NATIVE.dll");if(!h){text("DLL load failed\r\n");ExitProcess(1);}
    U32 (__stdcall *open)(void*,U32*)=GetProcAddress(h,"PassThruOpen");
    U32 (__stdcall *version)(U32,char*,char*,char*)=GetProcAddress(h,"PassThruReadVersion");
    U32 (__stdcall *close)(U32)=GetProcAddress(h,"PassThruClose");
    U32 (__stdcall *error)(char*)=GetProcAddress(h,"PassThruGetLastError");
    U32 (__stdcall *connect)(U32,U32,U32,U32,U32*)=GetProcAddress(h,"PassThruConnect");
    U32 (__stdcall *disconnect)(U32)=GetProcAddress(h,"PassThruDisconnect");
    U32 (__stdcall *filter)(U32,U32,MSG*,MSG*,MSG*,U32*)=GetProcAddress(h,"PassThruStartMsgFilter");
    U32 (__stdcall *stop)(U32,U32)=GetProcAddress(h,"PassThruStopMsgFilter");
    U32 (__stdcall *ioctl)(U32,U32,void*,void*)=GetProcAddress(h,"PassThruIoctl");
    U32 (__stdcall *read)(U32,MSG*,U32*,U32)=GetProcAddress(h,"PassThruReadMsgs");
    if(!open||!version||!close||!error||!connect||!disconnect||!filter||!stop||!ioctl||!read)ExitProcess(2);
    U32 id=0,status=open(0,&id);text("PassThruOpen: ");hex(status);
    if(status){char e[80];error(e);text(e);text("\r\n");ExitProcess(3);}
    char firmware[80],driver[80],api[80];
    status=version(id,firmware,driver,api);text("PassThruReadVersion: ");hex(status);
    if(!status){text("Firmware raw reply: ");text(firmware);text("\r\nDriver: ");text(driver);text("\r\nAPI: ");text(api);text("\r\n");}
    U32 channel=0,filter_id=0;
    if(!status){status=connect(id,5,0,500000,&channel);text("CAN connect: ");hex(status);}
    if(!status){
        mask.protocol=pattern.protocol=5;mask.size=pattern.size=4;
        mask.data[2]=7;mask.data[3]=255;pattern.data[2]=7;pattern.data[3]=0xe8;
        status=filter(channel,1,&mask,&pattern,0,&filter_id);text("Start CAN filter: ");hex(status);
        if(!status){U32 count=1;U32 s=read(channel,&mask,&count,0);text("Empty nonblocking read (expected 16): ");hex(s);if(s!=16||count)status=7;}
        if(!status){U32 count=1;U32 s=read(channel,&mask,&count,20);text("Empty timed read (expected 9): ");hex(s);if(s!=9||count)status=7;}
        if(!status){status=stop(channel,filter_id);text("Stop CAN filter: ");hex(status);}
        if(!status){U32 expected=stop(channel,filter_id);text("Invalid filter (expected 22): ");hex(expected);if(expected!=22)status=7;}
        if(!status){status=ioctl(channel,10,0,0);text("Clear CAN filters: ");hex(status);}
    }
    if(channel){U32 s=disconnect(channel);text("CAN disconnect: ");hex(s);if(s)status=s;}
    channel=0;
    if(!status){status=connect(id,4,0,10400,&channel);text("K-line connect: ");hex(status);}
    if(!status){
        mask.protocol=pattern.protocol=4;mask.size=pattern.size=0;mask.tx=pattern.tx=0;
        status=filter(channel,1,&mask,&pattern,0,&filter_id);text("Start K-line filter: ");hex(status);
        if(!status){U32 count=1;U32 s=read(channel,&mask,&count,0);text("K-line empty poll (expected 16): ");hex(s);if(s!=16||count)status=7;}
        if(!status){U32 count=1;U32 s=read(channel,&mask,&count,20);text("K-line empty wait (expected 9): ");hex(s);if(s!=9||count)status=7;}
        if(!status){status=stop(channel,filter_id);text("Stop K-line filter: ");hex(status);}
        if(!status){status=ioctl(channel,10,0,0);text("Clear K-line filters: ");hex(status);}
    }
    if(channel){U32 s=disconnect(channel);text("K-line disconnect: ");hex(s);if(s)status=s;}
    U32 cleanup=close(id);text("PassThruClose: ");hex(cleanup);
    text("No CAN data frames, K-line initialization or ECU diagnostic requests transmitted.\r\n");
    ExitProcess(status||cleanup?4:0);
}
