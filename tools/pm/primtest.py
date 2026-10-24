"""Run a snapshot with PrimLog attached and check the logged primitives against the recorded frames.
usage: primtest.py SNAPSHOT SECONDS [OUT.png]"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
from PIL import Image, ImageDraw
SNAP = sys.argv[1]; SECS = float(sys.argv[2]); OUT = sys.argv[3] if len(sys.argv) > 3 else None
m = Demo(mem=0x10000000); snap.load(m, SNAP)
P = primlog.PrimLog(m)
DOWN = [0xe0, 0x50, 0xe0, 0xd0]
rows = []; shots = []
def rec(p):
    v = p.vga; full = v.frame(); bg = P.bg.frame(); start = (v.crtc[12] << 8) | v.crtc[13]
    if v.chain4: start = 0
    prims = P.frame_prims(start, bool(v.chain4))
    mask = (full != 0) if v.chain4 else full != bg
    img = Image.new('L', (full.shape[1], full.shape[0]), 0); d = ImageDraw.Draw(img)
    for q in prims:
        if q['e'] == 'B' and q['k'] != 's' and q['k'] != 'd' and isinstance(q.get('col'), tuple) and q['col'][0] == 0 and q['k'] != 'l': continue      # black fill: invisible on a black background
        if q['k'] in ('q', 't') and len(q['v']) >= 3: d.polygon([tuple(x) for x in q['v']], fill=255)
        elif q['k'] == 'l': d.line([tuple(x) for x in q['v']], fill=255)
        elif q['k'] in ('s', 'd'):
            for (x, y, c) in primlog.points_to_pixels(q['pg'], q['pts']):
                if 0 <= x < img.size[0] and 0 <= y < img.size[1] and mask[y, x]: img.putpixel((x, y), 255)
    pm = np.array(img) > 0
    if mask.sum() + pm.sum() > 0:
        inter = (mask & pm).sum(); union = (mask | pm).sum()
        rows.append((p.vframes, int(mask.sum()), int(pm.sum()), inter / max(1, union), len(prims), inter / max(1, mask.sum())))
        if len(shots) < 6 and mask.sum() > 200 and p.vframes % 40 == 0:
            a = np.zeros(full.shape + (3,), np.uint8); a[..., 0] = mask * 255; a[..., 1] = pm * 255; a[..., 2] = (full != 0) * 80; shots.append(Image.fromarray(a))
m.record = rec; t0 = time.time(); end = m.vclock + SECS * PIT_HZ
while m.vclock < end: m.tick()
print('frames with vector content', len(rows), 'stats', P.stats, 'wall %.0fs' % (time.time() - t0))
if rows:
    ious = [r[3] for r in rows]; print('IoU mean %.3f min %.3f  (mask px mean %.0f, logged px mean %.0f)' % (np.mean(ious), np.min(ious), np.mean([r[1] for r in rows]), np.mean([r[2] for r in rows])))
    print('recall mean %.3f' % np.mean([r[5] for r in rows])); print('worst', sorted(rows, key=lambda r: r[3])[:5])
if OUT and shots:
    W = Image.new('RGB', (330 * len(shots), 200))
    for i, s in enumerate(shots): W.paste(s, (i * 330, 0))
    W.save(OUT)
