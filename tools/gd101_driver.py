"""Linux GD101 USB session with native Python authentication.

Implements native framing, version queries and K-line channel setup/teardown.
Not a J2534 ABI replacement; vehicle initialization/message APIs remain pending.
"""
from collections import deque
from gd101_devices import serial_transport_call, open_serial_port
from gd101_transactions import serialized_operation, operation_lock, session_lock
from gd101_periodic import stop_periodic_before
import json
import os
from pathlib import Path
import selectors
import struct
import subprocess
import time

from gd101_native import (prepare_serial, encode_short, encode_extended, decode_short, decode_frame,
                          MAX_EXTENDED_PAYLOAD, build_kline_open_payload)
from gd101_crypto import sign_host_identity, shared_secret, verify_discovery_identity, derive_framing_key

from gd101_kline import KLINE_EVENT_IDS, packet_id

AUTH_FILE = Path(__file__).resolve().parents[1] / '.local' / 'gd101-auth.json'


def authenticate_native(port, record, auth_file=AUTH_FILE, *, expected_serial='MT000418'):
    material = json.loads(Path(auth_file).read_text())
    identifier = material['adapter_serial'].encode('ascii')
    if identifier != expected_serial.encode('ascii'):
        raise GD101Error('Authentication material does not match configured USB adapter')
    private = bytes.fromhex(material['host_private_key_le'])
    signature = sign_host_identity(private, identifier)
    secret = shared_secret(private, bytes.fromhex(material['peer_public_key_le']))
    frame = encode_short(b'\0\0'+signature)
    if serial_transport_call(port.write,frame) != len(frame):
        raise GD101Error('Short discovery write')
    serial_transport_call(port.flush)
    reply = read_frame(port)
    payload = decode_short(reply)
    record['authentication_request'] = frame.hex()
    record['authentication_reply'] = reply.hex()
    if not verify_discovery_identity(payload, identifier,
            bytes.fromhex(material['verification_key_a_le']),
            bytes.fromhex(material['verification_key_b_le'])):
        raise GD101Error('Adapter identity or signature verification failed')
    tx_key = derive_framing_key(signature, secret, payload)
    record['authentication_backend'] = 'native-python'
    record['authentication_verified'] = True
    return tx_key, tx_key[::-1]


class GD101Error(RuntimeError):
    pass


class GD101Timeout(GD101Error, TimeoutError):
    """A native operation exceeded its deadline; transmissions may be uncertain."""


def read_frame(port, timeout=4):
    deadline = time.monotonic() + timeout
    data = bytearray()
    needed = 1
    while len(data) < needed:
        if time.monotonic() >= deadline:
            raise GD101Timeout(f'Timed out with {len(data)}/{needed} frame bytes')
        data.extend(serial_transport_call(port.read,needed-len(data)))
        if len(data) == 1:
            units = data[0] & 0x7f
            if units == 127:
                needed = 3
            elif 1 <= units <= 120:
                needed = units*2+2
            else:
                raise GD101Error('Invalid frame header')
        if len(data) == 3 and data[0] & 0x7f == 127:
            size = int.from_bytes(data[1:3], 'little')
            if not 1 <= size <= MAX_EXTENDED_PAYLOAD:
                raise GD101Error('Invalid extended frame length')
            needed = size+4
    return bytes(data)


def authenticate(*args, **kwargs):
    raise GD101Error('The legacy vendor-DLL research backend is not distributed; use native')


