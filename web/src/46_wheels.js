// ===================================================================== wheels: repair, find and generate (port of wheels.py)
const TIRE_MATERIAL = makeMaterial({ name: 'tire_rubber', color: [0.055, 0.055, 0.060, 1.0] });
const RIM_MATERIAL = makeMaterial({ name: 'rim_metal', color: [0.72, 0.74, 0.78, 1.0] });
// (x / width, r / radius) outlines, outer side of the wheel at +x
const TIRE_PROFILE = [[-0.46, 0.60], [-0.50, 0.72], [-0.50, 0.88], [-0.42, 0.985], [-0.25, 1.0],
  [0.25, 1.0], [0.42, 0.985], [0.50, 0.88], [0.50, 0.72], [0.46, 0.60], [0.0, 0.57]];
const RIM_PROFILE = [[-0.36, 0.0], [-0.36, 0.50], [-0.44, 0.62], [0.44, 0.62], [0.48, 0.66],
  [0.40, 0.60], [0.30, 0.46], [0.26, 0.30], [0.34, 0.20], [0.34, 0.0]];

function lathe(profile, width, radius, segs, closed) {
  const n = profile.length;
  const pos = new Float32Array(n * segs * 3);
  for (let i = 0; i < n; i++) {
    const [x, r] = profile[i];
    for (let j = 0; j < segs; j++) {
      const a = (2 * Math.PI * j) / segs, o = (i * segs + j) * 3;
      pos[o] = x * width; pos[o + 1] = r * radius * Math.cos(a); pos[o + 2] = r * radius * Math.sin(a);
    }
  }
  const tris = [];
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i++) {
    const a = i, b = (i + 1) % n;
    for (let j = 0; j < segs; j++) {
      const k = (j + 1) % segs;
      const p00 = a * segs + j, p01 = a * segs + k, p10 = b * segs + j, p11 = b * segs + k;
      tris.push(p00, p10, p11, p00, p11, p01);
    }
  }
  let m = new Mesh(pos, tris, new Int32Array(tris.length / 3)).dropDegenerate();
  let vol = 0;
  const p = m.pos, t = m.tri;
  for (let i = 0; i < t.length; i += 3) {
    const a = t[i] * 3, b = t[i + 1] * 3, c = t[i + 2] * 3;
    vol += p[a] * (p[b + 1] * p[c + 2] - p[b + 2] * p[c + 1]) - p[a + 1] * (p[b] * p[c + 2] - p[b + 2] * p[c]) + p[a + 2] * (p[b] * p[c + 1] - p[b + 1] * p[c]);
  }
  if (vol < 0) m = m.flipped();          // make the faces point outwards
  return m.recomputeNormals();
}

// [tyre, rim] centred on the origin with the axle along X. side = +1 when the outside faces +X (right-hand wheels).
function makeWheel(radius, width, side, segs = 28) {
  let tire = lathe(TIRE_PROFILE, width, radius, segs, true);
  let rim = lathe(RIM_PROFILE, width, radius, segs, false);
  if (side < 0) {
    const mirror = [-1, 0, 0, 0, 1, 0, 0, 0, 1];
    tire = tire.transformed(mirror); rim = rim.transformed(mirror);
  }
  return [tire, rim];
}

// hints: [{hub:[x,y,z], radius, width}] already in stud space. Without hints the wheels go where an ordinary car has them.
function planWheels(lo, hi, hints) {
  const size = [0, 1, 2].map((k) => hi[k] - lo[k]);
  const length = size[2], widthC = size[0], height = size[1];
  const rDef = Math.max(Math.min(0.072 * length, 0.24 * height), 0.2);
  const plan = [];
  if (hints.length) {
    for (const h of hints) {
      const r = h.radius && h.radius > 0 ? h.radius : rDef;
      plan.push({ hub: h.hub.slice(), radius: r, width: h.width && h.width > 0 ? h.width : 0.7 * r });
    }
    return plan;
  }
  const tw = 0.7 * rDef;
  const xo = Math.max(0.5 * widthC - 0.5 * tw - 0.06 * rDef, 0.5 * tw);
  const zf = lo[2] + 0.21 * length, zr = hi[2] - 0.22 * length;
  const y = lo[1] + 0.55 * rDef, cx = (lo[0] + hi[0]) / 2;
  for (const z of [zf, zr]) for (const sx of [-1, 1]) plan.push({ hub: [cx + sx * xo, y, z], radius: rDef, width: tw });
  return plan;
}

