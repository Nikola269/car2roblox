// ===================================================================== util: errors, bytes, math, meshes
class ConversionError extends Error {
  constructor(message) { super(message); this.name = 'ConversionError'; }
}
const tick = () => new Promise((r) => setTimeout(r, 0));

// ---------------------------------------------------------------- bytes
function dv(u8) { return new DataView(u8.buffer, u8.byteOffset, u8.byteLength); }
function latin1(u8, a, b) {
  let s = '';
  for (let i = a; i < b; i++) s += String.fromCharCode(u8[i]);
  return s;
}
function cstr(u8, a, b) {           // NUL-terminated latin-1
  let e = a;
  while (e < b && u8[e] !== 0) e++;
  return latin1(u8, a, e);
}
function utf8(u8) { return new TextDecoder('utf-8').decode(u8); }
function decodeText(u8) {
  let s;
  try { s = new TextDecoder('utf-8', { fatal: true }).decode(u8); }
  catch (e) { s = new TextDecoder('latin1').decode(u8); }
  return s.charCodeAt(0) === 0xFEFF ? s.slice(1) : s;
}
function concatBytes(list) {
  let n = 0;
  for (const b of list) n += b.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const b of list) { out.set(b, o); o += b.length; }
  return out;
}
const _crcTable = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(u8) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < u8.length; i++) c = _crcTable[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
const baseName = (p) => p.replace(/\\/g, '/').split('/').pop();
const extOf = (p) => { const m = /\.[^./\\]+$/.exec(p); return m ? m[0].toLowerCase() : ''; };
const stemOf = (p) => baseName(p).replace(/\.[^.]+$/, '');
function fmt(x, d) { return Number(x).toFixed(d); }

// ---------------------------------------------------------------- small linear algebra (row-major 3x3 as arrays of 9)
const I3 = () => [1, 0, 0, 0, 1, 0, 0, 0, 1];
function mmul(a, b) {
  const o = new Array(9);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    o[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
  }
  return o;
}
function mvec(a, v) {
  return [a[0] * v[0] + a[1] * v[1] + a[2] * v[2],
          a[3] * v[0] + a[4] * v[1] + a[5] * v[2],
          a[6] * v[0] + a[7] * v[1] + a[8] * v[2]];
}
function det3(a) {
  return a[0] * (a[4] * a[8] - a[5] * a[7]) - a[1] * (a[3] * a[8] - a[5] * a[6]) +
         a[2] * (a[3] * a[7] - a[4] * a[6]);
}
function inv3(a) {
  const d = det3(a);
  if (Math.abs(d) < 1e-300) return null;
  const c = [
    a[4] * a[8] - a[5] * a[7], a[2] * a[7] - a[1] * a[8], a[1] * a[5] - a[2] * a[4],
    a[5] * a[6] - a[3] * a[8], a[0] * a[8] - a[2] * a[6], a[2] * a[3] - a[0] * a[5],
    a[3] * a[7] - a[4] * a[6], a[1] * a[6] - a[0] * a[7], a[0] * a[4] - a[1] * a[3]];
  return c.map((x) => x / d);
}
const scaleMat = (s) => [s, 0, 0, 0, s, 0, 0, 0, s];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm3 = (a) => Math.hypot(a[0], a[1], a[2]);

// ---------------------------------------------------------------- axes
const AXIS_INDEX = { x: 0, y: 1, z: 2 };
function parseAxis(token) {
  let t = String(token).trim().toLowerCase();
  const sign = t.startsWith('-') ? -1 : 1;
  t = t.replace(/^[+-]+/, '');
  if (!(t in AXIS_INDEX)) throw new ConversionError(`bad axis '${token}' (use x, y or z with an optional sign)`);
  const v = [0, 0, 0];
  v[AXIS_INDEX[t]] = sign;
  return v;
}
function axisTokenOf(v) {
  let i = 0;
  for (let k = 1; k < 3; k++) if (Math.abs(v[k]) > Math.abs(v[i])) i = k;
  return (v[i] < 0 ? '-' : '+') + 'xyz'[i];
}
function axisSpec(right, up, forward) { return { right, up, forward }; }
function axisValidate(s) {
  const idx = [s.right, s.up, s.forward].map((a) => {
    const v = parseAxis(a);
    return v.findIndex((x) => x !== 0);
  });
  if (new Set(idx).size !== 3) {
    throw new ConversionError(`right/up/forward must use three different axes (got ${s.right}, ${s.up}, ${s.forward})`);
  }
}
function axisToRoblox(s) {            // rows: right, up, -forward  (Roblox: +X right, +Y up, -Z forward)
  axisValidate(s);
  const r = parseAxis(s.right), u = parseAxis(s.up), f = parseAxis(s.forward);
  return [...r, ...u, -f[0], -f[1], -f[2]];
}
function axisFlipForward(s) {
  const flip = (a) => (a.startsWith('-') ? a.slice(1) : '-' + a.replace(/^\+/, ''));
  return axisSpec(flip(s.right), s.up, flip(s.forward));
}

// ---------------------------------------------------------------- meshes
// Mesh: pos Float32Array(3N), tri Int32Array(3M), mat Int32Array(M), nrm Float32Array(3N)|null, uv Float32Array(2N)|null.
// UVs are in image space (v = 0 is the top row). Triangles are counter-clockwise seen from outside.
class Mesh {
  constructor(pos, tri, mat, nrm, uv) {
    this.pos = pos instanceof Float32Array ? pos : Float32Array.from(pos);
    this.tri = tri instanceof Int32Array ? tri : Int32Array.from(tri);
    this.mat = mat instanceof Int32Array ? mat : Int32Array.from(mat);
    const nv = this.pos.length / 3;
    this.nrm = nrm && nrm.length === nv * 3 ? (nrm instanceof Float32Array ? nrm : Float32Array.from(nrm)) : null;
    this.uv = uv && uv.length === nv * 2 ? (uv instanceof Float32Array ? uv : Float32Array.from(uv)) : null;
    if (this.mat.length * 3 !== this.tri.length) throw new Error('material_ids must have one entry per triangle');
    for (let i = 0; i < this.tri.length; i++) {
      const v = this.tri[i];
      if (v < 0 || v >= nv) throw new Error('triangle index out of range');
    }
  }
  get triCount() { return this.tri.length / 3; }
  get vertCount() { return this.pos.length / 3; }
  static empty() { return new Mesh(new Float32Array(0), new Int32Array(0), new Int32Array(0), null, null); }
  bounds() {
    const p = this.pos;
    if (!p.length) return { lo: [0, 0, 0], hi: [0, 0, 0] };
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < p.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const v = p[i + k];
        if (v < lo[k]) lo[k] = v;
        if (v > hi[k]) hi[k] = v;
      }
    }
    return { lo, hi };
  }
  copy() {
    return new Mesh(this.pos.slice(), this.tri.slice(), this.mat.slice(),
                    this.nrm ? this.nrm.slice() : null, this.uv ? this.uv.slice() : null);
  }
  // Build from attribute arrays that have their own index lists (OBJ / Collada style).
  // pos: Float32Array|Array (3N); ptri: Int32Array (3M) ... uv (2K) / utri, nrm (3L) / ntri.
  static build(pos, ptri, matIds, uv, utri, nrm, ntri) {
    const nt = ptri.length;
    if (nt === 0) return Mesh.empty();
    const M = nt / 3;
    const mats = new Int32Array(M);
    if (typeof matIds === 'number') mats.fill(matIds); else mats.set(matIds);
    const useUv = !!(uv && utri && uv.length > 0);
    const useN = !!(nrm && ntri && nrm.length > 0);
    const nUv = useUv ? uv.length / 2 : 1, nN = useN ? nrm.length / 3 : 1;
    const nP = pos.length / 3;
    const numeric = nP * nUv * nN < 9e15;
    const map = new Map();
    const rep = [];           // [pi, ti, ni]
    const tri = new Int32Array(nt);
    for (let c = 0; c < nt; c++) {
      const pi = Math.max(ptri[c], 0);
      const ti = useUv ? Math.max(utri[c], 0) : 0;
      const ni = useN ? Math.max(ntri[c], 0) : 0;
      const key = numeric ? (pi * nUv + ti) * nN + ni : pi + ',' + ti + ',' + ni;
      let id = map.get(key);
      if (id === undefined) { id = rep.length / 3; map.set(key, id); rep.push(pi, ti, ni); }
      tri[c] = id;
    }
    const nv = rep.length / 3;
    const P = new Float32Array(nv * 3);
    const U = useUv ? new Float32Array(nv * 2) : null;
    const N = useN ? new Float32Array(nv * 3) : null;
    for (let i = 0; i < nv; i++) {
      const pi = rep[i * 3], ti = rep[i * 3 + 1], ni = rep[i * 3 + 2];
      P[i * 3] = pos[pi * 3]; P[i * 3 + 1] = pos[pi * 3 + 1]; P[i * 3 + 2] = pos[pi * 3 + 2];
      if (U) { U[i * 2] = uv[ti * 2]; U[i * 2 + 1] = uv[ti * 2 + 1]; }
      if (N) { N[i * 3] = nrm[ni * 3]; N[i * 3 + 1] = nrm[ni * 3 + 1]; N[i * 3 + 2] = nrm[ni * 3 + 2]; }
    }
    return new Mesh(P, tri, mats, N, U);
  }
  // x' = A x + t (A row-major 3x3). Mirroring matrices flip the winding.
  transformed(A, t) {
    t = t || [0, 0, 0];
    const out = this.copy();
    const p = this.pos, q = out.pos;
    for (let i = 0; i < p.length; i += 3) {
      const x = p[i], y = p[i + 1], z = p[i + 2];
      q[i] = A[0] * x + A[1] * y + A[2] * z + t[0];
      q[i + 1] = A[3] * x + A[4] * y + A[5] * z + t[1];
      q[i + 2] = A[6] * x + A[7] * y + A[8] * z + t[2];
    }
    if (this.nrm) {
      let B = inv3(A);          // n' = A^-T n  ->  n'_j = sum_i n_i * Ainv[i][j]
      let transposeA = false;
      if (!B) { B = A; transposeA = true; }
      const n = this.nrm, m = out.nrm;
      for (let i = 0; i < n.length; i += 3) {
        const x = n[i], y = n[i + 1], z = n[i + 2];
        let a, b, c;
        if (!transposeA) {
          a = x * B[0] + y * B[3] + z * B[6];
          b = x * B[1] + y * B[4] + z * B[7];
          c = x * B[2] + y * B[5] + z * B[8];
        } else {            // n @ A.T
          a = x * B[0] + y * B[1] + z * B[2];
          b = x * B[3] + y * B[4] + z * B[5];
          c = x * B[6] + y * B[7] + z * B[8];
        }
        let l = Math.hypot(a, b, c);
        if (l === 0) l = 1;
        m[i] = a / l; m[i + 1] = b / l; m[i + 2] = c / l;
      }
    }
    if (det3(A) < 0) reverseWinding(out.tri);
    return out;
  }
  flipped() {
    const out = this.copy();
    reverseWinding(out.tri);
    if (out.nrm) for (let i = 0; i < out.nrm.length; i++) out.nrm[i] = -out.nrm[i];
    return out;
  }
  // Keep only the given triangles (array/Int32Array of indices, ascending) and drop unused vertices.
  select(idx) {
    const n = idx.length;
    if (n === 0) return Mesh.empty();
    const nv = this.vertCount;
    const used = new Uint8Array(nv);
    for (let k = 0; k < n; k++) {
      const b = idx[k] * 3;
      used[this.tri[b]] = 1; used[this.tri[b + 1]] = 1; used[this.tri[b + 2]] = 1;
    }
    const remap = new Int32Array(nv).fill(-1);
    let cnt = 0;
    for (let v = 0; v < nv; v++) if (used[v]) remap[v] = cnt++;
    const P = new Float32Array(cnt * 3);
    const N = this.nrm ? new Float32Array(cnt * 3) : null;
    const U = this.uv ? new Float32Array(cnt * 2) : null;
    for (let v = 0; v < nv; v++) {
      const r = remap[v];
      if (r < 0) continue;
      P[r * 3] = this.pos[v * 3]; P[r * 3 + 1] = this.pos[v * 3 + 1]; P[r * 3 + 2] = this.pos[v * 3 + 2];
      if (N) { N[r * 3] = this.nrm[v * 3]; N[r * 3 + 1] = this.nrm[v * 3 + 1]; N[r * 3 + 2] = this.nrm[v * 3 + 2]; }
      if (U) { U[r * 2] = this.uv[v * 2]; U[r * 2 + 1] = this.uv[v * 2 + 1]; }
    }
    const T = new Int32Array(n * 3), Mt = new Int32Array(n);
    for (let k = 0; k < n; k++) {
      const b = idx[k] * 3;
      T[k * 3] = remap[this.tri[b]]; T[k * 3 + 1] = remap[this.tri[b + 1]]; T[k * 3 + 2] = remap[this.tri[b + 2]];
      Mt[k] = this.mat[idx[k]];
    }
    return new Mesh(P, T, Mt, N, U);
  }
  compact() {
    const idx = new Int32Array(this.triCount);
    for (let i = 0; i < idx.length; i++) idx[i] = i;
    return this.select(idx);
  }
  dropDegenerate() {
    const keep = [];
    const t = this.tri;
    for (let i = 0; i < t.length; i += 3) {
      if (t[i] !== t[i + 1] && t[i + 1] !== t[i + 2] && t[i] !== t[i + 2]) keep.push(i / 3);
    }
    return keep.length === this.triCount ? this : this.select(keep);
  }
  splitByMaterial() {
    const groups = new Map();
    for (let i = 0; i < this.mat.length; i++) {
      let g = groups.get(this.mat[i]);
      if (!g) { g = []; groups.set(this.mat[i], g); }
      g.push(i);
    }
    return [...groups.keys()].sort((a, b) => a - b).map((k) => [k, this.select(groups.get(k))]);
  }
  static concat(list) {
    list = list.filter((m) => m.triCount);
    if (!list.length) return Mesh.empty();
    const hasN = list.every((m) => m.nrm), hasUv = list.every((m) => m.uv);
    let nv = 0, nt = 0;
    for (const m of list) { nv += m.vertCount; nt += m.triCount; }
    const P = new Float32Array(nv * 3), T = new Int32Array(nt * 3), Mt = new Int32Array(nt);
    const N = hasN ? new Float32Array(nv * 3) : null, U = hasUv ? new Float32Array(nv * 2) : null;
    let vo = 0, to = 0;
    for (const m of list) {
      P.set(m.pos, vo * 3);
      for (let i = 0; i < m.tri.length; i++) T[to * 3 + i] = m.tri[i] + vo;
      Mt.set(m.mat, to);
      if (N) N.set(m.nrm, vo * 3);
      if (U) U.set(m.uv, vo * 2);
      vo += m.vertCount; to += m.triCount;
    }
    return new Mesh(P, T, Mt, N, U);
  }
  faceNormal(i, out) {
    const t = this.tri, p = this.pos;
    const a = t[i * 3] * 3, b = t[i * 3 + 1] * 3, c = t[i * 3 + 2] * 3;
    const ux = p[b] - p[a], uy = p[b + 1] - p[a + 1], uz = p[b + 2] - p[a + 2];
    const vx = p[c] - p[a], vy = p[c + 1] - p[a + 1], vz = p[c + 2] - p[a + 2];
    out[0] = uy * vz - uz * vy; out[1] = uz * vx - ux * vz; out[2] = ux * vy - uy * vx;
    return out;
  }
  // Smooth area-weighted normals; vertices at identical positions are welded for the calculation.
  recomputeNormals() {
    const out = this.copy();
    const nv = this.vertCount;
    out.nrm = new Float32Array(nv * 3);
    if (!this.triCount) return out;
    const weldOf = new Int32Array(nv);
    const wmap = new Map();
    for (let v = 0; v < nv; v++) {
      const k = Math.round(this.pos[v * 3] * 1e4) + ',' + Math.round(this.pos[v * 3 + 1] * 1e4) + ',' +
                Math.round(this.pos[v * 3 + 2] * 1e4);
      let w = wmap.get(k);
      if (w === undefined) { w = wmap.size; wmap.set(k, w); }
      weldOf[v] = w;
    }
    const acc = new Float64Array(wmap.size * 3);
    const f = [0, 0, 0];
    for (let i = 0; i < this.triCount; i++) {
      this.faceNormal(i, f);
      for (let k = 0; k < 3; k++) {
        const w = weldOf[this.tri[i * 3 + k]] * 3;
        acc[w] += f[0]; acc[w + 1] += f[1]; acc[w + 2] += f[2];
      }
    }
    for (let v = 0; v < nv; v++) {
      const w = weldOf[v] * 3;
      let l = Math.hypot(acc[w], acc[w + 1], acc[w + 2]);
      if (l === 0) l = 1;
      out.nrm[v * 3] = acc[w] / l; out.nrm[v * 3 + 1] = acc[w + 1] / l; out.nrm[v * 3 + 2] = acc[w + 2] / l;
    }
    return out;
  }
}
function reverseWinding(tri) {
  for (let i = 0; i < tri.length; i += 3) { const t = tri[i]; tri[i] = tri[i + 2]; tri[i + 2] = t; }
}

