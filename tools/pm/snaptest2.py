import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sched import *
import snap
S = sys.argv[1]
m2 = Demo(mem=0x10000000); snap.load(m2, S + '/snap16.bin')
for r in ('CS','EIP','SS','ESP','DS','ES','FS','GS','CR0','EFLAGS'): print(r, hex(m2.reg(r)), end='  ')
print()
print('GDTR', [hex(x) for x in m2.mu.reg_read(UC_X86_REG_GDTR)], 'IDTR', [hex(x) for x in m2.mu.reg_read(UC_X86_REG_IDTR)])
for r in ('CS','SS','DS','ES','FS','GS'): print(r, [hex(x) for x in seg_base(m2, m2.reg(r))])
try:
    for i in range(400): m2.tick()
except Exception as e: print('EXC', repr(e)[:90]); b = seg_base(m2, m2.reg('CS'))[0]; print(hex(m2.reg('CS')), hex(m2.reg('EIP')), m2.r(b + m2.reg('EIP') - 8, 24).hex(' '))
for r in ('CS','EIP','SS','ESP','DS','ES','FS','GS','EAX','EBX','ECX','EDX','ESI','EDI'): print(r, hex(m2.reg(r)), end='  ')
