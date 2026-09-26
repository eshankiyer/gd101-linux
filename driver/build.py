"""Build native Linux embedding library using this workspace's Python runtime."""
from pathlib import Path
from cffi import FFI
ROOT=Path(__file__).resolve().parents[1]
ffi=FFI()
header=(ROOT/'driver/gd101.h').read_text()
api='\n'.join(line for line in header.splitlines() if not line.startswith('#') and line not in ('extern "C" {','}'))
ffi.embedding_api(api)
ffi.set_source('_gd101_embed','#include "gd101.h"',include_dirs=[str(ROOT/'driver')])
ffi.embedding_init_code(f'''
import sys
sys.path.insert(0, {str(ROOT/'tools')!r})
from _gd101_embed import ffi
from gd101_abi import install
install(ffi)
''')
if __name__=='__main__':
    out=ROOT/'driver/build'
    out.mkdir(exist_ok=True)
    ffi.compile(tmpdir=str(out),target=str(out/'libgd101.so'),verbose=True)
