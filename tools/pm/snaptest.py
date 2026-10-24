import sys, os, hashlib, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap
S = sys.argv[1]
DOWN = [0xe0, 0x50, 0xe0, 0xd0]
SCRIPT = [DOWN, DOWN, [0x1c, 0x9c], DOWN, DOWN, DOWN, [0x1c, 0x9c]]
def run(m, until, h):
    def rec(p): h.update(p.vga.frame().tobytes()); h.update(p.vga.dac.tobytes())
    m.record = rec
    while m.vclock < until * PIT_HZ:
        key_script(m, SCRIPT); m.tick()
m = Demo(mem=0x10000000); m.boot()
run(m, 16.0, hashlib.md5())
snap.save(m, S + '/snap16.bin'); n_lpt = len(m.lpt)
h1 = hashlib.md5(); t0 = time.time(); run(m, 18.0, h1); print('continue in place', h1.hexdigest(), 'frames', m.vframes, 'lpt', len(m.lpt) - n_lpt, '%.0fs' % (time.time() - t0))
m2 = Demo(mem=0x10000000); snap.load(m2, S + '/snap16.bin'); m2.lpt = []; m2.keys = list(m.keys) if False else []
h2 = hashlib.md5(); run(m2, 18.0, h2); print('continue from snapshot', h2.hexdigest(), 'frames', m2.vframes, 'lpt', len(m2.lpt))
