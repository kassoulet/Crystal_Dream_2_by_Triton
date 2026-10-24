import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
m = Demo(mem=0x10000000); snap.load(m, sys.argv[1]); P = primlog.PrimLog(m)
n = [0]
def rec(p):
    n[0] += 1
    if n[0] % 15 == 0 and n[0] < 200:
        v = p.vga; start = (v.crtc[12] << 8) | v.crtc[13]
        print('frame', p.vframes, 'start %#x' % start, {hex(k): collections.Counter(q['k'] for q in l) for k, l in P.pages.items() if l})
m.record = rec; end = m.vclock + 3 * PIT_HZ
clear_calls = [0]; 
while m.vclock < end: m.tick()