const round3 = (v) => Math.round(v * 1000) / 1000;
function wheelPieces(plan, taken, maxTris = 20000, budget = null) {
  const out = [];
  let segs = Math.floor(Math.max(6, Math.min(28, Math.floor(maxTris / 22))));
  if (budget) segs = Math.floor(Math.max(6, Math.min(segs, Math.floor(budget / (40 * Math.max(plan.length, 1))))));
  const zs = [...new Set(plan.map((p) => round3(p.hub[2])))].sort((a, b) => a - b);
  const sorted = [...plan].sort((a, b) => (a.hub[2] - b.hub[2]) || (a.hub[0] - b.hub[0]));
  for (const p of sorted) {
    const hub = p.hub;
    const ai = zs.indexOf(round3(hub[2]));
    const letter = ai === 0 ? 'F' : (ai === zs.length - 1 ? 'R' : 'M');
    const left = hub[0] < 0;
    let slot = letter + (left ? 'L' : 'R');
    const base = slot;
    let k = 2;
    while (taken.has(slot)) { slot = `${base}${k}`; k++; }
    taken.add(slot);
    const [tire, rim] = makeWheel(p.radius, p.width, left ? -1 : 1, segs);
    for (const [kind, mesh, mat] of [['tire', tire, TIRE_MATERIAL], ['rim', rim, RIM_MATERIAL]]) {
      out.push({ name: `${kind}_${slot}`, part: `${kind}_${slot}`, kind: 'wheel', mesh: mesh.transformed(I3(), hub), material: mat, wheelSlot: slot });
    }
  }
  return out;
}

// connected pieces of a mesh (vertices at the same position count as shared), in order of first appearance
function splitIslands(mesh) {
  if (!mesh.triCount) return [];
  const nv = mesh.vertCount, weld = new Int32Array(nv), map = new Map();
  for (let v = 0; v < nv; v++) {
    const k = Math.round(mesh.pos[v * 3] * 1e3) + ',' + Math.round(mesh.pos[v * 3 + 1] * 1e3) + ',' + Math.round(mesh.pos[v * 3 + 2] * 1e3);
    let w = map.get(k);
    if (w === undefined) { w = map.size; map.set(k, w); }
    weld[v] = w;
  }
  const parent = new Int32Array(map.size);
  for (let i = 0; i < parent.length; i++) parent[i] = i;
  const find = (a) => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
  const join = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) { if (ra < rb) parent[rb] = ra; else parent[ra] = rb; } };
  for (let i = 0; i < mesh.triCount; i++) {
    const a = weld[mesh.tri[i * 3]], b = weld[mesh.tri[i * 3 + 1]], c = weld[mesh.tri[i * 3 + 2]];
    join(a, b); join(b, c);
  }
  const groups = new Map();
  for (let i = 0; i < mesh.triCount; i++) {
    const lab = find(weld[mesh.tri[i * 3]]);
    if (!groups.has(lab)) groups.set(lab, []);
    groups.get(lab).push(i);
  }
  return [...groups.values()].map((idx) => mesh.select(idx));
}

// a 'wheels' mesh holding all four wheels (or one axle) becomes one piece per wheel; returns how many pieces were added
function splitFusedWheels(pieces, lo, hi) {
  const size = [0, 1, 2].map((k) => hi[k] - lo[k]);
  let added = 0;
  for (const pc of [...pieces]) {
    if (pc.kind !== 'wheel') continue;
    const [sx, sy, sz] = pieceBox(pc).size;
    if (!(sx > 0.6 * size[0] || Math.max(sy, sz) > 0.3 * size[2])) continue;
    const parts = splitIslands(pc.mesh).filter((m) => m.triCount >= 6);
    if (parts.length < 2) continue;
    const idx = pieces.indexOf(pc);
    pieces.splice(idx, 1, ...parts.map((m, k) => ({ name: `${pc.name}_w${k + 1}`, part: pc.part, kind: 'wheel', mesh: m, material: pc.material, wheelSlot: null })));
    added += parts.length - 1;
  }
  return added;
}

