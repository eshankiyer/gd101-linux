"""Partial native J2534-style provider. No hardware is opened at import time.

Error/ID conventions are from the local j2534 reference header. CAN messaging,
K-line writes and FAST_INIT are supported while remaining exports are ported.
"""
from functools import wraps
import math
from pathlib import Path
import threading
import time
import uuid


class LifecycleProvider:
    def __init__(self, factory=None):
        self.factory=factory
        self.session=None
        self.device=None
        self.channel=None
        self.protocol=None
        self.connect_flags=0
        self.baudrate=None
        self.can_loopback=False
        self.can_timing=(80,15)
        self.timing_config={}
        self.isotp_filters={}
        self.periodic_handles={}
        self.isotp_config=None
        self.kline_filters={}
        self.next_id=1
        self.last_error=''
        self.lock=threading.RLock()

    def failure(self, status, text):
        self.last_error=text
        return status

    def open(self):
        if self.session is not None:
            return self.failure(14,'Adapter is already open'),0
        from gd101_devices import GD101NotConnected
        try:
            if self.factory is None:
                from gd101_driver import GD101
                root=Path(__file__).resolve().parents[1]/'.local'/'abi-captures'
                root.mkdir(parents=True,exist_ok=True,mode=0o700)
                session=GD101(root/(uuid.uuid4().hex+'.json'))
            else:
                session=self.factory()
            # Publish the handle only after native authentication succeeds.
            session.__enter__()
        except GD101NotConnected as error:
            return self.failure(8,str(error)),0
        self.session=session
        self.device=self.next_id;self.next_id+=1
        return 0,self.device

    def close(self, device):
        if device!=self.device or self.session is None:
            return self.failure(26,'Invalid device handle')
        session=self.session
        try:
            session.close()
            if any(name in session.record for name in ('close_error','receiver_close_error',
                    'serial_close_error','capture_close_error','log_close_error','pin_close_error')):
                return self.failure(7,'Adapter cleanup failed; session released, capture may be incomplete')
            return 0
        finally:
            self.session=None;self.device=None;self.channel=None;self.protocol=None;self.baudrate=None

    def read_version(self, device):
        if device!=self.device or self.session is None:
            return self.failure(26,'Invalid device handle'),None
        try:raw=self.session.read_version()
        except TimeoutError:return self.failure(9,'Adapter version read timed out'),None
        from gd101_version import firmware_version_text
        try:firmware=firmware_version_text(raw)
        except ValueError as error:return self.failure(7,str(error)),None
        return 0,(firmware,'GD101 native experimental 0.1','04.04')

    def configure_timing(self, channel, pairs=None, parameters=None):
        if channel!=self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),None
        if parameters is not None and 1 in parameters:
            # DATA_RATE is the committed channel rate, not a default or request.
            if self.baudrate is None:return self.failure(7,'Channel data rate is unavailable'),None
            others=[parameter for parameter in parameters if parameter!=1]
            if others:
                status,values=self.configure_timing(channel,parameters=others)
                if status:return status,None
                values=iter(values)
            return 0,[self.baudrate if parameter==1 else next(values) for parameter in parameters]
        if self.protocol==5:return self.configure_can(pairs=pairs,parameters=parameters)
        if self.protocol==6:return self.configure_isotp(pairs=pairs,parameters=parameters)
        if self.protocol not in (3,4):return self.failure(1,'Configuration is not ported for this protocol'),None
        defaults={32:0,22:0,3:0,7:40,10:110,12:10,14:300,15:20,16:20,17:50,19:300,20:25,21:50,33:0}
        defaults[18 if self.protocol==3 else 25]=300
        if parameters is not None:
            if any(p not in defaults for p in parameters):return self.failure(1,'Configuration parameter not ported'),None
            return 0,[self.timing_config.get(p,defaults[p]) for p in parameters]
        candidate=dict(self.timing_config);rate=self.baudrate
        for parameter,value in pairs:
            if parameter==1:
                from gd101_kline import KLINE_BAUD_RATES
                if value not in KLINE_BAUD_RATES:return self.failure(10,'Unsupported K-line data rate'),None
                rate=value
                continue
            if parameter not in defaults:return self.failure(1,'Configuration parameter not ported'),None
            if parameter in (3,22,32):
                if not 0<=value<=0xffffffff:return self.failure(10,'Invalid byte configuration value'),None
                candidate[parameter]=value&255
                continue
            limit={7:1999,10:59999,12:1999,19:7999,33:255}.get(parameter,65535)
            if not 0<=value<=limit:return self.failure(10,'Timing value out of range'),None
            # Original driver stores half-unit fields as integer milliseconds.
            candidate[parameter]=(value//2)*2 if parameter in (7,10,12) else value
        if candidate.get(3,0) not in (0,1):return self.failure(10,'K-line loopback must resolve to zero or one'),None
        if candidate.get(22,0) not in (0,1,2):return self.failure(10,'Unsupported K-line parity'),None
        if candidate.get(32,0)!=0:return self.failure(10,'Only eight-bit K-line data is supported'),None
        runtime={}
        if candidate.get(22,0)!=self.timing_config.get(22,0):runtime['parity']=candidate[22]
        if rate!=self.baudrate:runtime['baudrate']=rate
        if candidate.get(3,0)!=self.timing_config.get(3,0):runtime['loopback']=candidate[3]
        if candidate.get(7,40)!=self.timing_config.get(7,40):
            if self.session.receiver is None:return self.failure(7,'K-line reader is unavailable'),None
            runtime['gap']=candidate[7]/2000
        if candidate.get(12,10)!=self.timing_config.get(12,10):runtime['timing']=candidate[12]//2
        if candidate.get(10,110)!=self.timing_config.get(10,110):runtime['p2_max']=candidate[10]
        if runtime:
            try:self.session.configure_kline_runtime(**runtime)
            except TimeoutError:return self.failure(9,'K-line configuration timed out'),None
        self.timing_config=candidate;self.baudrate=rate
        return 0,None

    def configure_can(self, pairs=None, parameters=None):
        if parameters is not None:
            values={3:int(self.can_loopback),23:self.can_timing[0],24:self.can_timing[1]}
            if any(p not in values for p in parameters):return self.failure(1,'CAN configuration parameter not ported'),None
            return 0,[values[p] for p in parameters]
        candidate=self.can_loopback;rate=self.baudrate;timing=list(self.can_timing)
        for parameter,value in pairs:
            if parameter==1:
                if value not in (125000,200000,250000,500000,1000000):return self.failure(10,'Unsupported CAN data rate'),None
                rate=value
            elif parameter==3:
                if not 0<=value<=0xffffffff:return self.failure(10,'Invalid CAN loopback value'),None
                candidate=bool(value)
            elif parameter in (23,24):
                if not 0<=value<=0xffffffff:return self.failure(10,'Invalid CAN timing value'),None
                timing[parameter-23]=value&255
            else:return self.failure(1,'CAN configuration parameter not ported'),None
        if any(value>100 for value in timing):return self.failure(10,'Invalid CAN timing value'),None
        timing=tuple(timing)
        if rate!=self.baudrate or timing!=self.can_timing:
            options={'loopback':candidate}
            if timing!=self.can_timing:options['timing']=timing
            try:self.session.configure_can_bitrate(rate,**options)
            except TimeoutError:return self.failure(9,'CAN data-rate change timed out; hardware state uncertain'),None
            self.baudrate=rate;self.can_loopback=candidate;self.can_timing=timing
        elif candidate!=self.can_loopback:
            try:self.session.configure_can_loopback(candidate)
            except TimeoutError:return self.failure(9,'CAN loopback configuration timed out'),None
            self.can_loopback=candidate
        return 0,None

    def configure_isotp(self, pairs=None, parameters=None):
        # Original SET/GET_CONFIG 7ab6fdc0 / 7ab6dcf0; offsets in a7f0 region.
        from gd101_isotp import default_flow_config
        fields={3:(4,1),30:(5,1),31:(6,1),34:(8,2),35:(10,2),37:(12,1),43:(26,1),46:(18,2),49:(14,2)}
        current=self.isotp_config if self.isotp_config is not None else default_flow_config()
        if parameters is not None:
            if any(p not in fields and p not in (23,24) for p in parameters):
                return self.failure(1,'ISO-TP configuration parameter not ported'),None
            values={p:int.from_bytes(current[o:o+w],'little') for p,(o,w) in fields.items()}
            values.update({23:self.can_timing[0],24:self.can_timing[1]})
            return 0,[values[p] for p in parameters]
        candidate=bytearray(current);rate=self.baudrate;timing=list(self.can_timing)
        for parameter,value in pairs:
            if parameter==1:
                if value not in (125000,200000,250000,500000,1000000):return self.failure(10,'Unsupported ISO-TP data rate'),None
                rate=value
                continue
            if parameter in (23,24):
                if not 0<=value<=0xffffffff:return self.failure(10,'Invalid ISO-TP CAN timing'),None
                timing[parameter-23]=value&255
                continue
            if parameter not in fields:return self.failure(1,'ISO-TP configuration parameter not ported'),None
            offset,width=fields[parameter]
            if not 0<=value<=0xffffffff:return self.failure(10,'ISO-TP configuration value out of range'),None
            if parameter==3:
                value=int(bool(value))
            else:value &= (1<<(8*width))-1
            candidate[offset:offset+width]=value.to_bytes(width,'little')
        if any(v>100 for v in timing):return self.failure(10,'Invalid ISO-TP CAN timing'),None
        timing=tuple(timing)
        options={} if timing==self.can_timing else {'timing':timing}
        if candidate==current and rate==self.baudrate and not options:return 0,None
        if rate==self.baudrate and candidate[:4]==current[:4] and candidate[5:]==current[5:]:
            # Original 7ab6fdc0 does not reprogram tables for a7f4 alone.
            try:self.session.configure_isotp_loopback(bool(candidate[4]),**options)
            except TimeoutError:return self.failure(9,'ISO-TP loopback configuration timed out'),None
            status=0
        else:status=self._commit_isotp_filters(self.isotp_filters,config=bytes(candidate),bitrate=rate if rate!=self.baudrate else None,**options)
        if not status:
            self.isotp_config=bytes(candidate);self.baudrate=rate;self.can_timing=timing
        return status,None

    def initialization_timings(self):
        if not any(p in self.timing_config for p in (12,14,15,16,17,18,19,20,21,25,33)):return {}
        c=self.timing_config
        result={'timing_words':(c.get(12,10)//2,c.get(19,300),c.get(20,25),c.get(21,50),
                               c.get(18 if self.protocol==3 else 25,300),c.get(14,300),
                               c.get(15,20),c.get(16,20),c.get(17,50))}
        if 33 in c:result['option']=c[33]
        return result

    def set_programming_voltage(self, device, pin, voltage):
        if device!=self.device or self.session is None:
            return self.failure(26,'Invalid device handle')
        from gd101_pins import supported_pin,VOLTAGE_OFF
        if not 0<=voltage<=0xffffffff:return self.failure(10,'Programming voltage must be uint32')
        if not supported_pin(pin,voltage):return self.failure(19,'Unsupported programming pin/mode')
        try:
            if voltage==VOLTAGE_OFF:self.session.disable_programming_voltage(pin)
            else:self.session.set_programming_voltage(pin,voltage)
        except TimeoutError:return self.failure(9,'Programming-pin control timed out')
        return 0

    def read_battery_voltage(self, device):
        if device!=self.device or self.session is None:
            return self.failure(26,'Invalid device handle'),None
        try:
            value=self.session.read_battery_voltage()
        except TimeoutError:
            return self.failure(9,'Battery-voltage read timed out'),None
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            return self.failure(7,'Invalid battery-voltage result'),None
        return 0,value

    def connect(self, device, protocol, flags, baud):
        if device!=self.device or self.session is None:
            return self.failure(26,'Invalid device handle'),0
        if self.channel is not None:
            return self.failure(20,'Only one channel is currently supported'),0
        if protocol not in (3,4,5,6):
            return self.failure(1,'Protocol is not exported through the ABI yet'),0
        if flags & ~(0x200 if protocol in (3,4) else (0x100 if protocol==6 else 0x900)):
            return self.failure(6,'Nonzero connect flags are not exported yet'),0
        if protocol in (5,6):
            if baud not in (125000,200000,250000,500000,1000000):
                return self.failure(25,'Unsupported CAN bitrate'),0
            self.session.connect_can(baud)
            try:
                if protocol==6:
                    from gd101_isotp import ISOTPReassembler
                    self.session.configure_isotp_routes({})
                    self.session.start_receiver(isotp=ISOTPReassembler({},timeout=1))
                else:self.session.start_receiver(filter_can=True)
            except Exception:
                self.session.disconnect_can()
                raise
        else:
            from gd101_kline import KLINE_BAUD_RATES
            if baud not in KLINE_BAUD_RATES:
                return self.failure(25,'Unsupported K-line baud rate'),0
            self.session.connect_kline(baud)
            from gd101_kline import KLineAssembler
            try:self.session.start_receiver(assembler=KLineAssembler(no_checksum=bool(flags&0x200)))
            except Exception:
                self.session.disconnect_kline()
                raise
            self.kline_filters={}
        self.timing_config={}
        self.isotp_filters={}
        self.periodic_handles={}
        self.isotp_config=None
        self.baudrate=baud
        self.can_loopback=False
        self.can_timing=(80,15)
        self.channel=self.next_id;self.next_id+=1;self.protocol=protocol;self.connect_flags=flags
        return 0,self.channel

    def disconnect(self, channel):
        if channel!=self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle')
        if self.protocol in (5,6):self.session.disconnect_can()
        else:self.session.disconnect_kline()
        if self.session.receiver is not None:
            self.session.receiver.stop()
            self.session.receiver=None
        self.session.events.clear()
        self.channel=None;self.protocol=None;self.baudrate=None;self.periodic_handles={}
        return 0

    def _commit_isotp_filters(self, candidate, config=None, bitrate=None, timing=None):
        entries={item['slot']:item['entry'] for item in candidate.values()}
        standard=[];extended=[]
        for item in candidate.values():
            target=extended if item['flags']&0x100 else standard
            target.append((int.from_bytes(item['mask'][:4],'big'),int.from_bytes(item['pattern'][:4],'big')))
        chosen=self.isotp_config if config is None else config
        options={} if chosen is None else {'config':chosen}
        if timing is not None:options['timing']=timing
        if bitrate is not None:options['bitrate']=bitrate
        try:self.session.configure_isotp_routes(entries,standard=standard,extended=extended,**options)
        except TimeoutError:
            return self.failure(9,'ISO-TP route update timed out; adapter state may be uncertain')
        self.isotp_filters=candidate
        return 0

    def start_flow_filter(self, channel, protocol, flags, mask, pattern, flow):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle'),0
        if protocol!=6 or self.protocol!=6:return self.failure(21,'Flow filter requires ISO15765'),0
        if flags&~0x1c0 or bool(flags&0x100)!=bool(self.connect_flags&0x100):
            return self.failure(6,'Unsupported flow flags or CAN-ID width'),0
        size=5 if flags&0x80 else 4
        if any(len(x)!=size for x in (mask,pattern,flow)):return self.failure(10,'Invalid flow-filter message length'),0
        if mask!=b'\xff'*size:return self.failure(1,'Only exact ISO-TP flow masks are ported'),0
        if len(self.isotp_filters)>=29:return self.failure(12,'Native acceptance table capacity reached'),0
        if any((item['flow']==flow or item['pattern']==pattern) and
               (item['flags']&0x180)==(flags&0x180) for item in self.isotp_filters.values()):
            return self.failure(24,'Flow route identity is not unique'),0
        from gd101_isotp import make_flow_entry
        try:entry=make_flow_entry(int.from_bytes(flow[:4],'big'),int.from_bytes(pattern[:4],'big'),
                extended=bool(flags&0x100),transmit_address=flow[4] if size==5 else None,
                receive_address=pattern[4] if size==5 else None,pad=bool(flags&0x40))
        except ValueError as error:return self.failure(10,str(error)),0
        used={item['slot'] for item in self.isotp_filters.values()}
        slot=next(i for i in range(64) if i not in used);handle=self.next_id
        candidate={**self.isotp_filters,handle:dict(slot=slot,entry=entry,mask=bytes(mask),
                  pattern=bytes(pattern),flow=bytes(flow),flags=flags)}
        status=self._commit_isotp_filters(candidate)
        if status:return status,0
        self.next_id+=1
        return 0,handle

    def start_periodic(self, channel, protocol, flags, data, interval):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle'),0
        if protocol!=self.protocol:return self.failure(21,'Message protocol differs from channel'),0
        if protocol not in (3,4,5,6):return self.failure(1,'Periodic protocol is not ported'),0
        if not 5<=interval<=65535:return self.failure(10,'Invalid periodic interval'),0
        if flags&~(0 if protocol in (3,4) else (0x100 if protocol==5 else 0x1c0)):return self.failure(6,'Unsupported periodic flags'),0
        if protocol in (3,4):
            if not 1<=len(data)<=4096:return self.failure(10,'Invalid periodic K-line size'),0
            if interval<max(100,self.timing_config.get(10,110)//2):
                return self.failure(10,'Periodic K-line interval is below configured minimum'),0
        elif protocol==5:
            if not 4<=len(data)<=12:return self.failure(10,'Invalid periodic CAN size'),0
        else:
            size=5 if flags&0x80 else 4
            if not 1<=len(data)-size<=4095:return self.failure(10,'Invalid periodic ISO-TP size'),0
            routes=[item for item in self.isotp_filters.values() if item['flow']==data[:size] and item['flags']==flags]
            if len(routes)!=1:return self.failure(23,'No matching periodic ISO-TP flow route'),0
            _,minimum=self.configure_isotp(parameters=[49])
            if interval<minimum[0]:return self.failure(10,'Periodic interval is below configured minimum'),0
        try:
            if protocol in (3,4):
                native=self.session.start_periodic_kline(data,interval,protocol=protocol,
                    no_checksum=bool(self.connect_flags&0x200),timing=self.timing_config.get(12,10)//2,
                    p2_max=self.timing_config.get(10,110))
            elif protocol==5:
                native=self.session.start_periodic_can(int.from_bytes(data[:4],'big'),data[4:],interval,
                                                       extended=bool(flags&0x100))
            else:native=self.session.start_periodic_isotp(routes[0]['slot'],data[size:],interval)
        except ValueError as error:return self.failure(10,str(error)),0
        except OverflowError as error:return self.failure(12,str(error)),0
        handle=self.next_id;self.next_id+=1
        self.periodic_handles[handle]=native
        return 0,handle

    def stop_periodic(self, channel, handle):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle')
        if handle not in self.periodic_handles:return self.failure(13,'Invalid periodic message handle')
        native=self.periodic_handles[handle]
        try:self.session.stop_periodic(native)
        except KeyError:
            error=self.session.periodic_error(native)
            del self.periodic_handles[handle]
            return self.failure(7 if error else 13,str(error) if error else 'Periodic message no longer exists')
        del self.periodic_handles[handle]
        return 0

    def clear_periodic(self, channel):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle')
        if self.protocol not in (3,4,5,6):return self.failure(1,'Periodic protocol is not ported')
        self.session.clear_periodic();self.periodic_handles={}
        return 0

    def start_filter(self, channel, kind, mask, pattern, flags=0, protocol=None):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),0
        if protocol is not None and protocol!=self.protocol:
            return self.failure(21,'Filter protocol differs from channel'),0
        if kind not in (1,2):
            return self.failure(1,'Flow-control filters require ISO-TP integration'),0
        if self.protocol in (3,4):
            from gd101_kline import KLineFilter
            if flags:return self.failure(6,'Unsupported K-line filter flags'),0
            if len(mask)>12 or len(mask)!=len(pattern):
                return self.failure(10,'K-line filter mask/pattern require equal lengths up to 12'),0
            handle=self.next_id
            updated={**self.kline_filters,handle:KLineFilter(kind,mask,pattern)}
            self.session.receiver.set_kline_filters(updated.values())
            self.kline_filters=updated;self.next_id+=1
            return 0,handle
        if self.protocol!=5:return self.failure(1,'Protocol filters are not exported yet'),0
        if flags & ~0x100:
            return self.failure(6,'Unsupported CAN filter flags'),0
        if not self.connect_flags&0x800 and bool(flags&0x100)!=bool(self.connect_flags&0x100):
            return self.failure(10,'CAN filter ID width differs from channel'),0
        if len(mask)>12 or len(mask) != len(pattern):
            return self.failure(10,'CAN filter requires equal mask/pattern lengths up to 12'),0
        try:return 0,self.session.start_can_filter(kind,mask,pattern,extended=bool(flags&0x100))
        except OverflowError as error:return self.failure(12,str(error)),0

    def stop_filter(self, channel, handle):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle')
        if self.protocol==6:
            if handle not in self.isotp_filters:return self.failure(22,'Invalid filter handle')
            candidate=dict(self.isotp_filters);del candidate[handle]
            return self._commit_isotp_filters(candidate)
        if self.protocol in (3,4):
            if handle not in self.kline_filters:return self.failure(22,'Invalid filter handle')
            updated=dict(self.kline_filters);del updated[handle]
            self.session.receiver.set_kline_filters(updated.values());self.kline_filters=updated
            return 0
        if self.protocol!=5:return self.failure(1,'Protocol filters are not exported yet')
        try:
            self.session.stop_can_filter(handle)
        except ValueError:
            return self.failure(22,'Invalid filter handle')
        return 0

    def clear_rx(self, channel):
        if channel!=self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle')
        self.session.clear_rx_buffer()
        return 0

    def clear_filters(self, channel):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle')
        if self.protocol==6:
            return self._commit_isotp_filters({})
        if self.protocol in (3,4):
            self.session.receiver.set_kline_filters(());self.kline_filters={}
            return 0
        if self.protocol!=5:return self.failure(1,'Protocol filters are not exported yet')
        self.session.clear_can_filters()
        return 0

    def clear_tx(self, channel):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle')
        self.session.clear_tx_queue()
        return 0

    def queue_message(self, protocol, flags, data):
        if protocol not in (3,4,5,6):return self.failure(1,'Queued protocol is not ported')
        if flags&~(0 if protocol in (3,4) else (0x100 if protocol==5 else 0x1c0)):return self.failure(6,'Unsupported queued flags')
        if protocol in (3,4):
            if not 1<=len(data)<=4096:return self.failure(10,'Invalid queued K-line size')
        elif protocol==5:
            if not 4<=len(data)<=12:return self.failure(10,'Invalid queued CAN size')
        else:
            size=5 if flags&0x80 else 4
            if not 1<=len(data)-size<=4095:return self.failure(10,'Invalid queued ISO-TP size')
            routes=[item for item in self.isotp_filters.values() if item['flow']==data[:size] and item['flags']==flags]
            if len(routes)!=1:return self.failure(23,'No matching queued ISO-TP flow route')
        try:
            if protocol in (3,4):
                self.session.queue_kline(data,protocol=protocol,no_checksum=bool(self.connect_flags&0x200),
                                        timing=self.timing_config.get(12,10)//2)
            elif protocol==5:self.session.queue_can(int.from_bytes(data[:4],'big'),data[4:],extended=bool(flags&0x100))
            else:self.session.queue_isotp(routes[0]['slot'],data[size:])
        except ValueError as error:return self.failure(10,str(error))
        except OverflowError as error:return self.failure(17,str(error))
        return 0

    def write_message(self, channel, protocol, flags, data, timeout_ms):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle')
        if protocol != self.protocol:
            return self.failure(21,'Message protocol differs from channel')
        if timeout_ms==0:return self.queue_message(protocol,flags,data)
        if not 1 <= timeout_ms <= 25000:
            return self.failure(1,'Timeouts above 25 seconds are not bridged yet')
        if self.protocol in (3,4):
            if flags:return self.failure(6,'Unsupported K-line transmit flags')
            if not 1<=len(data)<=4096:return self.failure(10,'Invalid K-line message length')
            wire=bytes(data)
            # Original WriteMsgs block 7ab62aa0..7ab62ae5 uses channel flags.
            if not self.connect_flags&0x200:wire+=bytes([sum(wire)&255])
            try:self.session.write_kline_ordered(wire,timeout=timeout_ms/1000,
                **({'timing':self.timing_config[12]//2} if 12 in self.timing_config else {}))
            except TimeoutError:
                return self.failure(9,'K-line transmit timed out; do not retry an uncertain transmission')
            return 0
        if self.protocol==6:
            size=5 if flags&0x80 else 4
            if flags&~0x1c0:return self.failure(6,'Unsupported ISO-TP transmit flags')
            if not size+1<=len(data)<=size+4095:return self.failure(10,'Invalid ISO-TP message size')
            routes=[item for item in self.isotp_filters.values() if item['flow']==data[:size] and item['flags']==flags]
            if len(routes)!=1:return self.failure(23,'No unique configured transmit flow route')
            try:self.session.write_isotp_ordered(routes[0]['slot'],data[size:],timeout=timeout_ms/1000)
            except TimeoutError:return self.failure(9,'ISO-TP transmit timed out; delivery may be uncertain')
            return 0
        if self.protocol != 5:
            return self.failure(1,'Protocol writes are not bridged yet')
        if flags & ~0x100:
            return self.failure(6,'Unsupported CAN transmit flags')
        if not 4 <= len(data) <= 12:
            return self.failure(10,'CAN message requires a four-byte ID and up to eight data bytes')
        extended=bool(flags&0x100)
        identifier=int.from_bytes(data[:4],'big')
        if identifier > (0x1fffffff if extended else 0x7ff):
            return self.failure(10,'CAN arbitration ID exceeds selected width')
        if not 1 <= timeout_ms <= 25000:
            return self.failure(1,'Queued writes and timeouts above 25 seconds are not bridged yet')
        try:
            self.session.write_can_ordered(identifier,data[4:],extended=extended,timeout=timeout_ms/1000)
        except TimeoutError:
            return self.failure(9,'CAN transmit timed out; do not retry an uncertain transmission')
        return 0

    def read_can(self, channel, timeout_ms):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),None
        if self.protocol != 5:
            return self.failure(1,'Only raw CAN reads are bridged yet'),None
        if not 0 <= timeout_ms <= 25000:
            return self.failure(1,'Read timeouts above 25 seconds are not bridged yet'),None
        try:
            frame=self.session.read_can_message(timeout_ms/1000)
        except BufferError as error:return self.failure(18,str(error)),None
        except TimeoutError:
            return self.failure(16 if timeout_ms==0 else 9,'No matching CAN message before deadline'),None
        return 0,frame

    def read_isotp(self, channel, timeout_ms):
        if channel!=self.channel or self.channel is None:return self.failure(2,'Invalid channel handle'),None
        if self.protocol!=6:return self.failure(21,'Channel is not ISO15765'),None
        if not 0<=timeout_ms<=25000:return self.failure(1,'Unsupported read timeout'),None
        try:message=self.session.read_message(timeout_ms/1000)
        except BufferError as error:return self.failure(18,str(error)),None
        except TimeoutError:return self.failure(16 if timeout_ms==0 else 9,'No ISO-TP message before deadline'),None
        return 0,message

    def received_fields(self, frame):
        from gd101_isotp import ISOTPTransmitDone,ISOTPStartOfMessage
        if self.protocol==6 and isinstance(frame,(ISOTPTransmitDone,ISOTPStartOfMessage)):return frame.fields()
        if self.protocol in (3,4):
            from gd101_kline import KLineTransmitEcho
            if isinstance(frame,KLineTransmitEcho):return frame.data,1,frame.timestamp
            return frame.data,0,frame.first_timestamp
        if self.protocol==6 and not 1<=len(frame.data)<=4095:
            raise ValueError('Invalid ISO-TP receive payload length')
        from gd101_can import CANTransmitEcho
        echo_status=1 if isinstance(frame,CANTransmitEcho) else 0
        data=frame.arbitration_id.to_bytes(4,'big')
        addressed=self.protocol==6 and frame.address is not None
        if addressed:data+=bytes([frame.address])
        return data+frame.data,echo_status|(0x100 if frame.extended else 0)|(0x80 if addressed else 0),frame.last_timestamp if self.protocol==6 else frame.timestamp

    def read_kline(self, channel, timeout_ms):
        if channel!=self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),None
        if self.protocol not in (3,4):return self.failure(21,'Channel is not K-line'),None
        if not 0<=timeout_ms<=25000:return self.failure(1,'Unsupported read timeout'),None
        try:message=self.session.read_message(timeout_ms/1000)
        except BufferError as error:return self.failure(18,str(error)),None
        except TimeoutError:
            return self.failure(16 if timeout_ms==0 else 9,'No matching K-line message before deadline'),None
        return 0,message

    def five_baud_init(self, channel, data):
        if channel!=self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),None
        if self.protocol not in (3,4):
            return self.failure(1,'FIVE_BAUD_INIT requires a K-line channel'),None
        if len(data)!=1:return self.failure(10,'FIVE_BAUD_INIT requires one address byte'),None
        try:
            result=self.session.initialize_kline('five_baud',bytes(data),protocol=self.protocol,
                no_checksum=bool(self.connect_flags&0x200),**self.initialization_timings())
        except TimeoutError:
            return self.failure(9,'FIVE_BAUD_INIT timed out; initialization remains uncertain'),None
        if not isinstance(result,bytes) or len(result)!=2:
            return self.failure(7,'FIVE_BAUD_INIT returned malformed keywords'),None
        if self.protocol==4:
            rate=self.session.kline_baudrate
            if not isinstance(rate,int) or not 0<=rate<=0xffffffff:
                return self.failure(7,'FIVE_BAUD_INIT returned no valid detected rate'),None
            self.baudrate=rate
        return 0,result

    def fast_init(self, channel, protocol, flags, data, want_response):
        if channel != self.channel or self.channel is None:
            return self.failure(2,'Invalid channel handle'),None
        if self.protocol!=4:
            return self.failure(1,'FAST_INIT is currently exported for ISO14230 only'),None
        if protocol not in (0,4):
            return self.failure(21,'FAST_INIT input protocol differs from channel'),None
        if flags:
            return self.failure(6,'Unsupported FAST_INIT transmit flags'),None
        # Native builder 7ab60670 checks channel +0x1ec, not PASSTHRU_MSG.TxFlags.
        checksum_flags=self.connect_flags&0x200
        if len(data) > (228 if checksum_flags else 227) or (protocol==0 and data):
            return self.failure(10,'FAST_INIT message exceeds native short-frame capacity'),None
        try:
            result=self.session.initialize_kline('fast',data,flags=checksum_flags,
                capture_response=bool(want_response),response_timeout=(self.timing_config.get(10,110)/2+self.timing_config.get(19,300))/1000 if want_response else None,
                no_checksum=bool(self.connect_flags&0x200),**self.initialization_timings())
        except TimeoutError:
            return self.failure(9,'FAST_INIT timed out; ECU response was not established'),None
        return 0,result


def install(ffi, provider=None):
    provider=provider or LifecycleProvider()
    def export(name):
        def decorate(function):
            @wraps(function)
            def wrapped(*args):
                with provider.lock:
                    try:return function(*args)
                    except Exception as error:
                        from gd101_devices import device_error_status
                        return provider.failure(device_error_status(error),f'{type(error).__name__}: {error}')
            ffi.def_extern(name=name,error=7)(wrapped)
            return wrapped
        return decorate

    @export('PassThruOpen')
    def open_device(name, output):
        if output==ffi.NULL:return provider.failure(4,'Device ID output is null')
        output[0]=0
        if name!=ffi.NULL and ffi.string(ffi.cast('char *',name),9) not in (b'J2534-1:',b'J2534-2:'):
            return provider.failure(1,'Named-device suffix or connection pattern unsupported')
        status,handle=provider.open();output[0]=handle
        return status

    @export('PassThruClose')
    def close_device(device):return provider.close(device)

    @export('PassThruSetProgrammingVoltage')
    def set_programming_voltage(device,pin,voltage):
        return provider.set_programming_voltage(device,pin,voltage)

    @export('PassThruReadVersion')
    def read_version(device,firmware,driver,api):
        if any(p==ffi.NULL for p in (firmware,driver,api)):
            return provider.failure(4,'Null version output')
        status,values=provider.read_version(device)
        if not status:
            for target,value in zip((firmware,driver,api),values):
                ffi.memmove(target,value.encode('ascii')[:79].ljust(80,b'\0'),80)
        return status

    @export('PassThruConnect')
    def connect(device,protocol,flags,baud,output):
        if output==ffi.NULL:return provider.failure(4,'Channel ID output is null')
        output[0]=0
        status,handle=provider.connect(device,protocol,flags,baud);output[0]=handle
        return status

    @export('PassThruDisconnect')
    def disconnect(channel):return provider.disconnect(channel)

    @export('PassThruWriteMsgs')
    def write_messages(channel,messages,count,timeout_ms):
        if count==ffi.NULL:return provider.failure(4,'Message count is null')
        requested=count[0];count[0]=0
        if messages==ffi.NULL:return provider.failure(4,'Messages are null')
        if not requested:return provider.failure(10,'Empty message array')
        if not 0<=timeout_ms<=25000:return provider.failure(1,'Timeout above 25 seconds unsupported')
        deadline=time.monotonic()+timeout_ms/1000
        for index in range(requested):
            msg=messages[index]
            if not 1<=msg.DataSize<=4128:return provider.failure(10,'Message exceeds ABI buffer bounds')
            remaining=math.ceil((deadline-time.monotonic())*1000) if timeout_ms else 0
            if timeout_ms and remaining<=0:return provider.failure(9,'Message batch timeout')
            status=provider.write_message(channel,msg.ProtocolID,msg.TxFlags,
                bytes(ffi.buffer(msg.Data,msg.DataSize)),remaining)
            if status:return status
            count[0]=index+1
        return 0

    @export('PassThruReadMsgs')
    def read_messages(channel,messages,count,timeout_ms):
        if count==ffi.NULL:return provider.failure(4,'Message count is null')
        requested=count[0];count[0]=0
        if messages==ffi.NULL:return provider.failure(4,'Messages are null')
        if not requested:return provider.failure(10,'Empty receive array')
        if timeout_ms>25000:return provider.failure(1,'Read timeout above 25 seconds unsupported')
        deadline=time.monotonic()+timeout_ms/1000
        for index in range(requested):
            remaining=max(0,math.ceil((deadline-time.monotonic())*1000))
            if timeout_ms and remaining==0:return provider.failure(9,'Receive batch timeout')
            kline=provider.protocol in (3,4)
            read=provider.read_kline if kline else (provider.read_isotp if provider.protocol==6 else provider.read_can)
            status,frame=read(channel,remaining if timeout_ms else 0)
            if status:
                if not timeout_ms and status==16 and count[0]:return 0
                return status
            data,rx_status,timestamp=provider.received_fields(frame)
            if len(data)>4128:return provider.failure(18,'Receive message exceeds ABI capacity')
            msg=messages[index]
            msg.ProtocolID=provider.protocol
            msg.RxStatus=rx_status;msg.TxFlags=0
            msg.Timestamp=timestamp
            msg.DataSize=len(data);msg.ExtraDataIndex=len(data)
            ffi.memmove(msg.Data,data,len(data));count[0]=index+1
        return 0

    @export('PassThruStartPeriodicMsg')
    def start_periodic(channel,message,handle,interval):
        if handle==ffi.NULL:return provider.failure(4,'Null periodic handle output')
        handle[0]=0
        if message==ffi.NULL:return provider.failure(4,'Null periodic message')
        if message.DataSize>4128:return provider.failure(10,'Periodic message exceeds ABI capacity')
        status,value=provider.start_periodic(channel,message.ProtocolID,message.TxFlags,
                    bytes(ffi.buffer(message.Data,message.DataSize)),interval)
        if not status:handle[0]=value
        return status

    @export('PassThruStopPeriodicMsg')
    def stop_periodic(channel,handle):return provider.stop_periodic(channel,handle)

    @export('PassThruStartMsgFilter')
    def start_filter(channel,kind,mask,pattern,flow,output):
        if output==ffi.NULL:return provider.failure(4,'Filter output is null')
        output[0]=0
        if mask==ffi.NULL or pattern==ffi.NULL:return provider.failure(4,'Mask or pattern is null')
        if kind==3:
            if flow==ffi.NULL:return provider.failure(4,'Flow-control message is null')
            if not mask.ProtocolID==pattern.ProtocolID==flow.ProtocolID:return provider.failure(21,'Filter protocols differ')
            if not mask.TxFlags==pattern.TxFlags==flow.TxFlags:return provider.failure(6,'Filter flags differ')
            if any(m.DataSize>5 for m in (mask,pattern,flow)):return provider.failure(10,'Flow message too large')
            status,handle=provider.start_flow_filter(channel,mask.ProtocolID,mask.TxFlags,
                *[bytes(ffi.buffer(m.Data,m.DataSize)) for m in (mask,pattern,flow)])
            output[0]=handle
            return status
        if flow!=ffi.NULL:return provider.failure(10,'Flow message supplied for a pass/block filter')
        if mask.ProtocolID!=pattern.ProtocolID:return provider.failure(21,'Filter protocols differ')
        if mask.DataSize>12 or mask.DataSize!=pattern.DataSize:return provider.failure(10,'Invalid filter length')
        if mask.TxFlags!=pattern.TxFlags:return provider.failure(6,'Filter flags differ')
        status,handle=provider.start_filter(channel,kind,bytes(ffi.buffer(mask.Data,mask.DataSize)),
            bytes(ffi.buffer(pattern.Data,pattern.DataSize)),mask.TxFlags,protocol=mask.ProtocolID)
        output[0]=handle
        return status

    @export('PassThruStopMsgFilter')
    def stop_filter(channel,handle):return provider.stop_filter(channel,handle)

    @export('PassThruIoctl')
    def ioctl(identifier,code,input,output):
        if code in (1,2):
            if input==ffi.NULL:return provider.failure(4,'Null configuration list')
            config=ffi.cast('GD101_CONFIG_LIST *',input)
            if config.NumOfParams>64:return provider.failure(10,'Configuration list exceeds 64 entries')
            if config.NumOfParams and config.ConfigPtr==ffi.NULL:return provider.failure(4,'Null configuration entries')
            entries=[config.ConfigPtr[i] for i in range(config.NumOfParams)]
            if code==2:return provider.configure_timing(identifier,pairs=[(e.Parameter,e.Value) for e in entries])[0]
            status,values=provider.configure_timing(identifier,parameters=[e.Parameter for e in entries])
            if not status:
                for entry,value in zip(entries,values):entry.Value=value
            return status
        if code==3:
            if output==ffi.NULL:return provider.failure(4,'Null battery-voltage output')
            status,value=provider.read_battery_voltage(identifier)
            if not status:ffi.cast('uint32_t *',output)[0]=value
            return status
        if code==7:return provider.clear_tx(identifier)
        if code==9:return provider.clear_periodic(identifier)
        if code==8:return provider.clear_rx(identifier)
        if code==10:return provider.clear_filters(identifier)
        if code==4:
            if input==ffi.NULL or output==ffi.NULL:return provider.failure(4,'Null FIVE_BAUD_INIT array')
            source=ffi.cast('const GD101_BYTE_ARRAY *',input)
            target=ffi.cast('GD101_BYTE_ARRAY *',output)
            if source.BytePtr==ffi.NULL or target.BytePtr==ffi.NULL:
                return provider.failure(4,'Null FIVE_BAUD_INIT byte buffer')
            if source.NumOfBytes!=1:return provider.failure(10,'FIVE_BAUD_INIT requires one address byte')
            if target.NumOfBytes<2:return provider.failure(18,'FIVE_BAUD_INIT output needs two bytes')
            status,result=provider.five_baud_init(identifier,bytes(ffi.buffer(source.BytePtr,1)))
            if not status:
                ffi.memmove(target.BytePtr,result,2);target.NumOfBytes=2
            return status
        if code==5:
            protocol=flags=0;data=b''
            if input!=ffi.NULL:
                message=ffi.cast('const GD101_MESSAGE *',input)
                if message.DataSize>228:return provider.failure(10,'FAST_INIT request too large')
                protocol=message.ProtocolID;flags=message.TxFlags
                data=bytes(ffi.buffer(message.Data,message.DataSize))
            status,result=provider.fast_init(identifier,protocol,flags,data,output!=ffi.NULL)
            if not status and output!=ffi.NULL:
                if len(result.data)>4128:return provider.failure(18,'FAST_INIT response exceeds output capacity')
                message=ffi.cast('GD101_MESSAGE *',output)
                message.ProtocolID=4;message.RxStatus=message.TxFlags=0
                message.Timestamp=result.first_timestamp
                message.DataSize=message.ExtraDataIndex=len(result.data)
                ffi.memmove(message.Data,result.data,len(result.data))
            return status
        return provider.failure(1,'IOCTL not implemented yet')

    @export('PassThruGetLastError')
    def last_error(output):
        if output==ffi.NULL:return 4
        value=provider.last_error.encode('ascii','replace')[:79]+b'\0'
        ffi.memmove(output,value,len(value))
        return 0
    return provider
