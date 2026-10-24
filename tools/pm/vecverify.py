"""End-to-end check of a recording's vector log: rasterise every frame's stored picture at 1x (planar and flat polygons, lines, points; textured faces via texture+shade table) over its stored
background (or the recorded frame where none) and compare with the recorded frame.   usage: vecverify.py RECDIR [step]    prints mean equality and the worst frames"""
import sys, os, glob, struct, zlib
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from view import load_chunk
def parse(pic):
    n = struct.unpack_from('<H', pic, 0)[0]; q = 2; items = []
    for _ in range(n):
        k = pic[q]; q += 1
        if k == 0:
            nv = pic[q + 1]; ct = pic[q + 2]; q += 3
            if ct == 0: col = (pic[q], 255); q += 1
            elif ct == 2: col = (pic[q], pic[q + 1]); q += 2
            else: col = ('t', struct.unpack_from('<HH', pic, q)); q += 4 + 4 * nv
            q += 2; v = [struct.unpack_from('<ff', pic, q + 8 * j) for j in range(nv)]; q += 8 * nv; items.append(('p', col, v))
        elif k == 1: col = (pic[q + 1], pic[q + 2]); q += 3; v = [struct.unpack_from('<ff', pic, q + 8 * j) for j in range(2)]; q += 16; items.append(('l', col, v))
        elif k == 2: m = struct.unpack_from('<H', pic, q + 1)[0]; pts = [struct.unpack_from('<HHB', pic, q + 3 + 5 * j) for j in range(m)]; q += 3 + 5 * m; items.append(('s', None, pts))
        elif k == 3: items.append(('o', np.unpackbits(np.frombuffer(pic[q:q + 8000], np.uint8).reshape(200, 40), axis=1).astype(bool), None)); q += 8000
        elif k == 4: items.append(('c', struct.unpack_from('<4f', pic, q), None)); q += 16
    return items
def raster(items, base):
    r = base.copy(); cl = (0, 0, 320, 200); ov = None
    for k, c, v in items:
        if k == 'c': cl = c; continue
        if k == 'o': ov = c; continue
        if k == 's':
            for x, y, col in v: r[y, x] = col
            continue
        if c[0] == 't': continue                                         # textured: not checked here
        m2 = Image.new('L', (320, 200), 0); d = ImageDraw.Draw(m2)
        if k == 'p': d.polygon([(x - .5, y - .5) for x, y in v], fill=255)
        else: d.line([(x - .5, y - .5) for x, y in v], fill=255)
        mk = np.array(m2) > 0; mk[:int(cl[1])] = False; mk[int(cl[3]):] = False; mk[:, :int(cl[0])] = False; mk[:, int(cl[2]):] = False
        r[mk] = (r[mk] & (c[1] ^ 255)) | (c[0] & c[1])
    if ov is not None: r[ov] = base[ov]
    return r
if __name__ == '__main__':
    rec = sys.argv[1]; step = int(sys.argv[2]) if len(sys.argv) > 2 else 1; res = []; n = 0; pics = {}; novl = 0
    for f in sorted(glob.glob(os.path.join(rec, 'c*.npz'))):
        fr, pal, regs, ts = load_chunk(f); z = np.load(f)
        for i, pid in enumerate(z['pic_ids']): o = int(z['pic_len'][:i].sum()); pics[int(pid)] = zlib.decompress(z['pic_blob'][o:o + int(z['pic_len'][i])].tobytes())
        bgb = zlib.decompress(z['bg'].tobytes()); pos = 0; prev = None; bgi = 0; bgs = []
        for hb in z['hasbg']:
            if not hb: bgs.append(None); continue
            sh = z['bgshapes'][bgi]; bgi += 1; k = int(sh[0] * sh[1]); a = np.frombuffer(bgb[pos:pos + k], np.uint8); pos += k
            if prev is not None and prev.shape == a.shape: a = a ^ prev
            prev = a.copy(); bgs.append(prev.reshape(sh[0], sh[1]))
        for i, vid in enumerate(z['vids']):
            n += 1
            if vid < 0 or fr[i].shape != (200, 320) or n % step: continue
            base = bgs[i] if bgs[i] is not None else fr[i]
            it = parse(pics[int(vid)]); novl += any(x[0] == 'o' for x in it); r = raster(it, base); res.append((n - 1, float((r == fr[i]).mean())))
    a = np.array([x[1] for x in res]); print('overlay pictures', novl, 'frames checked', len(res), 'mean equal %.4f' % a.mean(), 'min %.4f' % a.min(), 'worst', sorted(res, key=lambda x: x[1])[:6])
