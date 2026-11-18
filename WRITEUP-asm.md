# CRYSTAL DREAM II (Triton, 1993) - asm snippets worth reading

All addresses are offsets in the unpacked program image (`tools/img.bin`, made by `tools/unpack.py`, which runs the EXE's own unpacker). Every listing below was
re-disassembled from that file for this write-up with `ndisasm -b16` (the same call as `tools/gen_asm.py`); `ndisasm` prints `shr r,1` as `shr r,0x0`, which is corrected to `1`
below. The program is Borland Pascal 7 in protected mode (see `WRITEUP-effects.md`, section 0): **16-bit code that uses 32-bit registers through the `66` prefix**, so `eax`,
`shrd`, `rep stosd` and `imul eax` show up inside otherwise 286-style routines. Pascal calling convention: arguments on the stack, the callee pops them (`ret 8`).

Not everything here is the authors' hand. The routines at 0x6E1, 0x22EAF, 0x16120, 0x16179, 0x18327, 0x3A82, 0x81ED and 0x195B8 are not compiler-shaped (no `push bp /
mov bp,sp` pairing with a Pascal body around them, 32-bit registers used freely); 0x30545 is compiler-shaped; 0x31571 belongs to the Borland runtime. That split is
my reading of the code, not something the binary states. Where a point is inference rather than something the bytes show, it says so.

The code is what the demo runs for its scenes; the shipped web player replays a recording of it (`WRITEUP-effects.md`, section 0), so nothing here is "ported", only
read and, for the plasma and the fractal, re-implemented as a check.

---

## 1. Mode X blit by columns, one `out` per column (0x6E1)

Unchained Mode X puts pixel `x` in plane `x mod 4`. A row-by-row copy would switch plane for every pixel. This loop is turned on its side: for each of the 320 columns
it selects the plane **once**, then walks the 200 rows of that column with `movsb`.

```
06e1: push di / push si / push cx
06e4: dec cx
06e5: add si,cx                  ; source column = base + (cx-1)
06e7: mov bx,cx
06e9: and bx,0x3                 ; plane = column mod 4
06ed: mov ah,[cs:bx+0x1dd]       ; plane mask from a 4 byte table in CS
06f2: mov al,0x2                 ; sequencer index 2 = map mask
06f4: mov dx,0x3c4
06f7: out dx,ax                  ; index and data with ONE out
06f8: mov ax,cx
06fa: shr ax,0x2
06fd: add di,ax                  ; destination byte = column / 4
06ff: mov cx,0xc8                ; 200 rows
0702: movsb                      ; si++, di++
0703: add si,0x13f               ; +319, plus movsb's 1 = a 320 byte source row
0707: sub di,0x51                ; -81, plus movsb's 1 = -80
070a: loop 0x702
070c: pop cx / pop si / pop di
070f: loop 0x6e1                 ; next column
```

* 320 port writes and 64,000 `movsb` for a whole 320x200 image, instead of 64,000 plane switches.
* The two strides have the `movsb` increment folded in: `0x13f + 1` = 320, `0x51` seen from a destination that already moved by 1 is -80.
* The destination goes **backwards**. The routine appears twice (0x6E1 and 0x723, same body; both are entered right after `mov ax,0x30 / mov es,ax / mov di,...` at 0x6CC and
  0x712, so both write through the flat selector 0x30). The first copy starts at `di = 0x7CB0` (0x7CB0 = 399 * 80, the last line of a 400 line page) and the second at `di = 0x3E30`
  (= 199 * 80): together two passes of 200 rows going upward over a 320x400 page. That is exactly the size of the archive's `.TGA` pictures (320x400, `WRITEUP-effects.md`
  section 9), which the TGA format stores bottom-up, so the blit un-flips the picture for free. The 320x400 match is shown by the constants; "because TGA is bottom-up" is
  my inference, I did not trace the caller.

## 2. A scanline fill that keeps its odd-pixel flags in the carry (0x22EAF, 0x22EE0)

The flat polygon filler first replicates the colour into all four bytes of `eax`, then fills each span with `rep stosd`:

```
22eaf: mov al,[ss:0x410]         ; the colour
22eb3: mov ah,al
22eb5: mov dx,ax
22eb7: shl eax,0x10
22ebb: mov ax,dx                 ; eax = cccccccc
...
22ee0: push cx
22ee1: mov di,bx
22ee3: mov cx,dx
22ee5: sub cx,bx                 ; length = x2 - x1
22ee7: jz 0x2f17                 ;   empty span
22ee9: jnc 0x2eef
22eeb: neg cx                    ; right to left: swap by negating
22eed: mov di,dx
22eef: add di,bp                 ; + line start
22ef1: test di,0x1
22ef5: jz 0x2efb
22ef7: stosb                     ; align to a word with at most one byte ...
22ef8: dec cx
22ef9: jz 0x2f17
22efb: shr cx,1                  ; CF = odd byte left over
22efd: jz 0x2f14
22eff: pushf                     ; park that bit on the stack
22f00: test di,0x2
22f04: jz 0x2f0a
22f06: stosw                     ; ... and at most one word, so the bulk is dword aligned
22f07: dec cx
22f08: jz 0x2f13
22f0a: shr cx,1                  ; CF = odd word left over
22f0c: jc 0x2f1d                 ;   (tail: rep stosd; stosw; popf; stosb if needed)
22f0e: jz 0x2f13
22f10: rep stosd                 ; four pixels per store
22f13: popf                      ; first CF is back
22f14: jnc 0x2f17
22f16: stosb                     ; trailing byte
22f17: pop cx
22f18: loop 0x2ec8               ; next scanline
```

* No branch on a counter and no separate register for the "is there a trailing byte" bit: `shr cx,1` leaves it in CF, `pushf`/`popf` carries it across the aligned
  part (the second `shr` clobbers CF), and one `jnc` at the end decides on the last `stosb`.
* A span whose ends come in the wrong order is repaired by `neg cx` and `mov di,dx`, with no swap of the edge registers.
* Just above the loop (0x22EC8..0x22EDC), the left and right edges are stepped with `add word [mem],0x1234 / adc bx,0x1234 / add word [mem],0x1234 / adc dx,0x1234 /
  add bp,0x1234`: a 32-bit edge accumulator split into a memory word and a register. The `0x1234` immediates look like placeholders; **I did not find the code that
  patches them**, so I do not claim self-modifying code here (contrast with section 6, where the patching is in the listing).

## 3. Integer square root: Newton with a 33-bit average (0x16120)

```
16124: mov ecx,[bp+0x8]          ; N low dword
16128: mov esi,[bp+0x4]          ; N high dword
1612c: mov ebx,ecx
1612f: or ebx,ebx
16132: jz 0x6165                 ; N = 0 -> result 0
16134: shr ebx,1
16137: inc ebx                   ; first guess N/2 + 1
16139: or esi,esi
1613c: jz 0x6144
1613e: mov ebx,0x7fffffff        ; N >= 2^32: largest positive dword
16144: mov di,0x11               ; 17 iterations
16147: mov eax,ecx
1614a: mov edx,esi
1614d: div ebx                   ; edx:eax / x  (64 bit dividend)
16150: xor ebp,ebp
16153: add eax,ebx               ; N/x + x ...
16156: adc ebp,0x0               ; ... as a 33 bit value
1615a: shrd eax,ebp,1            ; / 2, the 33rd bit shifted back in
1615f: mov ebx,eax
16162: dec di
16163: jnz 0x6147
```

The classic `x <- (x + N/x) / 2`, with the detail worth stealing in the averaging: `x + N/x` can overflow 32 bits, so the sum is made with `add` / `adc ebp,0` and halved
by one `shrd eax,ebp,1`. No overflow test, no branch inside the loop. 17 fixed iterations is a blunt but constant-time answer for any 64-bit input. (The divide is `div`
of a 64-bit value by a 32-bit one; the clamp to 0x7fffffff when the high dword is non-zero keeps the first quotient in range. I did not test the routine on inputs above
2^32, only read it.)

## 4. Normalising a vector without losing a bit (0x16179, 0x161B8)

```
16180: mov eax,[si]
16183: imul eax                  ; one operand form: edx:eax = x*x, 64 bit
16186: mov edi,edx
16189: mov ecx,eax               ; edi:ecx = x^2
1618c: mov eax,[si+0x4]
16190: imul eax
16193: add ecx,eax
16196: adc edi,edx               ; += y^2 with carry
16199: mov eax,[si+0x8]
1619d: imul eax
161a0: add eax,ecx
161a3: adc edx,edi               ; edx:eax = x^2+y^2+z^2
 ...                              ; push it, call the square root of section 3
161b8: mov ebx,[ss:0x6be]        ; |v|
161be: mov ecx,[bp+0x6]          ; wanted length
161c2: mov eax,[si]
161c5: imul ecx                  ; 64 bit product
161c8: idiv ebx                  ; / |v|  (64 / 32), no precision thrown away before the divide
161cb: mov [si],eax              ; repeated for y and z
```

Three squares of 32-bit signed components accumulate into a **64-bit sum** with plain `add`/`adc`, the result goes straight into the 64-bit square root above, and each
component is rescaled with `imul ecx` / `idiv ebx` where the intermediate stays 64 bit. On a 16-bit compiler this would be a software long-arithmetic call per step. It
is the only place in the file that normalises a vector in 3-D; I did not follow which scene calls it.

## 5. 16.16 squares: `shrd` as the rescale (0x18327)

```
18330: imul eax                  ; edx:eax = x*x   (16.16 * 16.16 = 32.32)
18333: shrd eax,edx,0x10         ; the middle dword: back to 16.16
18338: mov ecx,eax               ; running total
...                              ; same for the 2nd and 3rd value, added into ecx
18359: or esi,esi
1835c: jns 0x8361
1835e: neg ecx                   ; negate the total if the third value was negative
18361: mov [ss:bx],ecx
```

When both operands are 16.16 the product is 32.32; one `shrd eax,edx,16` pulls the right 32 bits out of `edx:eax`, so there is no multiply helper and no shift loop. Three
of them make the sum of squares. The sign fix-up at the end uses the sign of the third value (`esi`); what that means for the caller I did not trace.

## 6. The plasma loop: eight table offsets patched into its own code (0x3A82..0x3B63)

The inner loop reads the sine table at `si + k*step` for eight consecutive columns. `step` is a parameter of the routine, so instead of eight address registers the
routine **writes the eight multiples into the displacement fields of the loop** before it runs, through `fs`, which is loaded from a variable (`[ds:0x8b96]`) and, as the
numbers below show, addresses the same bytes as the code:

```
3a86: mov fs,[ds:0x8b96]
3a8b: mov ax,[bp+0x4]             ; step
3a8e: mov [fs:0x11a],ax           ; 1 * step
3a92: add ax,[bp+0x4]
3a95: and ax,0x7ff
3a98: mov [fs:0x106],ax           ; 2 * step
3a9c: add ax,[bp+0x4] / and ax,0x7ff
3aa2: mov [fs:0x10c],ax           ; 3 * step
 ...                              ; 0x138 (4x), 0x13e (5x), 0x128 (6x), 0x12e (7x)
3ad4: mov [fs:0x14c],ax           ; 8 * step: the loop's own advance
```

and the loop that receives them:

```
3b14: mov al,[si+0x201]           ; disp at 0x3b16  <- fs:0x106  (2x)
3b18: add al,bl
3b1a: mov ah,[si+0x201]           ; disp at 0x3b1c  <- fs:0x10c  (3x)
3b1e: add ah,bl
3b20: rol eax,0x10
3b24: mov al,[si]                 ; 0x
3b26: add al,bl
3b28: mov ah,[si+0x201]           ; disp at 0x3b2a  <- fs:0x11a  (1x)
3b2c: add ah,bl
3b2e: mov [es:di+0xa0],eax        ; the line below
3b34: stosd                       ; this line
 ...                              ; second dword: 4x,5x,6x,7x the same way
3b5a: add si,0x1234               ; imm at 0x3b5c <- fs:0x14c  (8x)
3b5e: and si,0x7fff
3b62: dec cx
3b63: jnz 0x3b14
```

How I know the patch targets are the loop's own immediates: the loop's `[si+0x201]` displacement bytes sit at 0x3B16, 0x3B1C, 0x3B2A, 0x3B38, 0x3B3E, 0x3B48, 0x3B4E and the
`add si` immediate at 0x3B5C. The patched offsets are 0x106, 0x10C, 0x11A, 0x128, 0x12E, 0x138, 0x13E and 0x14C. Each patched offset is the matching displacement address minus 0x3A10 (0x3B16-0x106, 0x3B1C-0x10C, ... 0x3B5C-0x14C), so `fs`
is the code segment of that unit, based at image offset 0x3A10 (the plasma palette routine of `WRITEUP-effects.md` section 3 is at 0x3A10 as well). The `0x201`
and `0x1234` in the file are placeholders.

* The step multiples are `k*step & 0x7ff`; the eight values are computed with seven `add` / `and` pairs, not seven multiplies.
* The loop has no index register besides `si`, no `+k` arithmetic, and per eight pixels only one `add si` and one `and si,0x7fff` (the wrap of the table position).
* Each trip makes **eight pixels** in two `eax`: `mov al / mov ah / rol eax,16 / mov al / mov ah` fills four bytes with two of the byte loads on each half. The row
  constant `bl` (`T[p3-3r] + T[p4-r]`, read at 0x3B06..0x3B10) is added to each loaded byte.
* **Two lines per store**: the same dword goes to `[es:di+0xa0]` and, with `stosd`, to `[es:di]`: the 160-byte line pitch of the planar page, so the half-height image is
  doubled vertically while it is computed, not afterwards (this is the "80 x 100 table sums" of effects section 3).
* The row terms advance after each row pair as `[bp-2] += 2`, `[bp-6] += -3`, `[bp-8] += -1`, each with `and ...,0x3ff` (0x3B66..0x3B7C), and `di += 0xf0` moves to the
  next pair of lines.

The routine, the table builder at 0x3BD1 and the palette routine at 0x3A10 were run under emulation against numpy versions and agree exactly
(`python3 tools/run_plasma.py`, `WRITEUP-effects.md` section 3).

## 7. The fractal scaler: Bresenham with no branch, entered like a Duff's device (0x81ED)

Each screen column of the zoom copies a vertical slice of the log-scaled strip, stretched by a per-column factor:

```
81f8: mov dx,[cs:si+0x20]        ; per-column record: count ...
81fc: cmp dx,0x3
81ff: jz 0x8240                  ;   (nothing to draw)
8201: mov di,[cs:si+0x22]        ; ... destination offset ...
8205: mov esi,[cs:si+0x24]       ; ... and a 32 bit source position
820a: add si,[bp-0xc]            ; + column * 128 = start of the source column
820d: sub si,cx                  ; pre-compensate the first "add esi,ecx"
820f: shr dx,1
8211: jc 0x821c
8213: shr dx,1
8215: jc 0x8225
8217: add esi,ecx                ; (count & 3) == 0
821a: jmp 0x8239
...
822a: add esi,ecx                ; (count & 3) == 3
822d: movsb                      ; copy a pixel; si++, di++
822e: adc esi,ecx                ; position += step; carry out of the high half comes back in as +1
8231: movsb
8232: adc esi,ecx
8235: movsb
8236: adc esi,ecx
8239: movsb
823a: adc esi,ecx
823d: dec dx
823e: jnz 0x822d                 ; four pixels per trip
```

* The low two bits of the count pick the entry point in a four times unrolled body, so the loop counter `dx` only counts groups of four.
* My reading of the arithmetic (the bytes show the structure, not the contents of `ecx`): `movsb` reads through `si`, the low half of `esi`, so that half is the integer
  source position and the high half is the fraction. `movsb` supplies the `+1` of the integer step, `ecx` supplies the rest, and `adc` takes the carry out of
  **bit 31**, i.e. out of the fraction, as the carry-in of the next addition into the position. A Bresenham or DDA step with no conditional jump per pixel and no
  second register for the fraction.
* `sub si,cx` before the first `add esi,ecx` cancels the extra addition every entry path makes, so the source position is exact at the first pixel.
* The scene that feeds it builds the column table with real numbers at start-up and then only changes `col(j) = T[z&7][j] - (z>>3)` per frame (`WRITEUP-effects.md`, section
  4). I ran this exact routine under emulation to read the pixel map out of `MegaFrac.DAT`, and the closed form built from it reproduces the emulator with 0 differing
  pixels (re-run, see that section).

## 8. Cropping a texture with `rep movsd` (0x195B8)

```
195c7: mov cx,0xc8               ; 200 rows
195ca: push cx
195cb: mov cx,0x40               ; 64 dwords = 256 bytes
195ce: rep movsd
195d1: add si,0x40               ; skip the other 64 of the 320 source bytes
195d4: pop cx
195d5: loop 0x95ca
```

Texture data of 256 x 200 is cut out of a 320 x 200 picture with 200 `rep movsd` runs and a 64-byte skip per row. The destination stays contiguous, so a later lookup can use
an 8-bit column index. Short, but it is the pattern for "convert pitch 320 to pitch 256" and costs 64 stores per row. (Which scene consumes the result: not traced.)

## 9. The page flip: retrace test, `out dx,ax`, and an atomic read-and-clear (0x30545)

```
30552: in al,dx                  ; dx = 0x3da
30553: test al,0x8
30555: jnz 0x552                 ; leave a retrace already in progress
30557: cli
30558: mov ax,[bp+0x6]           ; start address (argument)
3055b: mov [0x6672],ax
3055e: mov bl,al
30560: mov al,0xc                ; CRTC register 0x0c: start address high
30562: mov dx,0x3d4
30565: out dx,ax                 ; index (al) and data (ah) with one instruction
30566: mov ah,bl
30568: inc al                    ; register 0x0d: start address low
3056a: out dx,ax
3056b: sti
3056c: mov dx,0x3da
3056f: in al,dx
30570: test al,0x8
30572: jz 0x56f                  ; now wait for the start of the next retrace
30574: xor ax,ax
30576: xchg ax,[0x6684]          ; read and clear the pending palette count in one instruction
3057a: mov [0x6686],ax           ; hand it to the retrace routine
```

Standard Mode X double buffering, the frame written into the hidden page and the CRTC start address then moved (`push bp / mov bp,sp` and an argument at `[bp+6]`: this one is
compiler-shaped, so I credit the technique, not the tuning). Details worth noting:

* The two address bytes are written between `cli` and `sti` so an interrupt cannot land between the high and the low byte, which would show one frame with a torn start
  address. `out dx,ax` is what keeps the pair that short.
* `xchg ax,[0x6684]` takes the number of palette entries to upload **and zeroes it** in one instruction. This is the flag that the fades of `WRITEUP-effects.md` section 7
  use: a fade is a 768-byte DAC rewrite requested by writing a count there; the next retrace call consumes it exactly once.

## 10. For the record: the CPU test is Borland's (0x31571)

```
31571: xor ax,ax
31573: pushf / pop bx
31575: and bh,0xf                ; clear FLAGS bits 12..15
31578: push bx / popf
3157a: pushf / pop cx
3157c: and ch,0xf0
3157f: cmp ch,0xf0
31582: jz 0x1592                 ; still all set -> 8086 (level 0)
31584: inc ax
31585: or bh,0xf0
31588: push bx / popf
3158a: pushf / pop cx
3158b: and ch,0xf0
3158f: jz 0x1592                 ; cannot set them -> 286 (level 1)
31591: inc ax                    ; 386 or later (level 2)
31592: mov [0x6ffa],al
```

The classic FLAGS-bit probe. It sits beside the string `Copyright (c) 1983,92 Borland`: it is the Pascal runtime's start-up test, which stores the CPU level at `DS:6FFA`
and lets the runtime's long multiply/divide helpers use 32-bit instructions on a 386. It is **not** Triton's code and I list it only so it is not credited to them
(`tools/gen_asm.py` card 08 makes the same point).

---

**Left out on purpose.** `tools/gen_asm.py` and the article's card "06 Clip the edge, not the loop" show the listing 0x3B2E..0x3B52, which in `img.bin` is the middle of the
plasma loop of section 6 (`mov [es:di+0xa0],eax / stosd / mov al,[si+0x201] ...`), not an `imul` / `mul` edge clipper; its description does not match the bytes, so no
edge-clipping routine is claimed here. Not analysed at all: the polygon edge set-up of the 3-D scenes, the Gouraud/texture span loops, the MOD mixer, the `.TVO` loader.
