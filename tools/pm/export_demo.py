"""Turn a recording (record_full.py output) into the web files.
  video.bin : 'CD2V', u32 frames, u16 chunk, u32 chunks, u32 offsets[chunks+1]; chunk = raw deflate of, per frame: u16 w, u16 h, 768 palette bytes (XOR previous frame in the chunk),
              w*h pixels (XOR previous frame when the size is unchanged)
  index.bin : per frame: f32 t (seconds), f32 x0, y0, w, h (picture rectangle on the 4:3 screen, 0..1), u8 overscan colour
  audio.flac: the Mono-Lpt sample stream, unsigned 8 bit at the demo's own mixing rate, losslessly compressed (needs the `flac` program)
usage: export_demo.py RECDIR OUTDIR"""
import sys, os, glob, zlib, struct, wave, json, subprocess
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from view import load_chunk
from vga import timing
PIT_HZ = 1193182.0
CH = 30
class V:                                    # minimal register holder so timing() can be reused on recorded registers
    pass
def regs_to_vga(r):
    v = V(); r = bytes(r); v.crtc = list(r[:25]); v.seq = list(r[25:33]); v.gc = list(r[33:42]); v.ac = list(r[42:63]); v.misc = r[63]; return v
FLY_H = float(os.environ.get('FLY_H', 6.4e-6)); FLY_V = float(os.environ.get('FLY_V', 1.56e-3))
def rect(t, w, h):
    """where the picture sits on the screen, as fractions: the monitor sweeps (line time - horizontal flyback) wide and (frame time - vertical flyback) high"""
    char_s = t['dpc'] * t['half'] / t['dot']
    sw = t['line_s'] - FLY_H; sh = t['frame_s'] - FLY_V
    hx0 = max(0, t['hbe'] - t['ht']) * char_s                       # blanking that wraps past the end of the line eats into the start of the visible line
    vx0 = max(0, t['vbe'] - t['vt']) * t['line_s']
    act_w = w / (2 if t['pel2'] else 1) / 8 * char_s if False else t['hd'] * char_s
    act_h = h * t['maxscan'] * (2 if t['dbl'] else 1) * t['line_s']
    return (hx0 / sw, vx0 / sh, act_w / sw, act_h / sh)
if __name__ == '__main__':
    rec, out = sys.argv[1], sys.argv[2]; os.makedirs(out, exist_ok=True)
    files = sorted(glob.glob(os.path.join(rec, 'c*.npz'))); frames = []; idx = bytearray(); blobs = []; n = 0
    buf = bytearray(); prevpal = None; prev = None; cnt = 0
    def end_chunk():
        global buf
        co = zlib.compressobj(9, zlib.DEFLATED, -15); blobs.append(co.compress(bytes(buf)) + co.flush()); buf = bytearray()
    for f in files:
        fr, pal, regs, ts = load_chunk(f)
        for i in range(len(fr)):
            a = fr[i]; h, w = a.shape; p = pal[i].reshape(-1).astype(np.uint8)
            if cnt % CH == 0: prevpal = None; prev = None
            buf += struct.pack('<HH', w, h) + (p if prevpal is None else p ^ prevpal).tobytes(); prevpal = p
            flat = a.reshape(-1)
            buf += (flat if prev is None or prev.shape != flat.shape else flat ^ prev).tobytes(); prev = flat
            t = timing(regs_to_vga(regs[i])); r = rect(t, w, h)
            idx += struct.pack('<fffffB', float(ts[i] / PIT_HZ), r[0], r[1], r[2], r[3], t['overscan'] & 255)
            cnt += 1
            if cnt % CH == 0: end_chunk()
    if cnt % CH: end_chunk()
    head = b'CD2V' + struct.pack('<IHI', cnt, CH, len(blobs)); off = len(head) + 4 * (len(blobs) + 1); offs = []
    for b in blobs: offs.append(off); off += len(b)
    offs.append(off)
    open(os.path.join(out, 'video.bin'), 'wb').write(head + struct.pack('<%dI' % len(offs), *offs) + b''.join(blobs))
    open(os.path.join(out, 'index.bin'), 'wb').write(idx)
    lpt = np.load(os.path.join(rec, 'lpt.npy'))
    if len(lpt):
        rate = int(round((len(lpt) - 1) / ((lpt[-1, 0] - lpt[0, 0]) / PIT_HZ)))
        wf = wave.open(os.path.join(out, 'audio.wav'), 'wb'); wf.setnchannels(1); wf.setsampwidth(1); wf.setframerate(rate); wf.writeframes(lpt[:, 1].astype(np.uint8).tobytes()); wf.close()
        subprocess.run(['flac', '-8', '-s', '-f', '-o', os.path.join(out, 'audio.flac'), os.path.join(out, 'audio.wav')], check=True); os.remove(os.path.join(out, 'audio.wav'))
        meta = dict(frames=cnt, audio_rate=rate, audio_t0=float(lpt[0, 0] / PIT_HZ), samples=int(len(lpt)))
        if os.environ.get('CHAPTERS'): meta.update(json.load(open(os.environ['CHAPTERS'])))            # {"title": ..., "chapters": [[seconds from the first frame, name], ...]}
        json.dump(meta, open(os.path.join(out, 'meta.json'), 'w'))
    print('frames', cnt, 'video bytes', off, 'audio samples', len(lpt))
