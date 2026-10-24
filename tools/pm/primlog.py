"""Log the demo's vector drawing while it runs, and keep a copy of the VGA memory without that vector content.

Hooks (code hooks at function entry, addresses are image offsets + 0x10000):
  engine A (tunnel, Earth, chess; units 2612 + 296a)   polygon fillers 296a:bed2 (quad) and 296a:b54b (triangle), the projection routines 2612:0783/0863/0943
  engine B (intro, cube/spacecut; units 1f63/21a1/1df6/1bee)  21a1:0b3c quad, 21a1:08d0 triangle, 21a1:0fb5 / 21a1:1a20 polygon variants, 21a1:25c5 line,
                                                           1df6:0078 stars, 1bee:0265 dissolve dots, 1f63:0000 ClearPage, 1f63:0032 FillRect, projection 1f63:0194 / 1f63:022a
For every primitive the integer screen vertices the filler received are stored together with the camera-space 3D vertices they were projected from (found by
matching the integer vertex against the projection tables written by the engine's own projection routine), so a viewer can project them again at any resolution.
While a vector primitive runs, writes to the VGA window go to the real VGA (bit exact frames) but not to `bg`, a second VGA kept in step; bg.frame() is the picture
without the vector layer (text, logos, bitmap effects).   attach(m) installs everything on a sched.Demo."""
import struct, copy, zlib
import numpy as np
from unicorn import *
from unicorn.x86_const import *
from dosrun import seg_base
from vga import VGA

IMG = 0x10000
DATA = 0x42d20        # DS segment 42D2h
SSB = 0x52100         # SS segment 5210h

# image offsets
A_QUAD, A_TRI = 0x25572, 0x24beb
A_PROJ_FAR, A_PROJ_NEAR1, A_PROJ_NEAR2 = 0x168a3, 0x16983, 0x16a63
A_DRAWWORLD = 0x17e6f
B_QUAD, B_TRI, B_POLY1, B_POLY2, B_LINE = 0x1254c, 0x122e0, 0x129c5, 0x13430, 0x13fd5
B_STARS, B_DOTS = 0xdfd8, 0xc145
B_CLEAR, B_FILL = 0xf630, 0xf662
B_PROJ, B_PROJ2 = 0xf7c4, 0xf85a

