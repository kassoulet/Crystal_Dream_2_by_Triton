"""Vector layer of a recording (record_full.py with PRIMLOG) as web files, next to the video.bin / index.bin of export_demo.py (same frame numbering and 30-frame chunks).
  vec.bin : 'CD2X', u32 frames, u16 chunk, u32 chunks, u32 offsets[chunks+1]; chunk = raw deflate of
              u16 nframes, u16 npics, per frame (i16 picture index or -1, u8 has-own-background),
              per picture (u32 length, picture bytes as primlog.PrimLog.picture writes them),
              per frame with a background: u16 w, u16 h, pixels (XOR the previous background of the chunk when the size is unchanged)
  tex.bin : 'CD2T', u32 n, u32 offsets[n+1]; each a raw deflate of one 256x256 texture (index = (v<<8)|u)
  lut.bin : 'CD2L', u32 n, then n x 256 bytes (shade tables: final colour = lut[texel])
usage: export_vec.py OUTDIR RECDIR [RECDIR ...]    (several segment recordings, in time order, are joined: the frame numbering is that of export_demo.py on the same chunks)"""
import sys, os, glob, zlib, struct
import numpy as np
CH = 30
def raw(b):
    co = zlib.compressobj(9, zlib.DEFLATED, -15); return co.compress(bytes(b)) + co.flush()
class Gid(dict):
    """segment-local id -> global id: the content goes into `store` once (by crc), the global id is its index there"""
    def __init__(self, local, crc, store): self.local = local; self.crc = crc; self.store = store
    def __missing__(self, i):
        h = zlib.crc32(self.local[i])
        if h not in self.crc: self.crc[h] = len(self.store); self.store[len(self.store)] = self.local[i]
        self[i] = self.crc[h]; return self[i]
def quant(b, q, n):
    """n float32 coordinates at b[q:] rounded to 1/16 pixel (invisible, and deflate then packs them far better); returns the position after them"""
    for i in range(n): struct.pack_into('<f', b, q + 4 * i, round(struct.unpack_from('<f', b, q + 4 * i)[0] * 16) / 16)
    return q + 4 * n
def remap(pic, texmap, lutmap):
    """picture blob with its texture and shade-table ids replaced (ids are per recording segment, the web files number them globally)"""
    b = bytearray(pic); n = struct.unpack_from('<H', b, 0)[0]; q = 2
    for _ in range(n):
        k = b[q]; q += 1
        if k == 0:
            nv = b[q + 1]; ct = b[q + 2]; q += 3
            if ct == 1:
                t, l = struct.unpack_from('<HH', b, q); struct.pack_into('<HH', b, q, texmap[t], 0xFFFF if l == 0xFFFF else lutmap[l]); q += 4 + 4 * nv
            else: q += 1 if ct == 0 else 2
            q += 2; q = quant(b, q, 2 * nv)
        elif k == 1: q += 3; q = quant(b, q, 4)
        elif k == 2: q += 3 + 5 * struct.unpack_from('<H', b, q + 1)[0]
        elif k == 3: q += 8000
        elif k == 4: q += 16
        else: raise ValueError('bad picture')
    return bytes(b)
if __name__ == '__main__':
    out, recs = sys.argv[1], sys.argv[2:]; os.makedirs(out, exist_ok=True)
    tex = {}; texcrc = {}; lut = {}; lutcrc = {}; pics = {}; frames = []        # global texture / shade table lists (by content), pictures by (segment, id), per frame (picture key, background or None)
    for si, rec in enumerate(recs):
        ltex = {}; llut = {}
        for f in sorted(glob.glob(os.path.join(rec, 'c*.npz'))):
            z = np.load(f)
            if 'vids' not in z: sys.exit('%s has no vector log (record with PRIMLOG=1)' % f)
            for i, tid in enumerate(z['tex_ids']): o = int(z['tex_len'][:i].sum()); ltex[int(tid)] = zlib.decompress(z['tex_blob'][o:o + int(z['tex_len'][i])].tobytes())
            for i, lid in enumerate(z['lut_ids']): llut[int(lid)] = z['lut_blob'][256 * i:256 * i + 256].tobytes()
            for i, pid in enumerate(z['pic_ids']): o = int(z['pic_len'][:i].sum()); pics[(si, int(pid))] = (zlib.decompress(z['pic_blob'][o:o + int(z['pic_len'][i])].tobytes()), ltex, llut)
            bgb = zlib.decompress(z['bg'].tobytes()); pos = 0; prev = None; bgi = 0; shapes = z['bgshapes']
            for vid, hb in zip(z['vids'], z['hasbg']):
                a = None
                if hb:
                    sh = shapes[bgi]; bgi += 1; n = int(sh[0] * sh[1]); a = np.frombuffer(bgb[pos:pos + n], np.uint8); pos += n
                    if prev is not None and prev.shape == a.shape: a = a ^ prev
                    prev = a.copy(); a = prev.reshape(sh[0], sh[1])
                frames.append(((si, int(vid)) if vid >= 0 else None, a))
    # globalise: ids by content, pictures rewritten (only the ones frames use)
    gp = {}
    for key, _ in frames:
        if key is None or key in gp: continue
        blob, ltex, llut = pics[key]
        gp[key] = remap(blob, Gid(ltex, texcrc, tex), Gid(llut, lutcrc, lut))
    blobs = []; ids = {k: i for i, k in enumerate(gp)}
    for c in range(0, len(frames), CH):
        fr = frames[c:c + CH]; keys = sorted({p for p, _ in fr if p is not None}, key=lambda k: ids[k]); loc = {p: i for i, p in enumerate(keys)}
        b = bytearray(struct.pack('<HH', len(fr), len(keys)))
        for p, a in fr: b += struct.pack('<hB', loc.get(p, -1), a is not None)
        for p in keys: b += struct.pack('<I', len(gp[p])) + gp[p]
        prev = None
        for p, a in fr:
            if a is None: continue
            h, w = a.shape; flat = a.reshape(-1); b += struct.pack('<HH', w, h) + (flat if prev is None or prev.shape != flat.shape else flat ^ prev).tobytes(); prev = flat
        blobs.append(raw(b))
    head = b'CD2X' + struct.pack('<IHI', len(frames), CH, len(blobs)); off = len(head) + 4 * (len(blobs) + 1); offs = []
    for b in blobs: offs.append(off); off += len(b)
    offs.append(off)
    open(os.path.join(out, 'vec.bin'), 'wb').write(head + struct.pack('<%dI' % len(offs), *offs) + b''.join(blobs))
    tb = [raw(tex[i]) for i in sorted(tex)]; off = 8 + 4 * (len(tb) + 1); offs = []
    for b in tb: offs.append(off); off += len(b)
    offs.append(off)
    open(os.path.join(out, 'tex.bin'), 'wb').write(b'CD2T' + struct.pack('<I', len(tb)) + struct.pack('<%dI' % len(offs), *offs) + b''.join(tb))
    open(os.path.join(out, 'lut.bin'), 'wb').write(b'CD2L' + struct.pack('<I', len(lut)) + b''.join(lut[i] for i in sorted(lut)))
    print('frames', len(frames), 'pictures', len(gp), 'vec.bin', os.path.getsize(os.path.join(out, 'vec.bin')), 'textures', len(tb), 'luts', len(lut))
