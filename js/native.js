/* Native-resolution renderer for the recorded run (tools/pm/export_vec.py): the recorded picture without its vector layer, upscaled, with the demo's own polygons,
   lines, stars and dots drawn on top at the screen's resolution.  The vertices were projected again from the 3D coordinates the demo's engine fed to its polygon
   fillers (so they are smooth, not rounded to the 320x200 grid); colours, textures and shade tables are the demo's own.  WebGL2; painter's order, no depth test. */
(function () {
  "use strict";
  const VS_PRIM = `#version 300 es
  in vec2 a_pos; in vec3 a_col; in vec2 a_uv; in vec2 a_info;
  uniform vec4 u_rect; uniform vec2 u_src; uniform vec2 u_view;
  out vec3 v_col; out vec2 v_uv; out vec2 v_info;
  void main() {
    vec2 px = u_rect.xy + a_pos / u_src * u_rect.zw;
    gl_Position = vec4(px.x / u_view.x * 2.0 - 1.0, 1.0 - px.y / u_view.y * 2.0, 0.0, 1.0);
    v_col = a_col; v_uv = a_uv; v_info = a_info;
  }`;
  const FS_PRIM = `#version 300 es
  precision highp float; precision highp sampler2DArray;
  in vec3 v_col; in vec2 v_uv; in vec2 v_info;
  uniform sampler2DArray u_tex; uniform sampler2D u_lut; uniform sampler2D u_pal;
  out vec4 o;
  void main() {
    if (v_info.x < 0.5) { o = vec4(v_col, 1.0); return; }
    float t = texture(u_tex, vec3(v_uv / 256.0, floor(v_info.x - 0.5))).r * 255.0;
    if (v_info.y > 0.5) t = texelFetch(u_lut, ivec2(int(t + 0.5), int(v_info.y - 0.5)), 0).r * 255.0;
    o = vec4(texelFetch(u_pal, ivec2(int(t + 0.5), 0), 0).rgb, 1.0);
  }`;
  const VS_BMP = `#version 300 es
  in vec2 a_pos; uniform vec4 u_rect; uniform vec2 u_view; out vec2 v_uv;
  void main() {
    vec2 px = u_rect.xy + a_pos * u_rect.zw;
    gl_Position = vec4(px.x / u_view.x * 2.0 - 1.0, 1.0 - px.y / u_view.y * 2.0, 0.0, 1.0); v_uv = a_pos;
  }`;
  const FS_BMP = `#version 300 es
  precision highp float; in vec2 v_uv; uniform sampler2D u_bmp; uniform sampler2D u_pal; uniform sampler2D u_mask; uniform int u_usemask; out vec4 o;
  void main() { if (u_usemask == 1 && texture(u_mask, v_uv).r < 0.5) discard; float t = texture(u_bmp, v_uv).r * 255.0; o = vec4(texelFetch(u_pal, ivec2(int(t + 0.5), 0), 0).rgb, 1.0); }`;

  // planar (engine B) pictures: the VGA writes a colour into the planes its map mask enables and keeps the others, so each plane is its own 1-bit layer (a colour channel here,
  // drawn with gl.colorMask).  The layers are drawn supersampled and combined through the palette only at the end, so edges are smooth without ever mixing colour indices.
  const VS_PLANE = `#version 300 es
  in vec2 a_pos; in float a_c; uniform vec2 u_src; out float v_c;
  void main() { gl_Position = vec4(a_pos.x / u_src.x * 2.0 - 1.0, a_pos.y / u_src.y * 2.0 - 1.0, 0.0, 1.0); v_c = a_c; }`;
  const FS_PLANE = `#version 300 es
  precision highp float; in float v_c; out vec4 o;
  void main() { float c = floor(v_c + 0.5); o = vec4(mod(c, 2.0), mod(floor(c / 2.0), 2.0), mod(floor(c / 4.0), 2.0), floor(c / 8.0)); }`;
  const VS_BITS = `#version 300 es
  in vec2 a_pos; out vec2 v_uv; void main() { gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0); v_uv = a_pos; }`;
  const FS_BITS = `#version 300 es
  precision highp float; in vec2 v_uv; uniform sampler2D u_bmp; out vec4 o;
  void main() { float c = floor(texture(u_bmp, v_uv).r * 255.0 + 0.5); o = vec4(mod(c, 2.0), mod(floor(c / 2.0), 2.0), mod(floor(c / 4.0), 2.0), mod(floor(c / 8.0), 2.0)); }`;
  const FS_RESOLVE = `#version 300 es
  precision highp float; in vec2 v_uv; uniform sampler2D u_planes; uniform sampler2D u_pal; uniform ivec2 u_size; uniform int u_ss; out vec4 o;
  void main() {
    ivec2 base = ivec2(floor(v_uv * vec2(u_size))) - ivec2(u_ss / 2); vec3 sum = vec3(0.0);
    for (int j = 0; j < u_ss; j++) for (int i = 0; i < u_ss; i++) {
      vec4 b = texelFetch(u_planes, clamp(base + ivec2(i, j), ivec2(0), u_size - 1), 0);
      int idx = int(b.r + 0.5) + 2 * int(b.g + 0.5) + 4 * int(b.b + 0.5) + 8 * int(b.a + 0.5);
      sum += texelFetch(u_pal, ivec2(idx, 0), 0).rgb;
    }
    o = vec4(sum / float(u_ss * u_ss), 1.0);
  }`;
  function sh(gl, type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }
  function prog(gl, vs, fs) { const p = gl.createProgram(); gl.bindAttribLocation(p, 0, "a_pos"); gl.attachShader(p, sh(gl, gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl, gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)); return p; }

  function create(canvas) {
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, preserveDrawingBuffer: true });
    if (!gl) return null;
    const pPrim = prog(gl, VS_PRIM, FS_PRIM), pBmp = prog(gl, VS_BMP, FS_BMP), pPlane = prog(gl, VS_PLANE, FS_PLANE), pBits = prog(gl, VS_BITS, FS_BITS), pRes = prog(gl, VS_BMP, FS_RESOLVE);
    const vboPl = gl.createBuffer(), vaoPl = gl.createVertexArray(); let fbo = null, fbTex = null, fbW = 0, fbH = 0;
    gl.bindVertexArray(vaoPl); gl.bindBuffer(gl.ARRAY_BUFFER, vboPl);
    { const a = gl.getAttribLocation(pPlane, "a_pos"), c = gl.getAttribLocation(pPlane, "a_c"); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 12, 0); gl.enableVertexAttribArray(c); gl.vertexAttribPointer(c, 1, gl.FLOAT, false, 12, 8); }
    let pbuf = new Float32Array(3 * 6 * 4096), pn = 0, ops = [];
    const vboPrim = gl.createBuffer(), vboQuad = gl.createBuffer(), vaoPrim = gl.createVertexArray(), vaoQuad = gl.createVertexArray();
    gl.bindVertexArray(vaoPrim); gl.bindBuffer(gl.ARRAY_BUFFER, vboPrim);
    const stride = 9 * 4, attr = (n, size, off) => { const l = gl.getAttribLocation(pPrim, n); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, gl.FLOAT, false, stride, off * 4); };
    attr("a_pos", 2, 0); attr("a_col", 3, 2); attr("a_uv", 2, 5); attr("a_info", 2, 7);
    gl.bindVertexArray(vaoQuad); gl.bindBuffer(gl.ARRAY_BUFFER, vboQuad); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);
    { const l = gl.getAttribLocation(pBmp, "a_pos"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 2, gl.FLOAT, false, 0, 0); }
    const mkTex = () => { const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); return t; };
    const palTex = mkTex(), bmpTex = mkTex(), lutTex = mkTex(), maskTex = mkTex(); let texArr = null, nTex = 0, nLut = 0;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    let buf = new Float32Array(9 * 6 * 4096), nv = 0;
    function grow(k) { if ((nv + k) * 9 > buf.length) { const b = new Float32Array(buf.length * 2); b.set(buf); buf = b; } }
    function vert(x, y, r, g, b, u, v, layer, lut) { grow(1); const o = nv * 9; buf[o] = x; buf[o + 1] = y; buf[o + 2] = r; buf[o + 3] = g; buf[o + 4] = b; buf[o + 5] = u; buf[o + 6] = v; buf[o + 7] = layer + 1; buf[o + 8] = lut + 1; nv++; }       // layer and lut are stored +1 (0 = none)

    function setTextures(list) {                       // list: Uint8Array(65536) each
      if (texArr) gl.deleteTexture(texArr);
      nTex = list.length; texArr = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D_ARRAY, texArr);
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.texImage3D(gl.TEXTURE_2D_ARRAY, 0, gl.R8, 256, 256, Math.max(1, nTex), 0, gl.RED, gl.UNSIGNED_BYTE, null);
      list.forEach((t, i) => gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, i, 256, 256, 1, gl.RED, gl.UNSIGNED_BYTE, t));
    }
    function setLuts(u8) {                             // n x 256 bytes
      nLut = u8.length >> 8; gl.bindTexture(gl.TEXTURE_2D, lutTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 256, Math.max(1, nLut), 0, gl.RED, gl.UNSIGNED_BYTE, nLut ? u8 : new Uint8Array(256));
    }
    const palBytes = new Uint8Array(256 * 4);
    function setPalette(pal) {
      for (let k = 0; k < 256; k++) { for (let c = 0; c < 3; c++) { const v = pal[k * 3 + c]; palBytes[k * 4 + c] = v << 2 | v >> 4; } palBytes[k * 4 + 3] = 255; }
      gl.bindTexture(gl.TEXTURE_2D, palTex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, palBytes);
    }
    const rgb = (pal, i) => { const o = i * 3; return [(pal[o] << 2 | pal[o] >> 4) / 255, (pal[o + 1] << 2 | pal[o + 1] >> 4) / 255, (pal[o + 2] << 2 | pal[o + 2] >> 4) / 255]; };

    let segs = [], ovl = null;
    function pvert(x, y, c) { if ((pn + 1) * 3 > pbuf.length) { const b = new Float32Array(pbuf.length * 2); b.set(pbuf); pbuf = b; } pbuf[pn * 3] = x; pbuf[pn * 3 + 1] = y; pbuf[pn * 3 + 2] = c; pn++; }
    function pop(mask, fn) { const from = pn; fn(); const l = ops[ops.length - 1]; if (l && l.mask === mask && l.to === from) l.to = pn; else ops.push({ mask, from, to: pn }); }
    function planarPoly(mask, c, xs, ys) { pop(mask, () => { for (let i = 1; i + 1 < xs.length; i++) for (const k of [0, i, i + 1]) pvert(xs[k], ys[k], c); }); }
    function planarLine(mask, c, x0, y0, x1, y1, scale) {
      const dx = x1 - x0, dy = y1 - y0, L = Math.hypot(dx, dy) || 1, w = Math.max(0.8, 1.0 / scale) / 2, nx = -dy / L * w, ny = dx / L * w, ex = dx / L * w * 0.5, ey = dy / L * w * 0.5;
      const A = [x0 - ex + nx, y0 - ey + ny], B = [x0 - ex - nx, y0 - ey - ny], C = [x1 + ex + nx, y1 + ey + ny], D = [x1 + ex - nx, y1 + ey - ny];
      pop(mask, () => { for (const P of [A, B, C, C, B, D]) pvert(P[0], P[1], c); });
    }
    function build(pic, pal, scale) {                  // parse one packed picture into triangles (source pixel units); fills segs (scissor windows) and ovl (overlay bit mask)
      nv = 0; pn = 0; ops = []; segs = [{ from: 0, clip: null }]; ovl = null; const dv = new DataView(pic.buffer, pic.byteOffset, pic.byteLength); let p = 0; const n = dv.getUint16(p, true); p += 2;
      for (let q = 0; q < n; q++) {
        const kind = dv.getUint8(p); p += 1;
        if (kind === 4) { segs.push({ from: nv, clip: [dv.getFloat32(p, true), dv.getFloat32(p + 4, true), dv.getFloat32(p + 8, true), dv.getFloat32(p + 12, true)] }); p += 16; }
        else if (kind === 3) { ovl = pic.subarray(p, p + 8000); p += 8000; }
        else if (kind === 0) {
          const nvx = dv.getUint8(p + 1); const ct = dv.getUint8(p + 2); p += 3; let col = [0, 0, 0], layer = -1, lut = -1, uv = null, pcol = 0, pmask = 0;
          if (ct === 0) { col = rgb(pal, dv.getUint8(p)); p += 1; }
          else if (ct === 1) { layer = dv.getUint16(p, true); lut = dv.getUint16(p + 2, true); if (lut === 0xFFFF) lut = -1; p += 4; uv = []; for (let i = 0; i < nvx; i++) { uv.push(dv.getUint16(p, true), dv.getUint16(p + 2, true)); p += 4; } }
          else { pcol = dv.getUint8(p); pmask = dv.getUint8(p + 1); p += 2; }
          p += 2; const xs = [], ys = []; for (let i = 0; i < nvx; i++) { xs.push(dv.getFloat32(p, true)); ys.push(dv.getFloat32(p + 4, true)); p += 8; }
          if (ct === 2) { planarPoly(pmask, pcol, xs, ys); continue; }
          if (ct === 1 && layer >= nTex) continue;
          for (let i = 1; i + 1 < nvx; i++) for (const k of [0, i, i + 1]) vert(xs[k], ys[k], col[0], col[1], col[2], uv ? uv[2 * k] / 256 : 0, uv ? uv[2 * k + 1] / 256 : 0, layer, lut);
        } else if (kind === 1) {
          const pc = dv.getUint8(p + 1), pm = dv.getUint8(p + 2); p += 3; const x0 = dv.getFloat32(p, true), y0 = dv.getFloat32(p + 4, true), x1 = dv.getFloat32(p + 8, true), y1 = dv.getFloat32(p + 12, true); p += 16;
          planarLine(pm, pc, x0, y0, x1, y1, scale);
        } else {
          const cnt = dv.getUint16(p + 1, true); p += 3;
          pop(15, () => { for (let i = 0; i < cnt; i++, p += 5) { const x = dv.getUint16(p, true), y = dv.getUint16(p + 2, true), c = dv.getUint8(p + 4); for (const [a, b] of [[0, 0], [1, 0], [0, 1], [0, 1], [1, 0], [1, 1]]) pvert(x + a, y + b, c); } });
        }
      }
    }

    // draw one frame: f = {w,h,pal,pix} (recorded), bg = {w,h,pix}|null (recorded without the vector layer), pic = packed picture|null, r = picture rectangle (fractions), over = overscan colour
    function planarPass(f, src, rw, rh, W) {            // the bitmap's planes and the planar primitives into the offscreen planes texture; returns the supersampling factor
      const ss = W * 2 <= 4096 ? 2 : 1, fw = Math.max(2, Math.round(rw * ss)), fh = Math.max(2, Math.round(rh * ss));
      if (!fbo || fbW !== fw || fbH !== fh) {
        if (fbTex) gl.deleteTexture(fbTex); fbTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, fbTex);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, fw, fh, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        fbo = fbo || gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, fbTex, 0); fbW = fw; fbH = fh;
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo); gl.viewport(0, 0, fw, fh);
      gl.useProgram(pBits); gl.bindVertexArray(vaoQuad); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, bmpTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, src.w, src.h, 0, gl.RED, gl.UNSIGNED_BYTE, src.pix);
      gl.uniform1i(gl.getUniformLocation(pBits, "u_bmp"), 0); gl.drawArrays(gl.TRIANGLES, 0, 6);
      gl.useProgram(pPlane); gl.bindVertexArray(vaoPl); gl.bindBuffer(gl.ARRAY_BUFFER, vboPl); gl.bufferData(gl.ARRAY_BUFFER, pbuf.subarray(0, pn * 3), gl.DYNAMIC_DRAW);
      gl.uniform2f(gl.getUniformLocation(pPlane, "u_src"), f.w, f.h);
      for (const o of ops) { gl.colorMask(!!(o.mask & 1), !!(o.mask & 2), !!(o.mask & 4), !!(o.mask & 8)); gl.drawArrays(gl.TRIANGLES, o.from, o.to - o.from); }
      gl.colorMask(true, true, true, true); gl.bindFramebuffer(gl.FRAMEBUFFER, null); return ss;
    }
    function draw(f, bg, pic, r, over) {
      const dpr = window.devicePixelRatio || 1, W = Math.max(2, Math.round(canvas.clientWidth * dpr)), H = Math.max(2, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
      setPalette(f.pal);
      const rx = r[0] * W, ry = r[1] * H, rw = r[2] * W, rh = r[3] * H, src = bg || f;
      if (pic) build(pic, f.pal, rw / f.w); else { nv = 0; pn = 0; ops = []; segs = [{ from: 0, clip: null }]; ovl = null; }
      const ss = ops.length ? planarPass(f, src, rw, rh, W) : 0;
      gl.viewport(0, 0, W, H); const oc = rgb(f.pal, over); gl.clearColor(oc[0], oc[1], oc[2], 1); gl.clear(gl.COLOR_BUFFER_BIT);
      if (ss) {
        gl.useProgram(pRes); gl.bindVertexArray(vaoQuad);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, fbTex); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, palTex);
        gl.uniform1i(gl.getUniformLocation(pRes, "u_planes"), 0); gl.uniform1i(gl.getUniformLocation(pRes, "u_pal"), 1); gl.uniform2i(gl.getUniformLocation(pRes, "u_size"), fbW, fbH); gl.uniform1i(gl.getUniformLocation(pRes, "u_ss"), ss);
        gl.uniform4f(gl.getUniformLocation(pRes, "u_rect"), rx, ry, rw, rh); gl.uniform2f(gl.getUniformLocation(pRes, "u_view"), W, H);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      } else {
        gl.useProgram(pBmp); gl.bindVertexArray(vaoQuad);
        gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, bmpTex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, src.w, src.h, 0, gl.RED, gl.UNSIGNED_BYTE, src.pix);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, palTex);
        gl.uniform1i(gl.getUniformLocation(pBmp, "u_bmp"), 0); gl.uniform1i(gl.getUniformLocation(pBmp, "u_pal"), 1); gl.uniform1i(gl.getUniformLocation(pBmp, "u_usemask"), 0);
        gl.uniform4f(gl.getUniformLocation(pBmp, "u_rect"), rx, ry, rw, rh); gl.uniform2f(gl.getUniformLocation(pBmp, "u_view"), W, H);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }
      {
        if (nv) {
          gl.useProgram(pPrim); gl.bindVertexArray(vaoPrim); gl.bindBuffer(gl.ARRAY_BUFFER, vboPrim); gl.bufferData(gl.ARRAY_BUFFER, buf.subarray(0, nv * 9), gl.DYNAMIC_DRAW);
          gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D_ARRAY, texArr); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, lutTex); gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, palTex);
          gl.uniform1i(gl.getUniformLocation(pPrim, "u_tex"), 0); gl.uniform1i(gl.getUniformLocation(pPrim, "u_lut"), 1); gl.uniform1i(gl.getUniformLocation(pPrim, "u_pal"), 2);
          gl.uniform4f(gl.getUniformLocation(pPrim, "u_rect"), rx, ry, rw, rh); gl.uniform2f(gl.getUniformLocation(pPrim, "u_src"), f.w, f.h); gl.uniform2f(gl.getUniformLocation(pPrim, "u_view"), W, H);
          gl.enable(gl.SCISSOR_TEST); const sx = rw / f.w, sy = rh / f.h;
          segs.forEach((g, k) => {
            const to = k + 1 < segs.length ? segs[k + 1].from : nv; if (to <= g.from) return;
            if (g.clip) { const x0 = Math.round(rx + g.clip[0] * sx), x1 = Math.round(rx + g.clip[2] * sx), y0 = Math.round(ry + g.clip[1] * sy), y1 = Math.round(ry + g.clip[3] * sy); gl.scissor(x0, H - y1, Math.max(0, x1 - x0), Math.max(0, y1 - y0)); }
            else gl.scissor(0, 0, W, H);
            gl.drawArrays(gl.TRIANGLES, g.from, to - g.from);
          });
          gl.disable(gl.SCISSOR_TEST);
        }
        if (ovl) {                                     // what the demo drew after the vector layer shows through again
          const m8 = new Uint8Array(f.w * f.h); for (let y = 0; y < f.h; y++) for (let x = 0; x < f.w; x++) m8[y * f.w + x] = (ovl[y * (f.w >> 3) + (x >> 3)] >> (7 - (x & 7))) & 1 ? 255 : 0;
          gl.useProgram(pBmp); gl.bindVertexArray(vaoQuad);
          gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, bmpTex); gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, palTex);
          gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, maskTex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, f.w, f.h, 0, gl.RED, gl.UNSIGNED_BYTE, m8);
          gl.uniform1i(gl.getUniformLocation(pBmp, "u_mask"), 2); gl.uniform1i(gl.getUniformLocation(pBmp, "u_usemask"), 1);
          gl.uniform1i(gl.getUniformLocation(pBmp, "u_bmp"), 0); gl.uniform1i(gl.getUniformLocation(pBmp, "u_pal"), 1);
          gl.uniform4f(gl.getUniformLocation(pBmp, "u_rect"), rx, ry, rw, rh); gl.uniform2f(gl.getUniformLocation(pBmp, "u_view"), W, H);
          gl.drawArrays(gl.TRIANGLES, 0, 6); gl.uniform1i(gl.getUniformLocation(pBmp, "u_usemask"), 0);
        }
      }
    }
    return { draw, setTextures, setLuts, gl, dbg: () => ({ fbo, fbW, fbH, ops: ops.length, pn, nv }) };
  }
  window.CD2Native = { create };
})();
