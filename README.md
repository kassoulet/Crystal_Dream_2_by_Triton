# Crystal Dream II — Field Notes

A static, dependency-free website about Triton's 1993 MS-DOS demo
*Crystal Dream II*: the production, the reverse-engineering process,
annotated routines checked against the binary, and a playable browser
look-alike (with pause and seek).

## Run it

From this directory:

```sh
python3 -m http.server 8765
```

Then open <http://127.0.0.1:8765/>.

Opening `index.html` directly also works in modern browsers, but a local
server is recommended so that the captured PNGs and scripts load with the
right MIME types.

## Layout

- `index.html` — the long-form article and recreation UI
- `css/site.css` — all styling, including the CRT effect and responsive layout
- `js/site.js` — navigation, scroll reveals, syntax highlighting, copy buttons
- `js/demo.js` — the 320×200 JavaScript recreation and its scene engine
- `assets/` — stills captured from the original executable (intro strip)
- `assets/orig/` — files decoded from the archive inside `CD2.EXE`: pictures (PNG), the four .mod songs, `megafrac.dat`
- `js/modplayer.js` — offline MOD mixer (4/8 channels) used for the soundtrack
- `js/demo3d.js` — small software 3D renderer for the decoded `.TVO` models (Earth, chessboard)
- `tools/` — `unpack.py` (run the EXE's own unpacker), `extract.py` (the 58-file archive), `convert_assets.py` (TGA/IFF to PNG), `render_pages.py` (the six text pages), `read_tvo.py` (3D objects), `export_data.py` (objects.json, pages.json, strip.png), `run_fractal.py` + `extract_fractal_map.py` (the fractal scaler under emulation and its pixel map), `run_plasma.py` (the plasma routines under emulation vs the port), `run_title.py` (cube title card), `run_cube.py` + `export_cube.py` (cube scene loop under emulation → `assets/orig/cube_frames.bin`, replayed by the player), `run_slime.py` + `export_slime.py` (the Vectorslime scene under emulation → `assets/orig/slime_frames.bin`; needs `CD2_FONT` = the extracted `Nice-f16.LBM`), `run_tunnel.py` (tunnel flight under emulation; `export_slime.py frames.npz tunnel_frames.bin 4750` packs it), `run_intro.py` (the intro under emulation; packed with `export_cube.py`), `gen_asm.py`, `test_mod.js`, `test_3d.js`
- `CD2.EXE` — the original 3,154,579-byte release binary, kept for reference

## Notes

The recreation does not execute `CD2.EXE`. The effects are reimplemented in
JavaScript from the disassembly and captured frames. The original executable
is included for preservation and research; *Crystal Dream II* remains the work
of Triton.

The player keeps its 320×200 buffer inside a 4:3 frame, stretching the pixels
vertically the way a VGA CRT did; the captured stills are shown the same way.

Optional **CRT** checkbox (or key `C`, remembered in localStorage, off by default): a light scanline overlay with a faint vignette, one band per source row (480).
When on, the screen is sized to a whole number k of device pixels per source row so the bands stay perfectly regular; if the window is too small for k >= 2 it is disabled.
Display only, the frames are untouched.

## Reproduce the analysis

```sh
pip install unicorn
python3 tools/unpack.py     # runs the EXE's own unpacker -> tools/img.bin
python3 tools/gen_asm.py    # prints the asm cards used in index.html (needs ndisasm)
python3 tools/extract.py out/            # the 58 embedded files, decoded
python3 tools/convert_assets.py out/ png/ # pictures to PNG
python3 tools/export_data.py out/ assets/orig  # objects.json, pages.json, page PNGs, strip.png
node tools/test_mod.js                   # smoke-test the MOD renderer
```

Every byte listing in `index.html` was verified against `tools/img.bin`.
Partly decoded: the `.TVW` world files. Unknown: the provenance of the
packer stub, and which module the original plays in which scene (the page uses
the Credits page's composer split and guesses within each pair).
The Information page prints private home addresses and phone numbers; they are
left out of the site.

The decoded assets in `assets/orig/` are Triton's original work, kept here with the EXE for preservation and research.

## Deployment

`.github/workflows/pages.yml` checks the JavaScript and Python syntax and publishes the static site (`index.html`, `css/`, `js/`, `assets/`; not `tools/` or `CD2.EXE`) to GitHub Pages on every push to `main`. Enable it once under Settings → Pages → Source: GitHub Actions.
