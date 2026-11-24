// ===================================================================== pipeline: axes, scale, pieces, wheels
const ROBLOX_MAX_TRIANGLES = 20000;
const STUDS_PER_METER = 1 / 0.28;
const UNITS = { m: 1.0, cm: 0.01, mm: 0.001, in: 0.0254, ft: 0.3048 };
const DEFAULT_PAINT = { primary: '#2F6DB5', secondary: '#E8E8E8', tertiary: '#202020', quaternary: '#202020' };

function defaultOptions() {
  return {
    source: 'auto', name: null, scale: 1.0, length: null, unit: null, axes: null, flipForward: false,
    paint: null, paint2: null, paint3: null, includeLod: false, includeGlass: true, includeInterior: true,
    includeLights: true, maxTrisPerMesh: ROBLOX_MAX_TRIANGLES, targetTris: null, maxTexture: 1024,
    formats: ['glb', 'obj'], rig: true, addTires: true, newTires: false, topSpeed: null, drivetrain: 'awd',
    advancedChassis: false, vehicle: null, config: null,
  };
}
const sanitize = (name, fallback = 'Part') => {
  const s = String(name).replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return s || fallback;
};

function unionBounds(meshes) {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const m of meshes) {
    const b = m.bounds();
    for (let k = 0; k < 3; k++) { if (b.lo[k] < lo[k]) lo[k] = b.lo[k]; if (b.hi[k] > hi[k]) hi[k] = b.hi[k]; }
  }
  return { lo, hi };
}
function pieceBox(p) {
  if (!p._box) {
    const { lo, hi } = p.mesh.bounds();
    p._box = { lo, hi, center: [0, 1, 2].map((k) => (lo[k] + hi[k]) / 2), size: [0, 1, 2].map((k) => hi[k] - lo[k]) };
  }
  return p._box;
}

function autoAxes(model, log) {
  const spec = model.axes;
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const p of model.parts) {
    const b = p.mesh.bounds();
    for (let k = 0; k < 3; k++) { if (b.lo[k] < lo[k]) lo[k] = b.lo[k]; if (b.hi[k] > hi[k]) hi[k] = b.hi[k]; }
  }
  const ext = [0, 1, 2].map((k) => hi[k] - lo[k]);
  const upV = parseAxis(spec.up);
  const upI = upV.findIndex((x) => x !== 0);
  const horiz = [0, 1, 2].filter((i) => i !== upI);
  const fwdI = horiz.reduce((best, i) => (ext[i] > ext[best] ? i : best), horiz[0]);
  const curI = parseAxis(spec.forward).findIndex((x) => x !== 0);
  if (fwdI === curI) return spec;
  // the roof sits behind the middle of a car, so the front points away from the roof
  let hmin = Infinity, hmax = -Infinity;
  for (const p of model.parts) for (let i = upI; i < p.mesh.pos.length; i += 3) {
    const h = p.mesh.pos[i] * upV[upI];
    if (h < hmin) hmin = h; if (h > hmax) hmax = h;
  }
  const thr = hmin + 0.85 * (hmax - hmin);
  let sum = 0, cnt = 0;
  for (const p of model.parts) for (let i = 0; i < p.mesh.pos.length; i += 3) {
    if (p.mesh.pos[i + upI] * upV[upI] > thr) { sum += p.mesh.pos[i + fwdI]; cnt++; }
  }
  let sign = 1;
  if (cnt) {
    const c = sum / cnt - (lo[fwdI] + hi[fwdI]) / 2;
    if (Math.abs(c) > 0.02 * ext[fwdI]) sign = c > 0 ? -1 : 1;
  }
  const fwdV = [0, 0, 0];
  fwdV[fwdI] = sign;
  const rightV = cross3(fwdV, upV);
  const next = axisSpec(axisTokenOf(rightV), spec.up, axisTokenOf(fwdV));
  log(`  detected forward axis ${next.forward} (up ${next.up}); if the car faces backwards, use "Flip front/back"`);
  return next;
}

function finalMaterial(m, paints) {
  if (m.paint && paints[m.paint] && !m.texture) {
    const [r, g, b] = paints[m.paint];
    return makeMaterial({ name: m.name, color: [r, g, b, m.color[3]], texture: null, paint: m.paint, transparent: m.transparent, doubleSided: m.doubleSided });
  }
  return m;
}