// ---------------------------------------------------------------- mesh algorithms
function autoFixWinding(mesh, minTris = 8) {
  if (!mesh.nrm || mesh.triCount < minTris) return [mesh, false];
  const f = [0, 0, 0];
  let dotSum = 0, wSum = 0;
  for (let i = 0; i < mesh.triCount; i++) {
    mesh.faceNormal(i, f);
    let vx = 0, vy = 0, vz = 0;
    for (let k = 0; k < 3; k++) {
      const v = mesh.tri[i * 3 + k] * 3;
      vx += mesh.nrm[v]; vy += mesh.nrm[v + 1]; vz += mesh.nrm[v + 2];
    }
    dotSum += f[0] * vx + f[1] * vy + f[2] * vz;
    wSum += Math.hypot(f[0], f[1], f[2]) * Math.hypot(vx, vy, vz);
  }
  if (wSum <= 0) return [mesh, false];
  if (dotSum / wSum < -0.2) return [mesh.flipped(), true];
  return [mesh, false];
}

function splitByLimit(mesh, maxTris) {
  if (maxTris < 4) throw new Error('max_tris too small');
  if (mesh.triCount <= maxTris) return [mesh];
  const out = [];
  const stack = [mesh];
  while (stack.length) {
    const m = stack.pop();
    if (m.triCount <= maxTris) { out.push(m); continue; }
    const n = m.triCount;
    const cent = new Float64Array(n * 3);
    for (let i = 0; i < n; i++) {
      for (let k = 0; k < 3; k++) {
        cent[i * 3 + k] = (m.pos[m.tri[i * 3] * 3 + k] + m.pos[m.tri[i * 3 + 1] * 3 + k] + m.pos[m.tri[i * 3 + 2] * 3 + k]) / 3;
      }
    }
    let axis = 0, best = -1;
    for (let k = 0; k < 3; k++) {
      let lo = Infinity, hi = -Infinity;
      for (let i = 0; i < n; i++) { const v = cent[i * 3 + k]; if (v < lo) lo = v; if (v > hi) hi = v; }
      if (hi - lo > best) { best = hi - lo; axis = k; }
    }
    const order = Array.from({ length: n }, (_, i) => i);
    order.sort((a, b) => (cent[a * 3 + axis] - cent[b * 3 + axis]) || (a - b));
    const half = n >> 1;
    const a = order.slice(0, half).sort((x, y) => x - y);
    const b = order.slice(half).sort((x, y) => x - y);
    stack.push(m.select(b));
    stack.push(m.select(a));
  }
  return out;
}

