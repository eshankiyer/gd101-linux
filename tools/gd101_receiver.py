"""Single-owner background serial reader for an authenticated GD101 session."""
from collections import deque
from dataclasses import dataclass
import threading
import time
from gd101_native import decode_frame, MAX_EXTENDED_PAYLOAD
from gd101_kline import packet_id, decode_kline_byte


@dataclass(frozen=True)
class ReceivedPacket:
    wire: bytes
    payload: bytes
    arrival: float


class FrameBuffer:
    def __init__(self, key):
        self.key = key
        self.pending = bytearray()
        self.failed = False

    def feed(self, chunk):
        if self.failed:
            raise ValueError('Frame decoder failed; close the session')
        self.pending.extend(chunk)
        frames = []
        try:
            while self.pending:
                units = self.pending[0] & 127
                if units == 127:
                    if len(self.pending) < 3:
                        break
                    size = int.from_bytes(self.pending[1:3], 'little')
                    if not 1 <= size <= MAX_EXTENDED_PAYLOAD:
                        raise ValueError('Invalid extended payload size')
                    needed = size+4
                elif 1 <= units <= 120:
                    needed = units*2+2
                else:
                    raise ValueError('Invalid native frame header')
                if len(self.pending) < needed:
                    break
                wire = bytes(self.pending[:needed])
                payload = decode_frame(wire,self.key)
                del self.pending[:needed]
                frames.append((wire,payload))
        except ValueError:
            self.failed = True
            raise
        return frames