// Cluster wheel pieces into wheels. Returns { wheels, ground } (ground = y of the ground under them, or null).
function groupWheels(pieces, drivetrain, log) {
  const cand = pieces.filter((p) => p.kind === 'wheel');
  if (!cand.length) return { wheels: [], ground: null };
  const rad = (p) => Math.max(pieceBox(p).size[1], pieceBox(p).size[2]);
  const items = [...cand].sort((a, b) => rad(b) - rad(a));
  const clusters = [];
  for (const pc of items) {
    const c = pieceBox(pc).center;
    let hit = null;
    for (const cl of clusters) {
      // same axle position (tyre, rim, nuts, discs...) - the other side of the car is far in x
      if (Math.hypot(c[1] - cl.c[1], c[2] - cl.c[2]) < 0.6 * cl.r && Math.abs(c[0] - cl.c[0]) < 0.9 * cl.r) { hit = cl; break; }
    }
    if (hit) hit.m.push(pc); else clusters.push({ c, r: rad(pc) / 2, m: [pc] });
  }
  const groups = clusters.map((cl) => {
    // the hub comes from the tyre-sized pieces only, so brake discs or stray bolts cannot pull it
    const tyres = cl.m.filter((p) => rad(p) / 2 >= 0.8 * cl.r);
    const { lo, hi } = unionBounds(tyres.map((p) => p.mesh));
    return { lo, hi, hub: [0, 1, 2].map((k) => (lo[k] + hi[k]) / 2), r: Math.max(hi[1] - lo[1], hi[2] - lo[2]) / 2, m: cl.m, width: hi[0] - lo[0] };
  });
  // the ground is where at least two wheels bottom out together; anything clearly higher is a spare tyre or a prop
  const rmax0 = Math.max(...groups.map((g) => g.r));
  const lows = groups.map((g) => g.lo[1]).sort((a, b) => a - b);
  let groundY = lows[0];
  for (const lv of lows) {
    if (lows.filter((x) => x <= lv + 0.25 * rmax0).length >= Math.min(2, lows.length)) { groundY = lv; break; }
  }
  const ground = [], dropped = [];
  for (const g of groups) (g.lo[1] < groundY + 0.45 * g.r + 0.05 ? ground : dropped).push(g);
  for (const g of dropped) for (const p of g.m) { p.kind = 'body'; p.wheelSlot = null; }
  if (dropped.length) log(`  ${dropped.length} wheel-like parts are off the ground (spare/props): treated as body`);
  if (!ground.length) return { wheels: [], ground: null };
  const zs = ground.map((g) => g.hub[2]).sort((a, b) => a - b);
  const rmax = Math.max(...ground.map((g) => g.r));
  const axles = [];
  for (const z of zs) if (!axles.length || Math.abs(z - axles[axles.length - 1]) > 0.5 * rmax) axles.push(z);
  const out = [], used = {};
  const sorted = [...ground].sort((a, b) => (a.hub[2] - b.hub[2]) || (a.hub[0] - b.hub[0]));
  for (const g of sorted) {
    let ai = 0, bd = Infinity;
    axles.forEach((z, i) => { const d = Math.abs(z - g.hub[2]); if (d < bd) { bd = d; ai = i; } });
    let letter = ai === 0 ? 'F' : (ai === axles.length - 1 ? 'R' : 'M');
    if (axles.length === 1) letter = 'F';
    let slot = letter + (g.hub[0] < 0 ? 'L' : 'R');
    used[slot] = (used[slot] || 0) + 1;
    if (used[slot] > 1) slot += String(used[slot]);
    const vol = (p) => pieceBox(p).size[0] * pieceBox(p).size[1] * pieceBox(p).size[2];
    const primary = g.m.reduce((best, p) => (vol(p) > vol(best) ? p : best), g.m[0]);
    for (const p of g.m) p.wheelSlot = slot;
    out.push({ slot, hub: g.hub, radius: g.r, width: g.width, pieces: g.m.map((p) => p.name), primary: primary.name,
      front: (letter === 'F' && axles.length > 1) || axles.length === 1 });
  }
  log(`  ${out.length} wheels in ${axles.length} axle(s): ${out.map((w) => w.slot).join(', ')}`);
  return { wheels: out, ground: Math.min(...ground.map((g) => g.lo[1])) };
}

function shiftPieces(pieces, wheels, dy) {
  for (const p of pieces) { p.mesh = p.mesh.transformed(I3(), [0, dy, 0]); p._box = null; }
  return wheels.map((w) => Object.assign({}, w, { hub: [w.hub[0], w.hub[1] + dy, w.hub[2]] }));
}

function uniqueNames(extra, pieces) {
  const names = new Set(pieces.map((p) => p.name.toLowerCase()));
  for (const e of extra) {
    let k = 2;
    const base = e.name;
    while (names.has(e.name.toLowerCase())) { e.name = `${base}_${k}`; k++; }
    names.add(e.name.toLowerCase());
    e.part = e.name;
  }
}
const nearWheel = (wheels, hub, radius) => wheels.some((g) => Math.hypot(g.hub[0] - hub[0], g.hub[1] - hub[1], g.hub[2] - hub[2]) < 0.8 * Math.max(g.radius, radius || 1.0));

