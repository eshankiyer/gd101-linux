/* 32-bit Windows J2534 boundary for the native Linux broker.
 * Filesystem mailbox only; no serial driver, network, or vehicle access here.
 */
typedef unsigned long U32;
typedef void *HANDLE;
typedef struct {U32 ProtocolID,RxStatus,TxFlags,Timestamp,DataSize,ExtraDataIndex;unsigned char Data[4128];} MESSAGE;
typedef struct {U32 NumOfBytes;unsigned char*BytePtr;} BYTE_ARRAY;
typedef struct {U32 Parameter,Value;} CONFIG;
typedef struct {U32 NumOfParams;CONFIG*ConfigPtr;} CONFIG_LIST;
#define API __declspec(dllexport) U32 __stdcall
#define IMPORT __declspec(dllimport)
IMPORT HANDLE __stdcall CreateFileA(const char*,U32,U32,void*,U32,U32,HANDLE);
IMPORT int __stdcall ReadFile(HANDLE,void*,U32,U32*,void*);
IMPORT int __stdcall WriteFile(HANDLE,const void*,U32,U32*,void*);
IMPORT int __stdcall CloseHandle(HANDLE);
IMPORT int __stdcall MoveFileA(const char*,const char*);
IMPORT int __stdcall DeleteFileA(const char*);
IMPORT U32 __stdcall GetTickCount(void);
IMPORT U32 __stdcall GetCurrentProcessId(void);
IMPORT void __stdcall Sleep(U32);
IMPORT HANDLE __stdcall CreateMutexA(void*,int,const char*);
IMPORT U32 __stdcall WaitForSingleObject(HANDLE,U32);
IMPORT int __stdcall ReleaseMutex(HANDLE);
IMPORT HANDLE __stdcall LocalAlloc(U32,U32);
IMPORT HANDLE __stdcall LocalFree(HANDLE);
IMPORT void __stdcall OutputDebugStringA(const char*);
static U32 sequence=0;
static int uncertain=0;
static char last_error[80];
static void copy(void *d,const void*s,U32 n){for(U32 i=0;i<n;i++)((char*)d)[i]=((const char*)s)[i];}
static U32 fail(U32 status,const char*s){U32 i=0;while(s[i]&&i<79){last_error[i]=s[i];i++;}last_error[i]=0;OutputDebugStringA("GD101 bridge error:");OutputDebugStringA(last_error);return status;}
static void hex(char*p,U32 n){for(int i=0;i<8;i++)p[7-i]="0123456789abcdef"[(n>>(i*4))&15];}
static void trace_value(const char*label,U32 value){char text[9];hex(text,value);text[8]=0;OutputDebugStringA(label);OutputDebugStringA(text);}
static U32 rpc_data(U32 op,U32 a,U32 b,U32 c,U32 d,const void*input,U32 input_size,void*out,U32 wanted){
    HANDLE mutex=CreateMutexA(0,0,"Local\\GD101NativeBridgeRPC");
    if(!mutex)return fail(7,"Cannot create RPC lock");
    U32 wait=WaitForSingleObject(mutex,30000);
    if(wait!=0){if(wait==0x80)ReleaseMutex(mutex);CloseHandle(mutex);return fail(7,"RPC lock unavailable or abandoned");}
    U32 status=7;
    char request[]="C:\\gd101-bridge\\00000000-00000000.req";
    char temp[]="C:\\gd101-bridge\\00000000-00000000.tmp";
    char reply[]="C:\\gd101-bridge\\00000000-00000000.res";
    if(!sequence)sequence=GetTickCount();
    U32 pid=GetCurrentProcessId(),seq=++sequence,n=0;
    if(!seq)seq=++sequence;
    U32 packet[10]={0x31444742,1,pid,seq,op,a,b,c,d,input_size};
    static U32 response[1042]; /* 16-byte RPC header + 4152-byte J2534 message */
    hex(request+16,pid);hex(request+25,seq);
    hex(temp+16,pid);hex(temp+25,seq);
    hex(reply+16,pid);hex(reply+25,seq);
    if(uncertain){fail(7,"RPC timed out earlier; restart bridge and application");goto done;}
    HANDLE f=CreateFileA(temp,0x40000000,0,0,1,0x80,0);
    if(f==(HANDLE)-1){fail(7,"Cannot create request; native broker mailbox missing");goto done;}
    int ok=WriteFile(f,packet,sizeof(packet),&n,0);
    ok=ok&&n==sizeof(packet);
    if(ok&&input_size){ok=WriteFile(f,input,input_size,&n,0);ok=ok&&n==input_size;}
    CloseHandle(f);
    if(!ok||!MoveFileA(temp,request)){DeleteFileA(temp);fail(7,"Could not publish RPC request");goto done;}
    U32 start=GetTickCount();
    for(;;){
        f=CreateFileA(reply,0x80000000,0,0,3,0x80,0);
        if(f!=(HANDLE)-1){
            ok=ReadFile(f,response,sizeof(response),&n,0);CloseHandle(f);DeleteFileA(reply);
            if(!ok||n<16||response[0]!=0x31444742||response[1]!=seq||response[3]!=n-16){fail(7,"Malformed RPC response");break;}
            status=response[2];
            if(!status){if(response[3]!=wanted){status=fail(7,"Unexpected RPC payload length");break;}if(wanted)copy(out,response+4,wanted);}
            else if(response[3]==80){copy(last_error,response+4,80);last_error[79]=0;}
            else fail(status,"Native backend returned an error; inspect broker capture");
            break;
        }
        if(GetTickCount()-start>=30000){uncertain=1;DeleteFileA(request);fail(7,"Native RPC timeout; operation outcome uncertain");break;}
        Sleep(5);
    }
done:
    ReleaseMutex(mutex);CloseHandle(mutex);return status;
}
static U32 rpc(U32 op,U32 a,U32 b,U32 c,U32 d,void*out,U32 wanted){return rpc_data(op,a,b,c,d,0,0,out,wanted);}
/* Bare connection prefixes select the default device; a suffix is not ignored. */
static int default_name(const char*n){
    if(!n)return 1;
    const char*prefix="J2534-";
    for(U32 i=0;i<6;i++)if(n[i]!=prefix[i])return 0;
    return (n[6]=='1'||n[6]=='2')&&n[7]==':'&&n[8]==0;
}
API PassThruOpen(void*name,U32*id){OutputDebugStringA("GD101 PassThruOpen");if(!id)return fail(4,"Null device output");*id=0;if(!default_name(name))return fail(1,"Named-device suffix or connection pattern unsupported");return rpc(1,0,0,0,0,id,4);}
API PassThruClose(U32 id){return rpc(2,id,0,0,0,0,0);}
API PassThruConnect(U32 id,U32 protocol,U32 flags,U32 baud,U32*channel){if(!channel)return fail(4,"Null channel output");*channel=0;return rpc(3,id,protocol,flags,baud,channel,4);}
API PassThruDisconnect(U32 channel){return rpc(4,channel,0,0,0,0,0);}
API PassThruReadVersion(U32 id,char*firmware,char*driver,char*api){if(!firmware||!driver||!api)return fail(4,"Null version output");char data[240];U32 s=rpc(5,id,0,0,0,data,240);if(!s){copy(firmware,data,80);copy(driver,data+80,80);copy(api,data+160,80);}return s;}
API PassThruGetLastError(char*out){if(!out)return 4;copy(out,last_error,80);return 0;}
/* Explicit failures for functionality still being ported. */
API PassThruReadMsgs(U32 channel,MESSAGE*msgs,U32*count,U32 timeout){
    if(!count)return fail(4,"Null message count");
    U32 requested=*count;*count=0;
    if(!msgs)return fail(4,"Null message array");
    if(!requested)return fail(10,"Empty receive array");
    if(timeout>25000)return fail(1,"Read timeouts above 25 seconds are not bridged yet");
    MESSAGE*wire=LocalAlloc(0,sizeof(MESSAGE));
    if(!wire)return fail(7,"Receive allocation failed");
    U32 start=GetTickCount(),status=0;
    for(U32 i=0;i<requested;i++){
        U32 elapsed=GetTickCount()-start;
        if(timeout&&elapsed>=timeout){status=fail(9,"Receive batch timeout");break;}
        status=rpc(13,channel,timeout?timeout-elapsed:0,0,0,wire,sizeof(MESSAGE));
        if(status){if(!timeout&&status==16&&*count)status=0;break;}
        if((wire->ProtocolID!=3&&wire->ProtocolID!=4&&wire->ProtocolID!=5&&wire->ProtocolID!=6)||
            wire->DataSize>4128||wire->ExtraDataIndex!=wire->DataSize||
            (wire->ProtocolID==5&&(wire->DataSize<4||wire->DataSize>12))||
            (wire->ProtocolID==6&&((wire->RxStatus&0x0a)?
                wire->DataSize!=(wire->RxStatus&0x80?5:4):
                (wire->DataSize<(wire->RxStatus&0x80?6:5)||wire->DataSize>(wire->RxStatus&0x80?4100:4099))))){
            status=fail(7,"Malformed receive message");break;}
        copy(msgs+i,wire,24);copy(msgs[i].Data,wire->Data,wire->DataSize);
        *count=i+1;
    }
    LocalFree(wire);return status;
}
API PassThruWriteMsgs(U32 channel,MESSAGE*msgs,U32*count,U32 timeout){
    if(!count)return fail(4,"Null message count");
    U32 requested=*count;*count=0;
    if(!msgs)return fail(4,"Null message array");
    if(!requested)return fail(10,"Empty message array");
    if(timeout>25000)return fail(1,"Timeouts above 25 seconds are not bridged yet");
    U32 start=GetTickCount();
    for(U32 i=0;i<requested;i++){
        MESSAGE*m=msgs+i;
        if(m->DataSize<1||m->DataSize>4128)return fail(10,"Message exceeds ABI buffer bounds");
        U32 elapsed=GetTickCount()-start;
        if(timeout&&elapsed>=timeout)return fail(9,"Message batch timeout");
        U32 status=rpc_data(10,channel,m->ProtocolID,m->TxFlags,timeout?timeout-elapsed:0,m->Data,m->DataSize,0,0);
        if(status)return status;
        *count=i+1;
    }
    return 0;
}
API PassThruIoctl(U32 id,U32 code,void*in,void*out){
    trace_value("GD101 IOCTL (hex)",code);
    if(code==1||code==2){
        if(!in)return fail(4,"Null configuration list");
        CONFIG_LIST*config=in;U32 count=config->NumOfParams;
        if(count>64)return fail(10,"Configuration list exceeds 64 entries");
        if(count&&!config->ConfigPtr)return fail(4,"Null configuration entries");
        U32 data[128],values[64];
        for(U32 i=0;i<count;i++){
            if(code==2){data[2*i]=config->ConfigPtr[i].Parameter;data[2*i+1]=config->ConfigPtr[i].Value;}
            else data[i]=config->ConfigPtr[i].Parameter;
        }
        U32 status=rpc_data(code==2?19:20,id,0,0,0,data,count*(code==2?8:4),code==1?values:0,code==1?count*4:0);
        if(!status&&code==1)for(U32 i=0;i<count;i++)config->ConfigPtr[i].Value=values[i];
        return status;
    }
    if(code==3){if(!out)return fail(4,"Null battery-voltage output");return rpc(17,id,0,0,0,out,4);}
    if(code==7)return rpc(25,id,0,0,0,0,0);
    if(code==9)return rpc(24,id,0,0,0,0,0);
    if(code==8)return rpc(16,id,0,0,0,0,0);
    if(code==10)return rpc(9,id,0,0,0,0,0);
    if(code==4){
        BYTE_ARRAY*source=in;BYTE_ARRAY*target=out;
        if(!source||!target||!source->BytePtr||!target->BytePtr)return fail(4,"Null FIVE_BAUD_INIT buffer");
        if(source->NumOfBytes!=1)return fail(10,"FIVE_BAUD_INIT requires one address byte");
        if(target->NumOfBytes<2)return fail(18,"FIVE_BAUD_INIT output needs two bytes");
        unsigned char keywords[2];
        U32 status=rpc_data(15,id,0,0,0,source->BytePtr,1,keywords,2);
        if(!status){copy(target->BytePtr,keywords,2);target->NumOfBytes=2;}
        return status;
    }
    if(code==5){
        MESSAGE*message=in;
        if(message&&message->DataSize>228)return fail(10,"FAST_INIT request too large");
        MESSAGE*result=out?LocalAlloc(0,sizeof(MESSAGE)):0;
        if(out&&!result)return fail(7,"FAST_INIT output allocation failed");
        U32 status=rpc_data(12,id,message?message->ProtocolID:0,message?message->TxFlags:0,
            out?1:0,message?message->Data:0,message?message->DataSize:0,
            result,out?sizeof(MESSAGE):0);
        if(!status&&out){
            if(result->ProtocolID!=4||result->DataSize>4128||result->ExtraDataIndex!=result->DataSize)
                status=fail(7,"Malformed FAST_INIT response");
            else copy(out,result,sizeof(MESSAGE));
        }
        if(result)LocalFree(result);
        return status;
    }
    return fail(1,"This IOCTL is not implemented yet");
}
API PassThruStartMsgFilter(U32 c,U32 t,MESSAGE*m,MESSAGE*p,MESSAGE*f,U32*id){
    if(!id||!m||!p)return fail(4,"Null filter argument");*id=0;
    if(t==3){
        if(!f)return fail(4,"Null flow-control message");
        if(m->ProtocolID!=p->ProtocolID||m->ProtocolID!=f->ProtocolID)return fail(21,"Filter protocols differ");
        if(m->TxFlags!=p->TxFlags||m->TxFlags!=f->TxFlags)return fail(6,"Filter flags differ");
        U32 size=m->DataSize;
        if((size!=4&&size!=5)||size!=p->DataSize||size!=f->DataSize)return fail(10,"Invalid flow-filter length");
        unsigned char flow_data[15];copy(flow_data,m->Data,size);copy(flow_data+size,p->Data,size);copy(flow_data+2*size,f->Data,size);
        return rpc_data(21,c,m->ProtocolID,m->TxFlags,0,flow_data,3*size,id,4);
    }
    if(t!=1&&t!=2)return fail(1,"Only pass/block filters are bridged");
    if(f)return fail(10,"Flow-control message supplied for pass/block filter");
    if(m->ProtocolID!=p->ProtocolID)return fail(21,"Filter protocols differ");
    if(m->DataSize>12||m->DataSize!=p->DataSize)return fail(10,"Invalid filter length");
    if(m->TxFlags!=p->TxFlags)return fail(6,"Filter flags differ");
    unsigned char data[24];copy(data,m->Data,m->DataSize);copy(data+m->DataSize,p->Data,p->DataSize);
    return rpc_data(14,c,t,m->ProtocolID,m->TxFlags,data,2*m->DataSize,id,4);
}
API PassThruStopMsgFilter(U32 c,U32 id){return rpc(8,c,id,0,0,0,0);}
API PassThruStartPeriodicMsg(U32 c,MESSAGE*m,U32*id,U32 interval){
    if(!id)return fail(4,"Null periodic handle output");
    *id=0;
    if(!m)return fail(4,"Null periodic message");
    if(m->DataSize>4128)return fail(10,"Periodic message exceeds ABI capacity");
    return rpc_data(22,c,m->ProtocolID,m->TxFlags,interval,m->Data,m->DataSize,id,4);
}
API PassThruStopPeriodicMsg(U32 c,U32 id){return rpc(23,c,id,0,0,0,0);}
API PassThruSetProgrammingVoltage(U32 id,U32 pin,U32 volts){trace_value("GD101 programming pin (hex)",pin);trace_value("GD101 programming value (hex)",volts);return rpc(18,id,pin,volts,0,0,0);}
