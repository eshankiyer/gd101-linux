"""Identify the supported GODIAG USB serial interface without opening any port."""
import os
import errno

USB_ID=(0xe327,0x2534)


class GD101NotConnected(RuntimeError):
    """The configured device is absent or its established USB transport failed."""


class GD101DeviceInUse(RuntimeError):
    """Another process holds the adapter's serial interface."""


def open_serial_port(port):
    """Classify open failures without retrying or changing port permissions."""
    try:
        port.open()
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO):
            raise GD101NotConnected('GODIAG USB adapter disappeared before the port opened') from error
        if error.errno in (errno.EBUSY, errno.EAGAIN):
            raise GD101DeviceInUse('GODIAG USB port is busy; close the other adapter application') from error
        if error.errno in (errno.EACCES, errno.EPERM):
            raise PermissionError('Permission denied opening GODIAG USB port; check serial device access') from error
        raise


def select_gd101_port(expected_serial, *, requested=None, ports=None):
    if not isinstance(expected_serial,str) or len(expected_serial.encode('ascii'))!=8:
        raise ValueError('GD101 authentication identity must be eight ASCII characters')
    if ports is None:
        from serial.tools.list_ports import comports
        ports=comports()
    candidates={}
    for port in ports:
        if (port.vid,port.pid)!=USB_ID or port.serial_number!=expected_serial:continue
        canonical=os.path.realpath(port.device)
        candidates[canonical]=port.device
    if requested is not None:
        canonical=os.path.realpath(os.fspath(requested))
        if canonical not in candidates:
            raise GD101NotConnected('Requested serial port does not match the configured GODIAG USB identity')
        return os.fspath(requested)
    if not candidates:raise GD101NotConnected('Configured GODIAG adapter is not connected')
    if len(candidates)!=1:raise RuntimeError('Multiple matching GODIAG interfaces; set GD101_PORT explicitly')
    return next(iter(candidates.values()))


class GD101SerialTimeout(TimeoutError):
    """The serial transport timed out; delivery may be uncertain."""


def normalize_serial_error(error):
    from serial import SerialException,SerialTimeoutException
    if isinstance(error,SerialTimeoutException):
        result=GD101SerialTimeout('GD101 USB serial operation timed out; delivery may be uncertain')
    elif isinstance(error,SerialException):
        result=GD101NotConnected('GD101 USB serial transport is unavailable; close and reopen the session')
    else:return error
    result.__cause__=error
    return result


def serial_transport_call(function,*args,**kwargs):
    from serial import SerialException
    try:return function(*args,**kwargs)
    except SerialException as error:raise normalize_serial_error(error) from error


def device_error_status(error):
    """Preserve a disconnected-device cause through reader/worker wrappers."""
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,GD101NotConnected):return 8
        if isinstance(error,GD101DeviceInUse):return 14
        if isinstance(error,GD101SerialTimeout):return 9
        error=error.__cause__
    return 7
