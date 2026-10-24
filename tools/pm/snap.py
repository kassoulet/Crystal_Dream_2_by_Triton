"""Snapshots of the virtual PC: guest memory (first 16 MB, deflated), the Unicorn CPU context, and the Python-side machine state (clock, PIT/PIC, VGA, files, XMS).
A snapshot taken at a scene start lets later work (hooking a scene's drawing primitives, checking an exporter) start there instead of at boot.
save(demo, path) / load(demo, path): `demo` is a freshly constructed sched.Demo (hooks are not part of the snapshot)."""
import os, pickle, zlib
import numpy as np

MEM = 0x1000000                                   # the demo never touches memory above 16 MB (8 MB XMS block + conventional memory + VGA window)
SKIP = {'mu', 'record', 'on_frame', 'int_handlers', 'files', 'lpt', 'log', 'trace', 'portlog', 'frames', 'console'}

def save(m, path):
    state = {k: v for k, v in m.__dict__.items() if k not in SKIP}
    state['_files'] = {h: (f.name, f.tell()) for h, f in m.files.items()}
    state['_console'] = m.console
    state['_lpt_len'] = len(m.lpt)
    ctx = m.mu.context_save()
    import ctypes
    raw = ctypes.string_at(ctypes.cast(ctx.context, ctypes.c_void_p).value, ctx.size)       # opaque CPU context incl. hidden segment descriptors
    blob = pickle.dumps(dict(state=state, ctx=raw, mem=zlib.compress(bytes(m.mu.mem_read(0, MEM)), 1)), protocol=4)
    with open(path, 'wb') as f: f.write(blob)

def load(m, path):
    with open(path, 'rb') as f: d = pickle.load(f)
    for k, v in d['state'].items():
        if k in ('_files', '_console', '_lpt_len'): continue
        setattr(m, k, v)
    m.console = d['state']['_console']
    m.files = {}
    for h, (name, pos) in d['state']['_files'].items():
        fh = open(name, 'rb'); fh.seek(pos); m.files[h] = fh
    m.mu.mem_write(0, zlib.decompress(d['mem']))
    import ctypes
    ctx = m.mu.context_save()                                           # allocate a context of the right size, overwrite its bytes, restore it
    ctypes.memmove(ctypes.cast(ctx.context, ctypes.c_void_p).value, d['ctx'], ctx.size)
    m.mu.context_restore(ctx)
