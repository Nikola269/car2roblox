// ===================================================================== model helpers, classification, safety
function newModel(name, source, axes, extra) {
  return Object.assign({ name, source, parts: [], materials: [], textures: new Map(), axes,
    unitMeters: 1.0, forwardAuto: false, defaultPaint: {}, notes: [], wheelHints: [] }, extra || {});
}
const r4 = (c) => Math.round(c * 1e4) / 1e4;
function materialKey(m) {
  return [m.name, m.color.map(r4).join(','), m.texture, m.paint, m.transparent, m.doubleSided].join('|');
}
function makeMaterial(o) {
  return Object.assign({ name: 'material', color: [1, 1, 1, 1], texture: null, paint: null, transparent: false, doubleSided: false }, o);
}
function addMaterial(model, m) {
  const k = materialKey(m);
  for (let i = 0; i < model.materials.length; i++) if (materialKey(model.materials[i]) === k) return i;
  model.materials.push(m);
  return model.materials.length - 1;
}
const modelTriCount = (model) => model.parts.reduce((a, p) => a + p.mesh.triCount, 0);

// ---- classification (port of classify.py)
const K_WHEEL = new Set(['wheel', 'wheels', 'tire', 'tires', 'tyre', 'tyres', 'rim', 'rims', 'hubcap', 'hubcaps']);
// names that mention a wheel but are not the wheel itself (arches, liners, ...): they stay body parts
// names that mention a wheel but are not the wheel itself (arches, liners, ...), or hardware that
// sits right at the hub but isn't the tire/rim (brakes, suspension...): they stay body parts. Real
// mods commonly name these "wheel_hub_f", "wheel_brake_disc", "front_wheel_knuckle" and so on, so a
// plain "contains 'wheel'" check sweeps them in as if they were the tire.
const K_NOT_WHEEL = new Set(['arch', 'arches', 'well', 'wells', 'house', 'housing', 'fender', 'fenders', 'guard', 'guards', 'liner', 'liners', 'lining', 'flare', 'flares', 'mudguard', 'mudflap', 'flap', 'flaps', 'splash', 'skirt', 'skirts', 'cutout', 'opening', 'brake', 'brakes', 'caliper', 'calipers', 'disc', 'discs', 'rotor', 'rotors', 'pad', 'pads', 'drum', 'drums', 'hub', 'hubs', 'knuckle', 'knuckles', 'spindle', 'spindles', 'upright', 'uprights', 'carrier', 'carriers', 'bearing', 'bearings', 'strut', 'struts', 'shock', 'shocks', 'damper', 'dampers', 'arm', 'arms', 'axle', 'axles', 'driveshaft', 'driveshafts']);
const K_GLASS = new Set(['glass', 'window', 'windows', 'windscreen', 'windshield', 'windscreens']);
const K_INTERIOR = new Set(['interior', 'seat', 'seats', 'dash', 'dashboard', 'steering', 'cabin', 'cockpit', 'ped', 'headliner']);
const K_LIGHT = new Set(['light', 'lights', 'lamp', 'lamps', 'headlight', 'headlights', 'taillight', 'taillights', 'indicator', 'indicators', 'foglight']);
const SLOT_TOKENS = { fl: 'FL', fr: 'FR', rl: 'RL', rr: 'RR', lf: 'FL', rf: 'FR', lb: 'RL', lr: 'RL', rb: 'RR', bl: 'RL', br: 'RR', lm: 'ML', rm: 'MR' };
const KINDS = ['body', 'wheel', 'glass', 'interior', 'light', 'other'];
function tokens(name) {
  return name.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z]+/).filter(Boolean);
}
const anyIn = (set, list) => list.some((t) => set.has(t));
function isLodName(name) {
  const n = name.toLowerCase();
  return /(_vlo|_lod\d*|_lo|lod\d+)$/.test(n) || n.startsWith('lod');
}
function classifyPart(name, materialNames, transparent, hint) {
  if (hint && KINDS.includes(hint)) return hint;
  const tk = tokens(name);
  if (anyIn(K_INTERIOR, tk)) return 'interior';
  if (anyIn(K_WHEEL, tk)) return anyIn(K_NOT_WHEEL, tk) ? 'body' : 'wheel';
  if (anyIn(K_GLASS, tk)) return 'glass';
  const mt = [];
  for (const m of materialNames) mt.push(...tokens(m));
  const mGlass = anyIn(K_GLASS, mt);
  if (transparent && (mGlass || !anyIn(K_LIGHT, tk))) return 'glass';
  if (mGlass) return 'glass';
  if (anyIn(K_LIGHT, tk)) return 'light';
  return 'body';
}
function slotFromName(name) {
  for (const t of tokens(name)) if (t in SLOT_TOKENS) return SLOT_TOKENS[t];
  return null;
}

// ---- safety filter (port of safety.py)
const SAFE_TLDS = 'com|net|org|info|biz|ru|xyz|io|gg|ly|tk|ml|ga|cf|gq|su|cn|pw|icu|vip';
const URL_RE = () => new RegExp(
  '(?:(?<![A-Za-z0-9])(?:https?|ftp|file|javascript)\\s*:\\s*[^\\s"\'<>]*)' +
  '|(?:(?<![A-Za-z0-9])www\\.[^\\s"\'<>]+)' +
  `|(?:(?<![A-Za-z0-9-])[A-Za-z0-9][A-Za-z0-9-]*\\.(?:${SAFE_TLDS})(?![A-Za-z0-9])(?:/[^\\s"'<>]*)?)`, 'gi');
const ADULT_TOKENS = new Set(('nude nudes naked nsfw porn porno pornhub pornography xxx hentai erotic erotica lewd topless ' +
  'boob boobs tits nipple nipples vagina pussy penis genital genitals playboy onlyfans brazzers ' +
  'sex sexy sexual adult fetish bdsm dildo vibrator lingerie stripper milf').split(/\s+/));
const ADULT_RAW = /(?<![a-z0-9])(?:r-?18|18\s?\+|18plus|x-?rated)(?![a-z0-9])/i;
const RISKY_EXT = { '.url': 'internet shortcut', '.lnk': 'Windows shortcut', '.webloc': 'web shortcut', '.desktop': 'desktop shortcut',
  '.website': 'web shortcut', '.exe': 'program', '.dll': 'program library', '.bat': 'script', '.cmd': 'script', '.com': 'program',
  '.scr': 'program', '.msi': 'installer', '.ps1': 'script', '.vbs': 'script', '.vbe': 'script', '.js': 'script', '.jse': 'script',
  '.wsf': 'script', '.hta': 'script', '.jar': 'program', '.apk': 'program', '.sh': 'script', '.py': 'script', '.reg': 'registry file',
  '.cpl': 'program', '.pif': 'program', '.dmg': 'program', '.html': 'web page', '.htm': 'web page' };
const IMAGE_LIKE = new Set(['.png', '.jpg', '.jpeg', '.dds', '.tga', '.bmp', '.txt', '.pdf', '.dae', '.jbeam', '.json', '.dff', '.txd',
  '.obj', '.mtl', '.glb', '.gltf', '.zip', '.doc', '.docx']);
