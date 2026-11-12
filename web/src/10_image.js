// ===================================================================== images: PNG / DDS / TGA, DXT, resize, PNG encode
async function streamBytes(u8, Ctor, format) {
  const s = new Ctor(format);
  const w = s.writable.getWriter();
  w.write(u8).catch(() => {});
  w.close().catch(() => {});
  const r = s.readable.getReader();
  const chunks = [];
  for (;;) {
    const { done, value } = await r.read();
    if (done) break;
    chunks.push(value);
  }
  return concatBytes(chunks);
}
const inflateRaw = (u8) => streamBytes(u8, DecompressionStream, 'deflate-raw');
const inflateZlib = (u8) => streamBytes(u8, DecompressionStream, 'deflate');
const deflateRaw = (u8) => streamBytes(u8, CompressionStream, 'deflate-raw');
const deflateZlib = (u8) => streamBytes(u8, CompressionStream, 'deflate');

// image = { w, h, data: Uint8Array(w*h*4) RGBA }
function newImage(w, h, fill) {
  const data = new Uint8Array(w * h * 4);
  if (fill) for (let i = 0; i < data.length; i += 4) { data[i] = fill[0]; data[i + 1] = fill[1]; data[i + 2] = fill[2]; data[i + 3] = fill[3]; }
  return { w, h, data };
}
function imageHasAlpha(img) {
  const d = img.data;
  for (let i = 3; i < d.length; i += 4) if (d[i] < 250) return true;
  return false;
}

