"""Frame-stepped machine around dosrun.PC: runs the demo to each retrace edge, then delivers the timer IRQ0 (PIT rate programmed by the demo) and
keyboard IRQ1 through the demo's own IDT, the way a PIC would."""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dosrun as D
from dosrun import *

class Machine(D.PC):
    FPS = 70.0
    def __init__(self, **kw):
        super().__init__(**kw)
        self.pit_reload = 0; self.pit_lohi = 0; self.pit_ctrl = 0; self.irq0_acc = 0.0
        self.pic_master = 0x08; self.pic_state = 0; self.keys = []; self.last_scan = 0; self.lpt = []; self.irq_count = 0
        self.stop_requested = False; self.ticks = 0
        self.vga_frame_cb = None
    # --- extra ports
    def _out(self, mu, port, size, val, ud):
        if port == 0x43: self.pit_ctrl = val; self.pit_lohi = 0
        elif port == 0x40:
            if self.pit_lohi == 0: self.pit_reload = (self.pit_reload & 0xFF00) | (val & 0xFF); self.pit_lohi = 1
            else: self.pit_reload = (self.pit_reload & 0xFF) | ((val & 0xFF) << 8); self.pit_lohi = 0
            self.pit_set = True
        elif port == 0x20 and val & 0x10: self.pic_state = 1                     # ICW1 to the master
        elif port == 0x21 and self.pic_state == 1: self.pic_master = val & 0xF8; self.pic_state = 0     # ICW2
        elif port == 0x378: self.lpt.append((self.frames, self.irq_count, val & 0xFF))
        super()._out(mu, port, size, val, ud)
    def _in(self, mu, port, size, ud):
        if port == 0x60: return self.last_scan
        v = super()._in(mu, port, size, ud)
        return v
    def _in_edge_hook(self):
        pass
    # --- cpu state helpers
    def pe(self): return bool(self.reg('CR0') & 1)
    def cs_base(self):
        return seg_base(self, self.reg('CS'))[0] if self.pe() else self.reg('CS') << 4
    def pos(self): return self.cs_base() + self.reg('EIP')                      # linear address (for code hooks)
    def begin(self): return self.reg('CS') * 16 + self.reg('EIP')     # Unicorn (UC_MODE_16) sets EIP = begin - CS*16 whatever the real mode
    def desc(self, sel):
        base, lim, acc, fl = seg_base(self, sel); return base, fl
    # --- interrupt delivery (PM, same privilege, IDT gate)
    def deliver(self, vec):
        gd = self.mu.reg_read(UC_X86_REG_IDTR); idt = gd[1]
        g = self.r(idt + vec * 8, 8); off = g[0] | (g[1] << 8); sel = g[2] | (g[3] << 8); typ = g[5] & 0x1F
        if typ in (0xE, 0xF): off |= (g[6] | (g[7] << 8)) << 16; wide = True
        elif typ in (0x6, 0x7): wide = False
        else: raise RuntimeError('bad gate %x for vector %x' % (typ, vec))
        ss_base, ss_fl = self.desc(self.reg('SS')); big = bool(ss_fl & 0x40)
        sp = self.reg('ESP') if big else self.reg('SP')
        fl = self.reg('EFLAGS'); cs = self.reg('CS'); ip = self.reg('EIP')
        if wide: frame = struct.pack('<III', ip, cs, fl); sp -= 12
        else: frame = struct.pack('<HHH', ip & 0xFFFF, cs, fl & 0xFFFF); sp -= 6
        self.w(ss_base + sp, frame); self.setreg('ESP' if big else 'SP', sp)
        self.setreg('CS', sel); self.setreg('EIP', off)
        self.setreg('EFLAGS', self.reg('EFLAGS') & ~(0x4000 | 0x100 | (0x200 if typ in (0xE, 0x6) else 0)))   # gates clear NT and TF; interrupt gates also IF
    def run_isr(self, vec, limit=2_000_000):
        resume = self.pos(); saved_if = self.reg('EFLAGS') & 0x200
        self.deliver(vec); entry = self.begin(); done = [False]
        h = self.mu.hook_add(UC_HOOK_CODE, lambda m, a, s, u: (done.__setitem__(0, True), m.emu_stop()), None, resume, resume)
        self.in_isr = True
        try: self.mu.emu_start(entry, 0xFFFFFFFF, count=limit)
        finally: self.mu.hook_del(h); self.in_isr = False
        if not done[0]: raise RuntimeError('ISR %x did not return (at %x:%x)' % (vec, self.reg('CS'), self.reg('EIP')))
    def can_interrupt(self): return self.pe() and (self.reg('EFLAGS') & 0x200)
    # --- retrace edge: end the slice
    def _in_3da(self):
        pass
    SLICE = 1_200_000
    def step(self):
        """run to the next retrace edge, or for SLICE instructions (a spinning wait on a counter or a key): one tick of virtual time"""
        start = self.frames
        self.on_frame = lambda p: p.mu.emu_stop()
        self.mu.emu_start(self.begin(), 0xFFFFFFFF, count=self.SLICE)
        self.ticks += 1
        return self.frames > start
    def per_frame_irqs(self):
        if not self.can_interrupt(): return
        if self.keys:
            self.last_scan = self.keys.pop(0); self.run_isr(self.pic_master + 1)
        if self.pit_reload or getattr(self, 'pit_set', False):
            freq = 1193182.0 / (self.pit_reload or 65536)
            self.irq0_acc += freq / self.FPS
            while self.irq0_acc >= 1.0 and self.can_interrupt():
                self.irq0_acc -= 1.0; self.irq_count += 1; self.run_isr(self.pic_master)

def boot(m):
    m.start(); m.mu.reg_write(UC_X86_REG_CS, 0x1000)
    m.on_frame = lambda p: p.mu.emu_stop()
    m.mu.emu_start(lin(0x1000, 0x151), 0xFFFFFFFF, count=m.SLICE); m.ticks += 1

if __name__ == '__main__':
    from PIL import Image
    m = Machine(mem=0x10000000); boot(m)
    keys = {}                                   # tick -> scancodes to press
    SCRIPT = [[0xe0, 0x50, 0xe0, 0xd0], [0xe0, 0x50, 0xe0, 0xd0], [0x1c, 0x9c], [0x1c, 0x9c]]        # Down, Down, Enter at the setup menu: 'Mono Lpt 1' (samples go to port 378h)
    N = int(sys.argv[1]); out = sys.argv[2]
    try:
     while m.ticks < N:
          if SCRIPT and not m.keys and m.reg('CS') == 0xc8 and 0xf2f <= m.reg('EIP') <= 0xf3a and m.ticks > getattr(m, 'next_key', 0): m.keys += SCRIPT.pop(0); m.next_key = m.ticks + 30
          m.step(); m.per_frame_irqs()
          if m.ticks % 200 == 0: print('tick', m.ticks, 'frames', m.frames, 'irq', m.irq_count, 'cs:eip', hex(m.reg('CS')), hex(m.reg('EIP')), 'pit', m.pit_reload, 'lpt', len(m.lpt), flush=True)
    except SystemExit as e: print('EXIT', e)
    f = m.vga.frame(); Image.fromarray(np.clip(m.vga.dac[f].astype(int) * 4, 0, 255).astype(np.uint8)).save(out)
    for k, v in m.log.most_common(10): print(v, k)
    print('CONSOLE:', m.console)