function stripLinks(text) {
  const found = [];
  let out = text.replace(URL_RE(), (m) => { found.push(m); return ''; });
  if (found.length) out = out.replace(/\s{2,}/g, ' ').replace(/^[ _\-.]+|[ _\-.]+$/g, '');
  return [out, found];
}
function isAdultName(name) {
  if (ADULT_RAW.test(name)) return true;
  return tokens(name).some((t) => ADULT_TOKENS.has(t));
}
function riskyFile(path) {
  const base = path.replace(/\\/g, '/').toLowerCase().split('/').pop();
  const parts = base.split('.');
  const ext = parts.length > 1 ? '.' + parts[parts.length - 1] : '';
  if (ext in RISKY_EXT) {
    if (parts.length >= 3 && IMAGE_LIKE.has('.' + parts[parts.length - 2])) {
      return `double extension (${RISKY_EXT[ext]} disguised as .${parts[parts.length - 2]})`;
    }
    return RISKY_EXT[ext];
  }
  if (parts.length >= 3 && ('.' + parts[parts.length - 2]) in RISKY_EXT && IMAGE_LIKE.has(ext)) {
    return 'double extension (looks like a program or shortcut)';
  }
  return null;
}
function scanFiles(paths) {
  const out = [];
  for (const p of paths) { const why = riskyFile(p); if (why) out.push([p, why]); }
  return out.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
}
function cleanName(name, what, msgs) {
  const [cleaned, links] = stripLinks(name);
  if (links.length) { msgs.push(`removed a link from a ${what} name (now '${cleaned || what}')`); return cleaned || what; }
  return name;
}
function scrubModel(model) {
  const msgs = [];
  model.name = cleanName(model.name, 'model', msgs) || 'Car';
  const badTex = new Set();
  for (const [key, tex] of model.textures) {
    tex.name = cleanName(tex.name, 'texture', msgs);
    if (isAdultName(key) || isAdultName(tex.name)) badTex.add(key);
  }
  const badMats = new Set();
  model.materials.forEach((m, i) => {
    m.name = cleanName(m.name, 'material', msgs);
    if (isAdultName(m.name) || (m.texture !== null && badTex.has(m.texture))) badMats.add(i);
  });
  for (const key of badTex) {
    msgs.push('deleted a texture (adult-content name)');
    model.textures.delete(key);
  }
  for (const i of badMats) if (model.materials[i].texture !== null && badTex.has(model.materials[i].texture)) model.materials[i].texture = null;
  const kept = [];
  for (const part of model.parts) {
    part.name = cleanName(part.name, 'part', msgs);
    if (isAdultName(part.name)) { msgs.push('deleted a part (adult-content name)'); continue; }
    if (badMats.size) {
      const keep = [];
      for (let i = 0; i < part.mesh.mat.length; i++) if (!badMats.has(part.mesh.mat[i])) keep.push(i);
      const dropped = part.mesh.mat.length - keep.length;
      if (dropped) {
        msgs.push(`deleted ${dropped} triangles of '${part.name}' that use an adult-content material`);
        part.mesh = part.mesh.select(keep);
        if (part.mesh.triCount === 0) continue;
      }
    }
    kept.push(part);
  }
  model.parts = kept;
  if (!model.parts.length) {
    throw new ConversionError('every part was removed by the safety filter (links / adult-content names); nothing is left to convert');
  }
  model.notes.push(...msgs.map((m) => 'Safety: ' + m));
  return msgs;
}

// ===================================================================== GTA San Andreas
const PAINT_MARKERS = { '60,255,0': 'primary', '255,0,175': 'secondary', '0,255,255': 'tertiary', '255,255,0': 'quaternary' };
const WHEEL_DUMMIES = { wheel_lf_dummy: 'FL', wheel_rf_dummy: 'FR', wheel_lb_dummy: 'RL', wheel_rb_dummy: 'RR', wheel_lm_dummy: 'ML', wheel_rm_dummy: 'MR' };
const FLIP_Z = [-1, 0, 0, 0, -1, 0, 0, 0, 1];
const hex2 = (n) => n.toString(16).padStart(2, '0');

function dffGeometryMesh(g, matmap) {
  const nt = g.triangles.length;
  const tri = new Int32Array(nt * 3), mat = new Int32Array(nt);
  for (let i = 0; i < nt; i++) {
    tri[i * 3] = g.triangles[i][0]; tri[i * 3 + 1] = g.triangles[i][1]; tri[i * 3 + 2] = g.triangles[i][2];
    mat[i] = matmap.length ? matmap[Math.max(0, Math.min(g.triMats[i], matmap.length - 1))] : 0;
  }
  let nrm = g.normals;
  if (nrm && !nrm.some((x) => x !== 0)) nrm = null;
  return new Mesh(g.positions, tri, mat, nrm, g.uvs);
}
// dffBytes: Uint8Array; txds: [{name, bytes}] in priority order
function importGtaSa(dffName, dffBytes, txds, includeLod, log) {
  const dff = parseDff(dffBytes);
  const stem = stemOf(dffName);
  const model = newModel(stem, 'gta_sa', axisSpec('+x', '+z', '+y'));
  log(`Reading ${baseName(dffName)}: ${dff.frames.length} frames, ${dff.geometries.length} geometries, ${dff.atomics.length} atomics`);
  if (!txds.length) model.notes.push('No .txd was added - exporting flat colours only. Add the matching .txd to get textures.');
  for (const t of txds) {
    let imgs;
    try { imgs = parseTxd(t.bytes); }
    catch (e) { if (e instanceof ConversionError) { log(`  ! could not read ${t.name}: ${e.message}`); continue; } throw e; }
    log(`  ${t.name}: ${imgs.size} textures`);
    for (const [name, img] of imgs) if (!model.textures.has(name)) model.textures.set(name, makeTexture(name, img));
  }
  const matmaps = [];
  const missing = new Set();
  for (const g of dff.geometries) {
    const ids = [];
    for (const dm of g.materials) {
      const [r, gg, b, a] = dm.color;
      const paint = !dm.texture ? (PAINT_MARKERS[`${r},${gg},${b}`] || null) : null;
      let tex = dm.texture ? dm.texture.toLowerCase() : null;
      if (tex && !model.textures.has(tex)) { missing.add(tex); tex = null; }
      const name = tex || (paint ? `paint_${paint}` : `color_${hex2(r)}${hex2(gg)}${hex2(b)}`);
      ids.push(addMaterial(model, makeMaterial({ name, color: [r / 255, gg / 255, b / 255, a / 255], texture: tex, paint, transparent: a < 250 })));
    }
    matmaps.push(ids);
  }
  if (missing.size) model.notes.push('Textures referenced but not found in any TXD: ' + [...missing].sort().join(', '));

  let wheelTemplate = null;
  const haveWheelGeo = new Set();
  const parts = [];
  let skipped = 0;
  for (const [fi, gi] of dff.atomics) {
    if (!(fi >= 0 && fi < dff.frames.length) || !(gi >= 0 && gi < dff.geometries.length)) continue;
    const fname = dff.frames[fi].name, low = fname.toLowerCase();
    if (low.endsWith('_dam') || (!includeLod && isLodName(low))) { skipped++; continue; }
    const mesh = dffGeometryMesh(dff.geometries[gi], matmaps[gi]);
    if (mesh.triCount === 0) continue;
    const [R, t] = dffWorld(dff, fi);
    // a detailed car's own "wheel_brake_disc" or "wheel_hub_f" is hardware next to the wheel,
    // not the wheel itself - swallowing it into the wheel template put it on every wheel dummy
    if (low === 'wheel' || (low.startsWith('wheel') && !low.endsWith('_dummy') && !(low in WHEEL_DUMMIES)
                            && !tokens(low).some((t) => K_NOT_WHEEL.has(t)))) {
      if (wheelTemplate === null || low === 'wheel') wheelTemplate = [mesh, R];
      continue;
    }
    const slot = WHEEL_DUMMIES[low] || null;
    if (slot) haveWheelGeo.add(slot);
    parts.push({ name: fname, mesh: mesh.transformed(R, t), wheelSlot: slot, kindHint: slot ? 'wheel' : null });
  }
  if (skipped) log(`  skipped ${skipped} damage/LOD objects`);
  const hubs = {};
  dff.frames.forEach((f, i) => { const slot = WHEEL_DUMMIES[f.name.toLowerCase()]; if (slot) hubs[slot] = dffWorld(dff, i)[1]; });
  const hubSlots = Object.keys(hubs).sort();
  if (hubSlots.length) {                 // where the wheels belong, used if the wheel model is missing
    let radius = null, width = null;
    if (wheelTemplate) {
      const { lo, hi } = wheelTemplate[0].transformed(wheelTemplate[1]).bounds();
      const dims = [0, 1, 2].map((k) => hi[k] - lo[k]).sort((a, b) => a - b);
      radius = dims[2] / 2; width = dims[0];
    }
    for (const slot of hubSlots) model.wheelHints.push({ center: Array.from(hubs[slot], Number), radius, width, slot });
  }
  if (wheelTemplate && hubSlots.length) {
    const [wmesh, wR] = wheelTemplate;
    for (const slot of hubSlots) {
      if (haveWheelGeo.has(slot)) continue;
      const side = slot.endsWith('R') ? FLIP_Z : I3();
      parts.push({ name: `wheel_${slot}`, mesh: wmesh.transformed(mmul(side, wR), hubs[slot]), wheelSlot: slot, kindHint: 'wheel' });
    }
    log(`  placed wheel model at ${hubSlots.length} wheel dummies`);
  } else if (wheelTemplate) {
    parts.push({ name: 'wheel', mesh: wheelTemplate[0].transformed(wheelTemplate[1]), wheelSlot: null, kindHint: 'wheel' });
    model.notes.push('No wheel_*_dummy frames: wheel model exported once, unplaced.');
  } else if (hubSlots.length && !haveWheelGeo.size) {
    model.notes.push("Wheel dummies found but no 'wheel' model to place on them.");
  }
  if (!parts.length) throw new ConversionError('the DFF contains no usable geometry after filtering');
  model.parts = parts;
  return model;
}

