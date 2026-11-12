// ===================================================================== RenderWare (DFF / TXD)
const RWID = { STRUCT: 0x01, STRING: 0x02, EXT: 0x03, TEXTURE: 0x06, MATERIAL: 0x07, MATLIST: 0x08,
  FRAMELIST: 0x0E, GEOMETRY: 0x0F, CLUMP: 0x10, ATOMIC: 0x14, TEXNATIVE: 0x15, TEXDICT: 0x16,
  GEOMLIST: 0x1A, BINMESH: 0x50E, FRAMENAME: 0x253F2FE };
const GEO = { TRISTRIP: 1, TEXTURED: 4, PRELIT: 8, NORMALS: 0x10, TEXTURED2: 0x80, NATIVE: 0x01000000 };
const RW_LOCK = 0x01000000;

function rwVersion(lib) {
  if ((lib & 0xFFFF0000) !== 0) return (((lib >>> 14) & 0x3FF00) + 0x30000) | ((lib >>> 16) & 0x3F);
  return lib << 8;
}
function readChunk(u8, d, off, limit) {
  if (off + 12 > limit) throw new ConversionError('unexpected end of data (truncated file?)');
  const t = d.getUint32(off, true);
  let size = d.getUint32(off + 4, true);
  const lib = d.getUint32(off + 8, true);
  const start = off + 12;
  if (size > limit - start && (t === RWID.STRUCT || t === RWID.TEXTURE) && ((size & ~RW_LOCK) >>> 0) <= limit - start) {
    size = (size & ~RW_LOCK) >>> 0;
  }
  if (size > limit - start) size = limit - start;
  return { type: t, size, version: rwVersion(lib), start, end: start + size };
}
function chunksOf(u8, d, start, end) {
  const out = [];
  let off = start;
  while (off + 12 <= end) {
    const c = readChunk(u8, d, off, end);
    out.push(c);
    off = c.end;
  }
  return out;
}
const rwString = (u8, c) => cstr(u8, c.start, c.end);
function f32s(d, off, n) {
  const a = new Float32Array(n);
  for (let i = 0; i < n; i++) a[i] = d.getFloat32(off + i * 4, true);
  return a;
}