// ---------------------------------------------------------------- PNG decode
const PNG_SIG = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
async function decodePng(u8) {
  const d = dv(u8);
  let off = 8, hdr = null, plte = null, trns = null;
  const idat = [];
  while (off + 8 <= u8.length) {
    const len = d.getUint32(off), type = latin1(u8, off + 4, off + 8);
    const body = u8.subarray(off + 8, off + 8 + len);
    if (type === 'IHDR') {
      const b = dv(body);
      hdr = { w: b.getUint32(0), h: b.getUint32(4), depth: body[8], ctype: body[9], interlace: body[12] };
    } else if (type === 'PLTE') plte = body;
    else if (type === 'tRNS') trns = body;
    else if (type === 'IDAT') idat.push(body);
    else if (type === 'IEND') break;
    off += 12 + len;
  }
  if (!hdr || !idat.length) throw new ConversionError('damaged PNG');
  const { w, h, depth, ctype, interlace } = hdr;
  const chans = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ctype];
  if (!chans) throw new ConversionError('unsupported PNG colour type');
  const bpp = Math.max(1, (chans * depth) >> 3);           // bytes per pixel for filtering
  const raw = await inflateZlib(concatBytes(idat));
  const out = new Uint8Array(w * h * 4);
  const maxv = (1 << Math.min(depth, 8)) - 1;
  const passes = interlace
    ? [[0, 0, 8, 8], [4, 0, 8, 8], [0, 4, 4, 8], [2, 0, 4, 4], [0, 2, 2, 4], [1, 0, 2, 2], [0, 1, 1, 2]]
    : [[0, 0, 1, 1]];
  let pos = 0;
  for (const [x0, y0, dx, dy] of passes) {
    const pw = Math.ceil((w - x0) / dx), ph = Math.ceil((h - y0) / dy);
    if (pw <= 0 || ph <= 0) continue;
    const stride = (pw * chans * depth + 7) >> 3;
    let prev = new Uint8Array(stride);
    for (let y = 0; y < ph; y++) {
      const ft = raw[pos++];
      const cur = raw.slice(pos, pos + stride);
      pos += stride;
      if (cur.length < stride) throw new ConversionError('truncated PNG data');
      for (let i = 0; i < stride; i++) {
        const a = i >= bpp ? cur[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
        let v = cur[i];
        if (ft === 1) v += a;
        else if (ft === 2) v += b;
        else if (ft === 3) v += (a + b) >> 1;
        else if (ft === 4) {
          const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
          v += (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
        }
        cur[i] = v & 255;
      }
      const cd = dv(cur);
      const sample = (idx) => {                         // idx-th sample of the row
        if (depth === 8) return cur[idx];
        if (depth === 16) return cd.getUint16(idx * 2);
        const per = 8 / depth, byte = cur[(idx / per) | 0];
        return (byte >> (8 - depth * ((idx % per) + 1))) & ((1 << depth) - 1);
      };
      for (let x = 0; x < pw; x++) {
        const o = ((y0 + y * dy) * w + (x0 + x * dx)) * 4;
        let r, g, bl, al = 255;
        if (ctype === 3) {
          const i = sample(x);
          r = plte[i * 3]; g = plte[i * 3 + 1]; bl = plte[i * 3 + 2];
          if (trns && i < trns.length) al = trns[i];
        } else {
          const s = (k) => { const v = sample(x * chans + k); return depth === 16 ? v >> 8 : (depth < 8 ? Math.round(v * 255 / maxv) : v); };
          if (ctype === 0) {
            r = g = bl = s(0);
            if (trns && trns.length >= 2 && sample(x) === ((trns[0] << 8) | trns[1])) al = 0;
          } else if (ctype === 4) { r = g = bl = s(0); al = s(1); }
          else if (ctype === 2) { r = s(0); g = s(1); bl = s(2); }
          else { r = s(0); g = s(1); bl = s(2); al = s(3); }
        }
        out[o] = r; out[o + 1] = g; out[o + 2] = bl; out[o + 3] = al;
      }
      prev = cur;
    }
  }
  return { w, h, data: out };
}

// ---------------------------------------------------------------- PNG encode
async function encodePng(img) {
  const { w, h, data } = img;
  let opaque = true;
  for (let i = 3; i < data.length; i += 4) if (data[i] !== 255) { opaque = false; break; }
  const ch = opaque ? 3 : 4;
  const stride = w * ch;
  const rows = new Uint8Array((stride + 1) * h);
  const line = new Uint8Array(stride);
  const prev = new Uint8Array(stride);
  const cands = [0, 1, 2, 3, 4].map(() => new Uint8Array(stride));
  for (let y = 0; y < h; y++) {
    if (opaque) for (let x = 0, o = y * w * 4, p = 0; x < w; x++, o += 4) { line[p++] = data[o]; line[p++] = data[o + 1]; line[p++] = data[o + 2]; }
    else line.set(data.subarray(y * stride, (y + 1) * stride));
    let best = 0, bestScore = Infinity;
    for (let f = 0; f < 5; f++) {
      const c = cands[f];
      let score = 0;
      for (let i = 0; i < stride; i++) {
        const a = i >= ch ? line[i - ch] : 0, b = prev[i], cc = i >= ch ? prev[i - ch] : 0;
        let p;
        if (f === 0) p = 0;
        else if (f === 1) p = a;
        else if (f === 2) p = b;
        else if (f === 3) p = (a + b) >> 1;
        else { const q = a + b - cc, pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - cc); p = (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : cc); }
        const v = (line[i] - p) & 255;
        c[i] = v;
        score += v < 128 ? v : 256 - v;
      }
      if (score < bestScore) { bestScore = score; best = f; }
    }
    rows[y * (stride + 1)] = best;
    rows.set(cands[best], y * (stride + 1) + 1);
    prev.set(line);
  }
  const z = await deflateZlib(rows);
  const chunk = (type, body) => {
    const o = new Uint8Array(12 + body.length), d = dv(o);
    d.setUint32(0, body.length);
    for (let i = 0; i < 4; i++) o[4 + i] = type.charCodeAt(i);
    o.set(body, 8);
    d.setUint32(8 + body.length, crc32(o.subarray(4, 8 + body.length)));
    return o;
  };
  const ihdr = new Uint8Array(13), id = dv(ihdr);
  id.setUint32(0, w); id.setUint32(4, h); ihdr[8] = 8; ihdr[9] = opaque ? 2 : 6;
  return concatBytes([Uint8Array.from(PNG_SIG), chunk('IHDR', ihdr), chunk('IDAT', z), chunk('IEND', new Uint8Array(0))]);
}

