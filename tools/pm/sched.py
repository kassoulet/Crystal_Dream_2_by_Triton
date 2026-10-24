"""Time-driven machine: the demo's own protected-mode extender, timer ISR, keyboard ISR and mixer run under Unicorn with a virtual clock
counted in 8254 PIT counts (1193182 Hz).  Main code and the IRQ0 handler alternate the way a CPU of CPU_IPS instructions per second would run them;
the VGA retrace flag and the display sampling follow the clock (70.0865 Hz).  Everything the demo draws and plays is produced by its own code."""
import sys, os, struct, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dosrun as D
from dosrun import *
from vga import timing

PIT_HZ = 1193182.0
FRAME = PIT_HZ / 70.0865          # counts per video frame (mode 13h / X: 449 lines at 31.47 kHz)
LINE = FRAME / 449.0
CPU_IPS = float(os.environ.get('CPU_IPS', 30e6))

class Demo(D.PC):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.vclock = 0.0; self.next_frame = FRAME; self.vframes = 0; self._tim = None; self.frame_c = FRAME; self.line_c = LINE
        self.pit_reload = 0; self.pit_hi = False; self.pit_prog = False; self.pit_latch = None; self.pit_rd = 0; self.polls = 0; self.extra = 0.0; self.irq_acc = 0.0
        self.pic_master = 0x08; self.pic_state = 0; self.keys = []; self.last_scan = 0
        self.lpt = []; self.irq_count = 0; self.record = None; self.in_isr = False; self.reprogrammed = False
    # ---------------- ports
    def refresh_timing(self):
        t = timing(self.vga)
        if t['frame_s'] > 0.004 and t['line_s'] > 1e-5 and t['vt'] > 100: self.frame_c = t['frame_s'] * PIT_HZ; self.line_c = self.frame_c / t['vt']
        else: self.frame_c = FRAME; self.line_c = LINE
        self._tim = False
    def _in(self, mu, port, size, ud):
        if port == 0x3da:
            if self._tim is None or self._tim: self.refresh_timing()
            FRAME_, LINE_ = self.frame_c, self.line_c
            self.extra += 1.2                                         # an ISA port read takes about a microsecond
            now = self.vclock + self.extra
            t = now % FRAME_; ln = now % LINE_
            v = 0
            if t < 2 * LINE_: v |= 8                      # vertical retrace pulse (2 lines)
            if ln < 0.22 * LINE_ or t < 2 * LINE_: v |= 1   # horizontal blank / retrace: display disabled
            self.vga.ac_flip = 0
            return v
        if port == 0x60: return self.last_scan
        if port == 0x379: return 0xDF                    # LPT status: not busy
        if port == 0x40:                                   # 8254 counter 0: latched on the control word 'latch', then low byte, high byte
            if self.pit_latch is None:
                R = self.pit_reload or 65536; self.pit_latch = R - int((self.vclock + self.extra) % R); self.pit_rd = 0
            v = (self.pit_latch >> (8 * self.pit_rd)) & 0xFF; self.pit_rd ^= 1
            if self.pit_rd == 0: self.pit_latch = None
            return v
        return super()._in(mu, port, size, ud)
    def _out(self, mu, port, size, val, ud):
        if 0x3c0 <= port <= 0x3d5: self._tim = True
        if port == 0x43:
            self.pit_hi = False
            if (val >> 6) == 0 and ((val >> 4) & 3) == 0:           # latch command for counter 0
                R = self.pit_reload or 65536; self.pit_latch = R - int((self.vclock + self.extra) % R); self.pit_rd = 0
        elif port == 0x40:
            if not self.pit_hi: self.pit_reload = (self.pit_reload & 0xFF00) | (val & 0xFF); self.pit_hi = True
            else:
                self.pit_reload = (self.pit_reload & 0xFF) | ((val & 0xFF) << 8); self.pit_hi = False; self.pit_prog = True
                if not self.in_isr: self.reprogrammed = True; self.mu.emu_stop()          # the next IRQ0 time changes: end the slice
            if self.pit_reload == 0 and self.pit_prog: pass
        elif port == 0x20 and val & 0x10: self.pic_state = 1
        elif port == 0x21 and self.pic_state == 1: self.pic_master = val & 0xF8; self.pic_state = 0
        elif port == 0x378: self.lpt.append((self.vclock, val & 0xFF))
        return super()._out(mu, port, size, val, ud)
    # ---------------- interrupts
    def pe(self): return bool(self.reg('CR0') & 1)
    def can_interrupt(self): return self.pe() and bool(self.reg('EFLAGS') & 0x200)
    def begin(self): return self.reg('CS') * 16 + self.reg('EIP')          # UC_MODE_16: EIP = begin - CS*16
    def deliver(self, vec):
        gd = self.mu.reg_read(UC_X86_REG_IDTR); idt = gd[1]
        g = self.r(idt + vec * 8, 8); off = g[0] | (g[1] << 8); sel = g[2] | (g[3] << 8); typ = g[5] & 0x1F
        if typ in (0xE, 0xF): off |= (g[6] | (g[7] << 8)) << 16; wide = True
        elif typ in (0x6, 0x7): wide = False
        else: raise RuntimeError('bad gate %x for vector %x' % (typ, vec))
        ss_base, _, _, ss_fl = seg_base(self, self.reg('SS')); big = bool(ss_fl & 0x40)
        sp = self.reg('ESP') if big else self.reg('SP')
        fl = self.reg('EFLAGS'); cs = self.reg('CS'); ip = self.reg('EIP')
        if wide: frame = struct.pack('<III', ip, cs, fl); sp -= 12
        else: frame = struct.pack('<HHH', ip & 0xFFFF, cs, fl & 0xFFFF); sp -= 6
        self.w(ss_base + sp, frame); self.setreg('ESP' if big else 'SP', sp)
        self.setreg('CS', sel); self.setreg('EIP', off)
        self.setreg('EFLAGS', self.reg('EFLAGS') & ~(0x4000 | 0x100 | (0x200 if typ in (0xE, 0x6) else 0)))
    def run_isr(self, vec, limit=3_000_000):
        resume = seg_base(self, self.reg('CS'))[0] + self.reg('EIP'); done = [False]
        self.deliver(vec); self.in_isr = True
        h = self.mu.hook_add(UC_HOOK_CODE, lambda m, a, s, u: (done.__setitem__(0, True), m.emu_stop()), None, resume, resume)
        try: self.mu.emu_start(self.begin(), 0xFFFFFFFF, count=limit)
        finally: self.mu.hook_del(h); self.in_isr = False
        if not done[0]: raise RuntimeError('ISR %x did not return (at %x:%x)' % (vec, self.reg('CS'), self.reg('EIP')))
    # ---------------- time
    def advance(self, counts):
        self.vclock += counts
        while self.vclock >= self.next_frame:
            self.vframes += 1
            if self._tim is None or self._tim: self.refresh_timing()
            self.next_frame += self.frame_c
            if self.record: self.record(self)
    def tick(self):
        """one scheduling step: main code for one IRQ0 period (or a 100000-instruction slice before the timer runs), then the IRQ0 handlers that fell due"""
        irq_on = self.pit_prog and self.can_interrupt()
        R = (self.pit_reload or 65536)
        per_instr = PIT_HZ / CPU_IPS
        B = max(50, int(R / per_instr)) if irq_on else 100_000
        self.reprogrammed = False; self.extra = 0.0
        self.mu.emu_start(self.begin(), 0xFFFFFFFF, count=B)
        elapsed = B * per_instr + self.extra
        if self.reprogrammed: elapsed = self.extra + 1.0                # PIT written inside the slice: the time is only what the reads cost
        self.advance(elapsed)
        if self.keys and self.can_interrupt(): self.last_scan = self.keys.pop(0); self.run_isr(self.pic_master + 1)
        if irq_on and not self.reprogrammed:
            self.irq_acc += elapsed
            while self.irq_acc >= R and self.can_interrupt():
                self.irq_acc -= R; self.irq_count += 1; self.run_isr(self.pic_master)
            if self.irq_acc > 4 * R: self.irq_acc = 0.0
    def boot(self):
        self.start(); self.setreg('CS', 0x1000); self.setreg('EIP', 0x151)