function parseFrameList(u8, d, c) {
  const kids = chunksOf(u8, d, c.start, c.end);
  if (!kids.length || kids[0].type !== RWID.STRUCT) throw new ConversionError('frame list without struct');
  const s = kids[0];
  const count = d.getUint32(s.start, true);
  if (s.start + 4 + count * 56 > u8.length) throw new ConversionError('frame list is truncated');
  const frames = [];
  for (let i = 0; i < count; i++) {
    const o = s.start + 4 + i * 56;
    const m = []; for (let k = 0; k < 9; k++) m.push(d.getFloat32(o + k * 4, true));
    const pos = [d.getFloat32(o + 36, true), d.getFloat32(o + 40, true), d.getFloat32(o + 44, true)];
    const parent = d.getInt32(o + 48, true);
    // the file stores right, up, at vectors -> they are the columns of R
    const R = new Array(9);
    for (let r = 0; r < 3; r++) for (let q = 0; q < 3; q++) R[r * 3 + q] = m[q * 3 + r];
    frames.push({ name: `frame${i}`, rotation: R, position: pos, parent });
  }
  const exts = kids.slice(1).filter((k) => k.type === RWID.EXT);
  exts.slice(0, count).forEach((ext, i) => {
    for (const sub of chunksOf(u8, d, ext.start, ext.end)) {
      if (sub.type === RWID.FRAMENAME) frames[i].name = rwString(u8, sub) || frames[i].name;
    }
  });
  return frames;
}
function parseTextureRef(u8, d, c) {
  const strings = chunksOf(u8, d, c.start, c.end).filter((k) => k.type === RWID.STRING);
  return [strings.length ? rwString(u8, strings[0]) : null, strings.length > 1 ? rwString(u8, strings[1]) : null];
}
function parseMaterialList(u8, d, c) {
  const kids = chunksOf(u8, d, c.start, c.end);
  if (!kids.length || kids[0].type !== RWID.STRUCT) return [];
  const count = d.getUint32(kids[0].start, true);
  const indices = [];
  for (let i = 0; i < count; i++) indices.push(d.getInt32(kids[0].start + 4 + i * 4, true));
  const mats = [];
  const raw = kids.slice(1).filter((k) => k.type === RWID.MATERIAL);
  let ri = 0;
  for (const idx of indices) {
    if (idx >= 0 && idx < mats.length) { mats.push(mats[idx]); continue; }
    if (ri >= raw.length) { mats.push({ color: [255, 255, 255, 255], texture: null, mask: null }); continue; }
    const mc = raw[ri++];
    const m = { color: [255, 255, 255, 255], texture: null, mask: null };
    for (const sub of chunksOf(u8, d, mc.start, mc.end)) {
      if (sub.type === RWID.STRUCT && sub.size >= 16) {
        m.color = [u8[sub.start + 4], u8[sub.start + 5], u8[sub.start + 6], u8[sub.start + 7]];
      } else if (sub.type === RWID.TEXTURE) {
        [m.texture, m.mask] = parseTextureRef(u8, d, sub);
      }
    }
    mats.push(m);
  }
  return mats;
}
function readBinMesh(u8, d, c, numVerts) {
  const flags = d.getUint32(c.start, true), meshCount = d.getUint32(c.start + 4, true), total = d.getUint32(c.start + 8, true);
  const strip = flags === 1;
  const body = c.size - 12 - meshCount * 8;
  const wide = body >= total * 4;
  let off = c.start + 12;
  const tris = [], mats = [];
  for (let m = 0; m < meshCount; m++) {
    const n = d.getUint32(off, true), mat = d.getUint32(off + 4, true);
    off += 8;
    const idx = new Array(n);
    for (let i = 0; i < n; i++) idx[i] = wide ? d.getUint32(off + i * 4, true) : d.getUint16(off + i * 2, true);
    off += n * (wide ? 4 : 2);
    if (strip) {
      for (let i = 0; i + 2 < n; i++) {
        const a = idx[i], b = idx[i + 1], cc = idx[i + 2];
        const t = (i & 1) ? [b, a, cc] : [a, b, cc];
        if (t[0] !== t[1] && t[1] !== t[2] && t[0] !== t[2]) { tris.push(t); mats.push(mat); }
      }
    } else {
      for (let i = 0; i + 2 < n; i += 3) { tris.push([idx[i], idx[i + 1], idx[i + 2]]); mats.push(mat); }
    }
  }
  const T = [], M = [];
  tris.forEach((t, i) => { if (t[0] < numVerts && t[1] < numVerts && t[2] < numVerts) { T.push(t); M.push(mats[i]); } });
  return [T, M];
}
function parseGeometry(u8, d, c) {
  const kids = chunksOf(u8, d, c.start, c.end);
  if (!kids.length || kids[0].type !== RWID.STRUCT) throw new ConversionError('geometry without struct');
  const s = kids[0];
  const flags = d.getUint32(s.start, true), ntri = d.getUint32(s.start + 4, true), nvert = d.getUint32(s.start + 8, true);
  if (flags & GEO.NATIVE) {
    throw new ConversionError('this DFF contains console-native geometry (PS2/Xbox); only PC DFFs are supported');
  }
  let pos = s.start + 16;
  if (c.version < 0x34000) pos += 12;
  if (pos > s.end) throw new ConversionError('geometry struct is truncated');
  if (flags & GEO.PRELIT) pos += 4 * nvert;
  let nsets = (flags >>> 16) & 0xFF;
  if (nsets === 0) nsets = (flags & GEO.TEXTURED2) ? 2 : ((flags & GEO.TEXTURED) ? 1 : 0);
  let uvs = null;
  if (nsets) {
    if (pos + nvert * 8 > u8.length) throw new ConversionError('geometry struct is truncated');
    uvs = f32s(d, pos, nvert * 2);
    pos += nsets * nvert * 8;
  }
  const triArr = [];
  for (let i = 0; i < ntri; i++) {
    const o = pos + i * 8;
    triArr.push([d.getUint16(o, true), d.getUint16(o + 2, true), d.getUint16(o + 4, true), d.getUint16(o + 6, true)]);
  }
  pos += ntri * 8;
  pos += 16;
  const hasV = d.getUint32(pos, true), hasN = d.getUint32(pos + 4, true);
  pos += 8;
  if (!hasV) throw new ConversionError('geometry has no vertices');
  const positions = f32s(d, pos, nvert * 3);
  pos += nvert * 12;
  const normals = hasN ? f32s(d, pos, nvert * 3) : null;
  // file order is (v[1], v[0], material, v[2])
  let triangles = triArr.map((t) => [t[1], t[0], t[3]]);
  let triMats = triArr.map((t) => t[2]);
  let materials = [], binmesh = null;
  for (const k of kids.slice(1)) {
    if (k.type === RWID.MATLIST) materials = parseMaterialList(u8, d, k);
    else if (k.type === RWID.EXT) {
      for (const sub of chunksOf(u8, d, k.start, k.end)) if (sub.type === RWID.BINMESH) binmesh = sub;
    }
  }
  if (triangles.length === 0 && binmesh) [triangles, triMats] = readBinMesh(u8, d, binmesh, nvert);
  if (triangles.some((t) => t[0] >= nvert || t[1] >= nvert || t[2] >= nvert)) {
    const keep = triangles.map((t) => t[0] < nvert && t[1] < nvert && t[2] < nvert);
    triangles = triangles.filter((_, i) => keep[i]);
    triMats = triMats.filter((_, i) => keep[i]);
  }
  return { positions, normals, uvs, triangles, triMats, materials, flags };
}
function parseDff(u8) {
  if (u8.length < 12) throw new ConversionError('file is too small to be a DFF');
  const d = dv(u8);
  const top = readChunk(u8, d, 0, u8.length);
  if (top.type !== RWID.CLUMP) throw new ConversionError('not a RenderWare DFF (no clump chunk at the start)');
  const dff = { frames: [], geometries: [], atomics: [] };
  for (const c of chunksOf(u8, d, top.start, top.end)) {
    if (c.type === RWID.FRAMELIST) dff.frames = parseFrameList(u8, d, c);
    else if (c.type === RWID.GEOMLIST) {
      for (const g of chunksOf(u8, d, c.start, c.end)) if (g.type === RWID.GEOMETRY) dff.geometries.push(parseGeometry(u8, d, g));
    } else if (c.type === RWID.ATOMIC) {
      const kids = chunksOf(u8, d, c.start, c.end);
      if (kids.length && kids[0].type === RWID.STRUCT) {
        const s = kids[0].start;
        if (kids[0].size >= 16) dff.atomics.push([d.getUint32(s, true), d.getUint32(s + 4, true)]);
        else dff.atomics.push([d.getUint32(s, true), 0]);
      }
    }
  }
  if (!dff.geometries.length) throw new ConversionError('the DFF has no geometry');
  return dff;
}
// world rotation (row-major 3x3) / translation of a frame
function dffWorld(dff, idx) {
  const chain = [];
  const seen = new Set();
  let i = idx;
  while (i >= 0 && i < dff.frames.length && !seen.has(i)) { seen.add(i); chain.push(dff.frames[i]); i = dff.frames[i].parent; }
  let R = I3(), t = [0, 0, 0];
  for (let k = chain.length - 1; k >= 0; k--) {
    const f = chain[k];
    const rp = mvec(R, f.position);
    t = [rp[0] + t[0], rp[1] + t[1], rp[2] + t[2]];
    R = mmul(R, f.rotation);
  }
  return [R, t];
}