function clusterMesh(mesh, cell, uvCell) {
  const nv = mesh.vertCount;
  const p = mesh.pos;
  let o0 = Infinity, o1 = Infinity, o2 = Infinity;
  for (let i = 0; i < nv; i++) {
    if (p[i * 3] < o0) o0 = p[i * 3];
    if (p[i * 3 + 1] < o1) o1 = p[i * 3 + 1];
    if (p[i * 3 + 2] < o2) o2 = p[i * 3 + 2];
  }
  let umin0 = 0, umin1 = 0;
  const gu = mesh.uv ? new Float64Array(nv * 2) : null;
  if (gu) {
    umin0 = Infinity; umin1 = Infinity;
    for (let i = 0; i < nv; i++) {
      gu[i * 2] = Math.floor(mesh.uv[i * 2] / uvCell);
      gu[i * 2 + 1] = Math.floor(mesh.uv[i * 2 + 1] / uvCell);
      if (gu[i * 2] < umin0) umin0 = gu[i * 2];
      if (gu[i * 2 + 1] < umin1) umin1 = gu[i * 2 + 1];
    }
  }
  const map = new Map();
  const inv = new Int32Array(nv);
  for (let i = 0; i < nv; i++) {
    let key = Math.floor((p[i * 3] - o0) / cell) + ',' + Math.floor((p[i * 3 + 1] - o1) / cell) + ',' +
              Math.floor((p[i * 3 + 2] - o2) / cell);
    if (gu) key += ',' + (gu[i * 2] - umin0) + ',' + (gu[i * 2 + 1] - umin1);
    let id = map.get(key);
    if (id === undefined) { id = map.size; map.set(key, id); }
    inv[i] = id;
  }
  const n = map.size;
  const cnt = new Float64Array(n);
  const P = new Float64Array(n * 3), U = mesh.uv ? new Float64Array(n * 2) : null;
  const N = mesh.nrm ? new Float64Array(n * 3) : null;
  for (let i = 0; i < nv; i++) {
    const c = inv[i];
    cnt[c]++;
    P[c * 3] += p[i * 3]; P[c * 3 + 1] += p[i * 3 + 1]; P[c * 3 + 2] += p[i * 3 + 2];
    if (U) { U[c * 2] += mesh.uv[i * 2]; U[c * 2 + 1] += mesh.uv[i * 2 + 1]; }
    if (N) { N[c * 3] += mesh.nrm[i * 3]; N[c * 3 + 1] += mesh.nrm[i * 3 + 1]; N[c * 3 + 2] += mesh.nrm[i * 3 + 2]; }
  }
  for (let c = 0; c < n; c++) {
    for (let k = 0; k < 3; k++) P[c * 3 + k] /= cnt[c];
    if (U) { U[c * 2] /= cnt[c]; U[c * 2 + 1] /= cnt[c]; }
    if (N) {
      for (let k = 0; k < 3; k++) N[c * 3 + k] /= cnt[c];
      let l = Math.hypot(N[c * 3], N[c * 3 + 1], N[c * 3 + 2]);
      if (l === 0) l = 1;
      for (let k = 0; k < 3; k++) N[c * 3 + k] /= l;
    }
  }
  const keep = [];
  for (let i = 0; i < mesh.triCount; i++) {
    const a = inv[mesh.tri[i * 3]], b = inv[mesh.tri[i * 3 + 1]], c = inv[mesh.tri[i * 3 + 2]];
    if (a !== b && b !== c && a !== c) keep.push(i);
  }
  const T = new Int32Array(keep.length * 3), Mt = new Int32Array(keep.length);
  keep.forEach((ti, k) => {
    T[k * 3] = inv[mesh.tri[ti * 3]]; T[k * 3 + 1] = inv[mesh.tri[ti * 3 + 1]]; T[k * 3 + 2] = inv[mesh.tri[ti * 3 + 2]];
    Mt[k] = mesh.mat[ti];
  });
  return new Mesh(Float32Array.from(P), T, Mt, N ? Float32Array.from(N) : null, U ? Float32Array.from(U) : null).compact();
}

function decimateTo(mesh, target) {
  target = Math.max(Math.floor(target), 4);
  if (mesh.triCount <= target) return mesh;
  const { lo: lp, hi: hp } = mesh.bounds();
  const diag = Math.hypot(hp[0] - lp[0], hp[1] - lp[1], hp[2] - lp[2]) || 1;
  let lo = diag / 4096, hi = diag / 2, best = null;
  for (let it = 0; it < 14; it++) {
    const mid = Math.sqrt(lo * hi);
    const cand = clusterMesh(mesh, mid, Math.max(mid / diag, 1 / 2048));
    if (cand.triCount <= target) { best = cand; hi = mid; } else lo = mid;
  }
  if (!best) best = clusterMesh(mesh, hi, Math.max(hi / diag, 1 / 2048));
  return best;
}
