import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dosrun as D
from dosrun import *
pc = D.PC(mem=0x10000000); pc.start()
N = int(sys.argv[1]); 
pc.run(N)
print('frames', pc.frames, 'cs:eip', hex(pc.reg('CS')), hex(pc.reg('EIP')), 'cr0', hex(pc.reg('CR0')), 'eflags', hex(pc.reg('EFLAGS')))
for k, v in pc.log.most_common(25): print(v, k)
cs = pc.reg('CS'); eip = pc.reg('EIP'); b, lim, acc, fl = seg_base(pc, cs)
print('cs base', hex(b), 'limit', hex(lim), 'acc', hex(acc), 'flags', hex(fl), 'image off', hex(b + eip - 0x10000))
print(pc.r(b + eip - 16, 48).hex(' '))
for r in ('EAX','EBX','ECX','EDX','ESI','EDI','EBP','ESP','DS','ES','SS','FS','GS'): print(r, hex(pc.reg(r)), end='  ')
print()
from PIL import Image
f = pc.vga.frame(); Image.fromarray(np.clip(pc.vga.dac[f].astype(int) * 4, 0, 255).astype(np.uint8)).save(sys.argv[2] if len(sys.argv) > 2 else '/tmp/probe.png')
print(pc.portlog[:60])
gd=pc.mu.reg_read(UC_X86_REG_IDTR); print('idtr',[hex(x) for x in gd])
print([x for x in pc.portlog if x[0] in (0x40,0x41,0x42,0x43,0x378,0x37a,0x61)][:30])
print('in 40/60/64:', {k:v for k,v in pc.log.items() if k.startswith('in 4') or k.startswith('in 6')})