// Repair what the importers produced, group it into wheels, put wheels where they are missing (move a wheel that was
// left in the middle of the car, copy the wheels there are, or generate tyres), and stand the car on its wheels.
function finishWheels(model, opts, pieces, R, s, shift, log, warnings) {
  let { lo, hi } = unionBounds(pieces.map((p) => p.mesh));
  const nSplit = splitFusedWheels(pieces, lo, hi);
  if (nSplit) log(`  split ${nSplit} fused wheel meshes into separate wheels`);
  if (!pieces.some((p) => p.kind === 'wheel')) {
    const n = findRoundParts(pieces, lo, hi);
    if (n) {
      log(`  ${n} unnamed round parts look like wheels`);
      warnings.push(`${n} parts without 'wheel' in their name were treated as wheels because of their shape.`);
    }
  }
  const nDup = dropDuplicateWheels(pieces);
  if (nDup) log(`  removed ${nDup} duplicate wheel meshes (they flicker when stacked)`);

  // wheels sitting in the middle of the car are not on their hubs: set them aside for now
  const mid = centreLineWheels(pieces);
  for (const p of mid) p.kind = 'other';
  let { wheels, ground } = groupWheels(pieces, opts.drivetrain, mid.length ? () => {} : log);

  const make = opts.addTires || opts.newTires;
  const hints = (make || mid.length) ? model.wheelHints.map((h) => hintToStud(h, R, s, shift)) : [];
  if (make || mid.length) {
    const midSet = new Set(mid);
    const rest = pieces.filter((p) => !midSet.has(p));
    ({ lo, hi } = unionBounds((rest.length ? rest : pieces).map((p) => p.mesh)));
    const targets = cornerTargets(wheels, lo, hi, hints);
    const labels = targetLabels(targets);
    let need = [];
    targets.forEach((t, i) => { if (!nearWheel(wheels, t.hub, t.radius)) need.push(i); });
    if (wheels.length >= targets.length) need = [];
    const byName = new Map(pieces.map((p) => [p.name, p]));
    const newPieces = [];
    let moved = 0, copied = 0, madeN = 0;
    if (need.length && wheels.length) {
      // the model has some wheels: copy the nearest one onto each empty hub
      for (const i of need) {
        const t = targets[i], tLeft = t.hub[0] < 0;
        let g = null, best = null;
        for (const w of wheels) {
          const key = [(w.hub[0] < 0) === tLeft ? 0 : 1, Math.hypot(w.hub[0] - t.hub[0], w.hub[1] - t.hub[1], w.hub[2] - t.hub[2])];
          if (best === null || key[0] < best[0] || (key[0] === best[0] && key[1] < best[1])) { best = key; g = w; }
        }
        const src = g.pieces.map((n) => byName.get(n)).filter(Boolean);
        newPieces.push(...copyWheel(src, g.hub, t.hub, (g.hub[0] < 0) !== tLeft, labels[i]));
        copied++;
      }
      for (const p of mid) p.kind = 'body';
      const keep = pieces.filter((p) => !midSet.has(p)); pieces.length = 0; pieces.push(...keep);
    } else if (need.length && mid.length) {
      // nothing is on a hub: the wheel(s) in the middle are the only source
      const groups = new Map(), plain = [];
      for (const p of mid) {
        const sl = slotFromName(p.name) || slotFromName(p.part);
        if (sl) { if (!groups.has(sl)) groups.set(sl, []); groups.get(sl).push(p); } else plain.push(p);
      }
      if (groups.size >= 2) {            // one wheel per name (wheel_lf, wheel_rf ...)
        const spent = new Set();
        for (const i of need) {
          const t = targets[i];
          if (groups.has(labels[i])) {
            const g = groups.get(labels[i]);
            const c = unionCentre(g);
            const d = [t.hub[0] - c[0], t.hub[1] - c[1], t.hub[2] - c[2]];
            for (const p of g) { p.mesh = p.mesh.transformed(I3(), d); p._box = null; p.kind = 'wheel'; spent.add(p); }
            moved++;
          } else {
            const [sl, g] = groups.entries().next().value;
            newPieces.push(...copyWheel(g, unionCentre(g), t.hub, (sl[1] === 'L') !== (t.hub[0] < 0), labels[i]));
            copied++;
          }
        }
        for (const p of mid) if (!spent.has(p)) p.kind = 'body';
        const keep = pieces.filter((p) => !midSet.has(p) || spent.has(p)); pieces.length = 0; pieces.push(...keep);
      } else {                           // one template wheel for all four corners
        const rad = (p) => Math.max(pieceBox(p).size[1], pieceBox(p).size[2]);
        const big = mid.reduce((b, p) => (rad(p) > rad(b) ? p : b), mid[0]);
        const r0 = rad(big) / 2, bc = pieceBox(big).center;
        const tmpl = mid.filter((p) => { const c = pieceBox(p).center; return Math.hypot(c[0] - bc[0], c[1] - bc[1], c[2] - bc[2]) < 0.6 * r0; });
        const c = unionCentre(tmpl);
        const sl = groups.size ? groups.keys().next().value : null;
        for (const i of need) {
          const t = targets[i];
          const leftSrc = !!(sl && sl[1] === 'L');
          newPieces.push(...copyWheel(tmpl, c, t.hub, leftSrc !== (t.hub[0] < 0), labels[i]));
          moved++;
        }
        const keep = pieces.filter((p) => !midSet.has(p)); pieces.length = 0; pieces.push(...keep);
      }
    } else if (need.length && make) {
      const plan = need.map((i) => Object.assign({}, targets[i]));
      const taken = new Set(wheels.map((w) => w.slot));
      newPieces.push(...wheelPieces(plan, taken, opts.maxTrisPerMesh, opts.targetTris ? Math.floor(0.2 * opts.targetTris) : null));
      madeN = plan.length;
    } else {
      for (const p of mid) p.kind = 'body';      // a spare tyre in the boot, not a wheel
    }
    if (mid.length && !need.length) log(`  ${mid.length} wheel part(s) in the middle of the car look like a spare: kept as body`);
    if (newPieces.length) {
      uniqueNames(newPieces, pieces);
      pieces.push(...newPieces);
      if (moved) {
        log(`  a wheel was sitting in the middle of the car: moved to ${moved} wheel positions`);
        warnings.push(`A wheel was sitting in the middle of the car instead of on its hub, so it was moved onto ${moved} wheel position(s). Check the wheels in the preview.`);
      }
      if (copied) {
        log(`  only some wheels were there: copied them to ${copied} empty wheel position(s)`);
        warnings.push(`Only some wheels were found, so ${copied} wheel(s) were copied from the ones that exist.`);
      }
      if (madeN) {
        const how = hints.length ? 'from the JBeam hub nodes' : (!wheels.length ? 'at the usual spots for a car of this size' : 'from the wheels that exist');
        const had = !wheels.length ? 'no tyres' : 'tyres on only some wheels';
        log(`  ${had}: generated ${madeN} tyre + rim pair(s) ${how}`);
        warnings.push(`The model had ${had}, so ${madeN} tyre + rim pair(s) were generated ${how}. If the body already has wheels built into it, turn off 'Add tires' and convert again.`);
      }
      ({ wheels, ground } = groupWheels(pieces, opts.drivetrain, log));
    } else if (mid.length && wheels.length) {
      ({ wheels, ground } = groupWheels(pieces, opts.drivetrain, log));
    }
  }

  if (opts.newTires && wheels.length) {
    // always use clean tyres: keep where the wheels are, replace what they look like
    const plan = wheels.map((g) => ({ hub: g.hub.slice(), radius: g.radius, width: g.width > 0 ? g.width : 0.7 * g.radius }));
    const keep = pieces.filter((p) => p.kind !== 'wheel'); pieces.length = 0; pieces.push(...keep);
    const extra = wheelPieces(plan, new Set(), opts.maxTrisPerMesh, opts.targetTris ? Math.floor(0.2 * opts.targetTris) : null);
    uniqueNames(extra, pieces);
    pieces.push(...extra);
    log(`  replaced the model's wheels with ${plan.length} clean generated tyre + rim pairs`);
    warnings.push(`The wheels were replaced by ${plan.length} generated tyre + rim pairs at the same positions ('Always use new tires').`);
    ({ wheels, ground } = groupWheels(pieces, opts.drivetrain, log));
  }

  if (wheels.length && ground !== null && Math.abs(ground) > 0.01) {
    // stand the car on its wheels (an under-body plate or shadow mesh must not hold it up)
    wheels = shiftPieces(pieces, wheels, -ground);
  }
  return wheels;
}

