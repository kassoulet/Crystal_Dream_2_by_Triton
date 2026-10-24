"""Record the whole demo: every virtual retrace (picture indices, palette, VGA registers) and the Mono-Lpt sample stream.
When the demo reaches its main menu (it waits for a key again after the setup), the recording of the run stops a few seconds later, and the process forks one
child per menu entry that presses the keys for that entry and records what follows.   usage: record_full.py OUTDIR MAX_SECONDS [no-menu]"""
import sys, os, time, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap, primlog
OUT = sys.argv[1]; SECONDS = float(sys.argv[2]); MENU = 'menu' in sys.argv; os.makedirs(OUT, exist_ok=True)
SNAP_EVERY = float(os.environ.get('SNAP_EVERY', 0)); LPT_EVERY = 20
m = Demo(mem=0x10000000)
SNAPSHOT = os.environ.get('SNAPSHOT')
if SNAPSHOT: snap.load(m, SNAPSHOT)             # resume from a VM snapshot (testing a scene)
else: m.boot()
P = primlog.PrimLog(m) if os.environ.get('PRIMLOG', '1') == '1' else None       # vector layer log (see primlog.py)
DOWN = [0xe0, 0x50, 0xe0, 0xd0]
SCRIPT = [] if os.environ.get('SNAPSHOT') else [DOWN, DOWN, [0x1c, 0x9c], DOWN, DOWN, DOWN, [0x1c, 0x9c]]          # Mono Lpt 1, 44000 Hz
CH = 60
REC_FROM = float(os.environ.get('REC_FROM', 0))          # virtual second where recording starts (the run before it only warms the vector log up)
class Rec:
    def __init__(self, d):
        self.d = d; os.makedirs(d, exist_ok=True); self.frames = []; self.pals = []; self.regs = []; self.T = []; self.n = 0
        self.vids = []; self.bgs = []; self.pics = []; self.vid_n = 0; self.lastkey = None; self.lastid = -1; self.tex_sent = 0; self.lut_sent = 0
    def vec(self, p, full):
        """vector layer of the picture being shown: id of its packed list (-1 none), and the picture without the vector layer when that differs"""
        v = p.vga; chain4 = bool(v.chain4); start = (v.crtc[12] << 8) | v.crtc[13]
        lst = getattr(P, 'shown_a', []) if chain4 else P.pages.get(start, [])
        bg = P.bg.frame()
        if bg.shape != full.shape: self.vids.append(-1); self.bgs.append(None); self.lastkey = None; return          # shadow VGA not in the same mode (a mode change it missed): no vector layer for this frame
        key = (id(lst), len(lst), chain4)
        if key != self.lastkey:
            self.lastkey = key; self.lastlst = lst;      # (keeping the list alive so its id cannot be reused by another list of the same length)
            self.lastid = -1; self.lastbg = None
            prims = P.frame_prims(start, chain4)
            if prims:
                mask = (full != 0) if chain4 else (full != bg)
                if mask.any() and P.recall(prims, mask) >= 0.75:
                    blob = P.picture(prims, full, full if chain4 else bg, None if chain4 else start)
                    if blob is None: self.vids.append(-1); self.bgs.append(None); return
                    self.lastid = self.vid_n; self.vid_n += 1
                    # (the 4th argument `start` also keeps an overlay mask of what the demo drew after the vector layer, where that is a lot: it erases old shapes by writes of its own)
                    self.pics.append((self.lastid, zlib.compress(blob, 6)))
                    if prims[0]['e'] == 'A':       # engine A: no separate background in a RAM-buffer picture, and stale pixels under the polygons otherwise: clear what the polygons cover
                        self.lastbg = (full if chain4 else bg).copy(); self.lastbg[P.cover(prims, full.shape)] = 0
        self.vids.append(self.lastid)
        self.bgs.append(self.lastbg if self.lastbg is not None else None if chain4 or self.lastid < 0 or (bg == full).all() else bg.copy())
    def add(self, p):
        if p.vclock < REC_FROM * PIT_HZ: return
        full = p.vga.frame().copy(); self.frames.append(full); self.pals.append(p.vga.dac.copy()); self.regs.append(p.vga.regs()); self.T.append(p.vclock)
        if P: self.vec(p, full)
        if len(self.frames) >= CH: self.flush()
    def flush(self):
        if not self.frames: return
        body = bytearray(); prev = None; meta = []
        for f in self.frames:
            meta.append(f.shape); a = f.reshape(-1); body += (a if prev is None or prev.shape != a.shape else a ^ prev).tobytes(); prev = a
        extra = {}
        if P:
            bb = bytearray(); prev = None
            for f in self.bgs:
                if f is None: continue
                a = f.reshape(-1); bb += (a if prev is None or prev.shape != a.shape else a ^ prev).tobytes(); prev = a
            ids = [i for i, b in self.pics]; blobs = [b for i, b in self.pics]
            tx = list(range(self.tex_sent, len(P.tex))); lt = list(range(self.lut_sent, len(P.luts)))
            tb = [zlib.compress(P.tex[i], 6) for i in tx]
            extra = dict(vids=np.array(self.vids, np.int32), hasbg=np.array([f is not None for f in self.bgs]), bg=np.frombuffer(zlib.compress(bytes(bb), 6), np.uint8),
                         bgshapes=np.array([f.shape for f in self.bgs if f is not None]).reshape(-1, 2),
                         pic_ids=np.array(ids, np.int32), pic_len=np.array([len(b) for b in blobs], np.int64), pic_blob=np.frombuffer(b''.join(blobs), np.uint8),
                         tex_ids=np.array(tx, np.int32), tex_len=np.array([len(b) for b in tb], np.int64), tex_blob=np.frombuffer(b''.join(tb), np.uint8),
                         lut_ids=np.array(lt, np.int32), lut_blob=np.frombuffer(b''.join(P.luts[i] for i in lt), np.uint8))
            self.tex_sent = len(P.tex); self.lut_sent = len(P.luts); self.vids = []; self.bgs = []; self.pics = []
        np.savez(os.path.join(self.d, 'c%05d.npz' % self.n), frames=np.frombuffer(zlib.compress(bytes(body), 6), np.uint8), shapes=np.array(meta), pal=np.array(self.pals), regs=np.array([np.frombuffer(r, np.uint8) for r in self.regs]), t=np.array(self.T), **extra)
        self.n += 1; self.frames = []; self.pals = []; self.regs = []; self.T = []
    def finish(self, lpt_from=0):
        self.flush(); np.save(os.path.join(self.d, 'lpt.npy'), np.array(m.lpt[lpt_from:], dtype=np.float64)); open(os.path.join(self.d, 'console.txt'), 'wb').write(m.console)
