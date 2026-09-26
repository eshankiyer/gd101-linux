"""Bounded FIFO transmission worker. Acceptance is not bus completion."""
from collections import deque
import threading
import math
import time

class AsyncWriter:
    def __init__(self, send, *, capacity=100):
        if not isinstance(capacity,int) or capacity<1:raise ValueError('Invalid queue capacity')
        self._send=send
        self._capacity=capacity
        self._condition=threading.Condition()
        self._pending=deque()
        self._results=deque(maxlen=capacity*2)
        self._inflight=None
        self._next_id=1
        self._closed=False
        self._failure=None
        self._thread=None

    def enqueue(self, message):
        # Immutable native message object; snapshot caller-owned byte buffers.
        from gd101_periodic import PeriodicMessage
        if not isinstance(message,PeriodicMessage):raise TypeError('Expected native message')
        copied=PeriodicMessage(message.protocol,message.flags,bytes(message.data),bytes(message.route))
        with self._condition:
            if self._closed:raise RuntimeError('Transmit queue is closed')
            if self._failure is not None:raise RuntimeError('Transmit queue failed; reopen channel') from self._failure
            if len(self._pending)+(self._inflight is not None)>=self._capacity:
                raise OverflowError('Transmit queue is full')
            handle=self._next_id;self._next_id+=1
            self._pending.append((handle,copied))
            if self._thread is None:
                self._thread=threading.Thread(target=self._run,name='gd101-async-tx',daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return handle

    def results(self):
        with self._condition:
            result=list(self._results);self._results.clear();return result

    def failure(self):
        with self._condition:return self._failure

    def _cancel_pending(self):
        while self._pending:
            handle,_=self._pending.popleft()
            self._results.append((handle,'cancelled',None))

    def _check_not_worker(self):
        if threading.current_thread() is self._thread:raise RuntimeError('Transmit callback cannot join its worker')

    def clear(self):
        self._check_not_worker()
        with self._condition:
            self._cancel_pending()
            while self._inflight is not None:self._condition.wait()

    def drain(self, timeout):
        """Wait for accepted work without cancellation; return remaining budget."""
        self._check_not_worker()
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid drain timeout')
        deadline=time.monotonic()+timeout
        with self._condition:
            while True:
                if self._failure is not None:
                    raise RuntimeError('Earlier queued transmission failed') from self._failure
                if self._closed:raise RuntimeError('Transmit queue is closed')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Queued transmissions exceed write deadline')
                if not self._pending and self._inflight is None:return remaining
                self._condition.wait(remaining)

    def close(self):
        self._check_not_worker()
        with self._condition:
            self._closed=True;self._cancel_pending();self._condition.notify_all()
            thread=self._thread
        if thread is not None:thread.join()

    def _run(self):
        while True:
            with self._condition:
                while not self._pending and not self._closed:self._condition.wait()
                if self._closed:return
                handle,message=self._pending.popleft();self._inflight=handle
            error=None
            try:self._send(message)
            except Exception as exc:error=exc
            with self._condition:
                self._inflight=None
                self._results.append((handle,'failed' if error else 'completed',error))
                if error is not None:
                    self._failure=error;self._cancel_pending()
                    self._condition.notify_all()
                    return
                self._condition.notify_all()