function pickUnit(model, extentSrc, opts, warnings) {
  if (opts.unit) {
    if (!(opts.unit in UNITS)) throw new ConversionError(`unknown unit '${opts.unit}' (use ${Object.keys(UNITS).join(', ')})`);
    return UNITS[opts.unit];
  }
  if (model.source !== 'generic') return model.unitMeters;
  const e = extentSrc * model.unitMeters;
  let u, label;
  if (e >= 1000) [u, label] = [0.001, 'millimetres'];
  else if (e >= 100) [u, label] = [0.01, 'centimetres'];
  else if (e >= 20) [u, label] = [0.0254, 'inches'];
  else return model.unitMeters;
  warnings.push(`The model is ${e.toFixed(0)} units long, so I assumed ${label}. Pick the unit by hand if that is wrong.`);
  return u;
}

async function buildPieces(model, opts, log, warnings) {
  if (!model.parts.length) throw new ConversionError('nothing to convert: the model has no geometry');
  let axes;
  if (opts.axes) axes = axisSpec(...opts.axes);
  else if (model.forwardAuto) axes = autoAxes(model, log);
  else axes = model.axes;
  if (opts.flipForward) axes = axisFlipForward(axes);
  const R = axisToRoblox(axes);
  let meshes = model.parts.map((p) => p.mesh.transformed(R));
  let { lo, hi } = unionBounds(meshes);
  const extent = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const unit = pickUnit(model, extent[2], opts, warnings);
  let s;
  if (opts.length) {
    if (extent[2] <= 0) throw new ConversionError('cannot derive a scale: the model has no length');
    s = opts.length / extent[2];
  } else s = unit * STUDS_PER_METER * opts.scale;
  meshes = meshes.map((m) => m.transformed(scaleMat(s)));
  ({ lo, hi } = unionBounds(meshes));
  const shift = [-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2];
  meshes = meshes.map((m) => m.transformed(I3(), shift));
  ({ lo, hi } = unionBounds(meshes));
  const size = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  log(`  size: ${size[0].toFixed(1)} wide x ${size[1].toFixed(1)} tall x ${size[2].toFixed(1)} long studs (${(size[2] * 0.28).toFixed(2)} m)`);
  if (!(size[2] >= 4 && size[2] <= 60)) warnings.push(`The car came out ${size[2].toFixed(1)} studs long (typical: 12-30). Check the units / length setting.`);
  if (Math.max(...size) > 2048) warnings.push("The car is larger than Roblox's 2048-stud mesh limit; set a length.");

  const paints = {};
  for (const [k, v] of Object.entries(DEFAULT_PAINT)) paints[k] = parseHexColor(v);
  for (const [k, v] of Object.entries(model.defaultPaint)) paints[k] = v;
  for (const [k, v] of [['primary', opts.paint], ['secondary', opts.paint2], ['tertiary', opts.paint3]]) if (v) paints[k] = parseHexColor(v);
  const finalMats = model.materials.map((m) => finalMaterial(m, paints));

  const entries = [];
  for (let pi = 0; pi < model.parts.length; pi++) {
    const part = model.parts[pi];
    let mesh = meshes[pi];
    let fixed;
    [mesh, fixed] = autoFixWinding(mesh);
    if (fixed) log(`  fixed inside-out winding on '${part.name}'`);
    mesh = mesh.dropDegenerate();
    if (mesh.triCount === 0) continue;
    if (!mesh.nrm) mesh = mesh.recomputeNormals();
    const used = [...new Set(mesh.mat)].sort((a, b) => a - b);
    const mats = used.map((i) => finalMats[i]);
    const transparent = mats.every((m) => m.transparent);
    let kind = classifyPart(part.name, mats.map((m) => m.name), transparent, part.kindHint);
    if (part.wheelSlot) kind = 'wheel';
    if (kind === 'glass' && !opts.includeGlass) continue;
    if (kind === 'interior' && !opts.includeInterior) continue;
    if (kind === 'light' && !opts.includeLights) continue;
    entries.push({ part, kind, mesh });
    if ((pi & 7) === 7) await tick();
  }
  if (!entries.length) throw new ConversionError('every part was filtered out - nothing left to export');
  const total = entries.reduce((a, e) => a + e.mesh.triCount, 0);
  let ratio = 1.0;
  if (opts.targetTris && total > opts.targetTris) {
    ratio = opts.targetTris / total;
    log(`  decimating ${total} -> about ${opts.targetTris} triangles (ratio ${ratio.toFixed(2)})`);
  }
  const pieces = [];
  const names = new Set();
  const unique = (base) => {
    let n = base, k = 2;
    while (names.has(n.toLowerCase())) { n = `${base}_${k}`; k++; }
    names.add(n.toLowerCase());
    return n;
  };
  for (const { part, kind, mesh } of entries) {
    const byMat = mesh.splitByMaterial();
    for (let [mid, sub] of byMat) {
      const mat = finalMats[mid];
      if (ratio < 1.0 && sub.triCount > 24) sub = decimateTo(sub, Math.max(24, Math.floor(sub.triCount * ratio)));
      let base = sanitize(part.name);
      if (byMat.length > 1) base += '__' + sanitize(mat.name, 'mat');
      const chunks = splitByLimit(sub, opts.maxTrisPerMesh);
      chunks.forEach((ch, ci) => {
        const nm = unique(chunks.length === 1 ? base : `${base}_p${ci + 1}`);
        const m2 = new Mesh(ch.pos, ch.tri, new Int32Array(ch.triCount), ch.nrm, ch.uv);
        pieces.push({ name: nm, part: part.name, kind, mesh: m2, material: mat, wheelSlot: part.wheelSlot || null });
      });
    }
    await tick();
  }
  const wheels = finishWheels(model, opts, pieces, R, s, shift, log, warnings);
  return { pieces, wheels, size, axes };
}