// ---- TXD
function decodeRawPixels(pixels, w, h, fmtType, depth, d3dFmt, platform) {
  const n = w * h;
  let kind;
  if (platform === 9 && d3dFmt) kind = { 21: '8888', 22: '888', 23: '565', 24: '555', 25: '1555', 26: '4444', 50: 'L8', 51: 'A8L8' }[d3dFmt];
  else kind = { 1: '1555', 2: '565', 3: '4444', 4: 'L8', 5: '8888', 6: '888', 10: '555' }[fmtType];
  if (!kind) throw new ConversionError(`unsupported raster format (type ${fmtType}, d3d ${d3dFmt})`);
  const out = new Uint8Array(n * 4);
  const pd = dv(pixels);
  const sc = (v, bits) => Math.floor(v * 255 / ((1 << bits) - 1));
  if (kind === '8888' || kind === '888') {
    if (pixels.length < n * 4) throw new ConversionError('truncated 32-bit texture');
    for (let i = 0; i < n; i++) {
      out[i * 4] = pixels[i * 4 + 2]; out[i * 4 + 1] = pixels[i * 4 + 1]; out[i * 4 + 2] = pixels[i * 4];
      out[i * 4 + 3] = kind === '8888' ? pixels[i * 4 + 3] : 255;
    }
  } else if (kind === 'L8') {
    for (let i = 0; i < n; i++) { const l = pixels[i]; out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = l; out[i * 4 + 3] = 255; }
  } else if (kind === 'A8L8') {
    for (let i = 0; i < n; i++) { const l = pixels[i * 2]; out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = l; out[i * 4 + 3] = pixels[i * 2 + 1]; }
  } else {
    if (pixels.length < n * 2) throw new ConversionError('truncated 16-bit texture');
    for (let i = 0; i < n; i++) {
      const v = pd.getUint16(i * 2, true), o = i * 4;
      if (kind === '565') { out[o] = sc((v >> 11) & 31, 5); out[o + 1] = sc((v >> 5) & 63, 6); out[o + 2] = sc(v & 31, 5); out[o + 3] = 255; }
      else if (kind === '555') { out[o] = sc((v >> 10) & 31, 5); out[o + 1] = sc((v >> 5) & 31, 5); out[o + 2] = sc(v & 31, 5); out[o + 3] = 255; }
      else if (kind === '1555') { out[o + 3] = ((v >> 15) & 1) * 255; out[o] = sc((v >> 10) & 31, 5); out[o + 1] = sc((v >> 5) & 31, 5); out[o + 2] = sc(v & 31, 5); }
      else { out[o + 3] = sc((v >> 12) & 15, 4); out[o] = sc((v >> 8) & 15, 4); out[o + 1] = sc((v >> 4) & 15, 4); out[o + 2] = sc(v & 15, 4); }
    }
  }
  return { w, h, data: out };
}
function parseNativeTexture(u8, d, s) {
  const o = s.start;
  const platform = d.getUint32(o, true);
  if (platform !== 8 && platform !== 9) {
    throw new ConversionError(`texture uses unsupported platform id 0x${platform.toString(16)} (only PC Direct3D8/9 TXDs are supported)`);
  }
  const name = cstr(u8, o + 8, o + 40);
  const rasterFlags = d.getUint32(o + 72, true), d3dFmt = d.getUint32(o + 76, true);
  const w = d.getUint16(o + 80, true), h = d.getUint16(o + 82, true), depth = u8[o + 84];
  const prop = u8[o + 87];
  let p = o + 88;
  const fmtType = (rasterFlags >>> 8) & 0xF, palType = (rasterFlags >>> 13) & 0x3;
  let palette = null;
  if (palType) {
    const palLen = palType === 1 ? 1024 : (depth === 4 ? 64 : 128);
    palette = u8.subarray(p, p + palLen);
    p += palLen;
  }
  const size = d.getUint32(p, true);
  const pixels = u8.subarray(p + 4, p + 4 + size);
  let hasAlpha, fourcc = '';
  if (platform === 9) {
    hasAlpha = (prop & 1) !== 0;
    if (d3dFmt > 0xFFFF) fourcc = String.fromCharCode(d3dFmt & 255, (d3dFmt >>> 8) & 255, (d3dFmt >>> 16) & 255, (d3dFmt >>> 24) & 255);
  } else {
    hasAlpha = d3dFmt !== 0;
    fourcc = { 1: 'DXT1', 2: 'DXT3', 3: 'DXT3', 4: 'DXT5', 5: 'DXT5' }[prop] || '';
  }
  if (fourcc === 'DXT2') fourcc = 'DXT3';
  if (fourcc === 'DXT4') fourcc = 'DXT5';
  if (fourcc === 'DXT1' || fourcc === 'DXT3' || fourcc === 'DXT5') return [name, decodeDxtBlocks(w, h, fourcc, pixels)];
  let img;
  if (palType) {
    const n = w * h;
    const idx = new Uint8Array(n);
    if (palType === 1) idx.set(pixels.subarray(0, n));
    else for (let i = 0; i < n; i++) { const b = pixels[i >> 1]; idx[i] = (i & 1) ? (b & 15) : ((b >> 4) & 15); }
    const entries = palette.length >> 2;
    const data = new Uint8Array(n * 4);
    for (let i = 0; i < n; i++) {
      const e = Math.min(idx[i], entries - 1) * 4;
      data[i * 4] = palette[e]; data[i * 4 + 1] = palette[e + 1]; data[i * 4 + 2] = palette[e + 2];
      data[i * 4 + 3] = hasAlpha ? palette[e + 3] : 255;
    }
    img = { w, h, data };
  } else {
    img = decodeRawPixels(pixels, w, h, fmtType, depth, platform === 9 ? d3dFmt : 0, platform);
  }
  return [name, img];
}
function parseTxd(u8) {
  const d = dv(u8);
  const top = readChunk(u8, d, 0, u8.length);
  if (top.type !== RWID.TEXDICT) throw new ConversionError('not a RenderWare TXD (no texture dictionary chunk)');
  const out = new Map(), errors = [];
  for (const c of chunksOf(u8, d, top.start, top.end)) {
    if (c.type !== RWID.TEXNATIVE) continue;
    for (const k of chunksOf(u8, d, c.start, c.end)) {
      if (k.type === RWID.STRUCT) {
        try {
          const [name, img] = parseNativeTexture(u8, d, k);
          out.set(name.toLowerCase(), img);
        } catch (e) {
          if (e instanceof ConversionError) errors.push(e.message);
          else errors.push(`damaged texture (${e.message})`);
        }
        break;
      }
    }
  }
  if (!out.size && errors.length) throw new ConversionError(errors[0]);
  return out;
}