// ===================================================================== BeamNG
const SKIP_NODE = /^(col(lision|mesh)?[-_]?\d*|start\d*|base\d*|bounds?|detail\d*|collision.*|colmesh.*|ground.*|shadow.*)$/i;
const TEX_KEYS = ['baseColorMap', 'colorMap', 'diffuseMap'];
function beamngVehicleDirs(store) {
  const found = new Map();
  const rx = /^(.*?\/)?vehicles\/([^/]+)\//;
  for (const low of store.index.keys()) {
    const m = rx.exec(low);
    if (m && m[2] !== 'common') {
      const pre = m[1] || '';
      if (!found.has(m[2])) found.set(m[2], [pre, `${pre}vehicles/${m[2]}/`]);
    }
  }
  return found;
}
// A JBeam table (header row, then rows, with {...} modifier rows in between) as objects keyed by the header names.
// Modifier objects persist for the rows after them; an object at the end of a row applies to that row only (kept in _mods).
const jbNum = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const isPlainObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
function jbTable(section) {
  if (!Array.isArray(section)) return [];
  let header = null;
  const sticky = {}, out = [];
  for (const row of section) {
    if (isPlainObj(row)) { Object.assign(sticky, row); continue; }
    if (!Array.isArray(row) || !row.length) continue;
    if (header === null) {
      if (row.every((c) => typeof c === 'string')) header = row.map((c) => c.replace(/[[\]:]/g, ''));
      continue;
    }
    const mods = Object.assign({}, sticky);
    let cells = row;
    const last = row[row.length - 1];
    if (isPlainObj(last)) { Object.assign(mods, last); cells = row.slice(0, -1); }
    const rec = {};
    for (let i = 0; i < Math.min(header.length, cells.length); i++) rec[header[i]] = cells[i];
    rec._mods = mods;
    out.push(rec);
  }
  return out;
}
// Follow the slot chain from the main part, taking each slot's default part ('slots' and the newer 'slots2').
function resolveDefaultParts(parts) {
  const names = Object.keys(parts);
  const stack = names.filter((n) => parts[n] && typeof parts[n] === 'object' && parts[n].slotType === 'main');
  const chosen = [];
  while (stack.length) {
    const name = stack.pop();
    if (chosen.includes(name) || !(name in parts)) continue;
    chosen.push(name);
    for (const key of ['slots', 'slots2']) {
      for (const row of jbTable(parts[name][key])) {
        const d = row.default;
        if (typeof d === 'string' && d in parts) stack.push(d);
      }
    }
  }
  return chosen;
}
const WHEEL_GROUP_HEAD = new Set(['tire', 'tires', 'tyre', 'tyres', 'rim', 'rims', 'hubcap', 'hubcaps']);
function isWheelGroup(g) {
  const tk = tokens(g);
  if (!tk.length || tk.some((t) => K_NOT_WHEEL.has(t))) return false;
  return tk[0].startsWith('wheel') || WHEEL_GROUP_HEAD.has(tk[0]);
}
function jbVec3(v, def) {
  let vals;
  if (isPlainObj(v)) vals = ['x', 'y', 'z'].map((k) => jbNum(v[k]));
  else if (Array.isArray(v) && v.length >= 3) vals = v.slice(0, 3).map(jbNum);
  else if (jbNum(v) !== null) vals = [jbNum(v), jbNum(v), jbNum(v)];
  else vals = [null, null, null];
  return vals.map((x, i) => (x === null ? def[i] : x));
}
function jbKnown(v) {
  if (isPlainObj(v)) return ['x', 'y', 'z'].every((k) => jbNum(v[k]) !== null);
  if (Array.isArray(v)) return v.length >= 3 && v.slice(0, 3).every((c) => jbNum(c) !== null);
  return jbNum(v) !== null;
}
// [{mesh, groups, pos, rot, scale, hasPos, posBad, rotBad, scaleBad}] for every mesh the part shows
function flexbodyRows(part) {
  const out = [];
  for (const r of jbTable(part && part.flexbodies)) {
    const mesh = r.mesh;
    if (typeof mesh !== 'string' || !mesh || mesh === 'mesh') continue;
    const g = r.group;
    const groups = Array.isArray(g) ? g.filter((x) => typeof x === 'string') : (typeof g === 'string' && g ? [g] : []);
    const m = r._mods;
    const pos = m.pos, rot = m.rot, scale = m.scale;
    // a value made of variables ("$=$trackwidth/2", used for e.g. a mirrored left/right offset)
    // cannot be read, and falling back to its default can place or orient a part somewhere badly
    // wrong; "not given at all" is different (and fine) - it means no override, i.e. identity
    out.push({
      mesh, groups, pos: jbVec3(pos, [0, 0, 0]), rot: jbVec3(rot, [0, 0, 0]), scale: jbVec3(scale, [1, 1, 1]),
      hasPos: pos !== undefined && pos !== null && jbKnown(pos),
      posBad: pos !== undefined && pos !== null && !jbKnown(pos),
      rotBad: rot !== undefined && rot !== null && !jbKnown(rot),
      scaleBad: scale !== undefined && scale !== null && !jbKnown(scale),
    });
  }
  return out;
}
// BeamNG's flexbody rot (degrees): intrinsic rotations about Z, then X, then Y
function eulerMatrix(rotDeg) {
  const [rx, ry, rz] = rotDeg.map((a) => (a * Math.PI) / 180);
  const cx = Math.cos(rx), sx = Math.sin(rx), cy = Math.cos(ry), sy = Math.sin(ry), cz = Math.cos(rz), sz = Math.sin(rz);
  const Rx = [1, 0, 0, 0, cx, -sx, 0, sx, cx], Ry = [cy, 0, sy, 0, 1, 0, -sy, 0, cy], Rz = [cz, -sz, 0, sz, cz, 0, 0, 0, 1];
  return mmul(mmul(Rz, Rx), Ry);
}
// Some configurations leave the wheel slots empty: add one wheel part for every wheel slot that has none.
function withWheelParts(partsDb, active) {
  const have = new Set(active.filter((n) => n in partsDb).map((n) => partsDb[n].slotType));
  const bySlot = new Map();
  for (const n of Object.keys(partsDb)) {
    const p = partsDb[n], st = isPlainObj(p) ? p.slotType : null;
    if (typeof st === 'string' && /wheel|tire|tyre/i.test(st) && !active.includes(n)) {
      if (flexbodyRows(p).some((r) => r.groups.some(isWheelGroup))) {
        if (!bySlot.has(st)) bySlot.set(st, []);
        bySlot.get(st).push(n);
      }
    }
  }
  const added = [];
  for (const st of [...bySlot.keys()].sort()) {
    if (!have.has(st)) added.push(bySlot.get(st).sort((a, b) => (a.length - b.length) || (a < b ? -1 : a > b ? 1 : 0))[0]);
  }
  return [active.concat(added), added];
}
// where the JBeam says the wheels are: the middle of the two hub nodes, with radius / width from the modifiers
function jbeamWheelHints(partsDb, active) {
  const nodes = new Map();
  for (const pn of active) {
    for (const r of jbTable((partsDb[pn] || {}).nodes)) {
      const xyz = ['posX', 'posY', 'posZ'].map((k) => jbNum(r[k]));
      if (!xyz.includes(null) && r.id !== undefined && r.id !== null) nodes.set(String(r.id), xyz);
    }
  }
  const hints = [];
  for (const pn of active) {
    for (const sec of ['pressureWheels', 'hubWheels', 'wheels']) {
      for (const r of jbTable((partsDb[pn] || {})[sec])) {
        const a = nodes.get(String(r.node1)), b = nodes.get(String(r.node2));
        if (!a || !b) continue;
        const m = r._mods;
        let rad = jbNum(m.radius) || jbNum(m.hubRadius) || null;
        let wid = jbNum(m.tireWidth) || jbNum(m.hubWidth) || null;
        if (rad !== null && !(rad >= 0.1 && rad <= 1.2)) rad = null;
        if (wid !== null && !(wid >= 0.05 && wid <= 1.0)) wid = null;
        hints.push({ center: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], radius: rad, width: wid, slot: slotFromName(String(r.name === undefined ? '' : r.name)) });
      }
    }
  }
  return nameHints(hints);
}
// Give every hint a slot (FL FR RL RR ...) from where it is: BeamNG has the front at -y and the right side at -x.
function nameHints(hints) {
  if (!hints.length) return hints;
  const axles = [];
  for (const y of hints.map((h) => h.center[1]).sort((a, b) => a - b)) if (!axles.length || y - axles[axles.length - 1] > 0.3) axles.push(y);
  return hints.map((h) => {
    let slot = h.slot;
    if (!slot) {
      let i = 0, bd = Infinity;
      axles.forEach((y, k) => { const d = Math.abs(y - h.center[1]); if (d < bd) { bd = d; i = k; } });
      slot = (i === 0 ? 'F' : (i === axles.length - 1 ? 'R' : 'M')) + (h.center[0] > 0 ? 'L' : 'R');
    }
    return { center: h.center, radius: h.radius, width: h.width, slot };
  });
}
function flexbodyMeshes(part) { return flexbodyRows(part).map((r) => r.mesh); }
const stageValue = (stage, keys) => {
  if (stage && typeof stage === 'object' && !Array.isArray(stage)) {
    for (const k of keys) { const v = stage[k]; if (typeof v === 'string' && v.trim()) return v; }
  }
  return null;
};
const normMat = (name) => name.trim().toLowerCase().replace(/-material$/, '');
const nonEmptyArr = (v) => (Array.isArray(v) && v.length ? v : null);

