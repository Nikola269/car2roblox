'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
const C = require('../dist/core.node.js');
const FX = process.argv[2];
const quiet = () => {};
let failed = 0, passed = 0;
async function t(name, fn) {
  try { await fn(); passed++; console.log('  ok  ', name); }
  catch (e) { failed++; console.log('  FAIL', name, '\n      ', (e && e.stack || e).toString().split('\n').slice(0, 4).join('\n       ')); }
}
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} vs ${b}`);
function dirStore(dir) {
  const ents = [];
  (function walk(d, rel) {
    for (const f of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, f.name), r = rel ? rel + '/' + f.name : f.name;
      if (f.isDirectory()) walk(p, r); else ents.push({ path: r, read: async () => new Uint8Array(fs.readFileSync(p)) });
    }
  })(dir, '');
  return new C.Store(ents);
}
async function convertDir(dir, opts, zipName) {
  let store;
  if (zipName) store = await C.storeFromZip(new C.BytesSource(new Uint8Array(fs.readFileSync(path.join(dir, zipName)))));
  else store = dirStore(dir);
  const srcs = C.detectSources(store, zipName);
  assert.ok(srcs.length, 'no source detected');
  const o = Object.assign(C.defaultOptions(), opts);
  const model = await C.loadModel(store, srcs[0], zipName || srcs[0].path, o, quiet);
  const { built, out } = await C.convertModel(model, o, quiet);
  return { model, built, out, srcs };
}
function compareManifest(js, py, label) {
  assert.deepStrictEqual(js.pieces.map((p) => p.name).sort(), py.pieces.map((p) => p.name).sort(), label + ' piece names');
  const pj = Object.fromEntries(js.pieces.map((p) => [p.name, p])), pp = Object.fromEntries(py.pieces.map((p) => [p.name, p]));
  for (const n of Object.keys(pp)) {
    const a = pj[n], b = pp[n];
    assert.strictEqual(a.triangles, b.triangles, `${label} ${n} triangles`);
    assert.strictEqual(a.kind, b.kind, `${label} ${n} kind`);
    assert.strictEqual(a.wheel, b.wheel, `${label} ${n} wheel`);
    assert.strictEqual(a.paint, b.paint, `${label} ${n} paint`);
    assert.strictEqual(a.transparent, b.transparent, `${label} ${n} transparent`);
    assert.strictEqual(a.texture, b.texture, `${label} ${n} texture`);
    for (let k = 0; k < 3; k++) { near(a.center[k], b.center[k], 2e-3, `${label} ${n} center`); near(a.size[k], b.size[k], 2e-3, `${label} ${n} size`); }
    for (let k = 0; k < 4; k++) near(a.color[k], b.color[k], 2e-3, `${label} ${n} color`);
  }
  assert.strictEqual(js.body, py.body, label + ' body');
  assert.strictEqual(js.triangles, py.triangles, label + ' tri total');
  for (const k of ['width', 'height', 'length']) near(js.size[k], py.size[k], 2e-3, `${label} size.${k}`);
  assert.deepStrictEqual(js.wheels.map((w) => [w.slot, w.steer, w.drive, w.primary]), py.wheels.map((w) => [w.slot, w.steer, w.drive, w.primary]), label + ' wheels');
  js.wheels.forEach((w, i) => { for (let k = 0; k < 3; k++) near(w.hub[k], py.wheels[i].hub[k], 2e-3, label + ' hub'); near(w.radius, py.wheels[i].radius, 2e-3, label + ' radius'); });
  for (let k = 0; k < 3; k++) { near(js.seat[k], py.seat[k], 3e-3, label + ' seat'); near(js.collider.center[k], py.collider.center[k], 3e-3, label + ' collider c'); near(js.collider.size[k], py.collider.size[k], 3e-3, label + ' collider s'); }
}
const refManifest = (n) => JSON.parse(fs.readFileSync(path.join(FX, 'ref', n, 'manifest.json'), 'utf8'));

(async () => {
  console.log('units');
  await t('png roundtrip (rgba + rgb)', async () => {
    const w = 13, h = 7, d = new Uint8Array(w * h * 4);
    for (let i = 0; i < d.length; i++) d[i] = (i * 37 + (i >> 3)) & 255;
    const png = await C.encodePng({ w, h, data: d });
    const back = await C.decodePng(png);
    assert.deepStrictEqual([...back.data], [...d]);
    for (let i = 3; i < d.length; i += 4) d[i] = 255;
    const back2 = await C.decodePng(await C.encodePng({ w, h, data: d }));
    assert.deepStrictEqual([...back2.data], [...d]);
  });
  await t('zip build -> open -> read (stored + deflated)', async () => {
    const big = new Uint8Array(5000).map((_, i) => i % 7);
    const blob = await C.buildZip([{ name: 'a/b.txt', data: new TextEncoder().encode('hello') }, { name: 'c.bin', data: big }, { name: 'd.png', data: big, store: true }]);
    const u8 = new Uint8Array(await blob.arrayBuffer());
    const z = await C.openZip(new C.BytesSource(u8));
    assert.deepStrictEqual(z.entries.map((e) => e.name), ['a/b.txt', 'c.bin', 'd.png']);
    assert.strictEqual(z.entries[1].method, 8);
    assert.strictEqual(new TextDecoder().decode(await z.read(z.entries[0])), 'hello');
    assert.deepStrictEqual([...await z.read(z.entries[1])], [...big]);
    assert.deepStrictEqual([...await z.read(z.entries[2])], [...big]);
  });
  await t('real zip (python-made) opens', async () => {
    const z = await C.openZip(new C.BytesSource(new Uint8Array(fs.readFileSync(path.join(FX, 'beam', 'mod.zip')))));
    assert.ok(z.entries.some((e) => e.name === 'vehicles/testcar/testcar.dae'));
  });
  await t('jbeam lenient parser', () => {
    const v = C.jbeamParse('{ // c\n "a": [1, 2 3,], /* x */ "b": {"c": true, "d": null, }, "e": "x\\n" "f": -1.5e2 }');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(v)), { a: [1, 2, 3], b: { c: true, d: null }, e: 'x\n', f: -150 });
  });
  await t('xml parser', () => {
    const r = C.xmlParse('<?xml version="1.0"?><a x="1&amp;2"><!-- c --><b>hi &lt;there&gt;</b><c/><d><![CDATA[z<y]]></d></a>');
    assert.strictEqual(r.get('x'), '1&2');
    assert.strictEqual(r.kid('b').text, 'hi <there>');
    assert.ok(r.kid('c'));
    assert.strictEqual(r.kid('d').text, 'z<y');
  });
  await t('dxt1 block decode', () => {
    // one block: c0 = pure red (565 0xF800), c1 = 0, indices 0,1,2,3 on the first row
    const b = new Uint8Array([0x00, 0xF8, 0x00, 0x00, 0b11100100, 0, 0, 0]);
    const im = C.decodeDxtBlocks(4, 4, 'DXT1', b);
    assert.deepStrictEqual([...im.data.slice(0, 4)], [255, 0, 0, 255]);
    assert.deepStrictEqual([...im.data.slice(4, 8)], [0, 0, 0, 255]);
    assert.deepStrictEqual([...im.data.slice(8, 12)], [170, 0, 0, 255]);
    assert.deepStrictEqual([...im.data.slice(12, 16)], [85, 0, 0, 255]);
  });
  await t('resize keeps colour and averages', () => {
    const d = new Uint8Array(4 * 4 * 4);
    for (let i = 0; i < 16; i++) { d[i * 4] = (i % 4 < 2) ? 200 : 100; d[i * 4 + 1] = 50; d[i * 4 + 2] = 0; d[i * 4 + 3] = 255; }
    const r = C.resizeArea({ w: 4, h: 4, data: d }, 2, 2);
    assert.deepStrictEqual([...r.data.slice(0, 4)], [200, 50, 0, 255]);
    assert.deepStrictEqual([...r.data.slice(4, 8)], [100, 50, 0, 255]);
  });
  const linkCases = [['wheel www.evil.com', 'wheel'], ['http://bit.ly/abc door', 'door'], ['body_https://example.org/x?y=1', 'body'], ['bumper.com', ''],
    ['javascript:alert(1) hood', 'hood'], ['downloaded from gta-mods.ru/cars', 'downloaded from'], ['door.top', 'door.top'], ['hood.001', 'hood.001'], ['wheel_lf_dummy', 'wheel_lf_dummy']];
  await t('safety: strip links (same vectors as python)', () => { for (const [a, b] of linkCases) assert.strictEqual(C.stripLinks(a)[0], b, a); });
  await t('safety: adult names', () => {
    for (const n of ['nude_decal', 'NSFW-sticker', 'r18_skin', 'skin 18+', 'xxx', 'Hentai poster', 'sexy_girl', 'adult_only', 'x-rated', 'onlyfans_logo']) assert.ok(C.isAdultName(n), n);
    for (const n of ['Essex', 'cockpit', 'Scunthorpe', 'bodyDecal', 'passenger_seat', 'wheel_lf', 'sextant', 'grass_mat', 'windshield', 'adulthood', 'r180', 'door_18']) assert.ok(!C.isAdultName(n), n);
  });
  await t('safety: risky files', () => {
    for (const p of ['mods/readme.url', 'setup.EXE', 'a/b/install.bat', 'skin.png.exe', 'icon.exe.png', 'ui/app.js', 'page.html', 'link.lnk']) assert.ok(C.riskyFile(p), p);
    for (const p of ['vehicles/car/car.dae', 'textures/body_d.dds', 'wheel.png', 'readme.txt', 'car.materials.json']) assert.ok(!C.riskyFile(p), p);
  });

  console.log('pipeline vs python');
  for (const [label, dir, opts, zip] of [
    ['sa', 'sa', { name: 'sa' }], ['sa_bin', 'sa_bin', { name: 'sa_bin' }],
    ['beam', 'beam', { name: 'beam' }, 'mod.zip'], ['beam_sport', 'beam', { name: 'beam_sport', config: 'sport', paint: '#c0392b' }, 'mod.zip'],
    ['obj_x', 'obj_x', { name: 'obj_x' }], ['obj_z', 'obj_z', { name: 'obj_z', flipForward: true }], ['glb', 'glb', { name: 'glb' }],
    ['wobj_named', 'wobj_named', { name: 'wobj_named' }], ['wobj_unnamed', 'wobj_unnamed', { name: 'wobj_unnamed' }],
    ['wobj_fused', 'wobj_fused', { name: 'wobj_fused' }], ['wobj_dup', 'wobj_dup', { name: 'wobj_dup' }],
    ['wobj_none', 'wobj_none', { name: 'wobj_none' }], ['wobj_none_notires', 'wobj_none', { name: 'wobj_none_notires', addTires: false }],
    ['bflex', 'bflex', { name: 'bflex' }, 'mod.zip'], ['bflex_nomesh', 'bflex_nomesh', { name: 'bflex_nomesh' }, 'mod.zip'],
    ['bflex_empty', 'bflex_empty', { name: 'bflex_empty' }, 'mod.zip'], ['bflex_dup', 'bflex_dup', { name: 'bflex_dup' }, 'mod.zip'],
    ['sa_nowheel', 'sa_nowheel', { name: 'sa_nowheel' }],
    ['wobj_template', 'wobj_template', { name: 'wobj_template' }], ['wobj_stacked', 'wobj_stacked', { name: 'wobj_stacked' }],
    ['wobj_partial', 'wobj_partial', { name: 'wobj_partial' }], ['wobj_spare', 'wobj_spare', { name: 'wobj_spare' }],
    ['wobj_template_notires', 'wobj_template', { name: 'wobj_template_notires', addTires: false }],
    ['wobj_named_new', 'wobj_named', { name: 'wobj_named_new', newTires: true }],
    ['wobj_partial_new', 'wobj_partial', { name: 'wobj_partial_new', newTires: true }],
    ['bflex_var', 'bflex_var', { name: 'bflex_var' }, 'mod.zip'], ['bflex_anon', 'bflex_anon', { name: 'bflex_anon' }, 'mod.zip'],
    ['bflex_frontonly', 'bflex_frontonly', { name: 'bflex_frontonly' }, 'mod.zip'],
    ['bflex_caliper', 'bflex_caliper', { name: 'bflex_caliper' }, 'mod.zip'],
  ]) {
    await t(`${label}: manifest matches python`, async () => {
      const r = await convertDir(path.join(FX, dir), opts, zip);
      compareManifest(r.out.manifest, refManifest(label), label);
    });
  }
  await t('sa: outputs are complete and the GLB reads back', async () => {
    const r = await convertDir(path.join(FX, 'sa'), { name: 'sa' });
    const names = r.out.files.map((f) => f.name);
    for (const n of ['sa/sa.glb', 'sa/manifest.json', 'sa/sa_setup.lua', 'sa/IMPORT_INSTRUCTIONS.txt']) assert.ok(names.includes(n), n);
    assert.ok(names.some((n) => n.startsWith('sa/obj/') && n.endsWith('.obj')));
    const glb = r.out.files.find((f) => f.name === 'sa/sa.glb').data;
    const m2 = await C.loadModel(new C.Store([{ path: 'x.glb', read: async () => glb }]), { kind: 'generic', path: 'x.glb' }, 'x.glb', C.defaultOptions(), quiet);
    assert.strictEqual(m2.parts.length, r.built.pieces.length);
    assert.strictEqual(m2.parts.reduce((a, p) => a + p.mesh.triCount, 0), r.out.manifest.triangles);
  });
  await t('lua script is filled in (no placeholders left)', async () => {
    const r = await convertDir(path.join(FX, 'sa'), { name: 'sa' });
    assert.ok(!/__[A-Z]+__/.test(r.out.lua), 'placeholder left');
    assert.ok(r.out.lua.includes('HingeConstraint') && r.out.lua.includes('sa'));
  });
  for (const [label, dir, opts] of [['sa', 'sa', { name: 'sa' }], ['sa_speed', 'sa', { name: 'sa_speed', topSpeed: 120 }], ['wobj_none', 'wobj_none', { name: 'wobj_none' }]]) {
  await t(`lua script equals the python one (numbers normalised): ${label}`, async () => {
    const py = fs.readFileSync(path.join(FX, 'ref', label, `${label}_setup.lua`), 'utf8');
    const r = await convertDir(path.join(FX, dir), opts);
    const norm = (s) => s.replace(/-?\d+\.\d+(e-?\d+)?|-?\d+/g, (m) => String(Math.round(parseFloat(m) * 1000) / 1000)).replace(/generator.*|car2roblox \d.*$/gm, '');
    const a = norm(r.out.lua).split('\n'), b = norm(py).split('\n');
    assert.strictEqual(a.length, b.length, `line count ${a.length} vs ${b.length}`);
    for (let i = 0; i < a.length; i++) assert.strictEqual(a[i], b[i], `line ${i}`);
  });
  }

  console.log('safety through the pipeline');
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'c2r-'));
  await t('adult part deleted, link removed (obj)', async () => {
    const src = fs.readFileSync(path.join(FX, 'obj_z', 'car.obj'), 'utf8').replace('o body', 'o body_www.evil.com').replace('o roof', 'o nude_sticker');
    fs.writeFileSync(path.join(tmp, 'car.obj'), src);
    fs.copyFileSync(path.join(FX, 'obj_z', 'car.mtl'), path.join(tmp, 'car.mtl'));
    const r = await convertDir(tmp, { name: 'x' });
    const names = r.built.pieces.map((p) => p.name);
    assert.ok(names.length && !names.some((n) => /nude|evil|www/i.test(n)), names.join());
    const warn = r.out.warnings.join('\n');
    assert.ok(/deleted a part \(adult-content name\)/.test(warn) && !/nude_sticker/.test(warn) && /removed a link/.test(warn), warn);
    for (const f of r.out.files) if (/\.(json|lua|txt|obj|mtl)$/.test(f.name)) assert.ok(!/evil\.com/i.test(Buffer.from(f.data).toString('utf8')), f.name);
  });
  await t('risky files in a BeamNG zip are reported', async () => {
    const z0 = await C.openZip(new C.BytesSource(new Uint8Array(fs.readFileSync(path.join(FX, 'beam', 'mod.zip')))));
    const files = [];
    for (const e of z0.entries) files.push({ name: e.name, data: await z0.read(e) });
    files.push({ name: 'readme.url', data: new TextEncoder().encode('[InternetShortcut]') }, { name: 'vehicles/testcar/setup.exe', data: new Uint8Array([77, 90]) });
    const u8 = new Uint8Array(await (await C.buildZip(files)).arrayBuffer());
    const store = await C.storeFromZip(new C.BytesSource(u8));
    const o = C.defaultOptions();
    const m = await C.loadModel(store, { kind: 'beamng', label: 'mod.zip' }, 'mod.zip', o, quiet);
    assert.ok(m.notes.some((n) => /ignored 2 risky/.test(n)), m.notes.join('|'));
  });
  await t('everything adult -> error', async () => {
    const d2 = fs.mkdtempSync(path.join(require('os').tmpdir(), 'c2r-'));
    const src = fs.readFileSync(path.join(FX, 'obj_z', 'car.obj'), 'utf8').replace('o body', 'o nude_a').replace('o roof', 'o porn_b');
    fs.writeFileSync(path.join(d2, 'car.obj'), src);
    await assert.rejects(convertDir(d2, {}), /safety filter/);
  });

  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