class PrimLog:
    def __init__(self, m, with_bg=True):
        self.m = m; self.with_bg = with_bg
        self.pages = {}                  # page id (byte offset of the page in VRAM) -> list of primitives drawn since it was last cleared
        self.proj = []                   # recent projection calls
        self.tex = {}                    # texture id -> 64 KB bytes (ids by content)
        self.texkey = {}; self.texcrc = {}
        self.luts = {}; self.lutcrc = {}  # shade table id (by content) -> 256 bytes
        self.bgcnt = [0] * 8                 # bytes the demo itself (not a vector primitive) wrote to each 8 KB page since the last primitive drawn there
        self.fresh = False; self.seq = 0; self.vec_seq = [0] * 0x10000; self.bg_seq = [0] * 0x10000    # last write to each VRAM byte: by a vector primitive / by anything else
        self.active = 0                  # >0 while a vector primitive is executing (writes are kept out of bg)
        self.cur = None                  # primitive being drawn (collects point writes)
        self.stats = dict(prims=0, matched=0, unmatched=0)
        self.bg = copy.deepcopy(m.vga) if with_bg else None
        self._hooks()
        if with_bg: self._wrap_vga()
    # ------------------------------------------------------------------ helpers
    def lin(self, off): return IMG + off
    def hook(self, img_off, fn):
        a = IMG + img_off; self.m.mu.hook_add(UC_HOOK_CODE, lambda mu, ad, sz, u: fn(), None, a, a)
    def r16(self, a): return struct.unpack('<H', self.m.r(a, 2))[0]
    def s16(self, a): return struct.unpack('<h', self.m.r(a, 2))[0]
    def stack(self, off, n=2):
        sp = self.m.reg('ESP') & 0xFFFF; return self.m.r(SSB + sp + off, n)
    def sw(self, off): return struct.unpack('<H', self.stack(off))[0]
    def ret_far(self):                                           # linear return address of a far call, from the stack at entry
        ip, cs = self.sw(0), self.sw(2); return seg_base(self.m, cs)[0] + ip
    def ret_near(self):
        return seg_base(self.m, self.m.reg('CS'))[0] + self.sw(0)
    def page_of(self, sel):
        b = seg_base(self.m, sel)[0]; return b - 0xA0000
    # ------------------------------------------------------------------ bg VGA
    def _wrap_vga(self):
        m = self.m; bg = self.bg; real_out = m.vga.out
        def out(port, size, val): real_out(port, size, val); bg.out(port, size, val)
        m.vga.out = out
        def _vw(mu, acc, addr, size, val, ud):
            for k in range(size):
                o = addr - 0xA0000 + k; b = (val >> (8 * k)) & 0xFF
                m.vga.write(o, b)
                if self.fresh and m.vga.chain4 and not self.active: self.fresh = False; a = getattr(self, 'last_a', None); self.shown_a = self.pages.get(a, []); self.pages[a] = []       # start of a blit of the RAM buffer to the screen: this is the picture shown
                self.seq += 1
                if self.active:
                    self._point_write(o, b); self.vec_seq[o & 0xFFFF] = self.seq
                    if self.cur is not None: self.cur['nw'] = self.cur.get('nw', 0) + 1
                else: bg.write(o, b); self.bg_seq[o & 0xFFFF] = self.seq; self.bgcnt[(o >> 13) & 7] += 1
        def _vr(mu, acc, addr, size, val, ud):
            for k in range(size):
                o = addr - 0xA0000 + k; v = m.vga.read(o); bg.read(o); mu.mem_write(addr + k, bytes([v]))
        m._vw = _vw; m._vr = _vr
    def _point_write(self, off, byte):
        if self.cur is not None and self.cur['k'] in ('s', 'd'):
            self.cur['pts'].append((off, byte, self.m.vga.gc[0], self.m.vga.seq[2], self.m.vga.gc[3], self.m.vga.gc[5], self.m.vga.gc[8]))
    def begin(self, rec, ret_lin):
        """a vector primitive starts: keep its VRAM writes out of bg until execution reaches ret_lin"""
        pg = rec.get('pg')
        if pg is not None and self.bgcnt[(pg >> 13) & 7] >= 4000 and self.active == 0:         # a bulk write since the last primitive: the page was cleared or redrawn by other code
            self.pages[pg] = [r for r in self.pages.get(pg, []) if r is rec]
        if pg is not None: self.bgcnt[(pg >> 13) & 7] = 0
        self.active += 1; self.cur = rec
        h = []
        def done(mu, ad, sz, u):
            self.active -= 1
            if self.active == 0: self.cur = None
            self.m.mu.hook_del(h[0])
        h.append(self.m.mu.hook_add(UC_HOOK_CODE, done, None, ret_lin, ret_lin))
    # ------------------------------------------------------------------ hooks
    def _hooks(self):
        for off, kind in ((A_QUAD, 'q'), (A_TRI, 't')): self.hook(off, (lambda k: lambda: self.a_fill(k))(kind))
        self.hook(A_DRAWWORLD, self.a_drawworld)
        self.hook(A_PROJ_FAR, lambda: self.a_proj('far'))
        self.hook(A_PROJ_NEAR1, lambda: self.a_proj('near'))
        self.hook(A_PROJ_NEAR2, lambda: self.a_proj('near'))
        for off, kind in ((B_QUAD, 'q'), (B_TRI, 't'), (B_POLY1, 'p'), (B_POLY2, 'p')): self.hook(off, (lambda k: lambda: self.b_fill(k))(kind))
        self.hook(B_LINE, self.b_line)
        self.hook(B_STARS, lambda: self.b_points('s', far=True))
        self.hook(B_DOTS, lambda: self.b_points('d', far=False))
        self.hook(B_CLEAR, self.b_clear); self.hook(B_FILL, self.b_fillrect)
        self.hook(B_PROJ, lambda: self.b_proj(False)); self.hook(B_PROJ2, lambda: self.b_proj(True))
    # ---- engine A
    def a_drawworld(self):
        sel = self.r16(DATA + 0x5f92); self.last_a = self.page_of(sel); self.pages[self.last_a] = []
    def a_proj(self, kind):
        if kind == 'far':     dst = (self.sw(4), self.sw(6)); src = (self.sw(8), self.sw(10))
        else:                 dst = (self.sw(2), self.sw(4)); src = (self.sw(6), self.sw(8))
        d = seg_base(self.m, dst[1])[0] + dst[0]; s = seg_base(self.m, src[1])[0] + src[0]
        self.proj.append(dict(e='A', src=s, dst=d, F=struct.unpack('<i', self.m.r(DATA + 0x5efe, 4))[0], cx=self.r16(DATA + 0x5f02), cy=self.r16(DATA + 0x5f04),
                              zoff=struct.unpack('<i', self.m.r(SSB + 0x6ba, 4))[0]))
        del self.proj[:-24]
    def tex_id(self, tb):
        k = (tb, self.m.vframes)
        if k not in self.texkey:
            b = bytes(self.m.r(tb, 65536)); h = zlib.crc32(b)
            if h not in self.texcrc: self.texcrc[h] = len(self.tex); self.tex[len(self.tex)] = b
            self.texkey = {k: self.texcrc[h]} if len(self.texkey) > 64 else self.texkey; self.texkey[k] = self.texcrc[h]
        return self.texkey[k]
    def a_fill(self, kind):
        m = self.m; n = 4 if kind == 'q' else 3
        v = struct.unpack('<%dh' % (2 * n), m.r(SSB + 0x400, 4 * n)); verts = [(v[2 * i], v[2 * i + 1]) for i in range(n)]
        fl = self.r16(SSB + 0x42a); colw = struct.unpack('<4H', m.r(SSB + 0x410, 8))
        rec = dict(k=kind, e='A', v=verts, fl=fl, col=colw[0] & 0xFF, tint=list(colw), clip=struct.unpack('<4h', m.r(DATA + 0x5f94, 8)))      # clip window: ymin, ymax, xmin, xmax (inclusive)
        sel = self.r16(DATA + 0x5f92); pg = self.page_of(sel); rec['pg'] = pg
        if fl & 0x20:
            rec['uv'] = [struct.unpack('<HH', m.r(SSB + 0x418 + 4 * i, 4)) for i in range(n)]
            tsel = self.r16(SSB + 0x428); tb = seg_base(m, tsel)[0]; rec['tx'] = self.tex_id(tb)
            c = colw[0] & 0xFF
            if c != 0x1f:           # span routine at 296a:ab58 shades: texel -> cs:[((c&0x1f)<<8) + (c&0xe0) + 0x29a + texel]
                lo = ((c & 0x1f) << 8) + (c & 0xe0) + 0x29a; b = bytes(m.r(0x296a0 + lo, 256)); h = zlib.crc32(b)
                if h not in self.lutcrc: self.lutcrc[h] = len(self.luts); self.luts[len(self.luts)] = b
                rec['lut'] = self.lutcrc[h]
        rec['v3'] = self.match3d(verts, 'A')
        self.pages.setdefault(pg, []).append(rec); self.stats['prims'] += 1; self.fresh = True; self.last_a = pg
        if self.with_bg: self.begin(rec, self.ret_far())
    # ---- engine B
    def b_proj(self, regs):
        if regs: src = DATA + (self.m.reg('ESI') & 0xFFFF); dst = DATA + (self.m.reg('EDI') & 0xFFFF)
        else:    dst = DATA + self.sw(4); src = DATA + self.sw(6)
        self.proj.append(dict(e='B', src=src, dst=dst, F=self.s16(DATA + 0x96d0), zoff=self.s16(DATA + 0x96ce), cx=0xA0, cy=0x64, ox=self.s16(DATA + 0x51f8), oy=self.s16(DATA + 0x51fa)))
        del self.proj[:-24]
    def b_colour(self):
        v = self.m.vga; return (v.gc[0] & 15, v.seq[2] & 15, v.gc[3], v.gc[5])
    def b_fill(self, kind):
        m = self.m; r = lambda n: m.reg(n) & 0xFFFF
        xs = [self.r16(DATA + 0xC5B0), r('EBX'), r('ECX')]; ys = [r('EDX'), r('ESI'), r('EDI')]
        if kind in ('q', 'p'): xs.append(self.r16(DATA + 0xC5B6)); ys.append(self.r16(DATA + 0xC5BE))
        sg = lambda x: x - 0x10000 if x >= 0x8000 else x
        verts = [(sg(x), sg(y)) for x, y in zip(xs, ys)]
        pg = self.page_of(m.reg('ES'))
        rec = dict(k='q' if kind in ('q', 'p') else 't', e='B', v=verts, col=self.b_colour(), pg=pg, fl=0)
        rec['v3'] = self.match3d(verts, 'B')
        self.pages.setdefault(pg, []).append(rec); self.stats['prims'] += 1
        if self.with_bg: self.begin(rec, self.ret_far())
    def b_line(self):
        a = [self.sw(4 + 2 * i) for i in range(6)]          # page, colour, y2, x2, y1, x1 (Pascal: last pushed first)
        sg = lambda x: x - 0x10000 if x >= 0x8000 else x
        pg = self.page_of(a[0]); verts = [(sg(a[5]), sg(a[4])), (sg(a[3]), sg(a[2]))]
        rec = dict(k='l', e='B', v=verts, col=self.b_colour(), pg=pg, fl=0, c2=a[1])
        rec['v3'] = self.match3d(verts, 'B')
        self.pages.setdefault(pg, []).append(rec); self.stats['prims'] += 1
        if self.with_bg: self.begin(rec, self.ret_far())
    def b_points(self, kind, far):
        pg = self.page_of(self.m.reg('ES')) if far else self.page_of(self.sw(6))        # stars: ES = page; dots (near call, pushed page, 1, mode): page is 3 words up
        rec = dict(k=kind, e='B', pg=pg, pts=[], v=[], fl=0)
        self.pages.setdefault(pg, []).append(rec); self.stats['prims'] += 1
        if self.with_bg: self.begin(rec, self.ret_far() if far else self.ret_near())
    def b_clear(self):
        self.pages[self.page_of(self.sw(4))] = []
    def b_fillrect(self):
        a = [self.sw(4 + 2 * i) for i in range(7)]          # colour, planes, y1, x1, y0, x0, page  (last pushed first)
        sg = lambda x: x - 0x10000 if x >= 0x8000 else x
        pg = self.page_of(a[6]); x0, y0, x1, y1 = sg(a[5]), sg(a[4]), sg(a[3]), sg(a[2])
        keep = []
        for p in self.pages.get(pg, []):
            if p['k'] in ('q', 't', 'l'):
                xs = [v[0] for v in p['v']]; ys = [v[1] for v in p['v']]
                if min(xs) >= (x0 & ~15) and max(xs) <= x1 + 16 and min(ys) >= y0 and max(ys) <= y1: continue          # erased with the rectangle
            keep.append(p)
        self.pages[pg] = keep
    # ------------------------------------------------------------------ 3D matching
    def read_tables(self, e):
        """list of (dst2d array, src3d array) for one logged projection call"""
        m = self.m; out = []
        if e['e'] == 'A':
            d = []; s = []; i = 0
            while i < 400:
                x, y = struct.unpack('<hh', m.r(e['dst'] + 4 * i, 4))
                if x == 0x7fff: break
                X, Y, Z = struct.unpack('<iii', m.r(e['src'] + 12 * i, 12)); d.append((x, y)); s.append((X, Y, Z)); i += 1
        else:
            d = []; s = []; i = 0
            while i < 400:
                x, y = struct.unpack('<hh', m.r(e['dst'] + 4 * i, 4))
                if x == 0x7fff: break
                X, Y, Z = struct.unpack('<hhh', m.r(e['src'] + 6 * i, 6)); d.append((x, y)); s.append((X, Y, Z)); i += 1
        return d, s
    def match3d(self, verts, eng):
        """3D camera-space vertices behind the integer vertices, plus the projection constants; None where the match failed"""
        for e in reversed(self.proj):
            if e['e'] != eng: continue
            d, s = self.read_tables(e)
            dx = e.get('ox', 0); dy = e.get('oy', 0)
            for off in ((0, 0), (dx, dy)) if (dx or dy) else ((0, 0),):
                idx = []
                for (x, y) in verts:
                    try: idx.append(d.index((x - off[0], y - off[1])))
                    except ValueError: idx = None; break
                if idx is not None:
                    self.stats['matched'] += 1
                    return dict(p=[s[i] for i in idx], F=e['F'], zoff=e['zoff'], cx=e['cx'] + off[0], cy=e['cy'] + off[1])
        self.stats['unmatched'] += 1
        return None
    # ------------------------------------------------------------------ per frame
    def frame_prims(self, start, chain4=False):
        """the vector layer of the picture that starts at VRAM offset `start` (a copy, so later drawing does not change it);
        engine A draws into a RAM buffer that is blitted to the chain-4 screen, so there it is the buffer of the last DrawWorld"""
        if chain4: return [dict(p) for p in getattr(self, 'shown_a', [])]
        return [dict(p) for p in self.pages.get(start, [])]

    # ------------------------------------------------------------------ packing for the recorder
    def cont(self, q):
        """continuous screen vertices (320x200 corner coordinates) of a polygon/line: the 3D vertices projected again without rounding; the logged integers when unmatched"""
        v = q['v']; r = q.get('v3'); out = [(a + 0.5, b + 0.5) for a, b in v]
        if not r or len(r['p']) != len(v): return out
        res = []
        for (X, Y, Z), (ix, iy) in zip(r['p'], v):
            d = Z + r['zoff']
            if d <= 0: self.stats['reproj_bad'] = self.stats.get('reproj_bad', 0) + 1; return out
            x = X * 1.25 * r['F'] / d + r['cx']; y = Y * r['F'] / d + r['cy']
            if abs(x - ix) > 1.5 or abs(y - iy) > 1.5: self.stats['reproj_bad'] = self.stats.get('reproj_bad', 0) + 1; return out
            res.append((x + 0.5, y + 0.5))
        self.stats['reproj_ok'] = self.stats.get('reproj_ok', 0) + 1
        return res
    def recall(self, prims, mask):
        """share of the pixels in `mask` that the logged primitives (drawn at 1x) cover: tells a live vector layer from a stale list"""
        from PIL import Image, ImageDraw
        img = Image.new('L', (mask.shape[1], mask.shape[0]), 0); d = ImageDraw.Draw(img)
        for q in prims:
            if q['k'] in ('q', 't', 'p') and len(q['v']) >= 3: d.polygon([tuple(x) for x in q['v']], fill=255)
            elif q['k'] == 'l': d.line([tuple(x) for x in q['v']], fill=255)
            elif q['k'] in ('s', 'd'):
                for x, y, c in points_to_pixels(q['pg'], q['pts']):
                    if 0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]: img.putpixel((x, y), 255)
        pm = np.array(img) > 0
        return (pm & mask).sum() / max(1, mask.sum())
    def cover(self, prims, shape):
        """pixels the logged engine-A polygons touch (clipped to the first one's window, grown by a pixel): a picture blitted from a RAM buffer has no separate background, and in the
        others the background holds stale pixels under the polygons, so what they cover is cleared to colour 0 (the recorded pixels along the silhouettes would otherwise show
        around the smooth polygons)"""
        from PIL import Image, ImageDraw
        im = Image.new('L', (shape[1], shape[0]), 0); d = ImageDraw.Draw(im); cl = None
        for q in prims:
            if q['e'] != 'A' or len(q['v']) < 3: continue
            cl = cl or q.get('clip'); d.polygon([(x, y) for x, y in q['v']], fill=255)
        m = np.array(im) > 0
        out = m.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1): out |= np.roll(np.roll(m, dy, 0), dx, 1)
        if cl: out[:max(0, cl[0])] = False; out[cl[1] + 1:] = False; out[:, :max(0, cl[2])] = False; out[:, cl[3] + 1:] = False
        return out
    def picture(self, prims, full, bg, start=None):
        """one displayed picture's vector layer as bytes: u16 n, then per item
        poly   B 0, B engine, B nv, B ctype (0 flat: B colour | 1 textured: H tex, H lut(0xFFFF none), nv x (H u, H v) | 2 planar: B colour, B plane mask), H flags, nv x (f32 x, f32 y)
        line   B 1, B engine, B colour, B plane mask, 2 x (f32 x, f32 y)
        points B 2, B engine, H n, n x (H x, H y, B colour from the recorded picture)
        clip   B 4, 4 x f32 (x0, y0, x1, y1): scissor window for the items that follow (engine A's clip window), until the next clip item
        overlay B 3, 200 x 40 bytes: bit mask (1 = pixel drawn by the demo after the vector layer: the recorded background shows instead)
        Planar (engine B) primitives write their colour in the VGA planes the map mask enables and keep the other planes of what is underneath; the player redraws them plane by plane."""
        from PIL import Image, ImageDraw
        partial = any(q['e'] == 'B' and q['k'] in ('q', 't', 'p', 'l') and (q['col'][1] & 15) not in (0, 15) for q in prims)
        items = []; clip = None                      # [kind, engine, verts, ctype bytes / colour, flags]
        for q in prims:
            e = 0 if q['e'] == 'A' else 1; k = q['k']
            if e == 1 and k in ('q', 't', 'p', 'l') and not q.get('nw'): continue                    # the filler wrote nothing (rejected or handed the polygon to its clipper, which calls it again)
            if k in ('q', 't', 'p'):
                v = self.cont(q)
                if e == 0:
                    if q['fl'] & 0x1f: self.stats['lowflags'] = self.stats.get('lowflags', 0) + 1
                    if 'uv' in q: ct = struct.pack('<BHH', 1, q['tx'], q.get('lut', 0xFFFF)) + b''.join(struct.pack('<HH', a & 0xFFFF, b & 0xFFFF) for a, b in q['uv'])
                    else: ct = struct.pack('<BB', 0, q['col'] & 0xFF)
                    if q.get('clip') != clip: clip = q.get('clip'); items.append(['c', 0, (clip[2], clip[0], clip[3] + 1, clip[1] + 1), b'', 0])
                    items.append(['p', 0, v, ct, q['fl'] & 0xFFFF]); continue
                c = q['col'][0] & 15; mk = q['col'][1] & 15
                if mk == 15 and c and not partial:                           # (planes blend when partial masks are around, so colours are no evidence there) a stale primitive (its pixels were later erased by the demo): none of its pixels shows its colour any more
                    im = Image.new('L', (full.shape[1], full.shape[0]), 0); ImageDraw.Draw(im).polygon([(x - .5, y - .5) for x, y in v], fill=255); reg = np.array(im) > 0
                    if reg.any() and np.mean(full[reg] == c) < 0.1: continue
                if mk: items.append(['p', 1, v, struct.pack('<BBB', 2, c, mk), q['fl'] & 0xFFFF])          # a zero plane mask writes nothing
            elif k == 'l':
                c = q['col'][0] & 15; mk = q['col'][1] & 15
                if mk: items.append(['l', 1, self.cont(q), (c, mk), 0])
            elif k in ('s', 'd'):
                seen = {}
                for x, y, c in points_to_pixels(q['pg'], q['pts']):
                    if 0 <= x < full.shape[1] and 0 <= y < full.shape[0] and full[y, x] != bg[y, x]: seen[(x, y)] = int(full[y, x])
                if seen: items.append(['s', 1, seen, b'', 0])
        # Wireframe pictures (engine B): the demo hides lines with black fills and black outlines, but its integer rasteriser lets many of those survive.  Which lines are visible is
        # taken from the recorded picture: a line whose pixels mostly carry its colour is drawn last (on top), a black or mostly covered line is dropped.
        if any(it[0] == 'l' for it in items):
            tops = []; rest = []
            for it in items:
                if it[0] != 'l': rest.append(it); continue
                if it[3][0] & it[3][1] == 0: continue
                im = Image.new('L', (full.shape[1], full.shape[0]), 0); ImageDraw.Draw(im).line([(x - .5, y - .5) for x, y in it[2]], fill=255)
                pts = np.nonzero(np.array(im)); cm = full == (it[3][0] & it[3][1]); near = cm.copy()                  # the demo's line and PIL's differ by a pixel here and there: look in the 3x3 neighbourhood
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1): near |= np.roll(np.roll(cm, dy, 0), dx, 1)
                if len(pts[0]) and np.mean(near[pts]) >= 0.6: tops.append(it)
            items = rest + tops
        out = bytearray()
        for kind, e, v, ct, fl in items:
            if kind == 'p': out += struct.pack('<BBB', 0, e, len(v)) + ct + struct.pack('<H', fl) + b''.join(struct.pack('<ff', x, y) for x, y in v)
            elif kind == 'c': out += struct.pack('<B4f', 4, *v)
            elif kind == 'l': out += struct.pack('<BBBB', 1, e, ct[0], ct[1]) + b''.join(struct.pack('<ff', x, y) for x, y in v)
            else: out += struct.pack('<BBH', 2, e, len(v)) + b''.join(struct.pack('<HHB', x, y, c) for (x, y), c in v.items())
        n = len(items)
        if start is not None and items and full.shape == (200, 320) and any(it[1] == 1 for it in items):          # planar picture: pixels the demo drew after the vector layer cover it
            offs = start + np.arange(200)[:, None] * 40 + np.arange(40)[None, :]
            vs = np.array(self.vec_seq)[offs & 0xFFFF]; bs = np.array(self.bg_seq)[offs & 0xFFFF]
            flag = np.repeat((bs > vs) & (vs > 0), 8, axis=1) & (full == bg)
            from PIL import ImageFilter
            cov = Image.new('L', (320, 200), 0); cd = ImageDraw.Draw(cov)                    # only where a vector polygon/line is (grown by a pixel)
            for it in items:
                if it[0] == 'p' and it[1] == 1 and len(it[2]) >= 3: cd.polygon([(x - .5, y - .5) for x, y in it[2]], fill=255)
                elif it[0] == 'l': cd.line([(x - .5, y - .5) for x, y in it[2]], fill=255)
            flag &= np.array(cov.filter(ImageFilter.MaxFilter(3))) > 0
            if flag.sum() >= 400: out += struct.pack('<B', 3) + np.packbits(flag, axis=1).tobytes(); n += 1
        return struct.pack('<H', n) + bytes(out)


def points_to_pixels(page, pts, pitch=40):
    """(x, y, colour) for the pixels a point primitive wrote: write mode 3 plots the bits of (cpu data AND bit mask) in the set/reset colour"""
    out = []
    for off, byte, gc0, seq2, gc3, gc5, gc8 in pts:
        rel = off - page; y, xb = divmod(rel, pitch)
        bits = byte & gc8 if (gc5 & 3) == 3 else byte
        for b in range(8):
            if (bits >> (7 - b)) & 1: out.append((xb * 8 + b, y, gc0 & 15))
    return out
