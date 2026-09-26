"""Simulation fixture. Never imports or opens serial hardware."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from gd101_abi import LifecycleProvider
from gd101_bridge import serve
from gd101_can import CANFrame
from gd101_kline import KLineMessage

class SimulatedSession:
    def __init__(self):
        self.record={};self.attempts=[];self.uncertain=False;self.receiver=None;self.events=[]
        self.frames=iter([CANFrame(0x7e8,b'\x41\x0c',False,1234),
            CANFrame(0x18daf110,b'\x22',True,0xfffffff0),None,
            CANFrame(0x123,b'',False,50),None,None])
    def __enter__(self):return self
    def read_version(self):
        self.version_reads=getattr(self,'version_reads',0)+1
        if self.version_reads==1:raise TimeoutError('Synthetic version timeout')
        return bytes.fromhex('4003000e014d54303030343138360217531e50424758383930300b4511f3')
    def read_battery_voltage(self):return 12345
    def disable_programming_voltage(self,pin):
        assert pin in (9,12,13)
    def set_programming_voltage(self,pin,value):
        assert (pin,value) in ((9,0xfffffffe),(12,12000),(13,18000),(12,12345))
        with (mailbox/'programming-pins.jsonl').open('a') as out:
            out.write(json.dumps({'pin':pin,'value':value})+'\n')
        if value==12345:raise TimeoutError('Synthetic programming output timeout')
    def queue_kline(self,data,**kwargs):
        assert data==b'\x81'
        assert kwargs==dict(protocol=4,no_checksum=True,timing=5)
        self.queued=True
        return 1
    def queue_isotp(self,slot,data):
        assert slot in self.iso_routes and data==bytes(range(256))*2
        self.queued=True
        return 1
    def queue_can(self,identifier,data,**kwargs):
        assert identifier==0x7df
        if data==b'\x22':raise OverflowError('Synthetic queue full')
        assert data==b'\x11'
        self.queued=True
        return 1
    def clear_tx_queue(self):self.queued=False
    def start_periodic_can(self,identifier,data,interval,**kwargs):
        assert identifier==0x7df and data==b'\x01' and interval==1000
        self.periodic=True
        return 42
    def configure_kline_runtime(self,*,timing=None,gap=None,p2_max=None,loopback=None,baudrate=None,parity=None):
        if parity is not None:
            self.kline_parity=parity
            with (mailbox/'kline-parity-changes.jsonl').open('a') as out:out.write(json.dumps(parity)+'\n')
        if baudrate is not None:
            assert baudrate in (9600,10400)
            self.kline_baudrate=baudrate
            with (mailbox/'kline-rate-changes.jsonl').open('a') as out:
                out.write(json.dumps({'rate':baudrate})+'\n')
        if loopback is not None:self.kline_loopback=loopback
        if gap is not None:self.receiver.set_kline_gap(gap)
        if timing is not None:self.periodic_timing=timing
    def start_periodic_kline(self,data,interval,**kwargs):
        assert data==b'\x81' and interval==1000
        assert kwargs==dict(protocol=4,no_checksum=True,timing=5,p2_max=110)
        self.periodic=True
        return 42
    def start_periodic_isotp(self,slot,data,interval):
        assert slot in self.iso_routes and data==bytes(range(256))*2 and interval==1000
        self.periodic=True
        return 42
    def stop_periodic(self,handle):
        assert handle==42 and self.periodic
        self.periodic=False
    def clear_periodic(self):self.periodic=False
    def configure_isotp_loopback(self,enabled):
        config=bytearray(self.iso_config);config[4]=int(enabled);self.iso_config=bytes(config)
    def configure_isotp_routes(self,entries,**kwargs):
        if 'bitrate' in kwargs:
            assert kwargs['bitrate']==250000 and entries==self.iso_routes and len(entries)==1
            assert kwargs['config'][4:6]==b'\x01\x09'
            self.can_bitrate=kwargs['bitrate']
            (mailbox/'iso-rate-change.json').write_text(json.dumps({'rate':self.can_bitrate,'slots':list(entries)}))
        self.iso_routes=dict(entries)
        from gd101_isotp import default_flow_config
        self.iso_config=kwargs.get('config') or default_flow_config()
    def write_isotp_ordered(self,slot,data,**kwargs):
        assert slot in self.iso_routes
        assert data==bytes(range(256))*2
        (mailbox/'isotp-write.json').write_text(json.dumps({'slot':slot,'hex':data.hex()}))
    def configure_can_bitrate(self,rate,*,loopback=None,timing=None):
        assert rate in (250000,500000)
        assert timing==((75,20) if rate==250000 else (80,15))
        self.can_bitrate=rate
        if loopback is not None:self.can_loopback=loopback
        with (mailbox/'can-rate-changes.jsonl').open('a') as out:
            out.write(json.dumps({'rate':rate,'loopback':self.can_loopback})+'\n')
    def configure_can_loopback(self,enabled):self.can_loopback=enabled
    def connect_can(self,baud):
        assert baud==500000
        self.can_loopback=False;self.can_echo=None;self.can_bitrate=baud
    def disconnect_can(self):pass
    def connect_kline(self,baud):
        assert baud==10400
        self.kline_loopback=0
    def disconnect_kline(self):pass
    def write_kline_ordered(self,data,**kwargs):
        assert 0<kwargs['timeout']<=1
        if data==b'\x81':raise TimeoutError('Synthetic K-line write timeout')
        if self.kline_loopback:self.receiver.publish_kline_echo(data,67890)
        if len(data)==4096:
            assert data==bytes(range(256))*16
            (mailbox/'kline-long-write.json').write_text(json.dumps({'size':len(data),'hex':data.hex()}))
            return
        assert data==bytes(range(256))*2
        (mailbox/'kline-write.json').write_text(json.dumps({'size':len(data),'hex':data.hex()}))
    def initialize_kline(self,kind,data,**kwargs):
        if kind=='five_baud':
            assert kwargs=={'no_checksum':True,'protocol':4}
            if data==b'\x34':raise TimeoutError('Synthetic five-baud initialization timeout')
            assert data==b'\x33'
            self.kline_baudrate=9600
            return b'\x08\x08'
        assert kind=='fast'
        assert kwargs['no_checksum'] is True
        if data==b'\x81':raise TimeoutError('Synthetic ECU response timeout')
        assert data==b'\xc1\x33\xf1\x81\x66'
        assert kwargs['flags']==0x200
        if not kwargs['capture_response']:return None
        assert kwargs['response_timeout']==0.355
        return KLineMessage(0,4321,4350,bytes(range(256))*2)
    def start_receiver(self,**kwargs):
        if 'isotp' in kwargs:
            from gd101_receiver import PacketReceiver
            self.receiver=PacketReceiver(None,bytes(32),isotp=kwargs['isotp'])
            self.receiver.stop=lambda:None
            self.iso_mode=True;self.iso_read=False
            return
        if 'assembler' in kwargs:
            from gd101_receiver import PacketReceiver
            self.receiver=PacketReceiver(None,bytes(32),assembler=kwargs['assembler'])
            # Feed deterministic byte events directly; no reader thread or port.
            self.receiver.stop=lambda:None
            self.kline_injected=False
    def read_message(self,timeout):
        if getattr(self,'iso_mode',False):
            if not getattr(self,'iso_start_read',False):
                self.iso_start_read=True
                from gd101_isotp import ISOTPStartOfMessage
                return ISOTPStartOfMessage(0x7e8,False,None,900)
            from gd101_isotp import ISOTPMessage
            if self.iso_read:
                if getattr(self,'iso_done_read',False):
                    if not getattr(self,'iso_echo_read',False):
                        self.iso_echo_read=True
                        assert self.iso_config[4]==1, 'Loopback setting did not survive route configuration'
                        from gd101_isotp import ISOTPTransmitEcho
                        return ISOTPTransmitEcho(0x7e0,False,None,1001,bytes(range(256))*2)
                    if not getattr(self,'iso_overflow_read',False):
                        self.iso_overflow_read=True
                        raise BufferError('Synthetic lost completion indication')
                    raise TimeoutError('No ISO-TP fixture message')
                self.iso_done_read=True
                from gd101_isotp import ISOTPTransmitDone
                return ISOTPTransmitDone(0x7e0,False,None,1001)
            self.iso_read=True
            return ISOTPMessage(0x7e8,bytes(range(256))*2,False,None,901,999,0)
        from gd101_kline import KLineByte
        if not self.kline_injected:
            self.kline_injected=True
            for index,value in enumerate((0x81,0x82,0x83)):
                for i in range(512):
                    self.receiver._accept_kline(index+i*.001,KLineByte(0,1000*(index+1)+i,value))
                self.receiver._accept_kline(index+.550)
        return self.receiver.read_message(0)
    def clear_rx_buffer(self):
        if self.receiver is not None:self.receiver.clear_receive(4)
        else:self.frames=iter(())
    def read_can_message(self,timeout):
        if self.can_echo is not None:
            frame=self.can_echo;self.can_echo=None
            return frame
        frame=next(self.frames,None)
        if frame is None:raise TimeoutError('Synthetic empty receive queue')
        return frame
    def write_can_ordered(self,identifier,data,**kwargs):
        if self.uncertain:raise RuntimeError('Previous send has uncertain outcome')
        self.attempts.append({'id':identifier,'data':data.hex(),'options':kwargs})
        if len(self.attempts)==2:
            self.uncertain=True
            raise TimeoutError('Simulated lost completion')
        if self.can_loopback:
            from gd101_can import CANTransmitEcho
            self.can_echo=CANTransmitEcho(identifier,bytes(data),kwargs.get('extended',False),43210)
    def close(self):
        (mailbox/'simulation.json').write_text(json.dumps(self.attempts,indent=2))

mailbox=Path(sys.argv[1])
assert (mailbox.parent/'SIMULATION_ONLY').is_file(), 'Test marker required'
serve(mailbox,LifecycleProvider(SimulatedSession))
