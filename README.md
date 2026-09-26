# GD101 Linux driver (experimental)

Native Python transport and a Linux J2534-style shared library for the GODIAG GD101 USB adapter. A separately built 32-bit Windows DLL can forward application calls through a private file mailbox to the Linux driver, allowing experiments with Windows diagnostic applications under Wine.

This is an independent reverse-engineering project, unaffiliated with GODIAG or Honda. It is not a complete or certified J2534 implementation.

## Current evidence and limits

- Real laptop-only adapter: native authentication, firmware identification, supply voltage, and clean close work on firmware **1.14**.
- Earlier physical tests exercised CAN and K-line channel setup, filtering, empty reads, and cleanup. These do not establish vehicle communication.
- Implemented paths include raw CAN, ISO15765, ISO9141, ISO14230, filters, timing configuration, initialization, queued and periodic writes, and programming-pin commands. Most messaging behavior is covered by simulation rather than a connected vehicle.
- J1850 codecs are preliminary and are not exposed as usable J2534 protocols.
- No working ECU diagnostics, navigation HDD access, map installation, or firmware flashing is demonstrated by this repository. Firmware 1.15 compatibility is not yet verified.
- HDS is a Windows application. The Linux driver is native; HDS itself requires Wine or Windows. HDS and its databases are not included.

## Build and test

Python 3.14 was used during development. Install Python development headers and a C compiler for the shared library. The library embeds the build environment's Python and uses this checkout's absolute source path; rebuild after moving it.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-gd101.txt -r driver/requirements-build.txt
.venv/bin/python driver/build.py
PYTHONPATH=tools GD101_RUN_ABI_TESTS=1 .venv/bin/python -m unittest discover -s tools -p 'test_gd101*.py'
```

Tests use simulated adapters and do not open serial hardware. A comparison test requiring a proprietary reference binary and an optional legacy Wine-lab test are skipped when their external fixtures are unavailable. Those fixtures are not distributed.

To build the Windows bridge, install `clang`, `llvm-dlltool`, and `lld-link`, then run:

```sh
.venv/bin/python driver/build_wine_bridge.py
```

Outputs are under `driver/build/`. See `driver/gd101.h` for the fixed-width ABI. The Windows bridge is a 32-bit DLL, not a Windows USB driver.

## Authentication and hardware access

Real hardware access requires private authentication material in `.local/gd101-auth.json`. This repository contains **no working credentials or credential extractor**. The native session expects `adapter_serial`, `host_private_key_le`, `peer_public_key_le`, `verification_key_a_le`, and `verification_key_b_le`. Keys must use the byte ordering expected by `tools/gd101_crypto.py`. Keep the file owner-readable only. A fresh clone cannot authenticate an adapter without separately obtained matching material.

Device selection checks USB VID:PID `e327:2534` and the configured serial number. `GD101_PORT` can select a matching serial-device path. The user must already have permission to access that device.

After building and configuring authentication, this explicit live check opens the adapter, reads version and voltage, and closes it. It does not open vehicle channels:

```sh
.venv/bin/python tools/check_gd101_adapter.py
```

The Windows bridge expects the mailbox at `C:\gd101-bridge`. Map that to an owner-only host directory and run the broker with:

```sh
.venv/bin/python tools/gd101_bridge.py /path/to/private/mailbox
```

The broker gives its client access to the implemented adapter operations. Do not connect untrusted clients. USB-only tests do not verify electrical behavior, vehicle timing, or programming-pin outputs. A timed-out transmission can have an unknown delivery outcome; the driver deliberately does not retry it automatically.

## Repository contents

- `tools/gd101_*.py`: transport, protocol codecs, scheduling, device matching, and J2534 provider.
- `driver/`: C header, CFFI build, Windows bridge, and synthetic Windows probes.
- `tools/test_gd101_*.py`: offline and opt-in compiled ABI tests.
- `tools/check_gd101_adapter.py`: explicit live identity/voltage check.

Downloaded vendor binaries, HDS installers, firmware files, captured traffic, private keys, and generated build artifacts are excluded. The legacy vendor-DLL authentication backend is intentionally unavailable in this source distribution.

## Firmware updater development

The source includes offline firmware-container inspection, updater packet
encoding/validation, and an update workflow with injectable transports. An exclusive Linux serial backend is included, tested using pseudoterminals;
there is no flashing CLI and the backend has not been validated with a physical
bootloader session. Firmware 1.15 was
retrieved from the server used by the official updater; physical installation
and native-driver compatibility with 1.15 have not been verified.

The workflow requires a pinned image digest, confirms bootloader mode before
erasing, stops after any uncertain exchange, checks integrity and requires an
independent installed-version/native-driver check before reporting completion.
These checks are tested with simulated transports. They do not establish safe
power requirements, reliable USB mode transitions, or recovery from a failed
physical update. Firmware files and vendor binaries are not included.
