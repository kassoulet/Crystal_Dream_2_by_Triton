"""Boot the demo's unpacked image the way DOS would and let its own protected-mode extender run under Unicorn.
A small PC around it: DOS INT 21h/2Fh, XMS, BIOS stubs, PIC/PIT/keyboard/VGA/DMA ports.  Everything the demo does in protected mode
(selectors, 32-bit moves, its own GDT/IDT, real-mode round trips for DOS calls) is executed, not modelled."""
import sys, os, struct, collections
from unicorn import *
from unicorn.x86_const import *
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vga import VGA
import numpy as np
ROOT = os.path.join(HERE, '..', '..')
IMG = open(os.path.join(ROOT, 'tools', 'img.bin'), 'rb').read()
EXE = os.path.join(ROOT, 'CD2.EXE')
PSP = 0x0FF0; ENV = 0x0F00; STUBSEG = 0xF000
def lin(s, o): return (s << 4) + o

class PC:
    def __init__(self, mem=0x4000000):
        self.mu = mu = Uc(UC_ARCH_X86, UC_MODE_16)
        mu.mem_map(0, mem); self.mem = mem
        mu.mem_write(0x10000, IMG)
        self.log = collections.Counter(); self.trace = []
        self.console = b''; self.files = {}; self.next_h = 5; self.dos_blocks = {}
        self.xms = {'next': 0x110000, 'blocks': {}, 'handle': 1}
        self.ports = {}; self.portlog = []
        self.vga = VGA(); self.frames = 0; self.on_frame = None
        mu.hook_add(UC_HOOK_MEM_WRITE, lambda *a: self._vw(*a), None, 0xA0000, 0xAFFFF)         # dispatched through the instance so a logger can wrap them
        mu.hook_add(UC_HOOK_MEM_READ, lambda *a: self._vr(*a), None, 0xA0000, 0xAFFFF)
        self._build_dos_area()
        mu.hook_add(UC_HOOK_INSN, self._in, None, 1, 0, UC_X86_INS_IN)
        mu.hook_add(UC_HOOK_INSN, self._out, None, 1, 0, UC_X86_INS_OUT)
        self.int_handlers = {0x21: self.int21, 0x2f: self.int2f, 0x10: self.int10, 0x15: self.int15, 0x16: self.int16, 0x1a: self.int1a, 0x33: self.int33, 0x11: self.int11, 0x12: self.int12}
        mu.hook_add(UC_HOOK_INTR, self._intr)
        self.tick_reads = 0
        mu.hook_add(UC_HOOK_MEM_READ, self._tick_read, None, 0x46c, 0x46c)
        self.xms_entry = lin(STUBSEG, 0x800); mu.mem_write(self.xms_entry, b'\xcb')                      # far-call entry of the XMS driver: RETF after the hook
        mu.hook_add(UC_HOOK_CODE, lambda m, a, s, u: self.xms_call(), None, self.xms_entry, self.xms_entry)
    def _tick_read(self, mu, acc, addr, size, val, ud):         # every few reads of the BIOS tick the clock moves on, so the CRT unit's delay calibration finishes
        self.tick_reads += 1
        if self.tick_reads % 3 == 0: self.bump_bios_tick()
    def bump_bios_tick(self):                                  # BIOS timer tick (0040:006C): advanced by the retrace loop below and by the CRT unit's calibration loop
        t = struct.unpack('<I', self.r(0x46c, 4))[0]; self.w(0x46c, struct.pack('<I', (t + 1) & 0xFFFFFFFF))
    # ------------------------------------------------------------------ memory helpers
    def r(self, a, n): return bytes(self.mu.mem_read(a, n))
    def w(self, a, b): self.mu.mem_write(a, bytes(b))
    def reg(self, n): return self.mu.reg_read(getattr(__import__('unicorn.x86_const', fromlist=['x']), 'UC_X86_REG_' + n))
    def setreg(self, n, v): self.mu.reg_write(getattr(__import__('unicorn.x86_const', fromlist=['x']), 'UC_X86_REG_' + n), v)
    def _build_dos_area(self):
        mu = self.mu
        env = b'PATH=C:\\\0COMSPEC=C:\\COMMAND.COM\0' + (b'BLASTER=A220 I5 D1 T3\0' if os.environ.get('BLASTER') else b'') + b'\0' + struct.pack('<H', 1) + b'C:\\CD2.EXE\0'
        mu.write if False else None
        self.w(lin(ENV, 0), env)
        psp = bytearray(256); psp[0:2] = b'\xcd\x20'; struct.pack_into('<H', psp, 2, 0xA000); struct.pack_into('<H', psp, 0x2c, ENV)
        args = os.environ.get('CD2_ARGS', '').encode(); psp[0x80] = len(args) + (1 if args else 0); psp[0x81:0x81 + len(args) + 2] = (b' ' + args if args else b'') + b'\r'     # command tail: '3' starts at the endpart
        self.w(lin(PSP, 0), psp)
        self.w(0x408, struct.pack('<HH', 0x378, 0x278))                                         # BIOS data area: LPT1/LPT2 base ports
    # ------------------------------------------------------------------ interrupts (real mode)
    def _intr(self, mu, n, ud):
        """software INT in real mode: our DOS/BIOS services, or the vector the program installed itself"""
        pe = self.reg('CR0') & 1
        if not pe and n in self.int_handlers:
            self.log['int%02x' % n] += 1; ax = self.reg('AX'); self.int_handlers[n](ax >> 8, ax & 255); return
        if not pe:
            off, seg = struct.unpack('<HH', self.r(n * 4, 4)); self.log['int%02x->vector' % n] += 1
            sp = (self.reg('SP') - 6) & 0xFFFF; ss = self.reg('SS')
            self.w(lin(ss, sp), struct.pack('<HHH', self.reg('IP') & 0xFFFF, self.reg('CS'), self.reg('EFLAGS') & 0xFFFF)); self.setreg('SP', sp)
            self.setreg('CS', seg); self.setreg('EIP', off); return
        self.log['pm exception/int %d' % n] += 1; raise RuntimeError('protected-mode interrupt/exception %d at %x:%x' % (n, self.reg('CS'), self.reg('EIP')))
    def set_cf(self, cf):
        fl = self.reg('EFLAGS'); self.setreg('EFLAGS', (fl | 1) if cf else (fl & ~1))
    def set_flag_zf(self, z):
        fl = self.reg('EFLAGS'); self.setreg('EFLAGS', (fl | 0x40) if z else (fl & ~0x40))
    def int21(self, ah, al):
        mu = self.mu; ds = self.reg('DS'); dx = self.reg('DX'); bx = self.reg('BX'); cx = self.reg('CX')
        if ah == 0x30: self.setreg('AX', 0x0005 if True else 0); self.setreg('BX', 0); self.setreg('CX', 0)       # DOS 5.0
        elif ah == 0x62: self.setreg('BX', PSP)
        elif ah == 0x3d:
            name = self.r(lin(ds, dx), 80).split(b'\0')[0]; self.log['open ' + name.decode('latin1')] += 1
            h = self.next_h; self.next_h += 1; self.files[h] = open(EXE, 'rb'); self.setreg('AX', h); self.set_cf(0)
        elif ah == 0x3e: self.files.pop(bx, None); self.set_cf(0)
        elif ah == 0x3f:
            data = self.files[bx].read(cx); self.w(lin(ds, dx), data); self.setreg('AX', len(data)); self.set_cf(0)
        elif ah == 0x42:
            f = self.files[bx]; off = (cx << 16) | dx
            if off >= 0x80000000: off -= 0x100000000
            pos = f.seek(off, al); self.setreg('AX', pos & 0xFFFF); self.setreg('DX', pos >> 16); self.set_cf(0)
        elif ah == 0x35:                                                                      # get vector
            v = self.r(al * 4, 4); self.setreg('BX', struct.unpack('<H', v[:2])[0]); self.setreg('ES', struct.unpack('<H', v[2:])[0])
        elif ah == 0x25: self.w(al * 4, struct.pack('<HH', dx, ds))                           # set vector
        elif ah == 0x4c: raise SystemExit('DOS exit code %d' % al)
        elif ah == 0x09:
            t = bytearray(); a = lin(ds, dx)
            while True:
                c = self.r(a, 1)[0]
                if c == 0x24 or len(t) > 400: break
                t.append(c); a += 1
            self.console += bytes(t); self.log['print'] += 1
        elif ah == 0x02: self.console += bytes([self.reg('DX') & 255])
        elif ah == 0x40:
            if bx in (1, 2): self.console += self.r(lin(ds, dx), cx)
            self.setreg('AX', cx); self.set_cf(0)
        elif ah == 0x0e: self.setreg('AX', 3)
        elif ah in (0x19,): self.setreg('AX', 2)
        elif ah == 0x48:                                                                      # allocate paragraphs (conventional memory 5E20h..9FFFh, like a DOS arena)
            top = getattr(self, 'dos_top', 0x5E20)
            if bx > 0xA000 - top: self.setreg('AX', 8); self.setreg('BX', 0xA000 - top); self.set_cf(1)
            else: self.dos_top = top + bx; self.dos_blocks[top] = bx; self.setreg('AX', top); self.set_cf(0)
        elif ah == 0x49: self.dos_blocks.pop(self.reg('ES'), None); self.set_cf(0)
        elif ah == 0x4a:
            es = self.reg('ES')
            if es in self.dos_blocks and es + self.dos_blocks[es] == getattr(self, 'dos_top', 0): self.dos_top = es + bx; self.dos_blocks[es] = bx
            self.set_cf(0)
        else: self.log['int21 unhandled %02x' % ah] += 1; self.set_cf(1)
    def int2f(self, ah, al):
        ax = self.reg('AX')
        if ax == 0x4300: self.setreg('AX', 0x80)                                            # XMS driver present
        elif ax == 0x4310: self.setreg('BX', 0x0800); self.setreg('ES', STUBSEG)            # entry F000:0800
        elif ax == 0x1687: self.setreg('AX', 1)                                             # no DPMI
        else: self.log['int2f unhandled %04x' % ax] += 1
    BIOS = {                                   # standard BIOS register sets for the graphics modes the demo asks for with INT 10h AH=0
        0x0d: dict(misc=0x63, seq=[3, 9, 0x0f, 0, 6], crtc=[0x2d, 0x27, 0x28, 0x90, 0x2b, 0x80, 0xbf, 0x1f, 0, 0xc0, 0, 0, 0, 0, 0, 0, 0x9c, 0x8e, 0x8f, 0x14, 0, 0x96, 0xb9, 0xe3, 0xff],
                   gc=[0, 0, 0, 0, 0, 0x10, 5, 0x0f, 0xff], ac=[0, 1, 2, 3, 4, 5, 0x14, 7, 0x38, 0x39, 0x3a, 0x3b, 0x3c, 0x3d, 0x3e, 0x3f, 0x01, 0, 0x0f, 0, 0]),
        0x13: dict(misc=0x63, seq=[3, 1, 0x0f, 0, 0x0e], crtc=[0x5f, 0x4f, 0x50, 0x82, 0x54, 0x80, 0xbf, 0x1f, 0, 0x41, 0, 0, 0, 0, 0, 0, 0x9c, 0x8e, 0x8f, 0x28, 0x40, 0x96, 0xb9, 0xa3, 0xff],
                   gc=[0, 0, 0, 0, 0, 0x40, 5, 0x0f, 0xff], ac=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 0x41, 0, 0x0f, 0, 0])}
    def int10(self, ah, al):
        self.log['int10 %02x%02x' % (ah, al)] += 1
        if ah == 0 and (al & 0x7f) in self.BIOS:
            b = self.BIOS[al & 0x7f]; v = self.vga
            v.misc = b['misc']; v.seq[:5] = b['seq']; v.crtc[:25] = b['crtc']; v.gc[:9] = b['gc']; v.ac[:21] = b['ac']
            if not al & 0x80:
                for p in v.planes: p[:] = bytes(len(p))
            self._tim = True if hasattr(self, '_tim') else None

    def int15(self, ah, al):
        if ah == 0x88: self.setreg('AX', 0x3000); self.set_cf(0)
        elif ah == 0xc0: self.set_cf(1)
        else: self.log['int15 unhandled %02x' % ah] += 1; self.set_cf(1)
    def int16(self, ah, al):
        if ah in (1, 0x11): self.set_flag_zf(True)
        elif ah in (0, 0x10): self.setreg('AX', 0x1c0d)
    def int1a(self, ah, al): self.setreg('CX', 0); self.setreg('DX', 0)
    def int33(self, ah, al): self.setreg('AX', 0)
    def int11(self, ah, al): self.setreg('AX', 0x0021)
    def int12(self, ah, al): self.setreg('AX', 640)
    # ------------------------------------------------------------------ XMS
    def xms_call(self):
        ah = self.reg('AX') >> 8; x = self.xms; self.log['xms %02x' % ah] += 1
        if ah == 0x00: self.setreg('AX', 0x0300); self.setreg('BX', 0x0301); self.setreg('DX', 1)
        elif ah in (0x03, 0x05): self.setreg('AX', 1); self.setreg('BX', 0)               # A20 on
        elif ah in (0x04, 0x06): self.setreg('AX', 1); self.setreg('BX', 0)
        elif ah == 0x07: self.setreg('AX', 1)
        elif ah == 0x08: free = x.get('free', 0x2000); self.setreg('AX', free); self.setreg('DX', free)   # free KB: the RTL takes everything it is offered, so offer 8 MB once
        elif ah == 0x09:                                                                      # allocate EMB: DX = KB
            kb = self.reg('DX'); x['free'] = max(0, x.get('free', 0x2000) - kb); h = x['handle']; x['handle'] += 1; x['blocks'][h] = [x['next'], kb * 1024]; x['next'] += kb * 1024
            self.setreg('AX', 1); self.setreg('DX', h)
        elif ah == 0x0a: self.setreg('AX', 1)
        elif ah == 0x0c: self.setreg('AX', 1)                                                 # lock: DX:BX = linear address
        elif ah == 0x0d: self.setreg('AX', 1)
        elif ah == 0x0e: self.setreg('AX', 1)
        else: self.setreg('AX', 0); self.setreg('BX', 0x80)
        if ah == 0x0c:
            h = self.reg('DX'); a = x['blocks'][h][0]; self.setreg('DX', a >> 16); self.setreg('BX', a & 0xFFFF)
    # ------------------------------------------------------------------ ports
    def _vw(self, mu, acc, addr, size, val, ud):
        for k in range(size): self.vga.write(addr - 0xA0000 + k, (val >> (8 * k)) & 0xFF)
    def _vr(self, mu, acc, addr, size, val, ud):
        for k in range(size): b = self.vga.read(addr - 0xA0000 + k); mu.mem_write(addr + k, bytes([b]))
    def _in(self, mu, port, size, ud):
        self.log['in %03x' % port] += 1
        if 0x3c0 <= port <= 0x3cf or port in (0x3d4, 0x3d5): return self.vga.inp(port)
        if port == 0x3da:
            self.vga.ac_flip = 0; self.vt = getattr(self, 'vt', 0) ^ 1
            if self.vt and not getattr(self, 'in_isr', False):
                self.frames += 1
                if self.frames % 4 == 0: self.bump_bios_tick()
                if self.on_frame: self.on_frame(self)
            return 0x09 if self.vt else 0
        if port == 0x60: return 0
        if port == 0x64: return 0
        if port == 0x92: return 2
        if port in (0x21, 0xa1): return 0xff
        return 0
    def _out(self, mu, port, size, val, ud):
        if 0x3c0 <= port <= 0x3d5 and port not in (0x3da,): self.vga.out(port, size, val)
        else:
            self.log['out %03x' % port] += 1
            if port in (0x20, 0x21, 0xa0, 0xa1, 0x40, 0x41, 0x42, 0x43, 0x378, 0x379, 0x37a, 0x92, 0x61, 0x64, 0x70): self.portlog.append((port, val, self.frames))

    def start(self):
        mu = self.mu
        mu.reg_write(UC_X86_REG_DS, PSP); mu.reg_write(UC_X86_REG_ES, PSP); mu.reg_write(UC_X86_REG_SS, 0x5210); mu.reg_write(UC_X86_REG_SP, 0xC000)
        mu.reg_write(UC_X86_REG_CS, 0x1000)
    def run(self, n):
        try: self.mu.emu_start(lin(0x1000, 0x151), 0xFFFFFFF0, count=n)
        except UcError as e: print('UC error', e, 'cs:ip', hex(self.reg('CS')), hex(self.reg('EIP')))

if __name__ == '__main__':
    pc = PC(); pc.start(); pc.run(int(sys.argv[1]) if len(sys.argv) > 1 else 20_000_000)
    print('cs:eip', hex(pc.reg('CS')), hex(pc.reg('EIP')), 'cr0', hex(pc.reg('CR0')))
    for k, v in pc.log.most_common(40): print(v, k)

def seg_base(pc, sel):
    """linear base of a selector (GDT only), 0 in real mode semantics handled by the caller"""
    gdtr = pc.mu.reg_read(UC_X86_REG_GDTR); base = gdtr[1]
    d = pc.r(base + (sel & 0xFFF8), 8)
    return d[2] | (d[3] << 8) | (d[4] << 16) | (d[7] << 24), (d[0] | (d[1] << 8) | ((d[6] & 15) << 16)), d[5], d[6]