// Which vehicles / configurations a BeamNG store holds (for the UI)
function beamngInfo(store) {
  const dirs = beamngVehicleDirs(store);
  const out = [];
  for (const [name, [, vdir]] of dirs) {
    const configs = [...store.index.keys()].filter((p) => p.startsWith(vdir) && p.endsWith('.pc'))
      .map((p) => p.split('/').pop().replace(/\.pc$/, '')).sort();
    out.push({ name, configs });
  }
  return out.sort((a, b) => (a.name < b.name ? -1 : 1));
}

async function importBeamng(store, srcName, opts, log) {
  const vdirs = beamngVehicleDirs(store);
  let name;
  if (opts.vehicle) {
    const key = [...vdirs.keys()].find((k) => k === opts.vehicle.toLowerCase());
    if (!key) throw new ConversionError(`vehicle '${opts.vehicle}' not found; available: ${[...vdirs.keys()].sort().join(', ') || 'none'}`);
    name = key;
  } else if (vdirs.size) {
    const weight = (k) => { const d = vdirs.get(k)[1]; let n = 0; for (const low of store.index.keys()) if (low.startsWith(d) && (low.endsWith('.dae') || low.endsWith('.jbeam'))) n++; return n; };
    name = [...vdirs.keys()].reduce((best, k) => (weight(k) > weight(best) ? k : best));
    if (vdirs.size > 1) log(`  several vehicles in the archive (${[...vdirs.keys()].sort().join(', ')}); using '${name}'`);
  } else {
    name = stemOf(srcName);
    vdirs.set(name, ['', '']);
  }
  const [prefix, vdir] = vdirs.get(name);
  store.prefixes.add(prefix);
  log(`BeamNG vehicle '${name}' from ${baseName(srcName)}`);
  const keys = [...store.index.keys()];
  const files = keys.filter((l) => l.startsWith(vdir));
  const daes = files.filter((f) => f.endsWith('.dae')).sort();
  const jbeams = files.filter((f) => f.endsWith('.jbeam')).sort();
  const pcs = files.filter((f) => f.endsWith('.pc')).sort();
  const matFiles = keys.filter((f) => (f.startsWith(vdir) || f.startsWith(prefix + 'vehicles/common/')) && f.endsWith('materials.json')).sort();
  if (!daes.length) {
    throw new ConversionError('no .dae mesh files found. BeamNG ships some vehicles as binary .cdae caches only - export the car to .dae from Blender or use the unpacked mod files.');
  }
  const model = newModel(name, 'beamng', axisSpec('-x', '+z', '-y'));
  const risky = scanFiles(store.paths());
  if (risky.length) {
    const shown = risky.slice(0, 6).map(([p, why]) => `${baseName(p)} (${why})`).join(', ');
    const more = risky.length > 6 ? ` and ${risky.length - 6} more` : '';
    model.notes.push(`Safety: ignored ${risky.length} risky file(s) in the archive - never opened, run or copied: ${shown}${more}`);
  }
  const partsDb = {};
  for (const jf of jbeams) {
    let data;
    try { data = jbeamLoadBytes(await store.read(jf)); }
    catch (e) { if (e instanceof JBeamError) { log(`  ! skipped ${baseName(jf)}: ${e.message}`); continue; } throw e; }
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      for (const k of Object.keys(data)) if (data[k] && typeof data[k] === 'object' && !Array.isArray(data[k])) partsDb[k] = data[k];
    }
  }
  let activeParts = null, pcData = null;
  if (opts.config) {
    const want = opts.config.toLowerCase();
    const pc = pcs.find((p) => p.split('/').pop().replace(/\.[^.]+$/, '') === want);
    if (!pc) throw new ConversionError(`configuration '${opts.config}' not found; available: ${pcs.map((p) => p.split('/').pop().replace(/\.[^.]+$/, '')).join(', ') || 'none'}`);
    try { pcData = jbeamLoadBytes(await store.read(pc)); }
    catch (e) { if (e instanceof JBeamError) throw new ConversionError(`cannot read configuration ${opts.config}: ${e.message}`); throw e; }
    const pp = (pcData && pcData.parts) || {};
    activeParts = Object.values(pp).filter((v) => typeof v === 'string' && v);
    log(`  configuration '${opts.config}': ${activeParts.length} parts`);
  } else if (Object.keys(partsDb).length) {
    activeParts = resolveDefaultParts(partsDb);
    log(`  default configuration: ${activeParts.length} parts`);
  }
  if (activeParts && activeParts.length && !opts.config) {
    const [withWheels, extraParts] = withWheelParts(partsDb, activeParts);
    activeParts = withWheels;
    if (extraParts.length) log(`  the default configuration has no wheels installed; added: ${extraParts.join(', ')}`);
  }
  // every mesh a part shows, with its placement: one wheel mesh is listed once per wheel
  const rowsByMesh = new Map();
  for (const pn of activeParts || []) {
    for (const r of flexbodyRows(partsDb[pn] || {})) {
      const k = r.mesh.toLowerCase();
      if (!rowsByMesh.has(k)) rowsByMesh.set(k, []);
      rowsByMesh.get(k).push(r);
    }
  }
  let wanted = null;
  if (rowsByMesh.size) wanted = new Set(rowsByMesh.keys());
  if (activeParts && activeParts.length) {
    model.wheelHints = jbeamWheelHints(partsDb, activeParts);
    if (model.wheelHints.length) log(`  JBeam lists ${model.wheelHints.length} wheels`);
  }
  if (pcData && Array.isArray(pcData.paints)) {
    ['primary', 'secondary', 'tertiary'].forEach((slot, i) => {
      const p = pcData.paints[i];
      const bc = p && typeof p === 'object' ? p.baseColor : null;
      if (Array.isArray(bc) && bc.length >= 3) model.defaultPaint[slot] = bc.slice(0, 3).map((c) => Math.max(0, Math.min(1, Number(c))));
    });
  }
  const scenes = [];
  for (const d of daes) {
    try { scenes.push([d, parseCollada(await store.read(d))]); }
    catch (e) { log(`  ! skipped ${baseName(d)}: ${e.message}`); }
    await tick();
  }
  if (!scenes.length) throw new ConversionError('none of the .dae files could be read');
  const foundNames = new Set();
  const keep = (inst) => {
    const node = inst.nodeName, geom = inst.geometryName;
    if (wanted) {
      const hit = wanted.has(node.toLowerCase()) || wanted.has(geom.toLowerCase());
      if (hit) { for (const n of [node.toLowerCase(), geom.toLowerCase()]) if (wanted.has(n)) foundNames.add(n); }
      return hit;
    }
    if (SKIP_NODE.test(node) || (!opts.includeLod && isLodName(node))) return false;
    return true;
  };
  let selected = [];
  for (const [, sc] of scenes) for (const inst of sc.instances) if (keep(inst)) selected.push([sc, inst]);
  if (wanted) {
    let missing = [...wanted].filter((n) => !foundNames.has(n));
    if (missing.length) {
      for (const d of keys.filter((f) => f.startsWith(prefix + 'vehicles/common/') && f.endsWith('.dae')).sort()) {
        let sc;
        try { sc = parseCollada(await store.read(d)); } catch (e) { continue; }
        for (const inst of sc.instances) if (keep(inst)) selected.push([sc, inst]);
      }
      missing = [...wanted].filter((n) => !foundNames.has(n));
    }
    if (!selected.length) {
      log("  ! none of the configuration's flexbody meshes were found in the .dae files; exporting every visible mesh instead");
      wanted = null;
      for (const [, sc] of scenes) for (const inst of sc.instances) if (keep(inst)) selected.push([sc, inst]);
    } else if (missing.length) {
      model.notes.push(`${missing.length} flexbody meshes are not in the exported .dae files (e.g. ${missing.sort().slice(0, 5).join(', ')})`);
    }
  }
  if (!selected.length) throw new ConversionError('no renderable meshes were found in the .dae files');

  const matdb = {};
  for (const mf of matFiles) {
    let data;
    try { data = jbeamLoadBytes(await store.read(mf)); }
    catch (e) { if (e instanceof JBeamError) { log(`  ! skipped ${baseName(mf)}: ${e.message}`); continue; } throw e; }
    if (!data || typeof data !== 'object' || Array.isArray(data)) continue;
    for (const k of Object.keys(data)) {
      const v = data[k];
      if (!v || typeof v !== 'object' || Array.isArray(v)) continue;
      for (const key of [k, v.name, v.mapTo]) if (typeof key === 'string' && !(normMat(key) in matdb)) matdb[normMat(key)] = v;
    }
  }
  const texCache = new Map();
  const unresolved = new Set();
  async function loadTex(ref) {
    if (texCache.has(ref)) return texCache.get(ref);
    let key = null;
    const low = store.find(ref);
    if (low === null) unresolved.add(ref);
    else {
      try {
        const img = await decodeImage(await store.read(low), low);
        key = low.replace(/\.[^./]*$/, '');
        if (!model.textures.has(key)) model.textures.set(key, makeTexture(key.split('/').pop(), img));
      } catch (e) { log(`  ! texture ${baseName(low)} could not be decoded: ${e.message}`); }
    }
    texCache.set(ref, key);
    return key;
  }
  const matCache = new Map();
  const sceneIds = new Map();
  const sid = (sc) => { if (!sceneIds.has(sc)) sceneIds.set(sc, sceneIds.size); return sceneIds.get(sc); };
  async function globalMaterial(sc, local) {
    const ck = sid(sc) + '|' + local;
    if (matCache.has(ck)) return matCache.get(ck);
    const bm = matdb[normMat(local)];
    const dm = sc.materials[local];
    let color = [1, 1, 1, 1], tex = null, paint = null, transparent = false, double = false;
    if (bm) {
      const stagesRaw = bm.Stages || bm.stages;
      const stages = Array.isArray(stagesRaw) ? stagesRaw : [];
      const st0 = stages.length ? stages[0] : {};
      const ref = stageValue(st0, TEX_KEYS) || stageValue(bm, TEX_KEYS);
      let col = null;
      for (const s of [st0, bm]) {
        if (s && typeof s === 'object' && !Array.isArray(s)) col = nonEmptyArr(s.baseColorFactor) || nonEmptyArr(s.diffuseColor) || col;
      }
      if (Array.isArray(col) && col.length >= 3) color = [...col, 1.0].slice(0, 4).map(Number);
      const instance = stages.some((s) => s && typeof s === 'object' && s.instanceDiffuse) || !!bm.instanceDiffuse;
      if (instance) { paint = 'primary'; color = [1, 1, 1, color[3]]; }
      else if (ref) tex = await loadTex(ref);
      transparent = !!bm.translucent;
      double = !!bm.doubleSided;
    } else if (dm) {
      if (dm.color) color = dm.color;
      if (dm.texturePath) tex = await loadTex(dm.texturePath);
      transparent = dm.transparent;
    }
    if (color[3] < 0.98) transparent = true;
    if (tokens(local).includes('glass')) transparent = true;
    const idx = addMaterial(model, makeMaterial({ name: local.replace(/-material$/, '') || 'material', color, texture: tex, paint, transparent, doubleSided: double }));
    matCache.set(ck, idx);
    return idx;
  }
  const hintFor = (slot) => (slot ? model.wheelHints.find((h) => h.slot === slot) || null : null);
  const parts = [], emitted = new Set(), droppedFormula = [];
  const NAME_WHEEL = ['wheel', 'tire', 'tyre', 'rim'];
  const rd = (x, n) => String(Math.round(x * n) / n);
  for (const [sc, inst] of selected) {
    const gl = new Int32Array(sc.materialNames.length);
    for (let i = 0; i < gl.length; i++) gl[i] = await globalMaterial(sc, sc.materialNames[i]);
    const m = inst.mesh;
    const mats = new Int32Array(m.mat.length);
    for (let i = 0; i < mats.length; i++) mats[i] = gl[m.mat[i]];
    const base = new Mesh(m.pos, m.tri, mats, m.nrm, m.uv);
    if (!base.triCount) continue;
    const node = inst.nodeName || inst.geometryName;
    const rows = [inst.nodeName, inst.geometryName].map((n) => rowsByMesh.get(n.toLowerCase())).find((r) => r && r.length) || [];
    const tk = tokens(node);
    // real mods commonly share one JBeam group (e.g. "wheel_FL") between the tyre/rim AND the
    // brake caliper, rotor, hub carrier etc. that live at that corner, so the group alone can't
    // tell the wheel from its hardware - a part whose own mesh name says what it is overrides it
    const nodeIsHardware = tk.some((t) => K_NOT_WHEEL.has(t));
    const nameWheel = NAME_WHEEL.some((t) => tk.includes(t)) && !nodeIsHardware;
    if (!rows.length) {
      const key = `${node.toLowerCase()}|${base.triCount}`;
      if (emitted.has(key)) continue;
      emitted.add(key);
      parts.push({ name: node, mesh: base, wheelSlot: nameWheel ? slotFromName(node) : null, kindHint: nameWheel && !slotFromName(node) ? 'wheel' : null });
      continue;
    }
    rows.forEach((row, k) => {
      const A = mmul(eulerMatrix(row.rot), [row.scale[0], 0, 0, 0, row.scale[1], 0, 0, 0, row.scale[2]]);
      let mesh = base.transformed(A, row.pos);
      const wheelGroups = row.groups.filter(isWheelGroup);
      let slot = null;
      for (const g of wheelGroups) { const sg = slotFromName(g); if (sg) { slot = sg; break; } }
      if ((wheelGroups.length || nameWheel) && !slot) slot = slotFromName(node);
      // sharing the corner's wheel group is not enough on its own: hardware named like a
      // brake/hub/suspension part stays out, even when it rides in the "wheel_FL" group -
      // that mismatch is what put a body-painted caliper or hub carrier jammed onto the tyre
      const isWheel = (wheelGroups.length > 0 || nameWheel) && !nodeIsHardware;
      // a part placed or oriented from a formula we can't evaluate (e.g. a mirrored side offset)
      // would otherwise fall back to a default that can be badly wrong - unlike a wheel (which gets
      // snapped onto its JBeam hub below), there is no hub to fall back on, so it is better left
      // out, with a warning, than drawn jammed through the bodywork
      if (!isWheel && (row.posBad || row.rotBad || row.scaleBad)) { droppedFormula.push(node); return; }
      const h = hintFor(slot);
      if (isWheel && h) {
        const { lo, hi } = mesh.bounds();
        const ctr = [0, 1, 2].map((q) => (lo[q] + hi[q]) / 2);
        const oneWheel = h.radius == null || Math.max(hi[1] - lo[1], hi[2] - lo[2]) <= 2.6 * h.radius;
        // a wheel meant for its hub but far from it (modelled at the origin, or a position that could not be read)
        // goes onto the hub; a listed position is trusted more
        const far = (row.hasPos ? 1.5 : 0.5) * Math.max(h.radius || 0.3, 0.3);
        if (oneWheel && Math.hypot(ctr[0] - h.center[0], ctr[1] - h.center[1], ctr[2] - h.center[2]) > far) {
          mesh = mesh.transformed(I3(), [h.center[0] - ctr[0], h.center[1] - ctr[1], h.center[2] - ctr[2]]);   // modelled at the origin
        }
      }
      const key = [node.toLowerCase(), row.pos.map((x) => rd(x, 1e4)).join(','), row.rot.map((x) => rd(x, 1e3)).join(','), row.scale.map((x) => rd(x, 1e4)).join(','), base.triCount].join('|');
      if (emitted.has(key)) return;            // the same placement listed twice would z-fight
      emitted.add(key);
      parts.push({ name: rows.length === 1 ? node : `${node}_${slot || k + 1}`, mesh, wheelSlot: isWheel ? slot : null,
        kindHint: isWheel && !slot ? 'wheel' : null });
    });
  }
  if (unresolved.size) model.notes.push(`${unresolved.size} textures referenced by materials were not found in the archive (e.g. ${[...unresolved].sort()[0]}); flat colours used.`);
  if (droppedFormula.length) {
    const uniq = [...new Set(droppedFormula)].sort();
    const shown = uniq.slice(0, 5).join(', ');
    const more = uniq.length > 5 ? ` and ${uniq.length - 5} more` : '';
    model.notes.push(`${droppedFormula.length} part(s) were left out because BeamNG places or orients them with a formula Car2Roblox can't compute (e.g. ${shown}${more}); drawing them with a default position/rotation could jam them through the bodywork instead.`);
  }
  log(`  ${parts.length} meshes, ${model.materials.length} materials, ${model.textures.size} textures`);
  model.parts = parts;
  return model;
}