rec = Rec(OUT); m.record = rec.add
def in_readkey(): return m.pe() and m.reg('CS') == 0xc8 and 0xf2f <= m.reg('EIP') <= 0xf3a
t0 = time.time(); last = -1; menu_t = None; waits = []
def run_until(limit, stop_after_menu=None, keys=None):
    global last, menu_t
    keys = list(keys) if keys is not None else None
    while m.vclock < limit * PIT_HZ:
        if keys is not None: key_script(m, keys)
        else: key_script(m, SCRIPT)
        m.tick()
        s = int(m.vclock / PIT_HZ)
        if s != last:
            last = s
            if s % LPT_EVERY == 0: np.save(os.path.join(OUT, 'lpt.npy'), np.array(m.lpt, dtype=np.float64))
            if SNAP_EVERY and s % int(SNAP_EVERY) == 0 and keys is None:
                os.makedirs(os.path.join(OUT, 'snaps'), exist_ok=True); snap.save(m, os.path.join(OUT, 'snaps', 's%04d.bin' % s))
            print('t=%ds frames=%d irq=%d lpt=%d wall=%.0fs cs:eip=%x:%x' % (s, m.vframes, m.irq_count, len(m.lpt), time.time() - t0, m.reg('CS'), m.reg('EIP')), flush=True)
        if keys is None and not SCRIPT and in_readkey() and not m.keys and menu_t is None and MENU and m.vclock > 90 * PIT_HZ: menu_t = m.vclock; waits.append(s); print('MAIN MENU reached at t=%.1f' % (m.vclock / PIT_HZ), flush=True)
        if menu_t is not None and m.vclock > menu_t + 8 * PIT_HZ: return 'menu'
    return 'time'
try: why = run_until(SECONDS)
except SystemExit as e: why = 'exit'; print('EXIT', e)
rec.finish(); print('primlog', P.stats if P else None); print('main run done:', why, 'frames', m.vframes, 'lpt', len(m.lpt), flush=True)
if why == 'menu':
    ENTRIES = ['credits', 'greetings', 'information', 'comments', 'messages', 'rumours']
    pids = []
    for i, name in enumerate(ENTRIES):
        pid = os.fork()
        if pid == 0:                                                         # child: this entry
            rec = Rec(os.path.join(OUT, 'menu_' + name)); m.record = rec.add; lpt0 = len(m.lpt); t_start = m.vclock
            keys = [DOWN] * i + [[0x1c, 0x9c]]
            try: run_until(m.vclock / PIT_HZ + 120, keys=keys)
            except SystemExit as e: print('EXIT', e)
            rec.finish(lpt0); print('child', name, 'done', flush=True); os._exit(0)
        pids.append(pid)
    for pid in pids: os.waitpid(pid, 0)
print('all done')