// models whose wheels have no telling name: mark parts that look like wheels (round from the side, narrower than tall,
// touching the ground, left and right matched); returns how many pieces were marked
function findRoundParts(pieces, lo, hi) {
  const length = hi[2] - lo[2], width = hi[0] - lo[0], cx = (lo[0] + hi[0]) / 2;
  const cand = [];
  for (const pc of pieces) {
    if (pc.kind !== 'body' && pc.kind !== 'other') continue;
    const [sx, sy, sz] = pieceBox(pc).size;
    const dia = Math.max(sy, sz);
    if (dia <= 0 || !(0.07 * length <= dia && dia <= 0.26 * length)) continue;
    if (Math.min(sy, sz) / dia < 0.82 || !(0.12 * dia <= sx && sx <= 0.85 * dia)) continue;
    const c = pieceBox(pc).center;
    if (pieceBox(pc).lo[1] > 0.35 * dia + lo[1]) continue;
    if (Math.abs(c[0] - cx) < 0.2 * width) continue;
    cand.push([pc, c, dia]);
  }
  const marked = new Set();
  cand.forEach(([a, ca, da], i) => {
    for (const [b, cb, db] of cand.slice(i + 1)) {
      if ((ca[0] - cx) * (cb[0] - cx) < 0 && Math.abs(ca[1] - cb[1]) < 0.25 * da && Math.abs(ca[2] - cb[2]) < 0.25 * da
          && Math.abs(da - db) < 0.15 * Math.max(da, db)) { marked.add(a); marked.add(b); }
    }
  });
  let n = 0;
  for (const [pc] of cand) if (marked.has(pc)) { pc.kind = 'wheel'; n++; }
  return n;
}

// two wheel pieces on top of each other (the same mesh listed twice) z-fight; keep one of each; returns how many were removed
function dropDuplicateWheels(pieces) {
  const wheels = pieces.filter((p) => p.kind === 'wheel');
  const gone = new Set();
  wheels.forEach((a, i) => {
    if (gone.has(a)) return;
    for (const b of wheels.slice(i + 1)) {
      if (gone.has(b)) continue;
      const A = pieceBox(a), B = pieceBox(b);
      const r = Math.max(A.size[1], A.size[2]) / 2 || 1;
      const d = Math.hypot(A.center[0] - B.center[0], A.center[1] - B.center[1], A.center[2] - B.center[2]);
      if (d < 0.03 * r && [0, 1, 2].every((k) => Math.abs(A.size[k] - B.size[k]) <= 0.06 * Math.max(A.size[k], B.size[k]) + 1e-6)) gone.add(b);
    }
  });
  if (gone.size) { const keep = pieces.filter((p) => !gone.has(p)); pieces.length = 0; pieces.push(...keep); }
  return gone.size;
}

function hintToStud(h, R, s, shift) {
  const v = mvec(R, h.center);
  return { hub: [v[0] * s + shift[0], v[1] * s + shift[1], v[2] * s + shift[2]], radius: h.radius ? h.radius * s : null, width: h.width ? h.width * s : null };
}

// ----------------------------------------------------------------- wheels that are not on their hubs
// Wheel pieces sitting on the middle of the car: an unplaced template wheel, wheels left at the origin of their bones, or a spare.
function centreLineWheels(pieces) {
  const body = pieces.filter((p) => p.kind !== 'wheel');
  if (!body.length) return [];
  const { lo, hi } = unionBounds(body.map((p) => p.mesh));
  const cx = (lo[0] + hi[0]) / 2, bw = hi[0] - lo[0];
  return pieces.filter((p) => p.kind === 'wheel' && Math.abs(pieceBox(p).center[0] - cx) < 0.2 * bw);
}