// ===================================================================== generic: OBJ / glTF / DAE
const srgbToLinear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const linearToSrgb = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(Math.max(c, 0), 1 / 2.4) - 0.055);
function normPath(p) {
  const out = [];
  for (const seg of p.replace(/\\/g, '/').split('/')) {
    if (seg === '' || seg === '.') continue;
    if (seg === '..') out.pop(); else out.push(seg);
  }
  return out.join('/');
}
// Resolve a texture / buffer reference next to a model file (case-insensitive). Remote links are never followed.
function findNear(store, baseDir, ref) {
  ref = ref.trim().replace(/^"|"$/g, '').replace(/\\/g, '/');
  if (!ref || /^[a-z][a-z0-9+.-]*:\/\//i.test(ref)) return null;
  const direct = normPath((baseDir ? baseDir + '/' : '') + ref).toLowerCase();
  if (store.index.has(direct)) return direct;
  const name = ref.split('/').pop().toLowerCase();
  const stem = name.replace(/\.[^.]+$/, '');
  const hits = [];
  for (const low of store.index.keys()) {
    const b = low.split('/').pop();
    if (b === name || (b.replace(/\.[^.]+$/, '') === stem && IMG_EXT.includes(extOf(b)))) hits.push(low);
  }
  if (!hits.length) return null;
  const bd = baseDir.toLowerCase();
  hits.sort((a, b) => ((b.startsWith(bd) ? 1 : 0) - (a.startsWith(bd) ? 1 : 0)) || (a.length - b.length));
  return hits[0];
}
const dirOf = (p) => { const i = p.replace(/\\/g, '/').lastIndexOf('/'); return i < 0 ? '' : p.slice(0, i); };

async function addStoreTexture(model, store, low, log) {
  const key = normName(baseName(low));
  if (model.textures.has(key)) return key;
  try {
    model.textures.set(key, makeTexture(key, await decodeImage(await store.read(low), low)));
    return key;
  } catch (e) {
    if (e instanceof ConversionError) { model.notes.push(`texture ${baseName(low)}: ${e.message}`); return null; }
    throw e;
  }
}
function parseMtl(text) {
  const mats = {};
  let cur = null;
  for (const ln of text.split(/\r?\n/)) {
    const s = ln.trim();
    if (!s || s[0] === '#') continue;
    const sp = s.search(/\s/);
    const key = (sp < 0 ? s : s.slice(0, sp)).toLowerCase();
    const rest = sp < 0 ? '' : s.slice(sp + 1).trim();
    if (key === 'newmtl') { cur = mats[rest] = mats[rest] || {}; }
    else if (cur) {
      if (key === 'kd') { const v = rest.split(/\s+/).slice(0, 3).map(Number); if (v.length === 3 && !v.some(Number.isNaN)) cur.kd = v; }
      else if (key === 'd') { const v = Number(rest.split(/\s+/)[0]); if (!Number.isNaN(v)) cur.d = v; }
      else if (key === 'tr') { const v = Number(rest.split(/\s+/)[0]); if (!Number.isNaN(v)) cur.d = 1 - v; }
      else if (key === 'map_kd') {
        const toks = rest.split(/\s+/);
        cur.map_kd = toks.length > 1 && toks[0].startsWith('-') ? toks[toks.length - 1] : rest;
      }
    }
  }
  return mats;
}
async function importObj(store, path, log) {
  const text = decodeText(await store.read(path.toLowerCase()));
  const baseDir = dirOf(path);
  const stem = stemOf(path);
  const model = newModel(stem, 'generic', axisSpec('+x', '+y', '-z'), { forwardAuto: true });
  const pos = [], uvs = [], nrm = [];
  let mtlDefs = {};
  const matIds = new Map();
  const matPending = new Map();
  async function materialFor(name) {
    if (matIds.has(name)) return matIds.get(name);
    const d = mtlDefs[name] || {};
    const kd = d.kd || [0.8, 0.8, 0.8], alpha = d.d === undefined ? 1.0 : d.d;
    let tex = null;
    if (d.map_kd) {
      const f = findNear(store, baseDir, d.map_kd);
      if (!f) model.notes.push(`texture '${d.map_kd}' referenced by the MTL was not found`);
      else tex = await addStoreTexture(model, store, f, log);
    }
    const idx = addMaterial(model, makeMaterial({ name: name || 'material', color: [kd[0], kd[1], kd[2], alpha], texture: tex, transparent: alpha < 0.98 }));
    matIds.set(name, idx);
    return idx;
  }
  const objs = new Map();
  let curObj = stem, curMat = '';
  const lines = text.split('\n');
  for (let li = 0; li < lines.length; li++) {
    const raw = lines[li];
    if (!raw || raw[0] === '#') continue;
    const parts = raw.trim().split(/\s+/);
    const k = parts[0];
    if (k === 'v') pos.push(+parts[1], +parts[2], +parts[3]);
    else if (k === 'vt') uvs.push(+parts[1], 1.0 - (parts.length > 2 ? +parts[2] : 0));
    else if (k === 'vn') nrm.push(+parts[1], +parts[2], +parts[3]);
    else if (k === 'f') {
      const corners = [];
      for (let j = 1; j < parts.length; j++) {
        const a = parts[j].split('/');
        let vi = parseInt(a[0], 10);
        vi = vi > 0 ? vi - 1 : pos.length / 3 + vi;
        let ti = -1, ni = -1;
        if (a.length > 1 && a[1]) { const t = parseInt(a[1], 10); ti = t > 0 ? t - 1 : uvs.length / 2 + t; }
        if (a.length > 2 && a[2]) { const n = parseInt(a[2], 10); ni = n > 0 ? n - 1 : nrm.length / 3 + n; }
        corners.push([vi, ti, ni]);
      }
      let o = objs.get(curObj);
      if (!o) { o = { p: [], u: [], n: [], m: [] }; objs.set(curObj, o); }
      const mid = await materialFor(curMat);
      for (let j = 1; j < corners.length - 1; j++) {
        for (const c of [corners[0], corners[j], corners[j + 1]]) { o.p.push(c[0]); o.u.push(c[1]); o.n.push(c[2]); }
        o.m.push(mid);
      }
    } else if (k === 'o' || k === 'g') curObj = parts.slice(1).join(' ') || stem;
    else if (k === 'usemtl') curMat = parts.slice(1).join(' ');
    else if (k === 'mtllib') {
      for (const nm of parts.slice(1).join(' ').split(',')) {
        const mf = findNear(store, baseDir, nm.trim());
        if (mf) Object.assign(mtlDefs, parseMtl(decodeText(await store.read(mf))));
      }
    }
    if ((li & 0x3FFF) === 0x3FFF) await tick();
  }
  if (!pos.length || !objs.size) throw new ConversionError('the OBJ file contains no faces');
  const P = Float32Array.from(pos), U = uvs.length ? Float32Array.from(uvs) : null, N = nrm.length ? Float32Array.from(nrm) : null;
  for (const [name, o] of objs) {
    const ptri = Int32Array.from(o.p), utri = Int32Array.from(o.u), ntri = Int32Array.from(o.n);
    let hasU = !!U, hasN = !!N;
    if (hasU) hasU = utri.some((x) => x >= 0);
    if (hasN) hasN = ntri.some((x) => x >= 0);
    for (let i = 0; i < ptri.length; i++) if (ptri[i] < 0 || ptri[i] >= P.length / 3) throw new ConversionError('OBJ face refers to a vertex that does not exist');
    const mesh = Mesh.build(P, ptri, Int32Array.from(o.m), hasU ? U : null, hasU ? utri : null, hasN ? N : null, hasN ? ntri : null);
    if (mesh.triCount) model.parts.push({ name, mesh, wheelSlot: null, kindHint: null });
  }
  log(`OBJ: ${model.parts.length} objects, ${modelTriCount(model)} triangles`);
  return model;
}

// ---- glTF
const GL_CT = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
const GL_NC = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };
async function importGltf(store, path, log) {
  const raw = await store.read(path.toLowerCase());
  const baseDir = dirOf(path);
  let json, binChunk = null;
  if (raw.length > 12 && latin1(raw, 0, 4) === 'glTF') {
    const d = dv(raw);
    let off = 12, js = null;
    while (off + 8 <= raw.length) {
      const clen = d.getUint32(off, true), ctype = latin1(raw, off + 4, off + 8);
      const body = raw.subarray(off + 8, off + 8 + clen);
      if (ctype === 'JSON') js = body; else if (ctype === 'BIN\0') binChunk = body;
      off += 8 + clen;
    }
    if (!js) throw new ConversionError('GLB has no JSON chunk');
    json = JSON.parse(utf8(js));
  } else {
    try { json = JSON.parse(decodeText(raw)); } catch (e) { throw new ConversionError(`not a glTF file: ${e.message}`); }
  }
  const buffers = new Map();
  const dataUri = (uri) => {
    const b64 = uri.split(',', 2)[1] || '';
    const bin = atob(b64);
    const o = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) o[i] = bin.charCodeAt(i);
    return o;
  };
  async function buffer(i) {
    if (buffers.has(i)) return buffers.get(i);
    const b = json.buffers[i];
    let data;
    if (b.uri === undefined) data = binChunk || new Uint8Array(0);
    else if (b.uri.startsWith('data:')) data = dataUri(b.uri);
    else {
      const f = findNear(store, baseDir, decodeURIComponent(b.uri));
      if (!f) throw new ConversionError(`glTF buffer file '${b.uri}' is missing`);
      data = await store.read(f);
    }
    buffers.set(i, data);
    return data;
  }
  const bufs = [];
  for (let i = 0; i < (json.buffers || []).length; i++) bufs.push(await buffer(i));
  const viewBytes = (vi) => { const v = json.bufferViews[vi]; const o = v.byteOffset || 0; return bufs[v.buffer].subarray(o, o + v.byteLength); };
  function accessor(ai) {
    const a = json.accessors[ai];
    const T = GL_CT[a.componentType], nc = GL_NC[a.type], count = a.count;
    const out = new Float64Array(count * nc);
    if (a.bufferView !== undefined) {
      const v = json.bufferViews[a.bufferView];
      const buf = bufs[v.buffer];
      const base = (v.byteOffset || 0) + (a.byteOffset || 0);
      const stride = v.byteStride || T.BYTES_PER_ELEMENT * nc;
      const d = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
      const get = { 5120: 'getInt8', 5121: 'getUint8', 5122: 'getInt16', 5123: 'getUint16', 5125: 'getUint32', 5126: 'getFloat32' }[a.componentType];
      const sz = T.BYTES_PER_ELEMENT;
      const norm = a.normalized && a.componentType !== 5126;
      const maxv = norm ? (a.componentType === 5120 ? 127 : a.componentType === 5121 ? 255 : a.componentType === 5122 ? 32767 : a.componentType === 5123 ? 65535 : 4294967295) : 1;
      for (let i = 0; i < count; i++) for (let k = 0; k < nc; k++) {
        let x = d[get](base + i * stride + k * sz, true);
        if (norm) x = Math.max(x / maxv, -1);
        out[i * nc + k] = x;
      }
    }
    return { data: out, nc };
  }
  const model = newModel(stemOf(path), 'generic', axisSpec('+x', '+y', '-z'), { forwardAuto: true });
  const texKeys = new Map();
  async function textureFor(ti) {
    if (texKeys.has(ti)) return texKeys.get(ti);
    let key = null;
    try {
      const src = json.textures[ti].source;
      const im = json.images[src];
      let data;
      if (im.bufferView !== undefined) data = viewBytes(im.bufferView);
      else if ((im.uri || '').startsWith('data:')) data = dataUri(im.uri);
      else {
        const f = findNear(store, baseDir, decodeURIComponent(im.uri || ''));
        if (!f) throw new ConversionError(`image '${im.uri}' not found`);
        data = await store.read(f);
      }
      key = normName(im.name || `texture_${src}`) + `_${src}`;
      if (!model.textures.has(key)) model.textures.set(key, makeTexture(key, await decodeImage(data, im.uri || im.name || '')));
    } catch (e) {
      if (e instanceof ConversionError || e instanceof TypeError) { model.notes.push(`glTF texture ${ti} skipped: ${e.message}`); key = null; } else throw e;
    }
    texKeys.set(ti, key);
    return key;
  }
  const matMap = new Map();
  async function materialFor(mi) {
    const k = mi === undefined || mi === null ? -1 : mi;
    if (matMap.has(k)) return matMap.get(k);
    let color = [0.8, 0.8, 0.8, 1.0], tex = null, name = 'material', transparent = false, double = false;
    if (k >= 0 && k < (json.materials || []).length) {
      const m = json.materials[k];
      name = m.name || `material_${k}`;
      const pbr = m.pbrMetallicRoughness || {};
      const f = pbr.baseColorFactor;
      color = f ? [linearToSrgb(f[0]), linearToSrgb(f[1]), linearToSrgb(f[2]), f[3]] : [1, 1, 1, 1];
      if (pbr.baseColorTexture) tex = await textureFor(pbr.baseColorTexture.index);
      transparent = m.alphaMode === 'BLEND' || color[3] < 0.98;
      double = !!m.doubleSided;
    }
    const idx = addMaterial(model, makeMaterial({ name, color, texture: tex, transparent, doubleSided: double }));
    matMap.set(k, idx);
    return idx;
  }
  function nodeMatrix(n) {                      // row-major 3x3 + translation
    if (n.matrix) { const m = n.matrix; return [m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14]]; }
    let A = I3();
    if (n.rotation) {
      const [x, y, z, w] = n.rotation;
      A = [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
           2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
           2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)];
    }
    const s = n.scale || [1, 1, 1];
    A = [A[0] * s[0], A[1] * s[1], A[2] * s[2], A[3] * s[0], A[4] * s[1], A[5] * s[2], A[6] * s[0], A[7] * s[1], A[8] * s[2]];
    const t = n.translation || [0, 0, 0];
    return [A[0], A[1], A[2], t[0], A[3], A[4], A[5], t[1], A[6], A[7], A[8], t[2]];
  }
  async function primitiveMesh(prim) {
    const attrs = prim.attributes || {};
    if (attrs.POSITION === undefined) return null;
    const P = accessor(attrs.POSITION).data;
    const N = attrs.NORMAL !== undefined ? accessor(attrs.NORMAL).data : null;
    const U = attrs.TEXCOORD_0 !== undefined ? accessor(attrs.TEXCOORD_0).data : null;
    const nv = P.length / 3;
    let idx;
    if (prim.indices !== undefined) idx = accessor(prim.indices).data; else { idx = new Float64Array(nv); for (let i = 0; i < nv; i++) idx[i] = i; }
    const mode = prim.mode === undefined ? 4 : prim.mode;
    const tris = [];
    if (mode === 4) for (let i = 0; i + 2 < idx.length; i += 3) tris.push(idx[i], idx[i + 1], idx[i + 2]);
    else if (mode === 5) for (let i = 0; i + 2 < idx.length; i++) { const a = idx[i], b = idx[i + 1], c = idx[i + 2]; if (i & 1) tris.push(b, a, c); else tris.push(a, b, c); }
    else if (mode === 6) for (let i = 1; i + 1 < idx.length; i++) tris.push(idx[0], idx[i], idx[i + 1]);
    else return null;
    if (!tris.length) return null;
    const mid = await materialFor(prim.material);
    return new Mesh(Float32Array.from(P), Int32Array.from(tris), new Int32Array(tris.length / 3).fill(mid),
                    N ? Float32Array.from(N) : null, U ? Float32Array.from(U) : null);
  }
  const nodes = json.nodes || [], scenes = json.scenes || [];
  const roots = scenes.length ? ((scenes[json.scene || 0] || {}).nodes || []) : nodes.map((_, i) => i);
  async function walk(ni, parent, depth = 0) {
    if (depth > 64) return;
    const n = nodes[ni];
    const L = nodeMatrix(n);
    const Al = [L[0], L[1], L[2], L[4], L[5], L[6], L[8], L[9], L[10]], tl = [L[3], L[7], L[11]];
    const A = mmul(parent[0], Al);
    const pt = mvec(parent[0], tl);
    const world = [A, [pt[0] + parent[1][0], pt[1] + parent[1][1], pt[2] + parent[1][2]]];
    if (n.mesh !== undefined) {
      const msh = json.meshes[n.mesh];
      const subs = [];
      for (const p of (msh.primitives || [])) { const m = await primitiveMesh(p); if (m) subs.push(m); }
      if (subs.length) model.parts.push({ name: n.name || msh.name || `mesh${n.mesh}`, mesh: Mesh.concat(subs).transformed(world[0], world[1]), wheelSlot: null, kindHint: null });
    }
    for (const c of (n.children || [])) await walk(c, world, depth + 1);
  }
  for (const r of roots) await walk(r, [I3(), [0, 0, 0]]);
  if (!model.parts.length) throw new ConversionError('the glTF file contains no triangle meshes');
  log(`glTF: ${model.parts.length} meshes, ${modelTriCount(model)} triangles`);
  return model;
}