// ---------------------------------------------------------------- DXT (BC1/2/3)
function decodeDxtBlocks(w, h, fourcc, src) {
  const out = new Uint8Array(w * h * 4);
  const bw = (w + 3) >> 2, bh = (h + 3) >> 2;
  const bsz = fourcc === 'DXT1' ? 8 : 16;
  if (src.length < bw * bh * bsz) throw new ConversionError('truncated DXT texture data');
  const d = dv(src);
  const pal = new Uint8Array(16), al = new Uint8Array(16), alp = new Uint8Array(8);
  const e5 = (v) => (v << 3) | (v >> 2), e6 = (v) => (v << 2) | (v >> 4);
  for (let by = 0; by < bh; by++) {
    for (let bx = 0; bx < bw; bx++) {
      let o = (by * bw + bx) * bsz;
      if (fourcc === 'DXT3') {
        for (let i = 0; i < 8; i++) { const b = src[o + i]; al[i * 2] = (b & 15) * 17; al[i * 2 + 1] = (b >> 4) * 17; }
        o += 8;
      } else if (fourcc === 'DXT5') {
        const a0 = src[o], a1 = src[o + 1];
        alp[0] = a0; alp[1] = a1;
        if (a0 > a1) for (let i = 1; i < 7; i++) alp[i + 1] = Math.floor(((7 - i) * a0 + i * a1) / 7);
        else {
          for (let i = 1; i < 5; i++) alp[i + 1] = Math.floor(((5 - i) * a0 + i * a1) / 5);
          alp[6] = 0; alp[7] = 255;
        }
        let bits = 0n;
        for (let i = 7; i >= 2; i--) bits = (bits << 8n) | BigInt(src[o + i]);
        for (let i = 0; i < 16; i++) al[i] = alp[Number((bits >> BigInt(i * 3)) & 7n)];
        o += 8;
      }
      const c0 = d.getUint16(o, true), c1 = d.getUint16(o + 2, true);
      const r0 = e5(c0 >> 11), g0 = e6((c0 >> 5) & 63), b0 = e5(c0 & 31);
      const r1 = e5(c1 >> 11), g1 = e6((c1 >> 5) & 63), b1 = e5(c1 & 31);
      pal.set([r0, g0, b0, 255, r1, g1, b1, 255]);
      if (c0 > c1 || fourcc !== 'DXT1') {
        pal.set([Math.floor((2 * r0 + r1) / 3), Math.floor((2 * g0 + g1) / 3), Math.floor((2 * b0 + b1) / 3), 255,
                 Math.floor((r0 + 2 * r1) / 3), Math.floor((g0 + 2 * g1) / 3), Math.floor((b0 + 2 * b1) / 3), 255], 8);
      } else {
        pal.set([(r0 + r1) >> 1, (g0 + g1) >> 1, (b0 + b1) >> 1, 255, 0, 0, 0, 0], 8);
      }
      const idx = d.getUint32(o + 4, true);
      for (let i = 0; i < 16; i++) {
        const x = bx * 4 + (i & 3), y = by * 4 + (i >> 2);
        if (x >= w || y >= h) continue;
        const k = ((idx >>> (i * 2)) & 3) * 4, p = (y * w + x) * 4;
        out[p] = pal[k]; out[p + 1] = pal[k + 1]; out[p + 2] = pal[k + 2];
        out[p + 3] = fourcc === 'DXT1' ? pal[k + 3] : al[i];
      }
    }
  }
  return { w, h, data: out };
}

