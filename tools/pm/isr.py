import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import machine as M
from machine import *
m = Machine(mem=0x10000000); boot(m)
SCRIPT = [[0xe0, 0x50, 0xe0, 0xd0], [0xe0, 0x50, 0xe0, 0xd0], [0x1c, 0x9c], [0x1c, 0x9c]]
while m.ticks < 1000 and not m.pit_reload:
    if SCRIPT and not m.keys and m.reg('CS') == 0xc8 and 0xf2f <= m.reg('EIP') <= 0xf3a and m.ticks > getattr(m, 'next_key', 0): m.keys += SCRIPT.pop(0); m.next_key = m.ticks + 30
    m.step(); m.per_frame_irqs()
gd = m.mu.reg_read(UC_X86_REG_IDTR); idt = gd[1]
for vec in (0x10, 0x11, 0x12):
    g = m.r(idt + vec * 8, 8); off = g[0] | (g[1] << 8) | ((g[6] | (g[7] << 8)) << 16); sel = g[2] | (g[3] << 8)
    b, lim, acc, fl = seg_base(m, sel); print('vec', hex(vec), 'sel', hex(sel), 'off', hex(off), 'base', hex(b), 'type', hex(g[5]), 'fl', hex(fl))
    if vec == 0x10:
        open('/tmp/isr.bin', 'wb').write(m.r(b + off, 0x300)); print(subprocess.run(['ndisasm', '-b16', '-o', hex(off), '/tmp/isr.bin'], capture_output=True, text=True).stdout[:6000])