// ---- Collada as a generic source
async function importDaeGeneric(store, path, log) {
  const scene = parseCollada(await store.read(path.toLowerCase()));
  const baseDir = dirOf(path);
  const model = newModel(stemOf(path), 'generic', axisSpec('+x', '+z', '+y'), { forwardAuto: true });
  const ids = [];
  for (const nm of scene.materialNames) {
    const dm = scene.materials[nm];
    let tex = null, color = [0.8, 0.8, 0.8, 1.0];
    if (dm) {
      if (dm.color) color = dm.color;
      if (dm.texturePath) {
        const f = findNear(store, baseDir, dm.texturePath.replace('file://', ''));
        if (f) tex = await addStoreTexture(model, store, f, log);
      }
    }
    ids.push(addMaterial(model, makeMaterial({ name: nm, color, texture: tex, transparent: color[3] < 0.98 })));
  }
  for (const inst of scene.instances) {
    const m = inst.mesh;
    const gl = ids.length ? ids : [0];
    const mats = new Int32Array(m.mat.length);
    for (let i = 0; i < mats.length; i++) mats[i] = gl[m.mat[i]];
    model.parts.push({ name: inst.nodeName || inst.geometryName, mesh: new Mesh(m.pos, m.tri, mats, m.nrm, m.uv), wheelSlot: null, kindHint: null });
  }
  log(`DAE: ${model.parts.length} meshes`);
  return model;
}
async function importGeneric(store, path, log) {
  const ext = extOf(path);
  if (ext === '.obj') return importObj(store, path, log);
  if (ext === '.glb' || ext === '.gltf') return importGltf(store, path, log);
  if (ext === '.dae') return importDaeGeneric(store, path, log);
  if (ext === '.fbx') throw new ConversionError('FBX is a closed binary format and cannot be read directly. Open it in Blender and export OBJ, GLB or DAE, then convert that.');
  if (ext === '.rpf') {
    throw new ConversionError("'.rpf' is a GTA V game archive (it can hold thousands of files in a compressed, partly encrypted format), not a single car model, so Car2Roblox cannot read it directly - that would mean reimplementing Rockstar's RAGE engine format. Open it in CodeWalker (free) or OpenIV, find the car's .yft inside, export it as OBJ, GLB or DAE with its textures, and convert that file instead.");
  }
  if (['.yft', '.ydr', '.ytd', '.ydd', '.odr', '.oft'].includes(ext)) {
    throw new ConversionError(`'${ext}' is a GTA V / RAGE game format. Open it in CodeWalker or OpenIV (or Blender with the Sollumz add-on), export it as OBJ / GLB / DAE and convert that file. Textures must be exported next to it as .png or .dds.`);
  }
  throw new ConversionError(`unsupported file type '${ext}'`);
}