// ---------------------------------------------------------------- DDS
function decodeDds(u8) {
  const d = dv(u8);
  if (u8.length < 128 || latin1(u8, 0, 4) !== 'DDS ') throw new ConversionError('not a DDS file');
  const h = d.getUint32(12, true), w = d.getUint32(16, true);
  const pfFlags = d.getUint32(80, true), four = latin1(u8, 84, 88);
  const bits = d.getUint32(88, true);
  const masks = [d.getUint32(92, true), d.getUint32(96, true), d.getUint32(100, true), d.getUint32(104, true)];
  let off = 128;
  if (pfFlags & 4) {
    let fc = four;
    if (fc === 'DX10') {
      const dxgi = d.getUint32(128, true);
      off = 148;
      fc = { 71: 'DXT1', 72: 'DXT1', 74: 'DXT3', 75: 'DXT3', 77: 'DXT5', 78: 'DXT5' }[dxgi];
      if (!fc) {
        if (dxgi === 28 || dxgi === 29) return decodeDdsRaw(u8, off, w, h, 32, [0xFF, 0xFF00, 0xFF0000, 0xFF000000]);
        if (dxgi === 87 || dxgi === 91) return decodeDdsRaw(u8, off, w, h, 32, [0xFF0000, 0xFF00, 0xFF, 0xFF000000]);
        throw new ConversionError(`unsupported DDS format (DXGI ${dxgi})`);
      }
    }
    if (fc === 'DXT2') fc = 'DXT3';
    if (fc === 'DXT4') fc = 'DXT5';
    if (fc === 'DXT1' || fc === 'DXT3' || fc === 'DXT5') return decodeDxtBlocks(w, h, fc, u8.subarray(off));
    throw new ConversionError(`unsupported DDS compression '${fc}'`);
  }
  return decodeDdsRaw(u8, off, w, h, bits, masks, (pfFlags & 1) !== 0, (pfFlags & 0x20000) !== 0);
}
function decodeDdsRaw(u8, off, w, h, bits, masks, hasAlphaFlag = true, luminance = false) {
  const bytes = bits >> 3;
  if (![1, 2, 3, 4].includes(bytes)) throw new ConversionError('unsupported uncompressed DDS layout');
  if (u8.length < off + w * h * bytes) throw new ConversionError('truncated DDS data');
  const out = new Uint8Array(w * h * 4);
  const shifts = masks.map((m) => { if (!m) return [0, 0]; let s = 0; while (!((m >>> s) & 1)) s++; let n = 0; while ((m >>> (s + n)) & 1) n++; return [s, n]; });
  const ch = (v, i) => {
    const m = masks[i];
    if (!m) return null;
    const [s, n] = shifts[i];
    const x = (v & m) >>> s;
    return n >= 8 ? x >> (n - 8) : Math.round(x * 255 / ((1 << n) - 1));
  };
  for (let i = 0; i < w * h; i++) {
    let v = 0;
    for (let k = 0; k < bytes; k++) v += u8[off + i * bytes + k] * Math.pow(2, 8 * k);
    const p = i * 4;
    if (luminance) { const l = ch(v, 0); out[p] = out[p + 1] = out[p + 2] = l; const a = ch(v, 3); out[p + 3] = a === null ? 255 : a; continue; }
    const r = ch(v, 0), g = ch(v, 1), b = ch(v, 2), a = ch(v, 3);
    out[p] = r === null ? 0 : r; out[p + 1] = g === null ? 0 : g; out[p + 2] = b === null ? 0 : b;
    out[p + 3] = (a === null || !hasAlphaFlag) ? 255 : a;
  }
  return { w, h, data: out };
}

