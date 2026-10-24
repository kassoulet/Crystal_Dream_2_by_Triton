"""Check texel addressing of engine-A textured faces: rasterise them at 1x with each candidate (u,v) order and count pixels equal to the recorded frame.
usage: primtex.py SNAPSHOT SECONDS"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
m = Demo(mem=0x10000000); snap.load(m, sys.argv[1]); P = primlog.PrimLog(m)
PAIRS = []; CUR = [0]; LUT = []
score = {'vu': [0, 0], 'uv': [0, 0]}
def tri(tex, vs, uvs, full, key):
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    (x0, y0), (x1, y1), (x2, y2) = vs; den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if den == 0: return
    for y in range(max(0, min(ys)), min(200, max(ys) + 1)):
        for x in range(max(0, min(xs)), min(320, max(xs) + 1)):
            a = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / den; b = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / den; c = 1 - a - b
            if min(a, b, c) < 0.02: continue
            u = (a * uvs[0][0] + b * uvs[1][0] + c * uvs[2][0]) / 256; v = (a * uvs[0][1] + b * uvs[1][1] + c * uvs[2][1]) / 256
            if len(PAIRS) < 40 and x % 3 == 0 and y % 3 == 0: PAIRS.append((int((LUT or tex)[tex[((int(v) & 255) << 8) | (int(u) & 255)] if LUT else ((int(v) & 255) << 8) | (int(u) & 255)]), int(full[y, x]), tuple(CUR)))
            for k, (i, j) in (('vu', (v, u)), ('uv', (u, v))):
                t = tex[((int(i) & 255) << 8) | (int(j) & 255)]; t = LUT[t] if LUT else t
                score[k][0] += t == full[y, x]; score[k][1] += 1
done = [0]
def rec(p):
    if p.vga.chain4 and done[0] < 3 and p.vframes % 20 == 0:
        full = p.vga.frame(); ps = [q for q in P.shown_a if 'uv' in q] if hasattr(P, 'shown_a') else []
        for q in ps[-6:]:
            print('face', hex(q['fl']), q['tint'], q['v'], q['uv'], file=sys.stderr)
            CUR[:] = [q['fl'], q['tint'][0], q['tint'][1]]
            LUT[:] = list(P.luts[q['lut']]) if 'lut' in q else []
            tex = P.tex[q['tx']]; v = q['v']; uv = q['uv']
            tri(tex, v[:3], uv[:3], full, 0)
            if len(v) == 4: tri(tex, [v[0], v[2], v[3]], [uv[0], uv[2], uv[3]], full, 0)
        done[0] += bool(ps)
m.record = rec
end = m.vclock + float(sys.argv[2]) * PIT_HZ
while m.vclock < end and done[0] < 3: m.tick()
print(score); print(PAIRS)
