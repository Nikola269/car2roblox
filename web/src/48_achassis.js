// The real lisphm/A-Chassis (MPL-2.0) kit, mirrored from car2roblox/achassis.py. The actual
// script texts and the stock Tune module live in 47_achassis_data.js (generated from the same
// vendored files car2roblox/achassis.py reads); this file ports its derivation/substitution logic
// so the browser build produces byte-identical output to the Python app for the same car.

const ACHASSIS_REQUIRED_SLOTS = new Set(['FL', 'FR', 'RL', 'RR']);

function fitsAchassis(wheels) {
  const slots = new Set(wheels.map((w) => w.slot));
  return slots.size === 4 && [...slots].every((s) => ACHASSIS_REQUIRED_SLOTS.has(s))
    && [...ACHASSIS_REQUIRED_SLOTS].every((s) => slots.has(s));
}

function achassisDeriveTuneOverrides(size, wheels, drivetrain) {
  const width = size.width, height = size.height, length = size.length;
  const m = 0.28; // studs -> metres
  const fronts = wheels.filter((w) => w.slot === 'FL' || w.slot === 'FR');
  const rears = wheels.filter((w) => w.slot === 'RL' || w.slot === 'RR');
  const avgRadius = wheels.reduce((a, w) => a + w.radius, 0) / Math.max(1, wheels.length);

  const refVolume = 1.7 * 1.5 * 4.3;
  const volume = Math.max(0.3, width * m) * Math.max(0.3, height * m) * Math.max(0.3, length * m);
  let weightLb = 1440.0 * (volume / refVolume) * 1.8;
  weightLb = Math.max(900.0, Math.min(weightLb, 9000.0));

  const wbW = Math.max(0.25 * width, 2.0) * 280.0;
  const wbH = Math.max(0.18 * height, 1.5) * 280.0;
  const wbL = Math.max(0.35 * length, 3.0) * 280.0;

  const wheelKg = (group, stock) => {
    if (!group.length) return stock;
    const r = group.reduce((a, w) => a + w.radius, 0) / group.length * m;
    const w_ = group.reduce((a, w) => a + w.width, 0) / group.length * m;
    const scale = (r / 0.33) * (w_ / 0.20);
    return Math.max(4.0, Math.min(stock * scale, 90.0));
  };

  const axleSize = Math.max(0.6, Math.min(1.5 * (avgRadius * m / 0.33), 3.0));

  const overrides = {
    Weight: weightLb.toFixed(0),
    WeightBSize: `{${wbW.toFixed(0)}, ${wbH.toFixed(0)}, ${wbL.toFixed(0)}}`,
    FWheelWeight: wheelKg(fronts, 22.0).toFixed(1),
    RWheelWeight: wheelKg(rears, 23.0).toFixed(1),
    AxleSize: axleSize.toFixed(2),
    Mobile: 'false',
  };
  const dt = (drivetrain || '').trim().toUpperCase();
  if (dt === 'FWD' || dt === 'RWD' || dt === 'AWD') overrides.Config = `"${dt}"`;

  if (fronts.length && rears.length) {
    const fz = fronts.reduce((a, w) => a + w.hub[2], 0) / fronts.length;
    const rz = rears.reduce((a, w) => a + w.hub[2], 0) / rears.length;
    const wheelbase = Math.abs(fz - rz);
    if (wheelbase > 0.05) {
      const cz = wheels.reduce((a, w) => a + w.hub[2], 0) / wheels.length;
      let dist = fz !== rz ? (cz - rz) / (fz - rz) : 0.5;
      dist = Math.max(0.30, Math.min(dist, 0.70)) * 100.0;
      overrides.WeightDist = dist.toFixed(0);
    }
  }
  return overrides;
}

function achassisBuildTuneSource(size, wheels, drivetrain) {
  let text = ACHASSIS_TUNE_STOCK;
  const overrides = achassisDeriveTuneOverrides(size, wheels, drivetrain);
  const note = "  -- car2roblox: derived from your model's measured size/wheels";
  if (overrides.WeightBSize) {
    const re = /Tune\.WeightBSize\s*=\s*\{[\s\S]*?\}/;
    const replaced = text.replace(re, `Tune.WeightBSize = ${overrides.WeightBSize}${note}`);
    if (replaced === text) throw new Error('A-Chassis Tune.luau has no Tune.WeightBSize to override');
    text = replaced;
  }
  for (const [field, value] of Object.entries(overrides)) {
    if (field === 'WeightBSize') continue;
    const re = new RegExp(`^(\\tTune\\.${field}\\s*=\\s*)[^\\n]*(--.*)?$`, 'm');
    let found = false;
    const replaced = text.replace(re, (whole, g1) => { found = true; return g1 + value + note; });
    if (!found) throw new Error(`A-Chassis Tune.luau has no 'Tune.${field} = ...' line to override`);
    text = replaced;
  }
  return text;
}