function driveFlags(wheels, drivetrain) {
  const hasRear = wheels.some((w) => !w.front);
  return wheels.map((w) => {
    const steer = (w.front && hasRear) || (w.front && wheels.length <= 2);
    let drive;
    if (drivetrain === 'fwd' && wheels.some((x) => x.front)) drive = w.front;
    else if (drivetrain === 'rwd' && hasRear) drive = !w.front;
    else drive = true;
    return [steer, drive];
  });
}

// ===================================================================== exporters
function srgbPbr(mat) {
  const n = mat.name.toLowerCase();
  if (mat.transparent || n.includes('glass') || n.includes('window')) return [0.0, 0.05];
  if (mat.paint) return [0.3, 0.35];
  if (n.includes('chrome') || n.includes('metal')) return [0.9, 0.25];
  return [0.0, 0.75];
}
// textures: Map key -> { file, png: Uint8Array, alpha }
function writeGlb(pieces, textures, name) {
  const chunks = [];
  let len = 0;
  const views = [], accessors = [];
  const view = (data, target) => {
    const pad = (4 - (len % 4)) % 4;
    if (pad) { chunks.push(new Uint8Array(pad)); len += pad; }
    const v = { buffer: 0, byteOffset: len, byteLength: data.length };
    if (target) v.target = target;
    chunks.push(data); len += data.length;
    views.push(v);
    return views.length - 1;
  };
  const u8 = (ta) => new Uint8Array(ta.buffer, ta.byteOffset, ta.byteLength);
  const accessor = (ta, comp, type, count, target, minmax) => {
    const a = { bufferView: view(u8(ta), target), componentType: comp, count, type };
    if (minmax) {
      const nc = ta.length / count, mn = new Array(nc).fill(Infinity), mx = new Array(nc).fill(-Infinity);
      for (let i = 0; i < count; i++) for (let k = 0; k < nc; k++) { const v = ta[i * nc + k]; if (v < mn[k]) mn[k] = v; if (v > mx[k]) mx[k] = v; }
      a.min = mn; a.max = mx;
    }
    accessors.push(a);
    return accessors.length - 1;
  };
  const gltf = { asset: { version: '2.0', generator: `car2roblox ${APP_VERSION} (browser)` }, scene: 0, scenes: [{ nodes: [] }],
    nodes: [], meshes: [], materials: [], images: [], textures: [],
    samplers: [{ magFilter: 9729, minFilter: 9987, wrapS: 10497, wrapT: 10497 }] };
  const texIndex = new Map(), matIndex = new Map();
  const textureIdx = (key) => {
    if (!textures.has(key)) return null;
    if (!texIndex.has(key)) {
      const t = textures.get(key);
      gltf.images.push({ bufferView: view(t.png), mimeType: 'image/png', name: t.file });
      gltf.textures.push({ sampler: 0, source: gltf.images.length - 1 });
      texIndex.set(key, gltf.textures.length - 1);
    }
    return texIndex.get(key);
  };
  const materialIdx = (m) => {
    const k = materialKey(m);
    if (matIndex.has(k)) return matIndex.get(k);
    const [metal, rough] = srgbPbr(m);
    const pbr = { baseColorFactor: [srgbToLinear(m.color[0]), srgbToLinear(m.color[1]), srgbToLinear(m.color[2]), m.color[3]], metallicFactor: metal, roughnessFactor: rough };
    const gm = { name: m.name, pbrMetallicRoughness: pbr };
    const ti = m.texture ? textureIdx(m.texture) : null;
    if (ti !== null) {
      pbr.baseColorTexture = { index: ti };
      if (textures.get(m.texture).alpha && !m.transparent) { gm.alphaMode = 'MASK'; gm.alphaCutoff = 0.5; }
    }
    if (m.transparent) gm.alphaMode = 'BLEND';
    if (m.doubleSided) gm.doubleSided = true;
    gltf.materials.push(gm);
    matIndex.set(k, gltf.materials.length - 1);
    return gltf.materials.length - 1;
  };
  for (const pc of pieces) {
    const m = pc.mesh;
    const attrs = { POSITION: accessor(m.pos, 5126, 'VEC3', m.vertCount, 34962, true) };
    if (m.nrm) attrs.NORMAL = accessor(m.nrm, 5126, 'VEC3', m.vertCount, 34962);
    if (m.uv) attrs.TEXCOORD_0 = accessor(m.uv, 5126, 'VEC2', m.vertCount, 34962);
    const idx = m.vertCount < 65535
      ? accessor(Uint16Array.from(m.tri), 5123, 'SCALAR', m.tri.length, 34963)
      : accessor(Uint32Array.from(m.tri), 5125, 'SCALAR', m.tri.length, 34963);
    gltf.meshes.push({ name: pc.name, primitives: [{ attributes: attrs, indices: idx, material: materialIdx(pc.material), mode: 4 }] });
    const extras = { kind: pc.kind };
    if (pc.wheelSlot) extras.wheel = pc.wheelSlot;
    gltf.nodes.push({ name: pc.name, mesh: gltf.meshes.length - 1, extras });
    gltf.scenes[0].nodes.push(gltf.nodes.length - 1);
  }
  for (const k of ['images', 'textures', 'materials']) if (!gltf[k].length) delete gltf[k];
  if (!gltf.textures) delete gltf.samplers;
  gltf.bufferViews = views;
  gltf.accessors = accessors;
  const pad = (4 - (len % 4)) % 4;
  if (pad) { chunks.push(new Uint8Array(pad)); len += pad; }
  gltf.buffers = [{ byteLength: len }];
  let js = new TextEncoder().encode(JSON.stringify(gltf));
  const jpad = (4 - (js.length % 4)) % 4;
  if (jpad) { const t = new Uint8Array(js.length + jpad).fill(32); t.set(js); js = t; }
  const total = 12 + 8 + js.length + 8 + len;
  const head = new Uint8Array(12 + 8), d = dv(head);
  d.setUint32(0, 0x46546C67, true); d.setUint32(4, 2, true); d.setUint32(8, total, true);
  d.setUint32(12, js.length, true); d.setUint32(16, 0x4E4F534A, true);
  const binHead = new Uint8Array(8), bd = dv(binHead);
  bd.setUint32(0, len, true); bd.setUint32(4, 0x004E4942, true);
  return concatBytes([head, js, binHead, ...chunks]);
}

