import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dosrun as D
from dosrun import *
from PIL import Image
OUT = sys.argv[1]; WANT = [int(x) for x in sys.argv[2].split(',')]; LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 5_000_000_000
pc = D.PC(mem=0x10000000); pc.start(); t0 = time.time(); shots = {}
def onf(p):
    if p.frames in WANT:
        f = p.vga.frame(); shots[p.frames] = Image.fromarray(np.clip(p.vga.dac[f].astype(int) * 4, 0, 255).astype(np.uint8))
        print('frame', p.frames, 'cs:eip', hex(p.reg('CS')), hex(p.reg('EIP')), 'chain4', p.vga.chain4, 'h', f.shape[0], '%.0fs' % (time.time() - t0), flush=True)
    if p.frames >= max(WANT): p.mu.emu_stop()
pc.on_frame = onf
pc.run(LIMIT)
print('done frames', pc.frames, hex(pc.reg('CS')), hex(pc.reg('EIP')), 'cr0', hex(pc.reg('CR0')), '%.0fs' % (time.time() - t0), flush=True)
W = Image.new('RGB', (320 * len(shots), 400))
for k, (n, im) in enumerate(sorted(shots.items())): W.paste(im.resize((320, min(400, im.size[1]))), (k * 320, 0))
W.save(OUT)
for k, v in pc.log.most_common(12): print(v, k)