// ---------------------------------------------------------------- TGA
function decodeTga(u8) {
  if (u8.length < 18) throw new ConversionError('not a TGA file');
  const idLen = u8[0], cmType = u8[1], type = u8[2];
  const d = dv(u8);
  const cmFirst = d.getUint16(3, true), cmLen = d.getUint16(5, true), cmBits = u8[7];
  const w = d.getUint16(12, true), h = d.getUint16(14, true), bpp = u8[16], desc = u8[17];
  if (!w || !h || ![1, 2, 3, 9, 10, 11].includes(type)) throw new ConversionError('unsupported TGA type');
  const rle = type >= 9, kind = type & 3;
  let p = 18 + idLen;
  let cmap = null;
  if (cmType === 1) {
    const cb = (cmBits + 7) >> 3;
    cmap = [];
    for (let i = 0; i < cmLen; i++) { cmap.push(tgaPixel(u8, p + i * cb, cmBits)); }
    p += cmLen * cb;
  }
  const pb = (bpp + 7) >> 3;
  const px = new Array(w * h);
  let n = 0;
  const readOne = (q) => {
    if (kind === 1) { const i = u8[q] - cmFirst; return cmap[i] || [0, 0, 0, 255]; }
    if (kind === 3) { const l = u8[q]; return [l, l, l, bpp === 16 ? u8[q + 1] : 255]; }
    return tgaPixel(u8, q, bpp);
  };
  if (!rle) {
    for (; n < w * h; n++) { if (p + pb > u8.length) throw new ConversionError('truncated TGA'); px[n] = readOne(p); p += pb; }
  } else {
    while (n < w * h && p < u8.length) {
      const hd = u8[p++], cnt = (hd & 127) + 1;
      if (hd & 128) { const v = readOne(p); p += pb; for (let i = 0; i < cnt && n < w * h; i++) px[n++] = v; }
      else for (let i = 0; i < cnt && n < w * h; i++) { px[n++] = readOne(p); p += pb; }
    }
    if (n < w * h) throw new ConversionError('truncated TGA');
  }
  const out = new Uint8Array(w * h * 4);
  const topLeft = (desc & 0x20) !== 0, rightToLeft = (desc & 0x10) !== 0;
  for (let y = 0; y < h; y++) {
    const sy = topLeft ? y : h - 1 - y;
    for (let x = 0; x < w; x++) {
      const sx = rightToLeft ? w - 1 - x : x;
      const c = px[sy * w + sx], o = (y * w + x) * 4;
      out[o] = c[0]; out[o + 1] = c[1]; out[o + 2] = c[2]; out[o + 3] = bpp === 32 || (kind === 3 && bpp === 16) || (cmap && cmBits === 32) ? c[3] : 255;
    }
  }
  return { w, h, data: out };
}
function tgaPixel(u8, q, bits) {
  if (bits === 32) return [u8[q + 2], u8[q + 1], u8[q], u8[q + 3]];
  if (bits === 24) return [u8[q + 2], u8[q + 1], u8[q], 255];
  if (bits === 16 || bits === 15) {
    const v = u8[q] | (u8[q + 1] << 8);
    const e = (x) => (x << 3) | (x >> 2);
    return [e((v >> 10) & 31), e((v >> 5) & 31), e(v & 31), (v & 0x8000) ? 255 : 255];
  }
  const l = u8[q];
  return [l, l, l, 255];
}

