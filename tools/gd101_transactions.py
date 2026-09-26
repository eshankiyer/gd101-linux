"""Serialize complete adapter transactions without blocking message-queue reads."""
from functools import wraps
import inspect
import threading
import time

_creation_lock=threading.Lock()


def session_lock(session, name):
    # Lazy initialization also supports reconstructed offline test sessions.
    with _creation_lock:
        lock=getattr(session,name,None)
        if lock is None:
            lock=threading.RLock()
            setattr(session,name,lock)
        return lock


def operation_lock(session):
    return session_lock(session,'_operation_lock')


def serialized_operation(method):
    signature=inspect.signature(method)
    timed='timeout' in signature.parameters

    @wraps(method)
    def invoke(self,*args,**kwargs):
        lock=operation_lock(self)
        if lock.acquire(blocking=False):
            try:return method(self,*args,**kwargs)
            finally:lock.release()
        if not timed:
            with lock:return method(self,*args,**kwargs)
        bound=signature.bind(self,*args,**kwargs)
        bound.apply_defaults()
        timeout=bound.arguments['timeout']
        import math
        if not math.isfinite(timeout) or timeout<0:
            raise ValueError('Transaction timeout must be finite and nonnegative')
        started=time.monotonic()
        if not lock.acquire(timeout=timeout):
            raise TimeoutError('Adapter transaction ownership deadline expired; nothing sent')
        try:
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:
                raise TimeoutError('Adapter transaction ownership deadline expired; nothing sent')
            bound.arguments['timeout']=remaining
            return method(*bound.args,**bound.kwargs)
        finally:lock.release()
    return invoke
