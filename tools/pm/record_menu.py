"""Start the demo at its endpart (command-line parameter 3), choose one entry of the main menu and record what the demo does.
usage: CD2_ARGS=3 record_menu.py OUTDIR ENTRY(0..6) SECONDS [extra scancode groups, e.g. 48:0xe0,0x50,0xe0,0xd0]"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('CD2_ARGS', '3')
sys.argv_saved = list(sys.argv)
from sched import *
import importlib
OUT = sys.argv[1]; ENTRY = int(sys.argv[2]); SECONDS = float(sys.argv[3])
# reuse the recorder class from record_full without running it
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'record_full.py')).read()
rec_src = src[src.index('class Rec:'):src.index('rec = Rec(OUT)')]
import zlib
CH = 60
P = None; REC_FROM = float(os.environ.get('REC_FROM', 19))               # no vector layer on the menu pages; the endpart's loading before the menu is not recorded as video (its audio is kept)
exec(rec_src)
m = Demo(mem=0x10000000); m.boot()
rec = Rec(OUT); m.record = rec.add
DOWN = [0xe0, 0x50, 0xe0, 0xd0]
SETUP = [DOWN, DOWN, [0x1c, 0x9c], DOWN, DOWN, DOWN, [0x1c, 0x9c]]
MENU_T = 20.0                                                          # the menu is up well before this (virtual seconds)
timed = [(MENU_T + 1.0 + 0.6 * k, DOWN) for k in range(ENTRY)] + [(MENU_T + 1.0 + 0.6 * ENTRY + 0.8, [0x1c, 0x9c])]
for a in sys.argv[4:]:
    t, codes = a.split(':'); timed.append((float(t), [int(c, 0) for c in codes.split(',')]))
timed.sort(key=lambda x: x[0]); t0 = time.time(); last = -1
try:
    while m.vclock < (MENU_T + SECONDS) * PIT_HZ:
        key_script(m, SETUP)
        if timed and m.vclock >= timed[0][0] * PIT_HZ and not m.keys and m.can_interrupt(): m.keys += timed.pop(0)[1]
        m.tick(); s = int(m.vclock / PIT_HZ)
        if s != last: last = s; print('t=%ds frames=%d wall=%.0fs' % (s, m.vframes, time.time() - t0), flush=True)
except SystemExit as e: print('EXIT', e)
rec.finish(); print('done')