// ---------------------------------------------------------------- generic decode (uses the browser for JPEG/GIF/WebP/BMP)
async function browserDecode(u8) {
  if (typeof createImageBitmap !== 'function' || typeof Blob === 'undefined') {
    throw new ConversionError('this image format needs a browser to decode');
  }
  const bmp = await createImageBitmap(new Blob([u8]), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
  let ctx;
  if (typeof OffscreenCanvas !== 'undefined') ctx = new OffscreenCanvas(bmp.width, bmp.height).getContext('2d');
  else {
    const c = document.createElement('canvas');
    c.width = bmp.width; c.height = bmp.height;
    ctx = c.getContext('2d');
  }
  ctx.drawImage(bmp, 0, 0);
  const id = ctx.getImageData(0, 0, bmp.width, bmp.height);
  if (bmp.close) bmp.close();
  return { w: id.width, h: id.height, data: new Uint8Array(id.data.buffer.slice(0)) };
}
async function decodeImage(u8, nameHint) {
  const ext = nameHint ? extOf(nameHint) : '';
  try {
    if (u8.length > 8 && PNG_SIG.every((b, i) => u8[i] === b)) {
      try { return await decodePng(u8); }
      catch (e) { try { return await browserDecode(u8); } catch (e2) { throw e; } }
    }
    if (u8.length > 4 && latin1(u8, 0, 4) === 'DDS ') return decodeDds(u8);
    if (ext === '.tga') return decodeTga(u8);
    if (u8[0] === 0xFF && u8[1] === 0xD8) return await browserDecode(u8);
    if (latin1(u8, 0, 3) === 'GIF' || latin1(u8, 0, 2) === 'BM' || (latin1(u8, 0, 4) === 'RIFF' && latin1(u8, 8, 12) === 'WEBP')) {
      return await browserDecode(u8);
    }
    try { return decodeTga(u8); } catch (e) { /* fall through */ }
    return await browserDecode(u8);
  } catch (e) {
    if (e instanceof ConversionError) throw e;
    throw new ConversionError(`cannot decode image: ${e && e.message ? e.message : e}`);
  }
}

// ---------------------------------------------------------------- resize (area average, premultiplied alpha)
function fitTexture(img, maxSize) {
  if (!maxSize || Math.max(img.w, img.h) <= maxSize) return img;
  const s = maxSize / Math.max(img.w, img.h);
  return resizeArea(img, Math.max(1, Math.round(img.w * s)), Math.max(1, Math.round(img.h * s)));
}
function resizeArea(img, nw, nh) {
  const { w, h, data } = img;
  // horizontal pass -> Float32 (premultiplied)
  const tmp = new Float32Array(nw * h * 4);
  const sx = w / nw, sy = h / nh;
  for (let x = 0; x < nw; x++) {
    const a = x * sx, b = (x + 1) * sx;
    const i0 = Math.floor(a), i1 = Math.min(w - 1, Math.ceil(b) - 1);
    for (let y = 0; y < h; y++) {
      let r = 0, g = 0, bl = 0, al = 0, wt = 0;
      for (let i = i0; i <= i1; i++) {
        const k = Math.min(b, i + 1) - Math.max(a, i);
        if (k <= 0) continue;
        const p = (y * w + i) * 4, A = data[p + 3] / 255;
        r += data[p] * A * k; g += data[p + 1] * A * k; bl += data[p + 2] * A * k; al += A * k; wt += k;
      }
      const o = (y * nw + x) * 4;
      tmp[o] = r / wt; tmp[o + 1] = g / wt; tmp[o + 2] = bl / wt; tmp[o + 3] = al / wt;
    }
  }
  const out = new Uint8Array(nw * nh * 4);
  for (let y = 0; y < nh; y++) {
    const a = y * sy, b = (y + 1) * sy;
    const j0 = Math.floor(a), j1 = Math.min(h - 1, Math.ceil(b) - 1);
    for (let x = 0; x < nw; x++) {
      let r = 0, g = 0, bl = 0, al = 0, wt = 0;
      for (let j = j0; j <= j1; j++) {
        const k = Math.min(b, j + 1) - Math.max(a, j);
        if (k <= 0) continue;
        const p = (j * nw + x) * 4;
        r += tmp[p] * k; g += tmp[p + 1] * k; bl += tmp[p + 2] * k; al += tmp[p + 3] * k; wt += k;
      }
      r /= wt; g /= wt; bl /= wt; al /= wt;
      const o = (y * nw + x) * 4;
      if (al > 0) {
        out[o] = Math.min(255, Math.round(r / al)); out[o + 1] = Math.min(255, Math.round(g / al));
        out[o + 2] = Math.min(255, Math.round(bl / al));
      }
      out[o + 3] = Math.round(al * 255);
    }
  }
  return { w: nw, h: nh, data: out };
}

// ---------------------------------------------------------------- misc texture helpers
function normName(name) {
  const n = name.replace(/\\/g, '/').split('/').pop().trim().toLowerCase();
  return n.replace(/\.(dds|png|jpg|jpeg|tga|bmp|tif|tiff|gif|webp)$/, '');
}
function parseHexColor(value, dflt = [0.8, 0.1, 0.1]) {
  if (value === null || value === undefined) return dflt;
  let s = String(value).trim();
  const clamp = (v) => Math.max(0, Math.min(1, v));
  if (s.includes(',')) {
    const parts = s.split(',').slice(0, 3).map(Number);
    if (parts.some(Number.isNaN)) throw new ConversionError(`bad colour '${value}' (use #rrggbb)`);
    return parts.map((p) => clamp(p / 255));
  }
  s = s.replace(/^#+/, '');
  if (s.length === 3) s = s.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) throw new ConversionError(`bad colour '${value}' (use #rrggbb)`);
  return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16) / 255);
}
function makeTexture(name, image) {
  let alpha = null;
  return { name, image, hasAlpha() { if (alpha === null) alpha = imageHasAlpha(image); return alpha; } };
}