def key_script(m, script, gap=0.25):
    """feed scancode groups whenever the demo sits in its ReadKey loop (unit 3d0d: 0xF2F..0xF3A)"""
    if script and not m.keys and m.pe() and m.reg('CS') == 0xc8 and 0xf2f <= m.reg('EIP') <= 0xf3a and m.vclock > getattr(m, 'next_key', 0):
        m.keys += script.pop(0); m.next_key = m.vclock + gap * PIT_HZ

if __name__ == '__main__':
    from PIL import Image
    m = Demo(mem=0x10000000); m.boot()
    DOWN = [0xe0, 0x50, 0xe0, 0xd0]
    SCRIPT = [DOWN, DOWN, [0x1c, 0x9c], DOWN, DOWN, DOWN, [0x1c, 0x9c]]      # Mono Lpt 1; 44000 Hz
    shots = {}; T = float(sys.argv[1]); out = sys.argv[2]; t0 = time.time(); last = 0
    def rec(p):
        if p.vframes % 70 == 0:
            f = p.vga.frame(); shots[p.vframes] = Image.fromarray(np.clip(p.vga.dac[f].astype(int) * 4, 0, 255).astype(np.uint8))
    m.record = rec
    try:
        while m.vclock < T * PIT_HZ:
            key_script(m, SCRIPT); m.tick()
            if int(m.vclock / PIT_HZ) != last: last = int(m.vclock / PIT_HZ); print('[6674]=%x [667c]=%x [6670]=%x [6662]=%x ' % tuple(struct.unpack('<H', m.r(0x42d20 + a, 2))[0] for a in (0x6674, 0x667c, 0x6670, 0x6662)), end=''); print('t=%ds frames=%d irq=%d pit=%d lpt=%d wall=%.0fs cs:eip=%x:%x' % (last, m.vframes, m.irq_count, m.pit_reload, len(m.lpt), time.time() - t0, m.reg('CS'), m.reg('EIP')), flush=True)
    except SystemExit as e: print('EXIT', e)
    print('CONSOLE', m.console)
    ks = sorted(shots)[-8:]
    W = Image.new('RGB', (320 * len(ks), 400))
    for i, k in enumerate(ks): W.paste(shots[k].resize((320, min(400, shots[k].size[1]))), (i * 320, 0))
    W.save(out)