class GD101:
    """Exclusive adapter session. Use as a context manager; logs omit session keys."""
    def __init__(self, capture_path, *, backend='native', port_path=None, auth_file=AUTH_FILE):
        if backend not in ('native', 'wine'):
            raise ValueError('Unknown authentication backend')
        self.backend = backend
        self.capture_path = Path(capture_path)
        from gd101_devices import select_gd101_port
        self.auth_file=Path(auth_file)
        self.adapter_serial=(json.loads(self.auth_file.read_text())['adapter_serial']
                             if backend=='native' else 'MT000418')
        self.port_path=select_gd101_port(self.adapter_serial,
                                       requested=port_path if port_path is not None else os.environ.get('GD101_PORT'))
        self.port = prepare_serial(self.port_path)
        self.tx_key = self.rx_key = None
        self.kline_open = False
        self.can_open = False
        from gd101_can import CANFilters
        self.can_filters = CANFilters()
        self.receiver = None
        self.tx_counter = 0
        self.tx_uncertain = False
        self.init_uncertain = False
        self.flow_uncertain = False
        self.pin_uncertain = False
        self.flow_entries = {}
        self.flow_config = None
        self.events = deque()
        self.record = {'port':self.port_path, 'baud':2534, 'exchanges':[]}
        self.capture = self.errors = None

    @serialized_operation
    def __enter__(self):
        self.capture = self.capture_path.open('x')
        try:
            open_serial_port(self.port)
            self.record['initial_rx'] = serial_transport_call(self.port.read,4096).hex()
            if self.backend == 'native':
                self.tx_key,self.rx_key = authenticate_native(self.port,self.record,self.auth_file,expected_serial=self.adapter_serial)
            else:
                self.errors = self.capture_path.with_suffix('.wine.log').open('x')
                self.tx_key,self.rx_key = authenticate(self.port,self.record,self.errors)
            return self
        except Exception as error:
            self.record['error'] = str(error)
            self.close()
            raise

    @serialized_operation
    def send_packet(self, payload, *, extended=False):
        """Send a native adapter payload, not a J2534 vehicle message.

        Extended framing must be chosen from command semantics, not size.
        No retries: repeating a timed-out write could duplicate a command.
        """
        if self.tx_key is None:
            raise GD101Error('Session is not authenticated')
        if self.receiver is not None and self.receiver.stop_event.is_set():
            raise GD101Error('Background reader stopped; close the session') from self.receiver.error
        frame = (encode_extended if extended else encode_short)(payload,self.tx_key)
        if serial_transport_call(self.port.write,frame) != len(frame):
            raise GD101Error('Short serial write')
        serial_transport_call(self.port.flush)
        self.record.setdefault('packets', []).append({'direction':'tx','wire':frame.hex()})
        return frame

    @serialized_operation
    def start_receiver(self, *, assembler=None, isotp=None, capture_kline=False, filter_can=False):
        """Start sole USB reader; optional K-line assembler consumes byte events.

        Call before sending commands and from the session's single owner thread.
        Command transactions use shared ownership; the reader owns USB input.
        """
        from gd101_receiver import PacketReceiver
        if self.rx_key is None or self.receiver is not None:
            raise GD101Error('Receiver requires an authenticated session and no existing reader')
        def capture(packet):
            self.record.setdefault('packets', []).append({'direction':'rx',
                'wire':packet.wire.hex(),'payload':packet.payload.hex(),
                'arrival_monotonic':packet.arrival})
        candidate = PacketReceiver(self.port,self.rx_key,assembler=assembler,isotp=isotp,
                                      on_packet=capture,capture_kline=capture_kline,isotp_indications=isotp is not None,
                                      response_window=getattr(self,'_kline_response_window',None) if assembler is not None else None,
                                      can_filters=self.can_filters if filter_can else None)
        try:candidate.start()
        except Exception:
            try:candidate.stop()
            except Exception as cleanup_error:
                # Retain ownership if a reader could still be running, so later
                # shutdown can retry cleanup instead of losing the handle.
                self.receiver=candidate
                self.record['receiver_start_cleanup_error']=str(cleanup_error)
            raise
        self.receiver=candidate

    @serialized_operation
    def start_isotp_receiver(self, *, timeout=1):
        """Start message assembly from successfully programmed native flow routes.

        Timeout is host seconds, not a verified conversion of native timer ticks.
        Configure initial routes before starting; later updates gate message reception.
        """
        from gd101_isotp import FlowRoute,ISOTPReassembler
        if not self.can_open or self.flow_uncertain or not self.flow_entries:
            raise GD101Error('ISO-TP reception requires acknowledged nonempty flow routes')
        if self.receiver is not None or self.events:
            raise GD101Error('Start ISO-TP reception before another reader or queued events')
        routes={slot:FlowRoute(int.from_bytes(raw[12:16],'little'),
                              raw[16] if raw[9]&8 else None)
                for slot,raw in self.flow_entries.items()}
        self.start_receiver(isotp=ISOTPReassembler(routes,timeout=timeout))

    @serialized_operation
    def clear_rx_buffer(self):
        """Drain completed receive events without resetting USB/command state."""
        if self.can_open:protocol,receive_id=5,0x301
        elif self.kline_open:protocol,receive_id=4,0x303
        else:raise GD101Error('Open a channel before clearing receive data')
        retained=[p for p in self.events if packet_id(p)!=receive_id]
        if self.receiver is not None:self.receiver.clear_receive(protocol)
        self.events.clear();self.events.extend(retained)

    def read_message(self, timeout=4):
        if self.receiver is None:
            raise GD101Error('Start a message receiver first')
        return self.receiver.read_message(timeout)

    def read_kline_initialization(self, timeout):
        if self.receiver is None:raise GD101Error('Start a K-line receiver first')
        return self.receiver.read_kline_initialization(timeout)

    @serialized_operation
    def receive_packet(self, timeout=4):
        """Receive one CRC-checked native payload without guessing its meaning."""
        if self.rx_key is None:
            raise GD101Error('Session is not authenticated')
        if self.receiver is not None:
            return self.receiver.read_packet(timeout).payload
        response = read_frame(self.port,timeout)
        decoded = decode_frame(response,self.rx_key)
        self.record.setdefault('packets', []).append(
            {'direction':'rx','wire':response.hex(),'payload':decoded.hex()})
        return decoded

    @serialized_operation
    def receive_event(self, timeout=4):
        """Return an interleaved K-line event, preserving wire arrival order."""
        if self.events:
            return self.events.popleft()
        payload = self.receive_packet(timeout)
        if packet_id(payload) not in KLINE_EVENT_IDS:
            raise GD101Error('Expected K-line event, received: '+payload.hex())
        return payload

    @serialized_operation
    def exchange(self, payload, expected_prefix, *, timeout=4):
        frame = self.send_packet(payload)
        deadline = time.monotonic()+timeout
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise GD101Timeout('Timed out waiting for command acknowledgment')
            decoded = self.receive_packet(remaining)
            if packet_id(decoded) in KLINE_EVENT_IDS or (getattr(self,"can_open",False) and packet_id(decoded) in (0x300,0x301)):
                if len(self.events) >= 4096:
                    raise GD101Error('K-line event queue overflow')
                self.events.append(decoded)
                continue
            self.record['exchanges'].append({'request':frame.hex(),
                                             'decoded_response':decoded.hex()})
            if not decoded.startswith(expected_prefix):
                raise GD101Error('Unexpected response: '+decoded.hex())
            return decoded

    @serialized_operation
    def read_version(self):
        response = self.exchange(b'\0\x03',b'\x40\x03\0')
        if len(response) <= 28:
            raise GD101Error('Truncated version response')
        return response

    @serialized_operation
    def read_battery_voltage(self):
        """Return adapter-measured supply millivolts (native command 0x14)."""
        response = self.exchange(b'\x02\x00\x10\x00', b'\x42\x00\x00\x10', timeout=2)
        if len(response) < 8:
            raise GD101Error('Truncated battery-voltage response')
        return int.from_bytes(response[4:8], 'little')

    @serialized_operation
    def disable_programming_voltage(self, pin):
        """Disable the selected programming output, requiring its native ACK."""
        return self.set_programming_voltage(pin,0xffffffff)

    @serialized_operation
    def set_programming_voltage(self, pin, voltage):
        from gd101_pins import build_pin_control,VOLTAGE_OFF
        payload=build_pin_control(pin,voltage)
        if getattr(self,'pin_uncertain',False):
            raise GD101Error('Previous pin-control outcome uncertain; close the session')
        active=getattr(self,'active_programming_pins',set())
        previous=set(active)
        self.active_programming_pins=active
        # Track possibly enabled outputs before I/O, including a lost ACK.
        if voltage!=VOLTAGE_OFF:active.add(pin)
        self.pin_uncertain = True
        response=self.exchange(payload+b'\0',b'\x41\x05',timeout=2)
        if len(response)<3 or response[:2]!=b'\x41\x05':
            raise GD101Error('Truncated pin-control acknowledgment')
        self.pin_uncertain = False
        if response[2]:
            self.active_programming_pins=previous
            raise GD101Error('Adapter rejected programming-pin control')
        if voltage==VOLTAGE_OFF:active.discard(pin)

    @serialized_operation
    def connect_kline(self, baud=10400):
        from gd101_kline import KLINE_BAUD_RATES
        if baud not in KLINE_BAUD_RATES:raise ValueError('Unsupported K-line baud rate')
        if self.kline_open or self.can_open:
            raise GD101Error('A channel is already open')
        # Explicit zero padding was accepted in the laptop-only hardware test.
        response = self.exchange(build_kline_open_payload(baud)+b'\0',b'\x41\x01\0')
        from gd101_kline import validate_kline_config_ack
        try:validate_kline_config_ack(response)
        except ValueError as error:raise GD101Error(str(error)) from error
        self.kline_open = True
        self.kline_baudrate=baud
        self.kline_parity=0
        self.kline_loopback=0
        self._periodic_kline_timing=None
        from gd101_response_window import KLineResponseWindow
        self._kline_response_window=KLineResponseWindow()

    @serialized_operation
    def connect_can(self, bitrate=500000):
        """Open raw CAN with original defaults on selector zero, pins 6/14.

        No listen-only claim: this configures the CAN controller normally.
        Simultaneous K-line/CAN channels are not supported by this wrapper yet.
        """
        from gd101_can import build_can_open_payload,default_can_descriptor
        if self.can_open or self.kline_open:
            raise GD101Error('A channel is already open')
        payload=build_can_open_payload(default_can_descriptor(bitrate))
        response=self.exchange(payload,b'\x41\0\0')
        if len(response)<4 or response[3]:
            raise GD101Error('CAN open rejected: '+response.hex())
        self.can_open=True
        self.can_bitrate=bitrate
        self.can_loopback=False
        self.can_timing=(80,15)

    @serialized_operation
    def configure_can_bitrate(self, bitrate, *, loopback=None, timing=None):
        """Original protocol-5 SET_CONFIG reissues op4 with the new descriptor."""
        from gd101_can import build_can_open_payload,default_can_descriptor
        descriptor=bytearray(default_can_descriptor(bitrate))
        chosen=getattr(self,'can_timing',(80,15)) if timing is None else tuple(timing)
        if len(chosen)!=2 or any(not isinstance(v,int) or not 0<=v<=100 for v in chosen):
            raise ValueError('Invalid CAN sample point or synchronization jump width')
        descriptor[8:10]=bytes(chosen)
        payload=build_can_open_payload(descriptor)
        if loopback is not None and not isinstance(loopback,bool):raise ValueError('Invalid loopback')
        if not self.can_open or self.tx_uncertain or getattr(self,'flow_uncertain',False):
            raise GD101Error('Rate change requires CAN with no uncertain operation')
        reader=getattr(self,'receiver',None)
        if reader is not None and reader.isotp is not None:
            raise GD101Error('ISO-TP rate changes require separate flow-table handling')
        if bitrate!=getattr(self,'can_bitrate',None):
            # A lost reply can leave the hardware at either rate. Inhibit sends
            # and configuration retries until the channel/session is recovered.
            self.tx_uncertain=True
            reply=self.exchange(payload,b'\x41\0\0',timeout=2)
            if len(reply)<4 or reply[3]:raise GD101Error('CAN rate change rejected: '+reply.hex())
            self.can_bitrate=bitrate
            self.tx_uncertain=False
        if loopback is not None:self.can_loopback=loopback
        self.can_timing=chosen

    @serialized_operation
    def configure_can_loopback(self, enabled):
        if not isinstance(enabled,bool):raise ValueError('Loopback must be boolean')
        if not self.can_open or self.tx_uncertain or getattr(self,'flow_uncertain',False):
            raise GD101Error('Loopback setup requires CAN with no uncertain operation')
        self.can_loopback=enabled

    @serialized_operation
    def configure_isotp_loopback(self, enabled, *, timing=None):
        """Update the original host-only a7f4 option without touching routes."""
        if not isinstance(enabled,bool):raise ValueError('Loopback must be boolean')
        if not self.can_open or self.tx_uncertain or self.flow_uncertain:
            raise GD101Error('Loopback setup requires CAN with no uncertain operation')
        chosen=getattr(self,'can_timing',(80,15)) if timing is None else tuple(timing)
        if len(chosen)!=2 or any(not isinstance(v,int) or not 0<=v<=100 for v in chosen):
            raise ValueError('Invalid ISO-TP CAN timing')
        config=bytearray(self.flow_config)
        config[4]=int(enabled)
        self.flow_config=bytes(config)
        self.can_timing=chosen

    @serialized_operation
    def configure_isotp_routes(self, entries, *, standard=(), extended=(), config=None, bitrate=None, timing=None):
        """Experimental native flow-table/acceptance-table programming.

        Acknowledges adapter configuration only, not an ISO-TP ECU connection.
        Routes may make the controller send flow-control replies to bus traffic.
        """
        from gd101_isotp import (build_flow_table_packet,build_acceptance_table,
                                  default_flow_config,validate_flow_ack)
        if not self.can_open or self.tx_uncertain or self.flow_uncertain:
            raise GD101Error('Flow setup requires CAN with no uncertain operation')
        reader=getattr(self,"receiver",None)
        candidate={slot:bytes(entry) for slot,entry in entries.items()}
        chosen_config=default_flow_config() if config is None else bytes(config)
        first=build_flow_table_packet(candidate,chosen_config)
        second=build_acceptance_table(standard,extended)
        chosen_timing=getattr(self,'can_timing',(80,15)) if timing is None else tuple(timing)
        if len(chosen_timing)!=2 or any(not isinstance(v,int) or not 0<=v<=100 for v in chosen_timing):
            raise ValueError('Invalid ISO-TP CAN timing')
        rate_payload=None
        if bitrate is not None:
            from gd101_can import build_can_open_payload,default_can_descriptor
            descriptor=bytearray(default_can_descriptor(bitrate))
            descriptor[8:10]=bytes(chosen_timing)
            rate_payload=build_can_open_payload(descriptor)
        commands=[(first,True,0x0b),(second,False,4)]
        if rate_payload is not None:commands.insert(0,(rate_payload,False,0))
        replacement=None
        if reader is not None and reader.isotp is not None:
            from gd101_isotp import FlowRoute,ISOTPReassembler
            routes={slot:FlowRoute(int.from_bytes(raw[12:16],'little'),
                                  raw[16] if raw[9]&8 else None)
                    for slot,raw in candidate.items()}
            replacement=ISOTPReassembler(routes,timeout=reader.isotp.timeout)
            reader.begin_isotp_update()
        self.flow_uncertain=True
        for payload,large,command in commands:
            if not large:
                payload+=bytes(len(payload)%2)
            self.send_packet(payload,extended=large)
            deadline=time.monotonic()+2
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise GD101Timeout('ISO-TP table acknowledgment timeout')
                reply=self.receive_packet(remaining)
                if packet_id(reply) in (0x300,0x301,0x30a):
                    if len(self.events)>=4096:
                        raise GD101Error('ISO-TP event queue overflow')
                    self.events.append(reply)
                    continue
                try:
                    if command==0:
                        if len(reply)<4 or reply[:4]!=b'\x41\0\0\0':
                            raise ValueError('ISO-TP rate configuration rejected or malformed')
                    else:validate_flow_ack(reply,command)
                except ValueError as error:
                    raise GD101Error(str(error)) from error
                break
        self.flow_entries=candidate
        if bitrate is not None:self.can_bitrate=bitrate
        self.can_timing=chosen_timing
        self.flow_config=chosen_config
        if replacement is not None:reader.finish_isotp_update(replacement)
        self.flow_uncertain=False

    @serialized_operation
    def write_isotp_message(self, slot, data, *, timeout=4):
        """Experimental native ISO-TP send via an acknowledged flow-route slot.

        Completion means adapter acknowledgment; no live ECU validation yet.
        """
        import math
        from gd101_isotp import build_isotp_transmit
        if not math.isfinite(timeout) or timeout<=0:
            raise ValueError('Transmit timeout must be finite and positive')
        if not self.can_open or self.tx_uncertain or self.flow_uncertain:
            raise GD101Error('ISO-TP send requires CAN with no uncertain operation')
        if slot not in self.flow_entries:
            raise GD101Error('ISO-TP flow route is not configured')
        data=bytes(data)
        payload,large=build_isotp_transmit(self.flow_entries[slot],slot,data,counter=self.tx_counter,
                                         padding_byte=self.flow_config[26])
        tag=payload[2] if large else payload[3]
        expected=0x30a if large else 0x300
        self.tx_counter=(self.tx_counter+1)&0xffffffff
        self.tx_uncertain=True
        self.send_packet(payload if large else payload+bytes(len(payload)%2),extended=large)
        deadline=time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise GD101Timeout('ISO-TP completion timeout; delivery is uncertain')
            reply=self.receive_packet(remaining)
            code=packet_id(reply)
            if code==expected:
                if len(reply)<9:
                    raise GD101Error('Truncated ISO-TP completion')
                if reply[2]==0 and reply[3]==tag:
                    self.tx_uncertain=False
                    if reply[4]:
                        raise GD101Error(f'ISO-TP transmit rejected: status {reply[4]}')
                    timestamp=int.from_bytes(reply[5:9],'little')
                    receiver=getattr(self,'receiver',None)
                    if receiver is not None and receiver.isotp is not None:
                        from gd101_isotp import ISOTPTransmitDone,ISOTPTransmitEcho
                        entry=self.flow_entries[slot]
                        receiver.publish_isotp_completion(ISOTPTransmitDone(
                            int.from_bytes(entry[4:8],'little'),bool(entry[9]&2),
                            entry[8] if entry[9]&8 else None,timestamp))
                        if self.flow_config[4]:
                            receiver.publish_isotp_completion(ISOTPTransmitEcho(
                                int.from_bytes(entry[4:8],'little'),bool(entry[9]&2),
                                entry[8] if entry[9]&8 else None,timestamp,data))
                    return timestamp
            if code not in (0x300,0x301,0x30a):
                raise GD101Error('Unexpected ISO-TP completion packet: '+reply.hex())
            if len(self.events)>=4096:
                raise GD101Error('ISO-TP event queue overflow')
            self.events.append(reply)

    @serialized_operation
    def write_can_frame(self, arbitration_id, data, *, extended=False, timeout=4):
        """Experimental single classic-CAN send with completion matching.

        Optional host loopback is published only after a matching successful ACK.
        Timeout leaves delivery uncertain and prevents another send this session.
        """
        import math
        from gd101_can import build_can_transmit,decode_can_transmit_ack
        if not math.isfinite(timeout) or timeout<=0:
            raise ValueError('Transmit timeout must be finite and positive')
        if not self.can_open or self.tx_uncertain or getattr(self,"flow_uncertain",False):
            raise GD101Error('CAN must be open with no uncertain transmit')
        data=bytes(data)
        payload=build_can_transmit(arbitration_id,data,extended=extended,counter=self.tx_counter)
        tag=payload[3]
        self.tx_counter=(self.tx_counter+1)&0xffffffff
        self.tx_uncertain=True
        self.send_packet(payload+bytes(len(payload)%2))
        deadline=time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise GD101Timeout('CAN completion timeout; delivery is uncertain')
            response=self.receive_packet(remaining)
            event_id=packet_id(response)
            if event_id==0x300:
                ack=decode_can_transmit_ack(response)
                if ack.selector==0 and ack.tag==tag:
                    self.tx_uncertain=False
                    if ack.status:
                        raise GD101Error(f'CAN transmit rejected: status {ack.status}')
                    receiver=getattr(self,'receiver',None)
                    if getattr(self,'can_loopback',False) and receiver is not None:
                        from gd101_can import CANTransmitEcho
                        receiver.publish_can_echo(CANTransmitEcho(arbitration_id,data,extended,ack.timestamp))
                    return ack
            if event_id not in (0x300,0x301):
                raise GD101Error('Unexpected packet during CAN transmit: '+response.hex())
            if len(self.events)>=4096:
                raise GD101Error('CAN event queue overflow')
            self.events.append(response)

    def write_can_ordered(self, arbitration_id, data, *, extended=False, timeout=4):
        """Blocking API write follows all previously accepted FIFO messages."""
        import math
        from gd101_can import build_can_transmit
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid write timeout')
        data=bytes(data);build_can_transmit(arbitration_id,data,extended=extended,counter=0)
        deadline=time.monotonic()+timeout
        gate=session_lock(self,'_periodic_lifecycle_lock')
        if not gate.acquire(timeout=timeout):raise TimeoutError('Write lifecycle deadline expired; nothing sent')
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Write deadline expired; nothing sent')
            writer=getattr(self,'_async_writer',None)
            if writer is not None:remaining=writer.drain(remaining)
            # The lifecycle gate prevents later acceptance while the earlier
            # worker drains. Do not hold operation ownership during the drain.
            return self.write_can_frame(arbitration_id,data,extended=extended,timeout=remaining)
        finally:gate.release()

    def write_isotp_ordered(self, slot, data, *, timeout=4):
        """Blocking ISO-TP follows accepted FIFO messages, preserving its route."""
        import math
        from gd101_isotp import build_isotp_transmit
        from gd101_periodic import PeriodicMessage
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid write timeout')
        data=bytes(data)
        deadline=time.monotonic()+timeout
        gate=session_lock(self,'_periodic_lifecycle_lock')
        if not gate.acquire(timeout=timeout):raise TimeoutError('Write lifecycle deadline expired; nothing sent')
        try:
            entry=self.flow_entries.get(slot)
            if entry is None:raise ValueError('ISO-TP flow route is not configured')
            entry=bytes(entry)
            build_isotp_transmit(entry,slot,data,padding_byte=self.flow_config[26])
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Write deadline expired; nothing sent')
            writer=getattr(self,'_async_writer',None)
            if writer is not None:remaining=writer.drain(remaining)
            return self._send_periodic_message(PeriodicMessage(6,0,bytes([slot])+data,entry),timeout=remaining)
        finally:gate.release()

    def queue_can(self, arbitration_id, data, *, extended=False):
        """Accept CAN into a bounded FIFO; returned ID is not a completion."""
        from gd101_can import build_can_transmit
        from gd101_periodic import PeriodicMessage
        from gd101_async import AsyncWriter
        data=bytes(data)
        build_can_transmit(arbitration_id,data,extended=extended,counter=0)
        with session_lock(self,'_periodic_lifecycle_lock'):
            # Queue acceptance must not wait on an in-flight USB transaction.
            # Channel lifecycle is stable under this gate. Native send rechecks
            # channel/uncertainty while holding transaction ownership.
            if not self.can_open:raise GD101Error('Queued CAN requires an open channel')
            writer=getattr(self,'_async_writer',None)
            if writer is None:
                writer=AsyncWriter(self._send_periodic_message)
                self._async_writer=writer
            return writer.enqueue(PeriodicMessage(5,0x100 if extended else 0,
                                  arbitration_id.to_bytes(4,'big')+data))

    def queue_isotp(self, slot, data):
        """Accept ISO-TP data with an immutable flow-route snapshot."""
        from gd101_isotp import build_isotp_transmit
        from gd101_periodic import PeriodicMessage
        from gd101_async import AsyncWriter
        data=bytes(data)
        with session_lock(self,'_periodic_lifecycle_lock'):
            if not self.can_open:raise GD101Error('Queued ISO-TP requires an open channel')
            entry=self.flow_entries.get(slot)
            if entry is None:raise ValueError('ISO-TP flow route is not configured')
            entry=bytes(entry)
            build_isotp_transmit(entry,slot,data,padding_byte=self.flow_config[26])
            writer=getattr(self,'_async_writer',None)
            if writer is None:
                writer=AsyncWriter(self._send_periodic_message)
                self._async_writer=writer
            return writer.enqueue(PeriodicMessage(6,0,bytes([slot])+data,entry))

    def write_kline_ordered(self, data, *, timeout=4, timing=5):
        """Prepared-wire blocking write follows previously accepted FIFO work."""
        import math
        from gd101_kline import build_kline_wire_transmit
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid write timeout')
        data=bytes(data);build_kline_wire_transmit(data,counter=0,timing=timing)
        deadline=time.monotonic()+timeout
        gate=session_lock(self,'_periodic_lifecycle_lock')
        if not gate.acquire(timeout=timeout):raise TimeoutError('Write lifecycle deadline expired; nothing sent')
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Write deadline expired; nothing sent')
            writer=getattr(self,'_async_writer',None)
            if writer is not None:remaining=writer.drain(remaining)
            return self.write_kline_wire(data,timeout=remaining,timing=timing)
        finally:gate.release()

    def queue_kline(self, data, *, protocol=4, no_checksum=False, timing=5):
        """Accept K-line data; checksum is prepared once before queueing."""
        from gd101_kline import build_kline_wire_transmit
        from gd101_periodic import PeriodicMessage
        from gd101_async import AsyncWriter
        data=bytes(data)
        if protocol not in (3,4) or not 1<=len(data)<=4096:
            raise ValueError('Invalid queued K-line protocol or size')
        wire=data if no_checksum else data+bytes([sum(data)&255])
        build_kline_wire_transmit(wire,counter=0,timing=timing)
        with session_lock(self,'_periodic_lifecycle_lock'):
            if not self.kline_open:raise GD101Error('Queued K-line requires an open channel')
            writer=getattr(self,'_async_writer',None)
            if writer is None:
                writer=AsyncWriter(self._send_periodic_message)
                self._async_writer=writer
            return writer.enqueue(PeriodicMessage(protocol,timing,wire))

    def clear_tx_queue(self):
        with session_lock(self,'_periodic_lifecycle_lock'):
            writer=getattr(self,'_async_writer',None)
            if writer is not None:writer.clear()

    def queued_results(self):
        with session_lock(self,'_periodic_lifecycle_lock'):
            writer=getattr(self,'_async_writer',None)
            return [] if writer is None else writer.results()

    def start_periodic_can(self, arbitration_id, data, interval_ms, *, extended=False):
        """Experimental native host periodic CAN. No J2534 exposure yet."""
        from gd101_can import build_can_transmit
        from gd101_periodic import PeriodicMessage,PeriodicScheduler
        data=bytes(data)
        build_can_transmit(arbitration_id,data,extended=extended,counter=0)
        if not isinstance(interval_ms,int) or not 5<=interval_ms<=65535:
            raise ValueError('Periodic interval must be 5..65535 ms')
        with session_lock(self,'_periodic_lifecycle_lock'):
            with operation_lock(self):
                if not self.can_open or self.tx_uncertain or self.flow_uncertain:
                    raise GD101Error('Periodic CAN requires an open, certain CAN channel')
                scheduler=getattr(self,'_periodic_scheduler',None)
                if scheduler is None:
                    scheduler=PeriodicScheduler(self._send_periodic_message)
                    self._periodic_scheduler=scheduler
                message=PeriodicMessage(5,0x100 if extended else 0,
                                        arbitration_id.to_bytes(4,'big')+data)
                return scheduler.start(message,interval_ms)

    def start_periodic_isotp(self, slot, data, interval_ms):
        """Periodic ISO-TP bound to an acknowledged route snapshot."""
        from gd101_isotp import build_isotp_transmit
        from gd101_periodic import PeriodicMessage,PeriodicScheduler
        data=bytes(data)
        with session_lock(self,'_periodic_lifecycle_lock'):
            with operation_lock(self):
                if not self.can_open or self.tx_uncertain or self.flow_uncertain:
                    raise GD101Error('Periodic ISO-TP requires an open, certain CAN channel')
                if slot not in self.flow_entries:raise ValueError('ISO-TP flow route is not configured')
                # Original periodic handler uses a7fe (configuration parameter49).
                minimum=max(5,int.from_bytes(self.flow_config[14:16],'little'))
                if not isinstance(interval_ms,int) or not minimum<=interval_ms<=65535:
                    raise ValueError('Periodic ISO-TP interval is below configured minimum or exceeds 65535 ms')
                entry=bytes(self.flow_entries[slot])
                build_isotp_transmit(entry,slot,data,padding_byte=self.flow_config[26])
                scheduler=getattr(self,'_periodic_scheduler',None)
                if scheduler is None:
                    scheduler=PeriodicScheduler(self._send_periodic_message)
                    self._periodic_scheduler=scheduler
                return scheduler.start(PeriodicMessage(6,0,bytes([slot])+data,entry),interval_ms)

    @serialized_operation
    def configure_kline_runtime(self, *, timing=None, gap=None, p2_max=None, loopback=None, baudrate=None, parity=None):
        """Commit host timing under transaction ownership, between sends."""
        import math
        if timing is not None and (not isinstance(timing,int) or not 0<=timing<=65535):
            raise ValueError('Invalid K-line interbyte timing')
        if p2_max is not None and (not isinstance(p2_max,int) or not 0<=p2_max<=59999):
            raise ValueError('Invalid K-line P2 maximum')
        if loopback is not None and (not isinstance(loopback,int) or loopback not in (0,1)):
            raise ValueError('Invalid K-line loopback byte')
        if gap is not None:
            if not math.isfinite(gap) or gap<0:raise ValueError('Invalid K-line receive gap')
            if self.receiver is None:raise GD101Error('K-line reader is unavailable')
        if baudrate is not None or parity is not None:
            from gd101_kline import KLINE_BAUD_RATES
            if baudrate is None:baudrate=self.kline_baudrate
            if parity is None:parity=getattr(self,'kline_parity',0)
            if parity not in (0,1,2):raise ValueError('Unsupported K-line parity')
            if baudrate not in KLINE_BAUD_RATES:raise ValueError('Unsupported K-line baud rate')
            if not self.kline_open or self.tx_uncertain or self.init_uncertain:
                raise GD101Error('Rate change requires K-line with no uncertain operation')
            if baudrate!=getattr(self,'kline_baudrate',None) or parity!=getattr(self,'kline_parity',0):
                window=getattr(self,'_kline_response_window',None)
                if window is not None:window.wait(2)
                self.tx_uncertain=True
                reply=self.exchange(build_kline_open_payload(baudrate,parity=parity)+b'\0',b'\x41\x01\0',timeout=2)
                from gd101_kline import validate_kline_config_ack
                try:validate_kline_config_ack(reply)
                except ValueError as error:raise GD101Error(str(error)) from error
                self.kline_baudrate=baudrate
                self.kline_parity=parity
                self.tx_uncertain=False
        if gap is not None:self.receiver.set_kline_gap(gap)
        if loopback is not None:self.kline_loopback=loopback
        if timing is not None:self._periodic_kline_timing=timing
        if p2_max is not None:
            from gd101_response_window import KLineResponseWindow
            window=getattr(self,'_kline_response_window',None)
            if window is None:self._kline_response_window=KLineResponseWindow((p2_max//2)/1000)
            else:window.configure((p2_max//2)/1000)

    def start_periodic_kline(self, data, interval_ms, *, protocol=4, no_checksum=False,
                             timing=5, p2_max=110):
        """Native periodic K-line with registration-time timing snapshot."""
        from gd101_kline import build_kline_wire_transmit
        from gd101_periodic import PeriodicMessage,PeriodicScheduler
        data=bytes(data)
        if protocol not in (3,4) or not 1<=len(data)<=4096:
            raise ValueError('Invalid periodic K-line protocol or size')
        if not isinstance(p2_max,int) or not 0<=p2_max<=59999:
            raise ValueError('Invalid P2 maximum')
        if not isinstance(interval_ms,int) or not max(100,p2_max//2)<=interval_ms<=65535:
            raise ValueError('Periodic K-line interval is below configured minimum or exceeds 65535 ms')
        wire=data if no_checksum else data+bytes([sum(data)&255])
        build_kline_wire_transmit(wire,counter=0,timing=timing)
        with session_lock(self,'_periodic_lifecycle_lock'):
            with operation_lock(self):
                if not self.kline_open or self.tx_uncertain or self.init_uncertain:
                    raise GD101Error('Periodic K-line requires an open, certain channel')
                scheduler=getattr(self,'_periodic_scheduler',None)
                if scheduler is None:
                    scheduler=PeriodicScheduler(self._send_periodic_message)
                    self._periodic_scheduler=scheduler
                return scheduler.start(PeriodicMessage(protocol,timing,wire),interval_ms)

    @serialized_operation
    def _send_periodic_message(self, message, timeout=25):
        # No lifecycle lock here: shutdown joins before taking operation ownership.
        if message.protocol in (3,4):
            timing=getattr(self,'_periodic_kline_timing',None)
            return self.write_kline_wire(message.data,timing=message.flags if timing is None else timing,timeout=timeout)
        if message.protocol==5:
            return self.write_can_frame(int.from_bytes(message.data[:4],'big'),message.data[4:],
                                        extended=bool(message.flags&0x100),timeout=timeout)
        if message.protocol==6:
            slot=message.data[0]
            if self.flow_entries.get(slot)!=message.route:
                raise GD101Error('ISO-TP flow route changed; background message cancelled')
            return self.write_isotp_message(slot,message.data[1:],timeout=timeout)
        raise GD101Error('Unsupported native periodic protocol')

    def stop_periodic(self, handle):
        with session_lock(self,'_periodic_lifecycle_lock'):
            scheduler=getattr(self,'_periodic_scheduler',None)
            if scheduler is None:raise KeyError(handle)
            scheduler.stop(handle)

    def clear_periodic(self):
        with session_lock(self,'_periodic_lifecycle_lock'):
            scheduler=getattr(self,'_periodic_scheduler',None)
            if scheduler is not None:scheduler.clear()

    def periodic_error(self, handle):
        with session_lock(self,'_periodic_lifecycle_lock'):
            scheduler=getattr(self,'_periodic_scheduler',None)
            return None if scheduler is None else scheduler.error(handle)

    def start_can_filter(self, kind, mask, pattern, *, extended=None):
        """Install a host-side pass (1) or block (2) filter; return its handle."""
        if not self.can_open:
            raise GD101Error('CAN channel is not open')
        return self.can_filters.add(kind,mask,pattern,extended=extended)

    def stop_can_filter(self, handle):
        if not self.can_open:
            raise GD101Error('CAN channel is not open')
        self.can_filters.remove(handle)

    def clear_can_filters(self):
        self.can_filters.clear()

    def read_can_message(self, timeout=4):
        """Return the next CAN frame accepted by installed host filters.

        A filtered background reader decides on arrival. The synchronous fallback
        decides while consuming frames. Raw diagnostic reads require an unfiltered reader.
        """
        import math
        if not math.isfinite(timeout) or timeout<0:
            raise ValueError('Receive timeout must be finite and nonnegative')
        receiver=getattr(self,'receiver',None)
        if receiver is not None and receiver.can_filters is not None:
            return receiver.read_message(timeout)
        deadline=time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if timeout>0 and remaining<=0:
                raise TimeoutError('No matching CAN message before deadline')
            frame=self.read_can_frame(max(0,remaining))
            if self.can_filters.accepts(frame):
                return frame

    @serialized_operation
    def read_can_frame(self, timeout=4):
        """Read one unfiltered CAN frame. Zero timeout polls queued data only."""
        import math
        from gd101_can import decode_can_receive_record
        if not math.isfinite(timeout) or timeout<0:
            raise ValueError('Receive timeout must be finite and nonnegative')
        if not self.can_open:
            raise GD101Error('CAN channel is not open')
        if getattr(self,'receiver',None) is not None and self.receiver.can_filters is not None:
            raise GD101Error('Unfiltered CAN reads require an unfiltered receiver')
        # Keep completion events in their original order for transmit handling.
        for index,event in enumerate(self.events):
            if packet_id(event)==0x301:
                if len(event)<3 or event[2]!=0:
                    raise GD101Error('CAN receive selector mismatch')
                del self.events[index]
                return decode_can_receive_record(event)
        deadline=time.monotonic()+timeout
        while True:
            remaining=deadline-time.monotonic()
            if timeout>0 and remaining<=0:
                raise TimeoutError('No CAN frame before deadline')
            if timeout==0 and self.receiver is None:
                raise TimeoutError('No queued CAN frame; start the background receiver')
            event=self.receive_packet(max(0,remaining))
            event_id=packet_id(event)
            if event_id==0x301:
                if len(event)<3 or event[2]!=0:
                    raise GD101Error('CAN receive selector mismatch')
                return decode_can_receive_record(event)
            if event_id!=0x300:
                raise GD101Error('Unexpected packet during CAN reception: '+event.hex())
            if len(self.events)>=4096:
                raise GD101Error('CAN event queue overflow')
            self.events.append(event)

    @stop_periodic_before
    @serialized_operation
    def disconnect_can(self):
        if self.can_open:
            from gd101_can import build_can_close_payload
            response=self.exchange(build_can_close_payload(),b'\x41\0\0')
            if len(response)<4 or response[3]:
                raise GD101Error('CAN close rejected: '+response.hex())
            self.can_open=False
            self.can_filters.clear()
            self.flow_entries={}

    @serialized_operation
    def initialize_kline(self, kind, data=b'', *, flags=0, capture_response=False,
                         response_timeout=None, no_checksum=False,
                         timing_words=(5,300,25,50,300,300,20,20,50), option=0, protocol=None):
        """Experimental two-stage initialization, optionally capturing FAST_INIT RX.

        Five-baud returns two keyword bytes. Fast initialization normally returns
        after the adapter handshake acknowledgment. With capture_response=True it
        starts an assembler before the handshake and requires a received message
        within the explicitly supplied response_timeout. flags=0x200 preserves
        a caller-supplied checksum; no_checksum controls receive-side stripping.
        Response capture leaves the receiver active. A failed operation retains
        the uncertainty latch and requires session cleanup. No vehicle test has
        validated this API. Existing K-line reception is restarted with its
        normal filters preserved; initialization response capture bypasses those
        filters for exactly one valid message.
        """
        from gd101_kline import (kline_init_plan,kline_command_reply_id,parse_kline_init_reply,
                                KLineAssembler,KLineFilter)
        import math
        if not self.kline_open or self.tx_uncertain or self.init_uncertain:
            raise GD101Error('Initialization requires an open channel with no uncertain operation')
        if flags & ~0x200:
            raise ValueError('Unsupported K-line initialization flags')
        if protocol not in (None,3,4):raise ValueError('Invalid K-line initialization protocol')
        if capture_response and (kind!='fast' or response_timeout is None or
                not math.isfinite(response_timeout) or response_timeout<0):
            raise ValueError('Fast response capture requires an explicit nonnegative timeout')
        steps = kline_init_plan(kind,data,flags=flags,timing_words=timing_words,option=option)
        had_receiver=self.receiver is not None
        filters=(KLineFilter(1,b'',b''),)
        gap_seconds=.020
        if had_receiver:
            filters=self.receiver.kline_filters()
            gap_seconds=self.receiver.kline_gap()
            self.receiver.stop()
            self.receiver=None
        self.events.clear()
        serial_transport_call(self.port.reset_input_buffer)
        self.init_uncertain = True
        if capture_response or had_receiver:
            # Start before the handshake, so byte events preceding its ACK are
            # assembled with their real host arrival times rather than replayed.
            assembler=KLineAssembler(filters=filters,no_checksum=no_checksum,gap_seconds=gap_seconds)
            self.start_receiver(assembler=assembler,capture_kline=capture_response)
        try:
            result = None
            for step in steps:
                # The original short writer rounds odd lengths upward. Supply a
                # deterministic pad rather than copying an uninitialized tail byte.
                payload=step.payload+bytes(len(step.payload)%2)
                reply_id=kline_command_reply_id(step.payload)
                prefix=bytes([(reply_id>>8)|0x40,reply_id&255,0])
                reply=self.exchange(payload,prefix,timeout=step.timeout_ms/1000)
                try:
                    result=parse_kline_init_reply(step.operation,reply)
                    if step.operation==9 and protocol==4:
                        self.kline_baudrate=int.from_bytes(reply[4:8],'little')
                except ValueError as error:
                    raise GD101Error(str(error)) from error
            if capture_response:
                # An adapter ACK is not an ECU response. Timeout retains the latch.
                result=self.read_kline_initialization(response_timeout)
        except Exception:
            if capture_response and self.receiver is not None:
                self.receiver.cancel_kline_initialization()
            raise
        self.init_uncertain = False
        return result

    @serialized_operation
    def write_kline_wire(self, data, *, timeout=4, timing=5):
        """Send prepared K-line wire bytes and wait for matching adapter completion.

        Caller supplies the full frame including its checksum. No ECU init is
        performed. This experimental API has offline tests, not ECU validation.
        A failed wait prevents further sends until a new session is opened.
        """
        from gd101_kline import (build_kline_wire_transmit,build_kline_byte_transmit,
                                 decode_kline_transmit_ack)
        import math
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Transmit timeout must be finite and positive')
        if not self.kline_open or self.tx_uncertain or self.init_uncertain:
            raise GD101Error('K-line must be open with no uncertain transmit')
        data=bytes(data)
        # Validate the complete input before transmitting any bytes.
        build_kline_wire_transmit(data,counter=self.tx_counter,timing=timing)
        byte_mode=len(data)>1000
        window=getattr(self,'_kline_response_window',None)
        if window is not None:timeout=window.wait(timeout)
        deadline=time.monotonic()+timeout
        count=len(data) if byte_mode else 1
        for index in range(count):
            if time.monotonic()>=deadline:
                raise GD101Timeout('K-line message deadline expired; partial delivery may be uncertain')
            if byte_mode:
                payload=build_kline_byte_transmit(data[index],counter=self.tx_counter)
                tag=payload[3]
            else:
                payload=build_kline_wire_transmit(data,counter=self.tx_counter,timing=timing)
                tag=payload[2]
            self.tx_counter=(self.tx_counter+1)&0xffffffff
            self.tx_uncertain=True
            self.send_packet(payload+b'\0' if byte_mode else payload,extended=not byte_mode)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise GD101Timeout('K-line completion timeout; delivery is uncertain')
                reply=self.receive_packet(remaining)
                if packet_id(reply)==(0x0302 if byte_mode else 0x030b):
                    ack=decode_kline_transmit_ack(reply,byte_mode=byte_mode)
                    if ack.selector==0 and ack.tag==tag:
                        if ack.status:
                            # A long message may already be partly on the bus.
                            if not byte_mode:self.tx_uncertain=False
                            raise GD101Error(f'K-line transmit rejected: status {ack.status}')
                        break
                if packet_id(reply) not in KLINE_EVENT_IDS:
                    raise GD101Error('Unexpected reply during K-line transmit: '+reply.hex())
                if len(self.events)>=4096:raise GD101Error('K-line event queue overflow')
                self.events.append(reply)
            if byte_mode and index+1<count and timing:
                remaining=deadline-time.monotonic()
                if remaining>0:time.sleep(min(timing/1000,remaining))
        self.tx_uncertain=False
        if window is not None:window.note_completion()
        receiver=getattr(self,'receiver',None)
        if getattr(self,'kline_loopback',0) and receiver is not None:
            receiver.publish_kline_echo(data,ack.timestamp)
        return ack

    @stop_periodic_before
    @serialized_operation
    def disconnect_kline(self):
        if self.kline_open:
            reply=self.exchange(b'\x01\x01\0\0',b'\x41\x01\0')
            from gd101_kline import validate_kline_config_ack
            try:validate_kline_config_ack(reply,closing=True)
            except ValueError as error:raise GD101Error(str(error)) from error
            self.kline_open = False

    @stop_periodic_before
    @serialized_operation
    def close(self):
        for pin in sorted(getattr(self,'active_programming_pins',())):
            try:self.disable_programming_voltage(pin)
            except Exception as error:
                self.record['pin_close_error']=str(error)
        try:
            self.disconnect_kline()
            self.disconnect_can()
        except Exception as error:
            self.record['close_error'] = str(error)
        finally:
            if self.receiver is not None:
                try:
                    self.receiver.stop()
                except Exception as error:
                    self.record['receiver_close_error'] = str(error)
                self.receiver = None
            try:
                self.port.close()
            except Exception as error:
                self.record['serial_close_error'] = str(error)
            self.tx_key = self.rx_key = None
            self.kline_open = False
            self.can_open = False
            self.can_filters.clear()
            self.flow_entries={}
            if self.capture:
                capture = self.capture
                self.capture = None
                try:
                    json.dump(self.record,capture,indent=2)
                    capture.write('\n')
                except Exception as error:
                    self.record['capture_close_error'] = str(error)
                finally:
                    try:
                        capture.close()
                    except Exception as error:
                        self.record['capture_close_error'] = str(error)
            if self.errors:
                errors = self.errors
                self.errors = None
                try:
                    errors.close()
                except Exception as error:
                    self.record['log_close_error'] = str(error)

    def __exit__(self, kind, error, traceback):
        if error:
            self.record['error'] = str(error)
        self.close()
