"""Decode recorded chunks: view.py DIR frame[,frame...] OUT.png"""
import sys, os, zlib, glob
import numpy as np
from PIL import Image
def load_chunk(path):
    z = np.load(path); body = zlib.decompress(z['frames'].tobytes()); shapes = z['shapes']; out = []; pos = 0; prev = None
    for sh in shapes:
        n = int(sh[0] * sh[1]); a = np.frombuffer(body[pos:pos + n], np.uint8); pos += n
        if prev is not None and prev.shape == a.shape: a = a ^ prev
        prev = a.copy(); out.append(a.reshape(sh[0], sh[1]).copy())
    return out, z['pal'], z['regs'], z['t']
if __name__ == '__main__':
    d = sys.argv[1]; want = [int(x) for x in sys.argv[2].split(',')]; files = sorted(glob.glob(os.path.join(d, 'c*.npz')))
    W = Image.new('RGB', (330 * len(want), 410)); cache = {}
    for k, f in enumerate(want):
        c = f // 60
        if c not in cache: cache[c] = load_chunk(files[c])
        fr, pal, regs, t = cache[c]; i = f % 60
        im = Image.fromarray(np.clip(pal[i][fr[i]].astype(int) * 4, 0, 255).astype(np.uint8)); W.paste(im, (k * 330, 0))
    W.save(sys.argv[3])
