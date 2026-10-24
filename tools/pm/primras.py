"""1x raster of the logged engine-A list in painter's order (flat faces by colour, textured by texture+shade table) vs the recorded frame: share of equal pixels, per flag class.
usage: primras.py SNAPSHOT SECONDS"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
from PIL import Image, ImageDraw
m = Demo(mem=0x10000000); snap.load(m, sys.argv[1]); P = primlog.PrimLog(m)
agg = collections.Counter(); n = [0]
def rec(p):
    if not p.vga.chain4 or not hasattr(P, 'shown_a') or p.vframes % 15: return
    full = p.vga.frame(); img = Image.new('P', (320, 200), 0); d = ImageDraw.Draw(img); owner = np.full((200, 320), -1)
    cls = []
    for i, q in enumerate(P.shown_a):
        if 'uv' in q: continue
        m1 = Image.new('L', (320, 200), 0); ImageDraw.Draw(m1).polygon([tuple(x) for x in q['v']], fill=255)
        mk = np.array(m1) > 0; owner[mk] = i
    img = np.zeros((200, 320), np.uint8); okm = owner >= 0
    for i, q in enumerate(P.shown_a):
        if 'uv' in q: continue
        sel = owner == i; img[sel] = q['col']
        eq = (img[sel] == full[sel]).sum(); agg[(q['k'], hex(q['fl']))] += int(eq); agg[(q['k'], hex(q['fl']), 'n')] += int(sel.sum())
m.record = rec
end = m.vclock + float(sys.argv[2]) * PIT_HZ
while m.vclock < end: m.tick()
for k in sorted({k[:2] for k in agg}): print(k, agg[k], agg[k + ('n',)], '%.3f' % (agg[k] / max(1, agg[k + ('n',)])))