function writeObj(piece, texFile) {
  const m = piece.mesh, mat = piece.material;
  const mtlName = sanitize(mat.name, 'material');
  const L = [`# car2roblox ${APP_VERSION}`, `mtllib ${piece.name}.mtl`, `o ${piece.name}`];
  for (let i = 0; i < m.pos.length; i += 3) L.push(`v ${m.pos[i].toFixed(5)} ${m.pos[i + 1].toFixed(5)} ${m.pos[i + 2].toFixed(5)}`);
  if (m.uv) for (let i = 0; i < m.uv.length; i += 2) L.push(`vt ${m.uv[i].toFixed(5)} ${(1.0 - m.uv[i + 1]).toFixed(5)}`);
  if (m.nrm) for (let i = 0; i < m.nrm.length; i += 3) L.push(`vn ${m.nrm[i].toFixed(4)} ${m.nrm[i + 1].toFixed(4)} ${m.nrm[i + 2].toFixed(4)}`);
  L.push(`usemtl ${mtlName}`);
  for (let i = 0; i < m.tri.length; i += 3) {
    const a = m.tri[i] + 1, b = m.tri[i + 1] + 1, c = m.tri[i + 2] + 1;
    if (m.uv && m.nrm) L.push(`f ${a}/${a}/${a} ${b}/${b}/${b} ${c}/${c}/${c}`);
    else if (m.uv) L.push(`f ${a}/${a} ${b}/${b} ${c}/${c}`);
    else if (m.nrm) L.push(`f ${a}//${a} ${b}//${b} ${c}//${c}`);
    else L.push(`f ${a} ${b} ${c}`);
  }
  const [r, g, bl, al] = mat.color;
  const mtl = [`newmtl ${mtlName}`, 'Ka 1.000 1.000 1.000', `Kd ${r.toFixed(4)} ${g.toFixed(4)} ${bl.toFixed(4)}`, 'Ks 0.000 0.000 0.000', `d ${al.toFixed(4)}`, 'illum 2'];
  if (texFile) mtl.push(`map_Kd ${texFile}`);
  return [L.join('\n') + '\n', mtl.join('\n') + '\n'];
}

function toLua(obj, indent = 0) {
  const pad = '\t'.repeat(indent);
  if (obj === null || obj === undefined) return 'nil';
  if (typeof obj === 'boolean') return obj ? 'true' : 'false';
  if (typeof obj === 'number') return String(Math.round(obj * 1e4) / 1e4);
  if (typeof obj === 'string') return '"' + obj.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n') + '"';
  if (Array.isArray(obj)) {
    if (obj.every((x) => typeof x === 'number')) return '{' + obj.map((x) => toLua(x)).join(', ') + '}';
    const inner = obj.map((x) => `${pad}\t${toLua(x, indent + 1)}`).join(',\n');
    return obj.length ? `{\n${inner}\n${pad}}` : '{}';
  }
  const inner = Object.entries(obj).map(([k, v]) => `${pad}\t${/^[A-Za-z_][A-Za-z0-9_]*$/.test(k) ? k : `["${k}"]`} = ${toLua(v, indent + 1)}`);
  return inner.length ? `{\n${inner.join(',\n')}\n${pad}}` : '{}';
}
function makeRigScript(data, name, glbName) {
  const rep = (s, a, b) => s.split(a).join(b);
  let t = RIG_TEMPLATE;
  t = rep(t, '__VERSION__', APP_VERSION);
  t = rep(t, '__NAME__', name);
  t = rep(t, '__GLB__', glbName);
  t = rep(t, '__DATA__', toLua(data));
  t = rep(t, '__DRIVER__', RIG_DRIVER);
  return t;
}

