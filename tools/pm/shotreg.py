import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
from vga import timing
m = Demo(mem=0x10000000); m.boot()
DOWN = [0xe0, 0x50, 0xe0, 0xd0]
SCRIPT = [DOWN, DOWN, [0x1c, 0x9c], DOWN, DOWN, DOWN, [0x1c, 0x9c]]
seen = {}
def rec(p):
    t = timing(p.vga); key = tuple(sorted((k, round(v, 6) if isinstance(v, float) else v) for k, v in t.items() if k not in ('overscan',)))
    if key not in seen: seen[key] = (p.vframes, t, p.vga.chain4, p.vga.frame().shape)
m.record = rec
while m.vclock < float(sys.argv[1]) * PIT_HZ:
    key_script(m, SCRIPT); m.tick()
for k, (fr, t, c4, sh) in seen.items(): print(fr, {a: (round(b, 5) if isinstance(b, float) else b) for a, b in t.items()}, 'chain4', c4, sh)
