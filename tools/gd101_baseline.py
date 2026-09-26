#!/usr/bin/env python3
"""Bounded ELM interface check; optional standard OBD supported-PID query.

No DTC clears, programming, reset, security access or manufacturer requests.
--vehicle selects ISO15765 11-bit 500 kbit/s and sends service 01 PID 00.
Requires pyserial. JSON includes raw response bytes and prompt completion.
"""
import argparse
import datetime
import json
import re
import time
from pathlib import Path
import serial


def supported_pid_responses(text):
    """Accept header-enabled 11-bit CAN single-frame replies to 01 00 only."""
    replies = []
    for line in text.replace('>', '').splitlines():
        compact = re.sub(r'\s+', '', line).upper()
        if not re.fullmatch(r'7E[8-F]06(?:[0-9A-F]{2}){6,7}', compact):
            continue
        payload = bytes.fromhex(compact[3:])
        if payload[1:3] != b'\x41\x00':
            continue
        replies.append({'can_id': compact[:3],
                        'supported_pids_01_20_mask': payload[3:7].hex()})
    return replies


def exchange(port, command, timeout=4):
    port.write((command + '\r').encode('ascii'))
    port.flush()
    deadline = time.monotonic() + timeout
    response = bytearray()
    while time.monotonic() < deadline:
        response.extend(port.read(4096))
        if b'>' in response:
            break
    return {'command': command, 'raw_hex': response.hex(),
            'text': response.decode('ascii', 'backslashreplace'),
            'prompt_received': b'>' in response}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--vehicle', action='store_true')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # Refuse to overwrite an earlier capture before touching the adapter.
    with args.output.open('x') as output:
        record = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'port': args.port, 'baud': 115200, 'responses': [],
                  'vehicle_response_verified': False}
        try:
            with serial.Serial(args.port, 115200, timeout=.1, write_timeout=1,
                               exclusive=True) as port:
                record['initial_rx_hex'] = port.read(4096).hex()
                commands = ['ATI', 'ATRV', 'ATDP', 'ATDPN']
                if args.vehicle:
                    commands += ['ATE0', 'ATH1', 'ATSP6', '0100', 'ATDP', 'ATDPN']
                for command in commands:
                    reply = exchange(port, command)
                    record['responses'].append(reply)
                    if command == '0100':
                        record['supported_pid_responses'] = supported_pid_responses(reply['text'])
                        record['vehicle_response_verified'] = bool(record['supported_pid_responses'])
                    print(json.dumps(reply), flush=True)
                    if not reply['prompt_received']:
                        raise RuntimeError('Missing prompt; stopping before next command')
                    if command in ('ATE0', 'ATH1', 'ATSP6') and 'OK' not in reply['text']:
                        raise RuntimeError('Adapter rejected configuration; stopping')
        except Exception as error:
            record['error'] = str(error)
            raise
        finally:
            json.dump(record, output, indent=2)
            output.write('\n')


if __name__ == '__main__':
    main()
