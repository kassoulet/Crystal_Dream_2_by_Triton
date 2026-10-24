"""colour semantics of engine-A faces: flag histogram and, per flag class, how the tint words relate to the recorded pixel at the face centre (top-most faces only: the last 40 prims)
usage: primcol.py SNAPSHOT SECONDS"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
m = Demo(mem=0x10000000); snap.load(m, sys.argv[1]); P = primlog.PrimLog(m)
H = collections.Counter(); S = collections.defaultdict(list)
def rec(p):
    if not p.vga.chain4 or not hasattr(P, 'shown_a'): return
    full = p.vga.frame()
    for q in P.shown_a: H[(q['k'], hex(q['fl']))] += 1
    for q in P.shown_a[-40:]:
        v = q['v']; cx = sum(a for a, b in v) // len(v); cy = sum(b for a, b in v) // len(v)
        if 0 <= cx < 320 and 0 <= cy < 200 and len(S[q['fl']]) < 6: S[q['fl']].append((q['tint'], int(full[cy, cx]), cx, cy))
m.record = rec
end = m.vclock + float(sys.argv[2]) * PIT_HZ
while m.vclock < end: m.tick()
print(sorted(H.items(), key=lambda x: -x[1])[:12])
for fl, l in S.items(): print(hex(fl), l[:6])
