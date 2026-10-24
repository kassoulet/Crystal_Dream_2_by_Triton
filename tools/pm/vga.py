"""VGA for the protected-mode harness: sequencer, CRTC, graphics controller (write modes 0-3, latches, read modes 0-1), attribute controller, DAC.
Memory window A0000-AFFFF only (the demo uses nothing else).  Chain-4 (mode 13h style) maps CPU address a to plane a&3, offset a>>2."""
import numpy as np

class VGA:
    def __init__(self):
        self.planes = [bytearray(0x10000) for _ in range(4)]
        self.seq = [0] * 8; self.seq[2] = 0xF; self.seq[4] = 0x6
        self.gc = [0] * 9; self.gc[8] = 0xFF
        self.crtc = [0] * 0x19
        self.ac = [0] * 0x15; self.ac_flip = 0; self.ac_idx = 0
        self.latch = [0] * 4
        self.seq_idx = 0; self.gc_idx = 0; self.crtc_idx = 0
        self.dac = np.zeros((256, 3), np.uint8); self.dac_w = 0; self.dac_r = 0; self.dac_sub = 0; self.dac_rsub = 0; self.dac_mask = 0xFF
        self.misc = 0
    # ---- port I/O
    def out(self, port, size, val):
        if size == 2 and port in (0x3c4, 0x3ce, 0x3d4):
            self.out(port, 1, val & 0xFF); self.out(port + 1, 1, (val >> 8) & 0xFF); return
        if port == 0x3c4: self.seq_idx = val & 7
        elif port == 0x3c5: self.seq[self.seq_idx] = val & 0xFF
        elif port == 0x3ce: self.gc_idx = val & 15
        elif port == 0x3cf:
            if self.gc_idx < 9: self.gc[self.gc_idx] = val & 0xFF
        elif port == 0x3d4: self.crtc_idx = val & 0x1F
        elif port == 0x3d5:
            if self.crtc_idx < 0x19: self.crtc[self.crtc_idx] = val & 0xFF
        elif port == 0x3c2: self.misc = val
        elif port == 0x3c0:
            if self.ac_flip == 0: self.ac_idx = val & 0x1F
            elif (self.ac_idx & 0x1F) < 0x15: self.ac[self.ac_idx & 0x1F] = val & 0xFF
            self.ac_flip ^= 1
        elif port == 0x3c6: self.dac_mask = val & 0xFF
        elif port == 0x3c7: self.dac_r = val & 0xFF; self.dac_rsub = 0
        elif port == 0x3c8: self.dac_w = val & 0xFF; self.dac_sub = 0
        elif port == 0x3c9:
            self.dac[self.dac_w, self.dac_sub] = val & 0x3F; self.dac_sub += 1
            if self.dac_sub == 3: self.dac_sub = 0; self.dac_w = (self.dac_w + 1) & 0xFF
    def inp(self, port):
        if port == 0x3c9:
            v = int(self.dac[self.dac_r, self.dac_rsub]); self.dac_rsub += 1
            if self.dac_rsub == 3: self.dac_rsub = 0; self.dac_r = (self.dac_r + 1) & 0xFF
            return v
        if port == 0x3c4: return self.seq_idx
        if port == 0x3c5: return self.seq[self.seq_idx]
        if port == 0x3ce: return self.gc_idx
        if port == 0x3cf: return self.gc[self.gc_idx] if self.gc_idx < 9 else 0
        if port == 0x3d4: return self.crtc_idx
        if port == 0x3d5: return self.crtc[self.crtc_idx] if self.crtc_idx < 0x19 else 0
        if port == 0x3c0: return self.ac_idx
        if port == 0x3c1: return self.ac[self.ac_idx & 0x1F] if (self.ac_idx & 0x1F) < 0x15 else 0
        if port == 0x3c6: return self.dac_mask
        if port == 0x3cc: return self.misc
        return 0
    # ---- memory window
    @property
    def chain4(self): return bool(self.seq[4] & 8)
    def read(self, off):
        off &= 0xFFFF
        if self.chain4:
            p = off & 3; o = off >> 2
            for q in range(4): self.latch[q] = self.planes[q][o]
            return self.planes[p][o]
        for q in range(4): self.latch[q] = self.planes[q][off]
        if self.gc[5] & 8:
            cc, care, r = self.gc[2] & 15, self.gc[7] & 15, 0xFF
            for q in range(4):
                if (care >> q) & 1: r &= ~(self.planes[q][off] ^ (0xFF if (cc >> q) & 1 else 0)) & 0xFF
            return r
        return self.planes[self.gc[4] & 3][off]
    def write(self, off, b):
        off &= 0xFFFF; b &= 0xFF
        if self.chain4:
            p = off & 3
            if (self.seq[2] >> p) & 1: self.planes[p][off >> 2] = b
            return
        gc = self.gc; mode = gc[5] & 3; func = (gc[3] >> 3) & 3; rot = gc[3] & 7; bm = gc[8]; mm = self.seq[2] & 0xF
        if rot and mode in (0, 3): b = ((b >> rot) | (b << (8 - rot))) & 0xFF
        for p in range(4):
            if not (mm >> p) & 1: continue
            lat = self.latch[p]
            if mode == 1: self.planes[p][off] = lat; continue
            if mode == 0: d = (0xFF if (gc[0] >> p) & 1 else 0) if (gc[1] >> p) & 1 else b
            elif mode == 2: d = 0xFF if (b >> p) & 1 else 0
            else: d = 0xFF if (gc[0] >> p) & 1 else 0
            r = d if func == 0 else (d & lat if func == 1 else (d | lat if func == 2 else d ^ lat))
            m = (b & bm) if mode == 3 else bm
            self.planes[p][off] = (r & m) | (lat & ~m & 0xFF)
    # ---- what the monitor would show
    def frame(self):
        """the active picture as colour indices: (lines, pixels).  Pixel and line counts come from the CRTC (character count, pixel doubling, scan multiplier)"""
        t = timing(self); c = self.crtc
        w = t['hd'] * 8 // (2 if t['pel2'] else 1)
        h = t['vd'] // (t['maxscan'] * (2 if t['dbl'] else 1))
        w = max(1, min(w, 800)); h = max(1, min(h, 480))
        start = (c[0x0c] << 8) | c[0x0d]
        dword = bool(c[0x14] & 0x40); word = not (c[0x17] & 0x40)
        pitch = c[0x13] * 2 * (4 if dword else (2 if word else 1))
        out = np.zeros((h, w), np.uint8); xs = np.arange(w)
        if self.chain4:
            lin = np.zeros(0x40000, np.uint8)
            for p in range(4): lin[p::4] = np.frombuffer(bytes(self.planes[p]), np.uint8)
            base = start * 4
            for y in range(h): out[y] = lin[(base + y * pitch + xs) & 0x3FFFF]
        elif t['pel2']:                                                                 # unchained 256 colours, 4 pixels per plane byte
            pl = [np.frombuffer(bytes(self.planes[p]), np.uint8) for p in range(4)]
            for y in range(h):
                for p in range(4): out[y, p::4] = pl[p][(start + y * pitch + np.arange((w + 3) // 4)) & 0xFFFF][:len(out[y, p::4])]
        else:                                                                           # 16 colours, 8 pixels per byte of each plane
            pl = [np.frombuffer(bytes(self.planes[p]), np.uint8) for p in range(4)]
            for y in range(h):
                r = np.zeros(w, np.uint8)
                for p in range(4): r |= (((pl[p][(start + y * pitch + (xs >> 3)) & 0xFFFF] >> (7 - (xs & 7))) & 1) << p).astype(np.uint8)
                out[y] = r
        return out

    def regs(self):
        return bytes(self.crtc[:0x19]) + bytes(self.seq[:8]) + bytes(self.gc[:9]) + bytes(self.ac[:0x15]) + bytes([self.misc & 255, self.dac_mask])


def timing(v):
    """CRT timing from the registers: character clocks per line, scan lines per frame, active/blank windows (in chars and lines), dot clock"""
    c = v.crtc
    ht = c[0] + 5; hd = c[1] + 1; hbs = c[2]
    hbe = (c[3] & 0x1F) | ((c[5] & 0x80) >> 2); hbe_full = (hbs & ~0x3F) | hbe
    if hbe_full <= hbs: hbe_full += 0x40
    vt = c[6] | ((c[7] & 1) << 8) | ((c[7] & 0x20) << 4); vt += 2
    vd = c[0x12] | ((c[7] & 2) << 7) | ((c[7] & 0x40) << 3); vd += 1
    vbs = c[0x15] | ((c[7] & 8) << 5) | ((c[9] & 0x20) << 4)
    vbe = c[0x16]; vbe_full = (vbs & ~0xFF) | vbe
    if vbe_full <= vbs: vbe_full += 0x100
    dot = 28322000.0 if (v.misc >> 2) & 3 == 1 else 25175000.0
    dpc = 9 if not (v.seq[1] & 1) else 8
    half = 2 if (v.seq[1] & 8) else 1
    return dict(ht=ht, hd=hd, hbs=hbs, hbe=hbe_full, vt=vt, vd=vd, vbs=vbs, vbe=vbe_full, dot=dot, dpc=dpc, half=half,
                line_s=ht * dpc * half / dot, frame_s=vt * ht * dpc * half / dot, maxscan=(c[9] & 31) + 1, dbl=bool(c[9] & 0x80),
                pel2=bool(v.ac[0x10] & 0x40), overscan=v.ac[0x11])
