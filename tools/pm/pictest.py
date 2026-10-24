"""Rasterise the packed pictures (PrimLog.picture) at 1x over the recorded background and compare with the recorded frame (flat polygons, lines, points; textured faces drawn via texture+shade table).
usage: pictest.py SNAPSHOT SECONDS [frame dump.png]"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
from PIL import Image, ImageDraw
m = Demo(mem=0x10000000); snap.load(m, sys.argv[1]); P = primlog.PrimLog(m)
tot = [0, 0]; bad = []; size = []
def unpack(b):
    n = struct.unpack_from('<H', b, 0)[0]; p = 2; out = []
    for _ in range(n):
        kind = b[p]; p += 1
        if kind == 0:
            e, nv, ct = b[p], b[p + 1], b[p + 2]; p += 3
            if ct == 0: col = (b[p], 15); p += 1
            elif ct == 2: col = (b[p], b[p + 1]); p += 2
            else: col = None; p += 4 + 4 * nv
            p += 2; v = [struct.unpack_from('<ff', b, p + 8 * i) for i in range(nv)]; p += 8 * nv; out.append(('p', v, col))
        elif kind == 1:
            col = (b[p + 1], b[p + 2]); p += 3; v = [struct.unpack_from('<ff', b, p + 8 * i) for i in range(2)]; p += 16; out.append(('l', v, col))
        elif kind == 4:
            out.append(('c', struct.unpack_from('<4f', b, p), 0)); p += 16
        elif kind == 3:
            mk = np.unpackbits(np.frombuffer(b[p:p + 8000], np.uint8).reshape(200, 40), axis=1).astype(bool); p += 8000; out.append(('o', mk, 0))
        else:
            cnt = struct.unpack_from('<H', b, p + 1)[0]; p += 3; pts = [struct.unpack_from('<HHB', b, p + 5 * i) for i in range(cnt)]; p += 5 * cnt; out.append(('s', pts, 0))
    return out
def rec(p):
    v = p.vga; full = v.frame(); bg = P.bg.frame(); start = (v.crtc[12] << 8) | v.crtc[13]
    prims = P.frame_prims(start, bool(v.chain4))
    if not prims or v.chain4: return
    blob = P.picture(prims, full, bg, start); size.append(len(blob))
    ov = None; im = Image.fromarray(bg.copy()); d = ImageDraw.Draw(im)
    cl = (0, 0, 320, 200)
    for k, vv, col in unpack(blob):
        if k == 'c': cl = vv
        elif k == 'p' and col is not None:
            m2 = Image.new('L', (320, 200), 0); ImageDraw.Draw(m2).polygon([(x - .5, y - .5) for x, y in vv], fill=255); mk = np.array(m2) > 0
            mk[:int(cl[1])] = False; mk[int(cl[3]):] = False; mk[:, :int(cl[0])] = False; mk[:, int(cl[2]):] = False; ra = np.array(im); ra[mk] = (ra[mk] & (col[1] ^ 255)) | (col[0] & col[1]); im = Image.fromarray(ra); d = ImageDraw.Draw(im)
        elif k == 'l':
            m2 = Image.new('L', (320, 200), 0); ImageDraw.Draw(m2).line([(x - .5, y - .5) for x, y in vv], fill=255); mk = np.array(m2) > 0; ra = np.array(im); ra[mk] = (ra[mk] & (col[1] ^ 255)) | (col[0] & col[1]); im = Image.fromarray(ra); d = ImageDraw.Draw(im)
        elif k == 's':
            for x, y, c in vv: im.putpixel((x, y), c)
        elif k == 'o': ov = vv
    r = np.array(im)
    if ov is not None: r[ov] = bg[ov]
    eq = (r == full).sum(); tot[0] += eq; tot[1] += full.size
    if eq < full.size - 600: bad.append((p.vframes, int(full.size - eq)))
    if len(sys.argv) > 4 and p.vframes == int(sys.argv[3]):
        out = np.zeros((200, 960, 3), np.uint8); out[:, :320] = p.vga.dac[full] * 4; out[:, 320:640] = p.vga.dac[r] * 4; out[:, 640:] = (r != full)[..., None] * 255
        Image.fromarray(out).resize((1920, 400), Image.NEAREST).save(sys.argv[4])
m.record = rec
end = m.vclock + float(sys.argv[2]) * PIT_HZ
while m.vclock < end: m.tick()
print('equal %.4f' % (tot[0] / tot[1]), 'worst', sorted(bad, key=lambda x: -x[1])[:5], len(bad), 'mean picture bytes', int(np.mean(size)))