function instructionsText(name, opts, fmts, wheels, size) {
  const L = [`${name} - import into Roblox Studio`, '='.repeat(44), ''];
  if (fmts.has('glb')) {
    L.push('OPTION A (recommended): one file', `  1. In Studio choose File > Import and pick ${name}.glb.`,
      "  2. In the import window set Scale Unit = Studs (the default) and tick 'Import Only As Model', then import.",
      '     Textures travel inside the .glb.', '');
  }
  if (fmts.has('obj')) {
    L.push('OPTION B: separate OBJ pieces' + (fmts.has('glb') ? ' (fallback)' : ''),
      "  1. File > Import, then select every .obj in the 'obj' folder at once (keep the .mtl and .png files next to them).",
      "  2. Scale Unit = Studs, 'Import Only As Model' ticked.",
      "  Roblox's docs do not say whether the .mtl textures are applied for OBJ. If the parts arrive untextured, upload the PNGs from the 'textures' folder as images and paste their ids into each MeshPart's TextureID.", '');
  }
  if (opts.rig) {
    L.push('BUILD THE CAR', '  1. Select the imported Model in the Explorer.',
      `  2. Open ${name}_setup.lua, copy everything and paste it into the Command Bar (View tab > Command Bar), press Enter.`,
      '  3. Press Play, walk up to the car and press E at a seat to get in ("Drive" for the driver\'s seat, "Sit" for any passenger seats; the car spawns a couple of studs above the ground). The body does not collide, so you can walk straight through it to a seat.',
      '  Car drives backwards -> set DriveSign to 1.  Steering inverted -> SteerSign to 1.',
      '  Both values (and top speed) are also attributes on the finished Model.', '');
  }
  L.push(`Car size: ${size[0].toFixed(1)} wide, ${size[1].toFixed(1)} tall, ${size[2].toFixed(1)} long studs; ${wheels.length} wheel(s) detected.`,
    "Front of the car points to -Z (Roblox's forward). If the model imported facing the other way, use 'Flip front/back' and convert again.",
    'manifest.json lists every piece (position, size, material, texture).', '',
    "Only convert models you made or are licensed to use: GTA and BeamNG assets belong to their creators, and Roblox's rules apply to anything you upload.");
  return L.join('\n') + '\n';
}