class PacketReceiver:
    """Owns serial reads until stopped. Requires a port with bounded read timeout.

    With an assembler, 0303 events are consumed into messages rather than copied
    into the packet queue. Other packets stay available for command dispatch.
    Queues and partial-frame waits are bounded; errors are delivered to callers.
    """
    def __init__(self, port, key, *, assembler=None, isotp=None, limit=4096, on_packet=None,
                 capture_kline=False, can_filters=None, response_window=None, isotp_indications=False,
                 j1850_protocol=None):
        if limit < 1:
            raise ValueError('Queue limit must be positive')
        if assembler is not None and isotp is not None:
            raise ValueError("Choose one message assembly protocol")
        if can_filters is not None and (assembler is not None or isotp is not None):
            raise ValueError('Raw CAN filtering cannot share a message assembler')
        if j1850_protocol is not None:
            if j1850_protocol not in (1,2):raise ValueError('Invalid J1850 receive protocol')
            if assembler is not None or isotp is not None or can_filters is not None:
                raise ValueError('J1850 cannot share a receiver with another protocol')
        self.j1850_protocol=j1850_protocol
        self.j1850_filters=()
        self.j1850_error_count=0
        self.j1850_errors=deque(maxlen=32)
        if response_window is not None and assembler is None:
            raise ValueError('Response window requires a K-line assembler')
        self.response_window=response_window
        self.can_filters=can_filters
        if capture_kline and assembler is None:
            raise ValueError('K-line capture requires an assembler')
        if isotp_indications and isotp is None:raise ValueError('ISO indications require an assembler')
        self.isotp_indications=bool(isotp_indications)
        self.isotp = isotp
        self.isotp_updating=False
        self.isotp_update_drops=0
        self.indication_overflow=0
        self.indication_overflow_total=0
        self.isotp_error_count=0
        self.isotp_errors=deque(maxlen=32)
        self.port = port
        self.decoder = FrameBuffer(key)
        self.assembler = assembler
        self.limit = limit
        self.on_packet = on_packet
        self.packets, self.messages = deque(), deque()
        self.initial_messages=deque()
        self.capture_kline=bool(capture_kline)
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run,name='gd101-usb-reader',daemon=True)

    def start(self):
        self.thread.start()

    def kline_filters(self):
        with self.condition:
            if self.assembler is None:raise RuntimeError('K-line assembly is not enabled')
            return self.assembler.filters

    def kline_gap(self):
        with self.condition:
            if self.assembler is None:raise RuntimeError('K-line assembly is not enabled')
            return self.assembler.gap_seconds

    def set_kline_gap(self, seconds):
        import math
        if not math.isfinite(seconds) or seconds<0:
            raise ValueError('Receive gap must be finite and nonnegative')
        with self.condition:
            if self.assembler is None:raise RuntimeError('K-line assembly is not enabled')
            self.assembler.gap_seconds=seconds

    def set_kline_filters(self, filters):
        from gd101_kline import KLineFilter
        filters=tuple(filters)
        if not all(isinstance(f,KLineFilter) for f in filters):
            raise ValueError('Expected K-line filters')
        with self.condition:
            if self.assembler is None:raise RuntimeError('K-line assembly is not enabled')
            self.assembler.filters=filters

    def clear_receive(self, protocol):
        if protocol not in (1,2,3,4,5):raise ValueError('Unsupported receive protocol')
        receive_id={1:0x309,2:0x308,3:0x303,4:0x303,5:0x301}[protocol]
        with self.condition:
            # Preserve command acknowledgments, TX completions, framing state,
            # filters and a partial K-line message. This drains completed RX.
            retained=[p for p in self.packets if packet_id(p.payload)!=receive_id]
            self.packets.clear();self.packets.extend(retained)
            self.messages.clear()
            self.indication_overflow=0

    def _accept_kline(self, now, event=None):
        # Filter selection and completed-message routing share the same lock as
        # configuration changes. FAST_INIT consumes exactly one valid response
        # independently of normal pass/block filters, then resumes those filters.
        from gd101_kline import KLineFilter
        with self.condition:
            completed=bool(self.assembler.pending) and now>=self.assembler.last_arrival+self.assembler.gap_seconds
            saved=self.assembler.filters
            try:
                if self.capture_kline:self.assembler.filters=(KLineFilter(1,b'',b''),)
                message=(self.assembler.expire(now) if event is None else
                         self.assembler.feed(event,now))
            finally:self.assembler.filters=saved
            if self.response_window is not None:
                self.response_window.receive_state(bool(self.assembler.pending),completed=completed)
            if message is not None:
                if self.capture_kline:
                    self._put(self.initial_messages,message)
                    self.capture_kline=False
                else:self._put(self.messages,message)

    def publish_kline_echo(self, data, timestamp):
        from gd101_kline import KLineTransmitEcho
        if self.assembler is None:raise ValueError('K-line echo requires a K-line reader')
        self._put(self.messages,KLineTransmitEcho.from_wire(data,timestamp,no_checksum=self.assembler.no_checksum))

    def publish_can_echo(self, echo):
        from gd101_can import CANTransmitEcho
        if self.can_filters is None or not isinstance(echo,CANTransmitEcho):
            raise ValueError('CAN echo requires a raw CAN reader')
        self._put(self.messages,echo)

    def publish_isotp_completion(self, completion):
        from gd101_isotp import ISOTPTransmitDone
        if self.isotp is None or not isinstance(completion,ISOTPTransmitDone):
            raise ValueError('ISO-TP completion requires an ISO-TP reader')
        self._put(self.messages,completion)

    def _put(self, queue, item):
        if item is None:
            return
        with self.condition:
            if len(queue) >= self.limit:
                if queue is not self.messages:
                    # Losing an acknowledgment makes command delivery uncertain.
                    raise RuntimeError('GD101 receive queue overflow')
                self.indication_overflow+=1
                self.indication_overflow_total+=1
            else:queue.append(item)
            self.condition.notify_all()

    def _accept_can(self, payload):
        from gd101_can import decode_can_receive_record
        if packet_id(payload)!=0x301 or len(payload)<3 or payload[2]!=0:
            raise ValueError('Invalid CAN receive packet or selector')
        frame=decode_can_receive_record(payload)
        with self.condition:
            if self.can_filters.accepts(frame):self._put(self.messages,frame)

    def set_j1850_filters(self, filters):
        from gd101_kline import KLineFilter
        filters=tuple(filters)
        if not all(isinstance(f,KLineFilter) for f in filters):
            raise ValueError('Expected pass/block byte filters')
        with self.condition:
            if self.j1850_protocol is None:raise RuntimeError('J1850 receiver is not enabled')
            self.j1850_filters=filters

    def _accept_j1850(self, payload):
        from gd101_j1850 import decode_receive
        with self.condition:
            try:message=decode_receive(self.j1850_protocol,payload)
            except ValueError as error:
                self.j1850_error_count+=1
                self.j1850_errors.append(str(error))
                return
            filters=self.j1850_filters
            if (any(f.kind==1 and f.matches(message.data) for f in filters) and
                    not any(f.kind==2 and f.matches(message.data) for f in filters)):
                self._put(self.messages,message)

    def begin_isotp_update(self):
        with self.condition:
            if self.isotp is None or self.isotp_updating:
                raise RuntimeError('ISO-TP receiver cannot begin a route update')
            self.isotp_updating=True
            self.isotp.pending.clear()
            return self.isotp.timeout

    def finish_isotp_update(self, replacement):
        from gd101_isotp import ISOTPReassembler
        if not isinstance(replacement,ISOTPReassembler):raise ValueError('Invalid replacement assembler')
        with self.condition:
            if not self.isotp_updating:raise RuntimeError('No ISO-TP route update in progress')
            self.isotp=replacement
            self.isotp_updating=False

    def _accept_isotp(self, payload, now):
        from gd101_isotp import decode_isotp_input,ISOTPFrameError
        with self.condition:
            if self.isotp_updating:
                self.isotp_update_drops+=1
                return
            routed=decode_isotp_input(payload,self.isotp.routes)
            if routed is not None:
                previous=self.isotp.pending.get(routed.filter_index)
                try:message=self.isotp.feed(routed,now)
                except ISOTPFrameError as error:
                    self.isotp_error_count+=1
                    self.isotp_errors.append(str(error))
                else:
                    state=self.isotp.pending.get(routed.filter_index)
                    if self.isotp_indications and state is not None and state is not previous:
                        from gd101_isotp import ISOTPStartOfMessage
                        route=self.isotp.routes[routed.filter_index]
                        self._put(self.messages,ISOTPStartOfMessage(routed.frame.arbitration_id,
                                  routed.frame.extended,route.address,routed.frame.timestamp))
                    self._put(self.messages,message)

    def _run(self):
        partial_since = None
        try:
            while not self.stop_event.is_set():
                chunk = self.port.read(min(max(self.port.in_waiting,1),4096))
                now = time.monotonic()
                for wire,payload in self.decoder.feed(chunk):
                    packet = ReceivedPacket(wire,payload,now)
                    if self.on_packet:
                        self.on_packet(packet)
                    if self.assembler is not None and packet_id(payload) == 0x303:
                        self._accept_kline(now,decode_kline_byte(payload))
                    elif self.can_filters is not None and packet_id(payload) == 0x301:
                        self._accept_can(payload)
                    elif self.isotp is not None and packet_id(payload) == 0x301:
                        self._accept_isotp(payload,now)
                    elif self.j1850_protocol is not None and packet_id(payload)==(0x309 if self.j1850_protocol==1 else 0x308):
                        self._accept_j1850(payload)
                    else:
                        self._put(self.packets,packet)
                if self.decoder.pending:
                    if partial_since is None:
                        partial_since = now
                    elif now-partial_since >= 1:
                        raise TimeoutError('Incomplete GD101 frame for one second')
                else:
                    partial_since = None
                    if self.assembler is not None:
                        self._accept_kline(now)
                    if self.isotp is not None:
                        with self.condition:
                            if not self.isotp_updating:self.isotp.expire(now)
        except Exception as error:
            from gd101_devices import normalize_serial_error
            error=normalize_serial_error(error)
            if self.response_window is not None:self.response_window.fail(error)
            with self.condition:
                self.error = error
                self.condition.notify_all()
        finally:
            self.stop_event.set()
            with self.condition:
                self.condition.notify_all()

    def _get(self, queue, timeout):
        deadline = time.monotonic()+timeout
        with self.condition:
            while True:
                if queue is self.messages and self.indication_overflow:
                    lost=self.indication_overflow;self.indication_overflow=0
                    raise BufferError(f'{lost} messages or indications lost: receive queue full')
                if self.error:
                    from gd101_devices import GD101NotConnected
                    if isinstance(self.error,GD101NotConnected):raise self.error
                    raise RuntimeError('GD101 background reader failed') from self.error
                if queue:
                    return queue.popleft()
                if self.stop_event.is_set():
                    raise RuntimeError('GD101 background reader stopped')
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('No GD101 receive data before deadline')
                self.condition.wait(remaining)

    def read_packet(self, timeout=4):
        return self._get(self.packets,timeout)

    def read_message(self, timeout=4):
        if self.assembler is None and self.isotp is None and self.can_filters is None and self.j1850_protocol is None:
            raise RuntimeError('Message assembly is not enabled')
        return self._get(self.messages,timeout)

    def read_kline_initialization(self, timeout):
        try:return self._get(self.initial_messages,timeout)
        finally:self.cancel_kline_initialization()

    def cancel_kline_initialization(self):
        with self.condition:
            self.capture_kline=False
            self.initial_messages.clear()

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread.ident is None:return
        self.thread.join(2)
        if self.thread.is_alive():
            raise RuntimeError('Serial reader did not stop within two seconds')