// ===================================================================== JBeam / JSON with tolerance
class JBeamError extends Error {}
function jbeamParse(text) {
  let i = 0;
  const n = text.length;
  const NUM = /[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/y;
  const WORD = /[A-Za-z_$][A-Za-z0-9_$.\-]*/y;
  const ESC = { n: '\n', t: '\t', r: '\r', b: '\b', f: '\f', '/': '/', '\\': '\\', '"': '"' };
  const err = (msg) => {
    const line = text.slice(0, i).split('\n').length;
    const col = i - (text.lastIndexOf('\n', i - 1) + 1) + 1;
    return new JBeamError(`${msg} at line ${line}, column ${col}`);
  };
  function ws() {
    while (i < n) {
      const c = text[i];
      if (c === ' ' || c === '\t' || c === '\r' || c === '\n' || c === '﻿') i++;
      else if (c === '/' && text[i + 1] === '/') { const j = text.indexOf('\n', i); i = j < 0 ? n : j + 1; }
      else if (c === '/' && text[i + 1] === '*') { const j = text.indexOf('*/', i + 2); i = j < 0 ? n : j + 2; }
      else break;
    }
  }
  function comma() { ws(); if (i < n && text[i] === ',') { i++; ws(); } }
  function string() {
    i++;
    let out = '';
    while (i < n) {
      const c = text[i];
      if (c === '"') { i++; return out; }
      if (c === '\\' && i + 1 < n) {
        const e = text[i + 1];
        if (e === 'u' && i + 5 < n && /^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) {
          out += String.fromCharCode(parseInt(text.slice(i + 2, i + 6), 16)); i += 6; continue;
        }
        out += (e in ESC) ? ESC[e] : e;
        i += 2;
        continue;
      }
      out += c; i++;
    }
    throw err('unterminated string');
  }
  function arr() {
    i++;
    const out = [];
    for (;;) {
      ws();
      if (i >= n) throw err('unterminated array');
      if (text[i] === ']') { i++; return out; }
      out.push(value());
      comma();
    }
  }
  function obj() {
    i++;
    const out = Object.create(null);
    for (;;) {
      ws();
      if (i >= n) throw err('unterminated object');
      if (text[i] === '}') { i++; return out; }
      const k = value();
      if (typeof k !== 'string') throw err('object keys must be strings');
      ws();
      if (i >= n || text[i] !== ':') throw err("expected ':'");
      i++;
      out[k] = value();
      comma();
    }
  }
  function value() {
    ws();
    if (i >= n) throw err('unexpected end of file');
    const c = text[i];
    if (c === '{') return obj();
    if (c === '[') return arr();
    if (c === '"') return string();
    NUM.lastIndex = i;
    let m = NUM.exec(text);
    if (m) { i += m[0].length; return Number(m[0]); }
    WORD.lastIndex = i;
    m = WORD.exec(text);
    if (m) { i += m[0].length; const w = m[0]; return w === 'true' ? true : w === 'false' ? false : w === 'null' ? null : w; }
    throw err(`unexpected character '${c}'`);
  }
  const v = value();
  ws();
  return v;
}
function jbeamLoadBytes(u8) { return jbeamParse(decodeText(u8)); }

// ===================================================================== tiny XML parser (enough for Collada)
class XEl {
  constructor(tag, attrs) { this.tag = tag; this.attrs = attrs; this.children = []; this.text = ''; }
  get(k, d = null) { return k in this.attrs ? this.attrs[k] : d; }
  kids(tag) { return this.children.filter((c) => c.tag === tag); }
  kid(tag) { return this.children.find((c) => c.tag === tag) || null; }
  path(p) {
    let cur = [this];
    for (const part of p.split('/')) cur = cur.flatMap((e) => e.kids(part));
    return cur;
  }
  first(p) { return this.path(p)[0] || null; }
  textOf(p) { const e = this.first(p); return e ? e.text : null; }
  desc(tag, out = []) {
    for (const c of this.children) { if (c.tag === tag) out.push(c); c.desc(tag, out); }
    return out;
  }
  descAny(tags, out = []) {
    for (const c of this.children) { if (tags.includes(c.tag)) out.push(c); c.descAny(tags, out); }
    return out;
  }
}
function xmlDecode(s) {
  if (s.indexOf('&') < 0) return s;
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|lt|gt|amp|quot|apos);/g, (m, e) => {
    if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    return { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" }[e];
  });
}
function xmlParse(text) {
  let i = 0;
  const n = text.length;
  const stack = [];
  let root = null;
  const local = (t) => { const k = t.indexOf(':'); return k < 0 ? t : t.slice(k + 1); };
  while (i < n) {
    const lt = text.indexOf('<', i);
    if (lt < 0) break;
    if (lt > i && stack.length) {
      const chunk = text.slice(i, lt);
      if (chunk.trim() || stack[stack.length - 1].children.length === 0) stack[stack.length - 1].text += xmlDecode(chunk);
    }
    if (text.startsWith('<!--', lt)) { const e = text.indexOf('-->', lt + 4); i = e < 0 ? n : e + 3; continue; }
    if (text.startsWith('<![CDATA[', lt)) {
      const e = text.indexOf(']]>', lt + 9);
      if (stack.length) stack[stack.length - 1].text += text.slice(lt + 9, e < 0 ? n : e);
      i = e < 0 ? n : e + 3;
      continue;
    }
    if (text[lt + 1] === '?') { const e = text.indexOf('?>', lt); i = e < 0 ? n : e + 2; continue; }
    if (text[lt + 1] === '!') {
      let depth = 0, j = lt;
      for (; j < n; j++) { if (text[j] === '<') depth++; else if (text[j] === '>') { depth--; if (depth === 0) break; } }
      i = j + 1;
      continue;
    }
    if (text[lt + 1] === '/') {
      const e = text.indexOf('>', lt);
      stack.pop();
      i = e < 0 ? n : e + 1;
      continue;
    }
    // start tag: find its end, honouring quotes
    let j = lt + 1, q = null;
    for (; j < n; j++) {
      const c = text[j];
      if (q) { if (c === q) q = null; } else if (c === '"' || c === "'") q = c; else if (c === '>') break;
    }
    let body = text.slice(lt + 1, j);
    const selfClose = body.endsWith('/');
    if (selfClose) body = body.slice(0, -1);
    const m = /^([^\s/>]+)/.exec(body);
    if (!m) throw new ConversionError('not valid XML');
    const attrs = {};
    const rest = body.slice(m[0].length);
    const re = /([^\s=]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
    let a;
    while ((a = re.exec(rest))) attrs[local(a[1])] = xmlDecode(a[3] !== undefined ? a[3] : a[4]);
    const el = new XEl(local(m[1]), attrs);
    if (stack.length) stack[stack.length - 1].children.push(el); else if (!root) root = el;
    if (!selfClose) stack.push(el);
    i = j + 1;
  }
  if (!root) throw new ConversionError('not valid XML');
  return root;
}

// ===================================================================== Collada
function parseNums(text) {
  if (!text) return new Float64Array(0);
  const out = [];
  const n = text.length;
  let i = 0;
  while (i < n) {
    let c = text.charCodeAt(i);
    while (i < n && (c === 32 || c === 10 || c === 13 || c === 9)) c = text.charCodeAt(++i);
    if (i >= n) break;
    const s = i;
    while (i < n && !(c === 32 || c === 10 || c === 13 || c === 9)) c = text.charCodeAt(++i);
    out.push(Number(text.substring(s, i)));
  }
  return Float64Array.from(out);
}
const refOf = (u) => (u || '').replace(/^#+/, '');
function mat4mul(a, b) {
  const o = new Array(16);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
    o[i * 4 + j] = a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j] + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j];
  }
  return o;
}
const ID4 = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
function colladaNodeMatrix(node) {
  let M = ID4();
  for (const ch of node.children) {
    const t = ch.tag;
    const v = (t === 'matrix' || t === 'translate' || t === 'rotate' || t === 'scale') ? parseNums(ch.text) : null;
    if (t === 'matrix' && v.length === 16) M = mat4mul(M, Array.from(v));
    else if (t === 'translate' && v.length === 3) M = mat4mul(M, [1, 0, 0, v[0], 0, 1, 0, v[1], 0, 0, 1, v[2], 0, 0, 0, 1]);
    else if (t === 'rotate' && v.length === 4) {
      const l = Math.hypot(v[0], v[1], v[2]);
      if (l > 0) {
        const x = v[0] / l, y = v[1] / l, z = v[2] / l, a = v[3] * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
        M = mat4mul(M, [
          c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s, 0,
          y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s, 0,
          z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c), 0, 0, 0, 0, 1]);
      }
    } else if (t === 'scale' && v.length === 3) M = mat4mul(M, [v[0], 0, 0, 0, 0, v[1], 0, 0, 0, 0, v[2], 0, 0, 0, 0, 1]);
  }
  return M;
}
function colladaSource(meshEl, srcId, want) {
  for (const src of meshEl.kids('source')) {
    if (src.get('id') !== srcId) continue;
    const arr = src.kid('float_array');
    if (!arr) continue;
    const data = parseNums(arr.text);
    const acc = src.first('technique_common/accessor');
    let stride = acc && acc.get('stride') ? parseInt(acc.get('stride'), 10) : 0;
    stride = stride || want;
    if (stride < want) throw new ConversionError(`Collada source '${srcId}' has too few components`);
    const cnt = Math.floor(data.length / stride);
    const out = new Float32Array(cnt * want);
    for (let i = 0; i < cnt; i++) for (let k = 0; k < want; k++) out[i * want + k] = data[i * stride + k];
    return out;
  }
  throw new ConversionError(`Collada source '${srcId}' not found`);
}
function colladaGeometry(gEl) {
  const mesh = gEl.kid('mesh');
  if (!mesh) return null;
  const verticesMap = {};
  for (const v of mesh.kids('vertices')) for (const inp of v.kids('input')) {
    if (inp.get('semantic') === 'POSITION') verticesMap[v.get('id', '')] = refOf(inp.get('source'));
  }
  const out = { name: gEl.get('name') || gEl.get('id') || '', pos: new Float32Array(0), uv: null, nrm: null,
    ptri: null, utri: null, ntri: null, symbolIds: null, symbols: [] };
  let posSrc = null, uvArr = null, nArr = null, haveUv = false, haveN = false;
  const P = [], U = [], N = [], S = [];
  const symbols = [];
  const prims = mesh.children.filter((p) => p.tag === 'triangles' || p.tag === 'polylist' || p.tag === 'polygons');
  prims.forEach((prim, pi) => {
    const inputs = prim.kids('input');
    if (!inputs.length) return;
    const stride = Math.max(...inputs.map((i) => parseInt(i.get('offset', '0'), 10))) + 1;
    let offV = null, offN = null, offT = null, tSet = 1 << 30;
    for (const i of inputs) {
      const sem = i.get('semantic'), off = parseInt(i.get('offset', '0'), 10), src = refOf(i.get('source'));
      if (sem === 'VERTEX') {
        offV = off;
        const posId = verticesMap[src] || src;
        if (posSrc === null) { posSrc = posId; out.pos = colladaSource(mesh, posId, 3); }
      } else if (sem === 'NORMAL' && offN === null) {
        offN = off;
        if (nArr === null) nArr = colladaSource(mesh, src, 3);
      } else if (sem === 'TEXCOORD') {
        const s = parseInt(i.get('set', '0'), 10);
        if (offT === null || s < tSet) { offT = off; tSet = s; uvArr = colladaSource(mesh, src, 2); }
      }
    }
    if (offV === null) return;
    let idx, corner = [];              // idx: flat index stream; corner: flat list of corner ids (3 per tri)
    if (prim.tag === 'triangles') {
      const p = parseNums(prim.textOf('p'));
      idx = p;
      const rows = Math.floor(p.length / stride), ct = Math.floor(rows / 3) * 3;
      for (let k = 0; k < ct; k++) corner.push(k);
    } else if (prim.tag === 'polylist') {
      const vcount = parseNums(prim.textOf('vcount'));
      idx = parseNums(prim.textOf('p'));
      let base = 0;
      for (const nv of vcount) {
        for (let k = 1; k < nv - 1; k++) corner.push(base, base + k, base + k + 1);
        base += nv;
      }
    } else {
      let base = 0;
      const chunks = [];
      for (const pe of prim.kids('p')) {
        const q = parseNums(pe.text);
        const rows = Math.floor(q.length / stride);
        for (let k = 1; k < rows - 1; k++) corner.push(base, base + k, base + k + 1);
        chunks.push(q.subarray(0, rows * stride));
        base += rows;
      }
      const tot = chunks.reduce((a, c) => a + c.length, 0);
      idx = new Float64Array(tot);
      let o = 0;
      for (const c of chunks) { idx.set(c, o); o += c.length; }
    }
    const nt = corner.length / 3;
    if (nt === 0) return;
    const pt = new Int32Array(nt * 3), ut = new Int32Array(nt * 3), nt3 = new Int32Array(nt * 3);
    for (let c = 0; c < corner.length; c++) {
      const base = corner[c] * stride;
      pt[c] = idx[base + offV];
      if (offT !== null) ut[c] = idx[base + offT];
      if (offN !== null) nt3[c] = idx[base + offN];
    }
    P.push(pt); U.push(ut); N.push(nt3);
    haveUv = haveUv || offT !== null;
    haveN = haveN || offN !== null;
    const sym = prim.get('material') || `__${pi}`;
    if (!symbols.includes(sym)) symbols.push(sym);
    S.push(new Int32Array(nt).fill(symbols.indexOf(sym)));
  });
  if (!P.length) return null;
  const cat = (list) => { const o = new Int32Array(list.reduce((a, c) => a + c.length, 0)); let k = 0; for (const c of list) { o.set(c, k); k += c.length; } return o; };
  out.ptri = cat(P);
  out.symbolIds = cat(S);
  out.symbols = symbols;
  if (haveUv && uvArr) { out.uv = uvArr; out.utri = cat(U); }
  if (haveN && nArr) { out.nrm = nArr; out.ntri = cat(N); }
  return out;
}
function colladaEffects(root) {
  const images = {};
  for (const im of root.path('library_images/image')) {
    const init = (im.textOf('init_from') || '').trim();
    images[im.get('id', '')] = init;
    if (im.get('name') && !(im.get('name') in images)) images[im.get('name')] = init;
  }
  const effects = {};
  for (const eff of root.path('library_effects/effect')) {
    const info = { color: null, tex: null, transparent: false };
    const newparams = {};
    for (const np of eff.desc('newparam')) newparams[np.get('sid')] = np;
    for (const shader of ['phong', 'blinn', 'lambert', 'constant']) {
      const sh = eff.desc(shader)[0];
      if (!sh) continue;
      const dEl = shader !== 'constant' ? sh.kid('diffuse') : sh.kid('emission');
      if (dEl) {
        const col = dEl.kid('color');
        if (col && col.text) {
          const v = parseNums(col.text);
          if (v.length >= 3) info.color = [v[0], v[1], v[2], v.length > 3 ? v[3] : 1.0];
        }
        const tx = dEl.kid('texture');
        if (tx) {
          const ref = tx.get('texture', '');
          let path = null;
          const node = newparams[ref];
          if (node) {
            const src = node.textOf('sampler2D/source');
            const surf = src ? newparams[src.trim()] : null;
            if (surf) {
              const imgId = (surf.textOf('surface/init_from') || '').trim();
              path = imgId in images ? images[imgId] : (imgId || null);
            }
          }
          if (path === null) path = ref in images ? images[ref] : null;
          info.tex = path;
        }
      }
      const tr = sh.first('transparency/float');
      if (tr && tr.text && !Number.isNaN(Number(tr.text)) && Number(tr.text) < 0.99 && sh.kid('transparent')) info.transparent = true;
      break;
    }
    effects[eff.get('id', '')] = info;
  }
  const mats = {};
  for (const m of root.path('library_materials/material')) {
    const mid = m.get('id', '');
    const name = m.get('name') || mid.replace(/-material$/, '');
    const ie = m.kid('instance_effect');
    const eff = (ie ? effects[refOf(ie.get('url'))] : null) || {};
    mats[mid] = { name, color: eff.color || null, texturePath: eff.tex || null, transparent: !!eff.transparent };
  }
  return mats;
}
function parseCollada(u8) {
  let root;
  try { root = xmlParse(decodeText(u8)); }
  catch (e) { if (e instanceof ConversionError) throw new ConversionError(`not a valid Collada file: ${e.message}`); throw e; }
  if (root.tag !== 'COLLADA') throw new ConversionError('not a Collada (.dae) file');
  const scene = { upAxis: 'Z_UP', unitMeters: 1.0, materialNames: [], materials: {}, instances: [] };
  const asset = root.kid('asset');
  if (asset) {
    scene.upAxis = (asset.textOf('up_axis') || 'Z_UP').trim();
    const u = asset.kid('unit');
    if (u && u.get('meter') && !Number.isNaN(Number(u.get('meter')))) scene.unitMeters = Number(u.get('meter'));
  }
  const matsById = colladaEffects(root);
  const geoms = {};
  for (const g of root.path('library_geometries/geometry')) {
    const pg = colladaGeometry(g);
    if (pg) geoms[g.get('id', '')] = pg;
  }
  const controllers = {};
  for (const c of root.path('library_controllers/controller')) {
    const skin = c.kid('skin');
    if (skin) controllers[c.get('id', '')] = refOf(skin.get('source'));
  }
  const libNodes = {};
  for (const nd of root.path('library_nodes/node')) libNodes[nd.get('id', '')] = nd;
  const matIndex = (name) => {
    let i = scene.materialNames.indexOf(name);
    if (i < 0) { scene.materialNames.push(name); i = scene.materialNames.length - 1; }
    return i;
  };
  function emit(node, world, inst, gid) {
    const g = geoms[gid];
    if (!g) return;
    const symmap = {};
    for (const im of inst.desc('instance_material')) {
      const target = refOf(im.get('target'));
      const dm = matsById[target];
      symmap[im.get('symbol', '')] = dm ? dm.name : target.replace(/-material$/, '');
      if (dm && !(dm.name in scene.materials)) scene.materials[dm.name] = dm;
    }
    const localIds = g.symbols.map((s) => matIndex(s in symmap ? symmap[s] : s));
    const mids = new Int32Array(g.symbolIds.length);
    for (let i = 0; i < mids.length; i++) mids[i] = localIds[g.symbolIds[i]];
    let mesh = Mesh.build(g.pos, g.ptri, mids, g.uv, g.utri, g.nrm, g.ntri);
    if (mesh.uv) for (let i = 1; i < mesh.uv.length; i += 2) mesh.uv[i] = 1.0 - mesh.uv[i];
    const A = [world[0], world[1], world[2], world[4], world[5], world[6], world[8], world[9], world[10]];
    mesh = mesh.transformed(A, [world[3], world[7], world[11]]);
    scene.instances.push({ nodeName: node.get('name') || node.get('id') || g.name, geometryName: g.name, mesh });
  }
  function walk(node, parent, depth = 0) {
    if (depth > 64) return;
    const world = mat4mul(parent, colladaNodeMatrix(node));
    for (const inst of node.kids('instance_geometry')) emit(node, world, inst, refOf(inst.get('url')));
    for (const inst of node.kids('instance_controller')) emit(node, world, inst, controllers[refOf(inst.get('url'))] || '');
    for (const inst of node.kids('instance_node')) {
      const ref = libNodes[refOf(inst.get('url'))];
      if (ref) walk(ref, world, depth + 1);
    }
    for (const ch of node.kids('node')) walk(ch, world, depth + 1);
  }
  const visual = root.first('library_visual_scenes/visual_scene');
  if (!visual) throw new ConversionError('Collada file has no visual scene');
  for (const nd of visual.kids('node')) walk(nd, ID4());
  const up = scene.upAxis.toUpperCase();
  let A;
  if (up === 'Y_UP') A = [1, 0, 0, 0, 0, -1, 0, 1, 0];
  else if (up === 'X_UP') A = [0, 1, 0, 0, 0, 1, 1, 0, 0];
  else A = I3();
  const As = A.map((x) => x * scene.unitMeters);
  for (const inst of scene.instances) inst.mesh = inst.mesh.transformed(As);
  scene.upAxis = 'Z_UP';
  scene.unitMeters = 1.0;
  if (!scene.instances.length) throw new ConversionError('Collada file contains no triangle geometry');
  return scene;
}