// ===================================================================== full conversion -> list of output files
// built = result of buildPieces; returns { name, files:[{name,data,store}], manifest, lua, warnings, stats }
async function makeOutputs(model, built, opts, log, warnings, light = false) {
  const { pieces, wheels, size } = built;
  const name = sanitize(opts.name || model.name, 'Car');
  const files = [];
  const add = (rel, data, store) => files.push({ name: `${name}/${rel}`, data: typeof data === 'string' ? new TextEncoder().encode(data) : data, store: !!store });
  const texFiles = new Map();
  const usedNames = new Set();
  for (const pc of pieces) {
    const key = pc.material.texture;
    if (key && !texFiles.has(key) && model.textures.has(key)) {
      const tex = model.textures.get(key);
      const base = sanitize(tex.name, 'texture');
      let fn = base, k = 2;
      while (usedNames.has(fn.toLowerCase())) { fn = `${base}_${k}`; k++; }
      usedNames.add(fn.toLowerCase());
      const img = light ? null : fitTexture(tex.image, opts.maxTexture);
      texFiles.set(key, { file: fn + '.png', png: light ? null : await encodePng(img), alpha: tex.hasAlpha() });
      if (!light) await tick();
    }
  }
  if (!light) for (const t of texFiles.values()) add(`textures/${t.file}`, t.png, true);
  const fmts = new Set(opts.formats.map((f) => f.toLowerCase()));
  const glbName = `${name}.glb`;
  if (!light && fmts.has('glb')) add(glbName, writeGlb(pieces, texFiles, name));
  if (!light && fmts.has('obj')) {
    for (const pc of pieces) {
      const tf = pc.material.texture ? texFiles.get(pc.material.texture) : null;
      const [obj, mtl] = writeObj(pc, tf ? tf.file : null);
      add(`obj/${pc.name}.obj`, obj); add(`obj/${pc.name}.mtl`, mtl);
    }
    for (const t of texFiles.values()) add(`obj/${t.file}`, t.png, true);
  }
  const nonWheel = pieces.filter((p) => p.kind !== 'wheel');
  const nw = nonWheel.length ? nonWheel : pieces;
  const bodyLike = nw.filter((p) => ['body', 'light', 'other'].includes(p.kind));
  const bl = bodyLike.length ? bodyLike : nw;
  const { lo: cLo0, hi: cHi } = unionBounds(bl.map((p) => p.mesh));
  const avgR = wheels.length ? wheels.reduce((a, w) => a + w.radius, 0) / wheels.length : 0;
  const cLo = cLo0.slice();
  cLo[1] = Math.max(cLo[1], 0.6 * avgR);
  const cSize = [0, 1, 2].map((k) => (cHi[k] - cLo[k]) * 0.96);
  const cCenter = [0, 1, 2].map((k) => (cHi[k] + cLo[k]) / 2);
  const vol = (p) => pieceBox(p).size[0] * pieceBox(p).size[1] * pieceBox(p).size[2];
  const bodyPiece = bl.reduce((best, p) => (vol(p) > vol(best) ? p : best), bl[0]);
  const seat = [cCenter[0] - 0.22 * size[0], cLo[1] + 0.38 * (cHi[1] - cLo[1]), cCenter[2] + 0.06 * size[2]];
  // a front passenger seat mirrors the driver's; rear seats are added only when there is clearly
  // enough car behind the front seats for them (a rough guess, not a real interior layout)
  const passengerSeats = [[2 * cCenter[0] - seat[0], seat[1], seat[2]]];
  const rearRoom = cHi[2] - seat[2];
  if (rearRoom > Math.max(0.30 * size[2], 3.5)) {
    const rearZ = seat[2] + 0.55 * rearRoom;
    passengerSeats.push([seat[0], seat[1], rearZ], [2 * cCenter[0] - seat[0], seat[1], rearZ]);
  }
  const flags = driveFlags(wheels, opts.drivetrain);
  const r4f = (x) => Math.round(x * 1e4) / 1e4;
  const manifest = {
    generator: `car2roblox ${APP_VERSION} (browser)`, name, source: model.source, units: 'studs',
    size: { width: size[0], height: size[1], length: size[2] },
    triangles: pieces.reduce((a, p) => a + p.mesh.triCount, 0), max_triangles_per_mesh: opts.maxTrisPerMesh,
    body: bodyPiece.name,
    pieces: pieces.map((p) => ({
      name: p.name, part: p.part, kind: p.kind, material: p.material.name, triangles: p.mesh.triCount,
      center: pieceBox(p).center.map(r4f), size: pieceBox(p).size.map(r4f), color: p.material.color.map(r4f),
      paint: p.material.paint, transparent: p.material.transparent,
      texture: texFiles.has(p.material.texture) ? 'textures/' + texFiles.get(p.material.texture).file : null, wheel: p.wheelSlot })),
    wheels: wheels.map((w, i) => ({ slot: w.slot, hub: w.hub.map(r4f), radius: r4f(w.radius), width: r4f(w.width), pieces: w.pieces,
      primary: w.primary, steer: flags[i][0], drive: flags[i][1] })),
    seat: seat.map(r4f), passenger_seats: passengerSeats.map((p) => p.map(r4f)),
    collider: { center: cCenter.map(r4f), size: cSize.map(r4f) }, warnings,
  };
  add('manifest.json', JSON.stringify(manifest, null, 2));
  let lua = null;
  if (opts.rig) {
    // km/h -> studs per second (a stud is 0.28 m)
    const topSpeed = opts.topSpeed && opts.topSpeed > 0 ? Math.round(opts.topSpeed / 3.6 / 0.28 * 10) / 10 : null;
    if (!wheels.length) warnings.push("No wheels were detected, so the rig script builds a body + seat only. Wheel parts need 'wheel', 'tire' or 'rim' in their names.");
    const rigWheels = wheels.map((w, i) => ({ slot: w.slot, hub: w.hub.map(r4f), radius: r4f(w.radius), width: r4f(w.width), pieces: w.pieces, primary: w.primary, steer: flags[i][0], drive: flags[i][1] }));
    const achassisReal = !!opts.achassisReal && fitsAchassis(rigWheels);
    if (opts.achassisReal && !achassisReal) {
      warnings.push("Real A-Chassis needs exactly one front axle and one rear axle (a left and right wheel on each); this car's wheel layout doesn't match that, so the simple drivable rig was used instead.");
    }
    let achassisExtra = {};
    if (achassisReal) {
      const sizeWhl = { width: size[0], height: size[1], length: size[2] };
      achassisExtra = { achassis: {
        tune: achassisBuildTuneSource(sizeWhl, rigWheels, opts.drivetrain),
        initialize: ACHASSIS_SOURCES.initialize, readme: ACHASSIS_SOURCES.readme,
        units: ACHASSIS_SOURCES.units, drive: ACHASSIS_SOURCES.drive,
        values: ACHASSIS_VALUES_SPEC,
      } };
    }
    lua = makeRigScript({
      name, drivetrain: opts.drivetrain, body: bodyPiece.name,
      pieces: pieces.map((p) => ({ name: p.name, kind: p.kind, center: pieceBox(p).center.map(r4f), size: pieceBox(p).size.map(r4f), paint: p.material.paint })),
      wheels: rigWheels,
      collider: manifest.collider, seat: manifest.seat, passenger_seats: manifest.passenger_seats,
      advanced_chassis: !!opts.advancedChassis,
      achassis_real: achassisReal,
      ...achassisExtra,
      ...(topSpeed ? { top_speed: topSpeed } : {}),
    }, name, fmts.has('glb') ? glbName : 'the .obj files');
    add(`${name}_setup.lua`, lua);
  }
  add('IMPORT_INSTRUCTIONS.txt', instructionsText(name, opts, fmts, wheels, size));
  const biggest = Math.max(...pieces.map((p) => p.mesh.triCount));
  if (!light) log(`Done: ${pieces.length} pieces, ${manifest.triangles} triangles (largest piece ${biggest}), ${wheels.length} wheels`);
  return { name, files, manifest, lua, warnings, flags, bodyPiece, seat, collider: manifest.collider };
}

async function convertModel(model, opts, log) {
  const warnings = model.notes.slice();
  log(`Converting '${sanitize(opts.name || model.name, 'Car')}' (${model.source}), ${modelTriCount(model)} source triangles`);
  const built = await buildPieces(model, opts, log, warnings);
  const out = await makeOutputs(model, built, opts, log, warnings);
  return { built, out };
}

// ===================================================================== public API (used by the UI and by the Node tests)
const API = {
  ConversionError, Mesh, Store, BytesSource, BlobSource, openZip, storeFromZip, buildZip, decodeImage, encodePng,
  parseDff, parseTxd, parseCollada, jbeamParse, xmlParse, decodeDxtBlocks, decodeDds, decodeTga, decodePng,
  detectSources, loadModel, beamngInfo, defaultOptions, buildPieces, makeOutputs, convertModel, driveFlags,
  stripLinks, isAdultName, riskyFile, scanFiles, scrubModel, resizeArea, fitTexture, sanitize, inflateRaw, deflateRaw,
  crc32, tokens, APP_VERSION, STUDS_PER_METER, pieceBox,
};