// where a car's wheels should be, as plan entries: hint positions, else the corners the existing wheels imply, else the usual spots
// combines hints and the wheels already found per axle/side, instead of trusting either alone: a
// side with no data mirrors its axle-mate, and an axle with no data at all is added at the usual
// spot, sized like the nearest axle that has data. Falls back to the usual spots when nothing is known.
function cornerTargets(wheels, lo, hi, hints) {
  const size = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const length = size[2], widthC = size[0], height = size[1];
  const rDef = Math.max(Math.min(0.072 * length, 0.24 * height), 0.2);
  const cx = (lo[0] + hi[0]) / 2;

  const known = [];
  for (const h of hints) {
    const rad = h.radius && h.radius > 0 ? h.radius : rDef;
    known.push({ hub: h.hub, radius: rad, width: h.width && h.width > 0 ? h.width : 0.7 * rad, src: 0 });
  }
  for (const g of wheels) known.push({ hub: g.hub, radius: g.radius, width: g.width > 0 ? g.width : 0.7 * g.radius, src: 1 });
  if (!known.length) return planWheels(lo, hi, []);

  const rmax = Math.max(...known.map((e) => e.radius));
  const zs = [];
  for (const z of known.map((e) => e.hub[2]).sort((a, b) => a - b)) if (!zs.length || z - zs[zs.length - 1] > 0.5 * rmax) zs.push(z);
  const axles = zs.map((z) => {
    const members = known.filter((e) => Math.abs(e.hub[2] - z) <= 0.5 * rmax);
    const sides = {};
    for (const e of [...members].sort((a, b) => a.src - b.src)) {
      const s = e.hub[0] < cx ? -1 : 1;
      if (!(s in sides)) sides[s] = e;
    }
    const mz = members.reduce((s, e) => s + e.hub[2], 0) / members.length;
    return { z: mz, sides };
  });

  if (axles.length === 1) {
    const a = axles[0];
    const newZ = a.z < (lo[2] + hi[2]) / 2 ? hi[2] - 0.22 * length : lo[2] + 0.21 * length;
    axles.push({ z: newZ, sides: {} });
  }

  const out = [];
  for (const a of axles) {
    let sides = a.sides;
    const keys = Object.keys(sides);
    if (!keys.length) {
      let refAxle = null, best = Infinity;
      for (const x of axles) {
        if (!Object.keys(x.sides).length) continue;
        const d = Math.abs(x.z - a.z);
        if (d < best) { best = d; refAxle = x; }
      }
      const ref = refAxle ? Object.values(refAxle.sides)[0] : null;
      const refR = ref ? ref.radius : rDef;
      const refW = ref ? ref.width : 0.7 * rDef;
      const refY = ref ? ref.hub[1] : lo[1] + 0.55 * rDef;
      const refXo = ref ? Math.abs(ref.hub[0] - cx) : 0;
      const xo = refXo || Math.max(0.5 * widthC - 0.5 * refW - 0.06 * refR, 0.5 * refW);
      for (const s of [-1, 1]) out.push({ hub: [cx + s * xo, refY, a.z], radius: refR, width: refW });
      continue;
    }
    if (keys.length === 1) {
      const s0 = Number(keys[0]), e0 = sides[s0];
      sides = Object.assign({}, sides);
      sides[-s0] = { hub: [2 * cx - e0.hub[0], e0.hub[1], e0.hub[2]], radius: e0.radius, width: e0.width };
    }
    for (const s of [-1, 1]) {
      const e = sides[s];
      out.push({ hub: [e.hub[0], e.hub[1], a.z], radius: e.radius, width: e.width });
    }
  }
  return out;
}

function targetLabels(plan) {
  const zs = [...new Set(plan.map((p) => round3(p.hub[2])))].sort((a, b) => a - b);
  return plan.map((p) => {
    const ai = zs.indexOf(round3(p.hub[2]));
    return (ai === 0 ? 'F' : (ai === zs.length - 1 ? 'R' : 'M')) + (p.hub[0] < 0 ? 'L' : 'R');
  });
}

// copies of a wheel's pieces moved onto another hub; flip turns them half a turn about the vertical axis
function copyWheel(src, srcHub, dstHub, flip, tag) {
  const M = flip ? [-1, 0, 0, 0, 1, 0, 0, 0, -1] : [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const mc = mvec(M, srcHub);
  const t = [dstHub[0] - mc[0], dstHub[1] - mc[1], dstHub[2] - mc[2]];
  return src.map((p) => ({ name: `${p.name}_${tag}`, part: p.part, kind: 'wheel', mesh: p.mesh.transformed(M, t), material: p.material, wheelSlot: null }));
}

function unionCentre(pieces) {
  const { lo, hi } = unionBounds(pieces.map((p) => p.mesh));
  return [0, 1, 2].map((k) => (lo[k] + hi[k]) / 2);
}
