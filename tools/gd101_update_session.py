"""Transport-independent updater exchange validation.

No serial backend or flashing CLI is provided here. The caller owns exclusive
transport access. A failed or ambiguous exchange poisons this session: retrying
an auto-incrementing write could silently move data to the wrong address.
"""
import struct
import time
from gd101_update_protocol import encode_command, decode_reply


class UpdateSession:
    def __init__(self, transport):
        self.transport = transport
        self.failed = False

    def exchange(self, command, payload, *, timeout):
        if self.failed:
            raise RuntimeError('Updater session failed; recovery required')
        if timeout <= 0:
            raise ValueError('Timeout must be positive')
        request = encode_command(command, payload)
        deadline = time.monotonic() + timeout
        def remaining_budget():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Updater exchange deadline exceeded')
            return remaining
        try:
            written = self.transport.write(request)
            if written != len(request):
                raise IOError('Incomplete updater request write')
            header = self.transport.read_exact(4, timeout=remaining_budget())
            if len(header) != 4:
                raise IOError('Incomplete updater reply header')
            size = struct.unpack_from('<H', header)[0]
            if not 5 <= size <= 4096:
                raise ValueError('Invalid updater reply size')
            remaining = ((size + 9) & ~7) - 4
            tail = self.transport.read_exact(remaining, timeout=remaining_budget())
            remaining_budget()
            _, result = decode_reply(header + tail, expected_command=command)
            if command == 0xf023:
                return result[0] & 15
            if command == 0xf006:
                status = result[0] & 15
            else:
                status = result[0]
            if status:
                raise IOError(f'Updater command {command:04x} returned status {status}')
            return result
        except Exception:
            self.failed = True
            raise
