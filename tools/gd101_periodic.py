"""Host periodic-message scheduler, independent of USB transport.

The original driver keeps ten host-side periodic slots (7ab6ef10). The caller
must serialize send() with other transport writers and bound its completion time.
This module is not yet wired to the J2534 exports.
"""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class PeriodicMessage:
    protocol: int
    flags: int
    data: bytes
    route: bytes = b''


@dataclass
class _Job:
    message: PeriodicMessage
    interval: float
    due: float


class PeriodicScheduler:
    def __init__(self, send, *, clock=time.monotonic):
        self._send=send
        self._clock=clock
        self._condition=threading.Condition()
        self._jobs={}
        self._errors={}
        self._next_id=1
        self._inflight=None
        self._closed=False
        self._thread=None

    def start(self, message, interval_ms):
        if not isinstance(message,PeriodicMessage):raise TypeError('Expected PeriodicMessage')
        if not isinstance(interval_ms,int) or not 5<=interval_ms<=65535:
            raise ValueError('Periodic interval must be 5..65535 ms')
        message=PeriodicMessage(message.protocol,message.flags,bytes(message.data),bytes(message.route))
        with self._condition:
            if self._closed:raise RuntimeError('Periodic scheduler is closed')
            if len(self._jobs)>=10:raise OverflowError('Ten periodic slots are already in use')
            handle=self._next_id;self._next_id+=1
            self._jobs[handle]=_Job(message,interval_ms/1000,self._clock())
            if self._thread is None:
                self._thread=threading.Thread(target=self._run,name='gd101-periodic',daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return handle

    def _check_not_worker(self):
        if threading.current_thread() is self._thread:
            raise RuntimeError('Periodic send callback cannot wait on its own scheduler')

    def stop(self, handle):
        self._check_not_worker()
        with self._condition:
            if handle not in self._jobs:raise KeyError(handle)
            del self._jobs[handle]
            self._condition.notify_all()
            while self._inflight==handle:
                self._condition.wait()

    def clear(self):
        self._check_not_worker()
        with self._condition:
            self._jobs.clear()
            self._condition.notify_all()
            while self._inflight is not None:self._condition.wait()

    def error(self, handle):
        with self._condition:return self._errors.get(handle)

    def close(self):
        self._check_not_worker()
        with self._condition:
            self._closed=True
            self._jobs.clear()
            self._condition.notify_all()
            thread=self._thread
        if thread is not None:thread.join()

    def _run(self):
        while True:
            with self._condition:
                while not self._closed:
                    if not self._jobs:
                        self._condition.wait();continue
                    handle,job=min(self._jobs.items(),key=lambda pair:pair[1].due)
                    wait=job.due-self._clock()
                    if wait>0:
                        self._condition.wait(wait);continue
                    self._inflight=handle
                    break
                else:return
            error=None
            try:self._send(job.message)
            except Exception as exc:error=exc
            finally:
                with self._condition:
                    self._inflight=None
                    if error is not None:
                        # A timed-out send may have reached the bus. Never retry it.
                        self._jobs.pop(handle,None)
                        self._errors[handle]=error
                        # Bounded failure history even across repeated registrations.
                        if len(self._errors)>32:self._errors.pop(next(iter(self._errors)))
                    elif self._jobs.get(handle) is job:
                        # Skip missed intervals instead of bursting delayed messages.
                        now=self._clock()
                        steps=max(1,int((now-job.due)/job.interval)+1)
                        job.due+=steps*job.interval
                    self._condition.notify_all()


def stop_periodic_before(method):
    """Lifecycle gate must be acquired before the transport-operation lock."""
    from functools import wraps
    from gd101_transactions import session_lock
    @wraps(method)
    def invoke(self,*args,**kwargs):
        with session_lock(self,'_periodic_lifecycle_lock'):
            scheduler=getattr(self,'_periodic_scheduler',None)
            if scheduler is not None:
                scheduler.close()
                self._periodic_scheduler=None
            writer=getattr(self,'_async_writer',None)
            if writer is not None:
                writer.close()
                self._async_writer=None
            return method(self,*args,**kwargs)
    return invoke
