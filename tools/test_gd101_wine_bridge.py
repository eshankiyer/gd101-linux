"""Opt-in compiled Windows ABI test in a disposable, hardware-free prefix.

GD101_RUN_WINE_TESTS=1 PYTHONPATH=tools .venv-gd101/bin/python -m unittest tools/test_gd101_wine_bridge.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('GD101_RUN_WINE_TESTS')=='1','Opt-in Wine ABI integration test')
class WineBridgeTests(unittest.TestCase):
    def test_windows_write_batch(self):
        if not (ROOT/'research/hds/runtime-lab/run.py').exists():
            self.skipTest('Optional original isolated Wine lab is not distributed')
        subprocess.run([sys.executable,str(ROOT/'driver/build_wine_bridge.py')],check=True)
        with tempfile.TemporaryDirectory(prefix='gd101-sim-') as directory:
            prefix=Path(directory)/'prefix'
            subprocess.run(['cp','-a','--reflink=auto','/tmp/gd101-wine-prefix',str(prefix)],check=True)
            target=prefix/'drive_c/GenRad/DiagSystem/Runtime'
            target.mkdir(parents=True,exist_ok=True)
            for name in ('GD101_NATIVE.dll','probe_wine_write.exe'):
                shutil.copyfile(ROOT/'driver/build'/name,target/name)
            (prefix/'drive_c/SIMULATION_ONLY').write_text('Synthetic test; no hardware backend\n')
            mailbox=prefix/'drive_c/gd101-bridge';mailbox.mkdir(mode=0o700)
            with (Path(directory)/'broker.log').open('w') as log:
                broker=subprocess.Popen([sys.executable,str(ROOT/'driver/tests/fake_broker.py'),str(mailbox)],stdout=log,stderr=subprocess.STDOUT)
                try:
                    result=subprocess.run([sys.executable,str(ROOT/'research/hds/runtime-lab/run.py'),
                        'write-test','--prefix',str(prefix)],capture_output=True,text=True,timeout=50)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr+
                        (ROOT/'research/hds/runtime-lab/write-test.log').read_text())
                    attempts=json.loads((mailbox/'simulation.json').read_text())
                    self.assertEqual([a['id'] for a in attempts],[0x7df,0x7df])
                    self.assertEqual([a['data'] for a in attempts],['11','22'])
                    self.assertTrue(all(0<a['options']['timeout']<=1 for a in attempts))
                    pins=[json.loads(line) for line in (mailbox/'programming-pins.jsonl').read_text().splitlines()]
                    self.assertEqual(pins,[{'pin':9,'value':0xfffffffe},{'pin':12,'value':12000},
                                           {'pin':13,'value':18000},{'pin':12,'value':12345}])
                    rates=[json.loads(line) for line in (mailbox/'can-rate-changes.jsonl').read_text().splitlines()]
                    self.assertEqual(rates,[{'rate':250000,'loopback':True},{'rate':500000,'loopback':False}])
                    self.assertEqual([json.loads(line) for line in (mailbox/'kline-parity-changes.jsonl').read_text().splitlines()],[2,0])
                    kline_rates=[json.loads(line) for line in (mailbox/'kline-rate-changes.jsonl').read_text().splitlines()]
                    self.assertEqual(kline_rates,[{'rate':9600},{'rate':10400}])
                    kline=json.loads((mailbox/'kline-write.json').read_text())
                    self.assertEqual(kline,{'size':512,'hex':(bytes(range(256))*2).hex()})
                    self.assertEqual(json.loads((mailbox/'iso-rate-change.json').read_text()),{'rate':250000,'slots':[0]})
                    iso=json.loads((mailbox/'isotp-write.json').read_text())
                    self.assertEqual(iso,{'slot':0,'hex':(bytes(range(256))*2).hex()})
                    long=json.loads((mailbox/'kline-long-write.json').read_text())
                    self.assertEqual(long,{'size':4096,'hex':(bytes(range(256))*16).hex()})
                finally:
                    broker.terminate()
                    broker.wait(timeout=5)

if __name__=='__main__':unittest.main()