// ===================================================================== what did the user give us?
// store: Store of every dropped file (zips are opened by the UI and mounted at the root).
function detectSources(store, zipName) {
  const paths = store.paths();
  const byExt = (e) => paths.filter((p) => extOf(p) === e);
  const out = [];
  if (zipName) { out.push({ kind: 'beamng', path: zipName, label: zipName }); return out; }
  if (paths.some((p) => extOf(p) === '.jbeam')) { out.push({ kind: 'beamng', path: '', label: 'BeamNG vehicle folder' }); return out; }
  for (const p of byExt('.dff')) out.push({ kind: 'gta_sa', path: p, label: baseName(p) });
  for (const e of ['.obj', '.glb', '.gltf', '.dae', '.fbx', '.yft', '.ydr', '.odr', '.oft', '.rpf']) for (const p of byExt(e)) out.push({ kind: 'generic', path: p, label: baseName(p) });
  return out;
}
// src: one entry of detectSources; opts: { source, vehicle, config, includeLod }
async function loadModel(store, src, srcName, opts, log) {
  const kind = opts.source && opts.source !== 'auto' ? opts.source : src.kind;
  let model;
  if (kind === 'gta_sa') {
    if (!src.path || extOf(src.path) !== '.dff') throw new ConversionError('select the .dff model');
    const stem = stemOf(src.path).toLowerCase();
    const txdPaths = store.paths().filter((p) => extOf(p) === '.txd');
    const rank = (p) => { const s = stemOf(p).toLowerCase(); return s === stem ? 0 : (s === 'vehicle' || s === 'generic') ? 2 : 1; };
    txdPaths.sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : 1));
    const txds = [];
    for (const p of txdPaths) txds.push({ name: baseName(p), bytes: await store.read(p.toLowerCase()) });
    model = importGtaSa(src.path, await store.read(src.path.toLowerCase()), txds, !!opts.includeLod, log);
  } else if (kind === 'beamng') {
    model = await importBeamng(store, srcName || src.label, opts, log);
  } else if (kind === 'generic') {
    model = await importGeneric(store, src.path, log);
  } else throw new ConversionError(`unknown source type '${kind}'`);
  scrubModel(model);
  return model;
}
