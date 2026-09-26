"""Monotonic K-line response window, separate from USB acknowledgment timing."""
import math
import threading
import time

class KLineResponseWindow:
    def __init__(self, delay=.055, *, clock=time.monotonic):
        self._clock=clock
        self._condition=threading.Condition()
        self._until=0
        self._receiving=False
        self._error=None
        self.configure(delay)

    def configure(self, delay):
        if not math.isfinite(delay) or delay<0:raise ValueError('Invalid response delay')
        with self._condition:self._delay=delay

    def note_completion(self):
        with self._condition:
            self._until=self._clock()+self._delay
            self._condition.notify_all()

    def receive_state(self, receiving, *, completed=False):
        with self._condition:
            self._receiving=bool(receiving)
            if completed:self._until=self._clock()+self._delay
            self._condition.notify_all()

    def fail(self, error):
        with self._condition:
            self._error=error
            self._condition.notify_all()

    def wait(self, timeout):
        if not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid response-window timeout')
        deadline=self._clock()+timeout
        with self._condition:
            while True:
                if self._error is not None:raise RuntimeError('K-line receiver failed; transmission inhibited') from self._error
                now=self._clock()
                remaining=deadline-now
                if remaining<=0:raise TimeoutError('K-line response window exceeds send deadline; nothing sent')
                if self._receiving:
                    self._condition.wait(remaining)
                    continue
                delay=self._until-now
                if delay<=0:return remaining
                self._condition.wait(min(delay,remaining))
