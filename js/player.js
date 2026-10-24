/* Player for the recording of the demo's own run (tools/pm/*): video.bin + index.bin + audio.wav.
   Nothing here draws or mixes anything the demo did: it decodes recorded frames and a recorded sample stream, and places each frame on a 4:3 screen
   using the CRT timing registers of that frame (index.bin). */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const canvas = $("demoCanvas"); if (!canvas) return;
  const ctx = canvas.getContext("2d", { alpha: false });
  const root = canvas.dataset.base || "assets/demo/";
  let base = root + "main/";
  const S = { n: 0, chunkN: 30, offs: null, vbuf: null, t: null, rect: null, over: null, cache: new Map(), pending: new Set(), meta: null,
              vcache: new Map(), vpending: new Set(), vbuf2: null, voffs: null, native: false, nat: null, vecOK: false, playing: false, T: 0, T0: 0, wallAt: 0, T_at: 0, ac: null, abuf: null, src: null, gain: null, muted: false, last: -1, ready: false };
  const off = document.createElement("canvas"), octx = off.getContext("2d");

  function parseIndex(ab) {
    const dv = new DataView(ab), n = Math.floor(ab.byteLength / 21);
    S.t = new Float32Array(n); S.rect = new Float32Array(n * 4); S.over = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const o = i * 21; S.t[i] = dv.getFloat32(o, true);
      for (let k = 0; k < 4; k++) S.rect[i * 4 + k] = dv.getFloat32(o + 4 + 4 * k, true);
      S.over[i] = dv.getUint8(o + 20);
    }
    S.n = n;
  }
  async function inflate(u8) {
    return new Uint8Array(await new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).arrayBuffer());
  }
  async function decodeChunk(c) {
    const blob = new Uint8Array(S.vbuf, S.offs[c], S.offs[c + 1] - S.offs[c]), d = await inflate(blob);
    const n = Math.min(S.chunkN, S.n - c * S.chunkN), fr = []; let p = 0, prevPal = null, prev = null;
    for (let i = 0; i < n; i++) {
      const w = d[p] | (d[p + 1] << 8), h = d[p + 2] | (d[p + 3] << 8); p += 4;
      const pal = d.slice(p, p + 768); p += 768;
      if (prevPal) for (let k = 0; k < 768; k++) pal[k] ^= prevPal[k];
      prevPal = pal;
      const pix = d.slice(p, p + w * h); p += w * h;
      if (prev && prev.length === pix.length) for (let k = 0; k < pix.length; k++) pix[k] ^= prev[k];
      prev = pix; fr.push({ w, h, pal, pix });
    }
    S.cache.set(c, fr); if (S.cache.size > 6) S.cache.delete(S.cache.keys().next().value); S.pending.delete(c);
  }
  // ---- native layer (vec.bin, tex.bin, lut.bin from tools/pm/export_vec.py)
  async function decodeVec(c) {
    const d = await inflate(new Uint8Array(S.vbuf2, S.voffs[c], S.voffs[c + 1] - S.voffs[c])), dv = new DataView(d.buffer);
    const nf = dv.getUint16(0, true), np = dv.getUint16(2, true); let p = 4; const pic = [], hasbg = [];
    for (let i = 0; i < nf; i++) { pic.push(dv.getInt16(p, true)); hasbg.push(d[p + 2]); p += 3; }
    const pics = []; for (let i = 0; i < np; i++) { const n = dv.getUint32(p, true); p += 4; pics.push(d.subarray(p, p + n)); p += n; }
    const bg = []; let prev = null;
    for (let i = 0; i < nf; i++) {
      if (!hasbg[i]) { bg.push(null); continue; }
      const w = dv.getUint16(p, true), h = dv.getUint16(p + 2, true); p += 4; const pix = d.slice(p, p + w * h); p += w * h;
      if (prev && prev.length === pix.length) for (let k = 0; k < pix.length; k++) pix[k] ^= prev[k];
      prev = pix; bg.push({ w, h, pix });
    }
    S.vcache.set(c, { pic, pics, bg }); if (S.vcache.size > 6) S.vcache.delete(S.vcache.keys().next().value); S.vpending.delete(c);
  }
  function vecAt(i) {
    const c = (i / S.chunkN) | 0, ch = S.vcache.get(c);
    if (!ch) { if (!S.vpending.has(c)) { S.vpending.add(c); decodeVec(c); } return null; }
    const nc = c + 1; if (nc * S.chunkN < S.n && !S.vcache.has(nc) && !S.vpending.has(nc)) { S.vpending.add(nc); decodeVec(nc); }
    const k = i - c * S.chunkN; return { pic: ch.pic[k] >= 0 ? ch.pics[ch.pic[k]] : null, bg: ch.bg[k] };
  }
  async function loadNative() {                    // fetched when Native is first switched on for a segment
    const [vb, tb, lb] = await Promise.all(["vec.bin", "tex.bin", "lut.bin"].map(f => fetch(base + f).then(r => { if (!r.ok) throw new Error(f); return r.arrayBuffer(); })));
    const dv = new DataView(vb), nc = dv.getUint32(10, true); S.vbuf2 = vb; S.voffs = []; for (let i = 0; i <= nc; i++) S.voffs.push(dv.getUint32(14 + 4 * i, true));
    const td = new DataView(tb), nt = td.getUint32(4, true), toffs = []; for (let i = 0; i <= nt; i++) toffs.push(td.getUint32(8 + 4 * i, true));
    const tex = []; for (let i = 0; i < nt; i++) tex.push(await inflate(new Uint8Array(tb, toffs[i], toffs[i + 1] - toffs[i])));
    S.nat.setTextures(tex); S.nat.setLuts(new Uint8Array(lb, 8)); S.vcache.clear(); S.vpending.clear(); S.vecOK = true;
  }
  async function setNative(on) {
    const b = $("nativeBtn"); if (on && !S.nat) { const g = document.createElement("canvas"); g.id = "demoGL"; g.hidden = true; canvas.after(g); S.gcv = g; S.nat = window.CD2Native && window.CD2Native.create(g); if (!S.nat) { if (b) { b.disabled = true; b.title = "WebGL2 is not available"; } return; } }
    S.native = on; if (b) { b.setAttribute("aria-pressed", on); b.textContent = on ? "Native: on" : "Native: off"; }
    if (on && !S.vecOK) { try { await loadNative(); } catch (e) { S.native = false; if (b) { b.textContent = "Native: n/a"; b.disabled = true; } } }
    canvas.hidden = S.native; if (S.gcv) S.gcv.hidden = !S.native; S.needDraw = true; S.last = -1;
  }
  function drawNative(i, f) {
    const v = vecAt(i); if (!v) return false;
    const w = S.rect.subarray(i * 4, i * 4 + 4); S.nat.draw(f, v.bg, v.pic, w, S.over[i]); S.last = i; return true;
  }
  function frameAt(i) {
    const c = (i / S.chunkN) | 0, ch = S.cache.get(c);
    if (!ch) { if (!S.pending.has(c)) { S.pending.add(c); decodeChunk(c); } return null; }
    if (i % S.chunkN === 0 || true) { const nc = c + 1; if (nc * S.chunkN < S.n && !S.cache.has(nc) && !S.pending.has(nc)) { S.pending.add(nc); decodeChunk(nc); } }
    return ch[i - c * S.chunkN];
  }
  function indexOfTime(T) {                       // last frame with t <= T
    let lo = 0, hi = S.n - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (S.t[mid] <= T) lo = mid; else hi = mid - 1; }
    return lo;
  }
  function draw(i) {
    const f = frameAt(i);
    if (!f) return false;
    if (S.native && S.vecOK) return drawNative(i, f);
    if (off.width !== f.w || off.height !== f.h) { off.width = f.w; off.height = f.h; }
    const img = octx.createImageData(f.w, f.h), d = img.data;
    for (let k = 0, q = 0; k < f.pix.length; k++, q += 4) { const c = f.pix[k] * 3; d[q] = f.pal[c] << 2 | f.pal[c] >> 4; d[q + 1] = f.pal[c + 1] << 2 | f.pal[c + 1] >> 4; d[q + 2] = f.pal[c + 2] << 2 | f.pal[c + 2] >> 4; d[q + 3] = 255; }
    octx.putImageData(img, 0, 0);
    const W = canvas.width, H = canvas.height, r = S.rect, o = S.over[i] * 3;
    ctx.fillStyle = "rgb(" + (f.pal[o] << 2) + "," + (f.pal[o + 1] << 2) + "," + (f.pal[o + 2] << 2) + ")"; ctx.fillRect(0, 0, W, H);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, r[i * 4] * W, r[i * 4 + 1] * H, r[i * 4 + 2] * W, r[i * 4 + 3] * H);
    S.last = i; return true;
  }
  // ---- audio
  function stopAudio() { if (S.src) { try { S.src.stop(); } catch (e) {} S.src.disconnect(); S.src = null; } }
  function startAudio(T) {
    stopAudio();
    if (!S.abuf || !S.ac) return;
    const t0 = S.meta.audio_t0, dur = S.abuf.duration;
    const src = S.ac.createBufferSource(); src.buffer = S.abuf; src.connect(S.gain);
    if (T >= t0) { if (T - t0 < dur) src.start(0, T - t0); else return; } else src.start(S.ac.currentTime + (t0 - T));
    S.src = src;
  }
  function now() { return S.playing ? S.T_at + (performance.now() - S.wallAt) / 1000 : S.T_at; }
  function setT(T, keepPlaying) {
    S.T_at = Math.max(S.T0, Math.min(T, S.t[S.n - 1] + 0.05)); S.wallAt = performance.now();
    if (S.playing && keepPlaying !== false) startAudio(S.T_at);
  }
  function play() { if (!S.ready) return; if (S.T_at >= S.t[S.n - 1]) setT(S.T0, false); if (S.ac && S.ac.state === "suspended") S.ac.resume(); S.playing = true; S.wallAt = performance.now(); startAudio(S.T_at); sync(); }
  function pause() { S.T_at = now(); S.playing = false; stopAudio(); sync(); }
  function sync() { const b = $("pauseBtn"); if (b) b.textContent = S.playing ? "Pause" : "Play"; }
  function fmt(t) { t = Math.max(0, t); const m = Math.floor(t / 60), s = Math.floor(t % 60); return m + ":" + (s < 10 ? "0" : "") + s; }
  function loop() {
    const T = now();
    if (S.ready) {
      if (S.playing && T >= S.t[S.n - 1]) pause();
      const i = indexOfTime(T); if (i !== S.last || S.needDraw) { if (draw(i)) S.needDraw = false; }
      const sb = $("seekBar"); if (sb && document.activeElement !== sb) sb.value = T - S.T0;
      const ht = $("hudTime"); if (ht) ht.textContent = fmt(T - S.T0) + " / " + fmt(S.t[S.n - 1] - S.T0);
    }
    requestAnimationFrame(loop);
  }
  async function load(seg) {
    pause(); S.ready = false; S.cache.clear(); S.pending.clear(); S.vcache.clear(); S.vpending.clear(); S.vecOK = false; S.last = -1; stopAudio(); S.abuf = null;
    base = root + seg + "/";
    const st = $("hudScene"); if (st) st.textContent = "loading…";
    const [vb, ib, mj] = await Promise.all([fetch(base + "video.bin").then(r => r.arrayBuffer()), fetch(base + "index.bin").then(r => r.arrayBuffer()), fetch(base + "meta.json").then(r => r.json())]);
    const dv = new DataView(vb); S.vbuf = vb; S.chunkN = dv.getUint16(8, true); const nc = dv.getUint32(10, true);
    S.offs = []; for (let i = 0; i <= nc; i++) S.offs.push(dv.getUint32(14 + 4 * i, true));
    parseIndex(ib); S.meta = mj; S.T0 = S.t[0]; S.T_at = S.T0; S.ready = true; S.needDraw = true;
    const sb = $("seekBar"); if (sb) { sb.max = S.t[S.n - 1] - S.T0; sb.step = 0.05; sb.value = 0; }
    if (st) st.textContent = mj.title || "CRYSTAL DREAM II";
    const ch = $("chapters");
    if (ch) { ch.innerHTML = ""; (mj.chapters || []).forEach(c => { const b = document.createElement("button"); b.className = "sc"; b.textContent = c[1]; b.addEventListener("click", () => { setT(S.T0 + c[0]); S.needDraw = true; }); ch.appendChild(b); }); }
    if (S.native) { S.native = false; setNative(true); }
    if (S.ac) fetch(base + "audio.flac").then(r => r.arrayBuffer()).then(ab => S.ac.decodeAudioData(ab)).then(b => { S.abuf = b; if (S.playing) startAudio(now()); }).catch(() => {});
  }
  async function init() {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) { S.ac = new AC(); S.gain = S.ac.createGain(); S.gain.connect(S.ac.destination); }
    const sel = $("segSel"); if (sel) sel.addEventListener("change", () => load(sel.value));
    await load(new URLSearchParams(location.search).get("seg") || "main");
  }
  const pb = $("pauseBtn"); if (pb) pb.addEventListener("click", () => (S.playing ? pause() : play()));
  const sb = $("seekBar"); if (sb) sb.addEventListener("input", () => { setT(S.T0 + +sb.value); S.needDraw = true; });
  const nb = $("nativeBtn"); if (nb) nb.addEventListener("click", () => setNative(!S.native));
  const mb = $("soundBtn"); if (mb) mb.addEventListener("click", () => { S.muted = !S.muted; if (S.gain) S.gain.gain.value = S.muted ? 0 : 1; mb.textContent = S.muted ? "Sound off" : "Sound on"; });
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase(); if (tag === "input" && e.target.type !== "range" || tag === "textarea") return;
    if (e.key === " ") { e.preventDefault(); S.playing ? pause() : play(); }
    else if (e.key === "ArrowRight") { setT(now() + 5); S.needDraw = true; }
    else if (e.key === "ArrowLeft") { setT(now() - 5); S.needDraw = true; }
    else if (e.key === "n" || e.key === "N") setNative(!S.native);
    else if (e.key === "." && !S.playing) { setT(now() + 1 / 70); S.needDraw = true; }
    else if (e.key === "," && !S.playing) { setT(now() - 1 / 70); S.needDraw = true; }
  });
  init().then(loop);
  window.CD2Player = { play, pause, setT, setNative, S };
})();
