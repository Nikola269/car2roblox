// ===================================================================== page UI (only runs inside the page)
(function startUI() {
  const $ = (id) => document.getElementById(id);
  if (!$('drop') || !$('gl')) return;

  $('ver').textContent = 'v' + APP_VERSION;
  if ($('verFoot')) $('verFoot').textContent = 'Car2Roblox v' + APP_VERSION;

  const S = {
    store: null, zipName: null, sources: [], srcIdx: 0, model: null, built: null, plan: null, opts: null,
    warnBuild: [], extraSafety: [], loadLog: [], buildLog: [], paintTouched: [false, false, false],
    timer: 0, building: null, again: false, ok: false, newModel: true, imgCache: new Map(),
  };
  const logAll = () => { $('log').textContent = [...S.loadLog, ...S.buildLog].slice(-400).join('\n'); };
  const loadLog = (m) => { S.loadLog.push(m); };
  const buildLog = (m) => { S.buildLog.push(m); };

  // ---------- small helpers
  let toastTimer = 0;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }
  function setStatus(msg, kind) {
    const s = $('status');
    s.textContent = msg;
    s.className = 'status' + (kind ? ' ' + kind : '');
  }
  function showError(e) {
    console.error(e);
    const msg = e && e.name === 'ConversionError' ? e.message : `Something went wrong: ${e && e.message ? e.message : e}`;
    setStatus(msg, 'err');
    S.ok = false;
    $('dlBtn').disabled = true; $('copyBtn').disabled = true;
  }
  const fmt = (n) => (Math.round(n * 10) / 10).toString();

  // ---------- viewer
  let viewer = null;
  const kmh = $('kmh');
  let lastKmh = -1;
  try {
    viewer = new Viewer($('gl'), {
      onHud(speed) {
        const v = Math.round(speed * 0.28 * 3.6);
        if (v !== lastKmh) { lastKmh = v; kmh.textContent = String(v); }
      },
    });
    if (!viewer.ok) viewer = null;
  } catch (e) { console.error(e); viewer = null; }
  if (!viewer) {
    $('nogl').hidden = false;
    for (const id of ['driveBtn', 'spinBtn', 'viewBtn', 'saveBtn']) $(id).disabled = true;
    for (const r of document.querySelectorAll('input[name=vMode]')) r.disabled = true;
  }
  const driveBtn = $('driveBtn'), spinBtn = $('spinBtn'), viewBtn = $('viewBtn');
  function setDriving(on) {
    if (!viewer) return;
    viewer.setDrive(on);
    driveBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    driveBtn.textContent = on ? 'Stop driving' : 'Drive';
    viewBtn.textContent = on ? 'Reset car' : 'Reset view';
    $('hud').classList.toggle('on', on);
    $('touch').classList.toggle('on', on);
    spinBtn.disabled = on;
    if (on) { viewer.autoRotate = false; spinBtn.setAttribute('aria-pressed', 'false'); $('gl').focus({ preventScroll: true }); }
    lastKmh = -1; kmh.textContent = '0';
    updateTag();
  }
  driveBtn.addEventListener('click', () => setDriving(driveBtn.getAttribute('aria-pressed') !== 'true'));
  spinBtn.addEventListener('click', () => {
    if (!viewer) return;
    const on = spinBtn.getAttribute('aria-pressed') !== 'true';
    viewer.autoRotate = on; viewer.dirty = true;
    spinBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  viewBtn.addEventListener('click', () => {
    if (!viewer) return;
    if (viewer.drive) viewer.resetCar(); else viewer.resetCamera();
    viewer.dirty = true;
  });
  // ---- view mode (shaded / wireframe / pieces) and the little label in the corner of the picture
  let noWheelsHint = false;
  function updateTag() {
    const tag = $('tag');
    tag.textContent = '';
    const mode = viewer ? viewer.mode : 'shaded';
    if (noWheelsHint && !(viewer && viewer.drive)) { tag.textContent = 'No wheels found, so Drive is off'; return; }
    if (viewer && viewer.drive) { tag.textContent = 'Test drive, not Roblox physics'; return; }
    if (mode === 'pieces') {
      for (const [kind, hex] of Object.entries(KIND_COLORS)) {
        const sw = document.createElement('span');
        sw.style.cssText = `display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 8px;vertical-align:-1px;background:${hex}`;
        tag.append(sw, kind);
      }
      tag.firstChild.style.marginLeft = '0';
      return;
    }
    tag.textContent = mode === 'wire' ? 'Wireframe. Arrow = front' : 'Arrow = front';
  }
  for (const r of document.querySelectorAll('input[name=vMode]')) {
    r.addEventListener('change', () => { if (r.checked && viewer) { viewer.setMode(r.value); updateTag(); } });
  }
  async function savePicture() {
    if (!viewer || !viewer.scene) return;
    try {
      const w = viewer.canvas.clientWidth || 960, h = viewer.canvas.clientHeight || 600;
      const k = Math.max(1, Math.min(2, 1600 / Math.max(w, 1)));
      const png = await viewer.snapshot({ width: Math.round(w * k), height: Math.round(h * k) });
      if (!png) throw new ConversionError('The picture could not be captured in this browser.');
      await saveBlob(new Blob([png], { type: 'image/png' }), `${(S.plan && S.plan.name) || 'Car'}_view.png`);
    } catch (e) { console.error(e); toast(e && e.message ? e.message : 'The picture could not be saved.'); }
  }
  $('saveBtn').addEventListener('click', savePicture);

  // on-screen controls for touch devices
  for (const b of $('touch').querySelectorAll('button')) {
    const k = b.dataset.k;
    const set = (v) => {
      if (!viewer) return;
      if (k === 'gas') viewer.touch.throttle = v; else if (k === 'brake') viewer.touch.brake = v;
      else if (k === 'left') viewer.touch.steer = v ? 1 : (viewer.touch.steer > 0 ? 0 : viewer.touch.steer);
      else if (k === 'right') viewer.touch.steer = v ? -1 : (viewer.touch.steer < 0 ? 0 : viewer.touch.steer);
    };
    b.addEventListener('pointerdown', (e) => { e.preventDefault(); try { b.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ } set(1); });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) b.addEventListener(ev, () => set(0));
    b.addEventListener('contextmenu', (e) => e.preventDefault());
  }
  if (viewer) {
    new MutationObserver(() => viewer.readTheme()).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
    if (mq && mq.addEventListener) mq.addEventListener('change', () => viewer.readTheme());
  }

  // ---------- options
  const AXES = ['+x', '-x', '+y', '-y', '+z', '-z'];
  for (const id of ['axR', 'axU', 'axF']) {
    const sel = $(id);
    for (const a of AXES) { const o = document.createElement('option'); o.value = a; o.textContent = a.toUpperCase().replace('+', '+ ').replace('-', '- '); sel.appendChild(o); }
  }
  $('axR').value = '+x'; $('axU').value = '+y'; $('axF').value = '-z';

  function numOrNull(id) { const v = parseFloat($(id).value); return Number.isFinite(v) && v > 0 ? v : null; }
  // top speed is optional; a value that is not 1..1000 km/h is reported instead of being silently ignored
  function speedProblem() {
    const raw = $('oSpeed').value.trim();
    if (!raw) return null;
    const v = Number(raw);
    return Number.isFinite(v) && v >= 1 && v <= 1000 ? null : 'Top speed must be a number from 1 to 1000 km/h. Leave it empty for the default.';
  }
  function readOptions() {
    const o = defaultOptions();
    o.name = $('oName').value.trim() || null;
    o.scale = numOrNull('oScale') || 1;
    o.length = numOrNull('oLength');
    o.unit = $('oUnit').value || null;
    o.flipForward = $('oFlip').checked;
    o.axes = $('oAxes').checked ? [$('axR').value, $('axU').value, $('axF').value] : null;
    const paints = ['oPaint1', 'oPaint2', 'oPaint3'].map((id, i) => (S.paintTouched[i] ? $(id).value : null));
    [o.paint, o.paint2, o.paint3] = paints;
    o.includeGlass = $('oGlass').checked; o.includeInterior = $('oInterior').checked;
    o.includeLights = $('oLights').checked; o.includeLod = $('oLod').checked;
    o.maxTrisPerMesh = Math.max(1000, Math.min(ROBLOX_MAX_TRIANGLES, numOrNull('oMaxTris') || ROBLOX_MAX_TRIANGLES));
    o.targetTris = numOrNull('oTarget');
    o.maxTexture = parseInt($('oTex').value, 10) || 1024;
    o.drivetrain = (document.querySelector('input[name=oDrive]:checked') || { value: 'awd' }).value;
    o.formats = ['glb'].concat($('oObj').checked ? ['obj'] : []);
    o.rig = $('oRig').checked;
    o.addTires = $('oTires').checked;
    o.newTires = $('oNewTires').checked;
    o.topSpeed = speedProblem() ? null : numOrNull('oSpeed');
    o.advancedChassis = $('oAChassis').checked;
    o.achassisReal = $('oAChassisReal').checked;
    o.vehicle = $('vehWrap').hidden ? null : ($('vehSelect').value || null);
    o.config = $('cfgWrap').hidden ? null : ($('cfgSelect').value || null);
    return o;
  }

  // ---------- rendering of results
  function renderSafety(msgs) {
    const box = $('safety'), body = $('safetyBody');
    body.textContent = '';
    if (msgs.length) {
      box.classList.add('hit');
      const p = document.createElement('div'); p.className = 'hint';
      p.textContent = 'Removed before converting; none of this is in the download.';
      const ul = document.createElement('ul');
      for (const m of msgs) { const li = document.createElement('li'); li.textContent = m; ul.appendChild(li); }
      body.append(p, ul);
    } else {
      box.classList.remove('hit');
      body.className = 'hint';
      body.textContent = 'Nothing was removed. Part, material and texture names were checked for links and adult-content markers, and archive files for risky types. The pictures themselves are not scanned.';
    }
  }
  function renderNotes(warnings) {
    const list = $('noteList');
    list.textContent = '';
    const shown = warnings.filter((w) => !w.startsWith('Safety: '));
    for (const w of shown) { const li = document.createElement('li'); li.textContent = w; list.appendChild(li); }
    $('notes').hidden = shown.length === 0;
  }
  function renderStats(built, plan) {
    $('stPieces').textContent = String(built.pieces.length);
    $('stTris').textContent = built.pieces.reduce((a, p) => a + p.mesh.triCount, 0).toLocaleString('en-US');
    $('stSize').textContent = built.size.map(fmt).join(' x ');
    $('stWheels').textContent = String(built.wheels.length);
  }

  // ---------- build the preview + plan for the current model and options
  function sceneImages(opts) {
    const images = new Map(), alphaTex = new Set();
    const size = Math.min(opts.maxTexture, 1024);
    for (const pc of S.built.pieces) {
      const key = pc.material.texture;
      if (!key || images.has(key) || !S.model.textures.has(key)) continue;
      const tex = S.model.textures.get(key);
      const ck = `${key}@${size}`;
      if (!S.imgCache.has(ck)) S.imgCache.set(ck, fitTexture(tex.image, size));
      images.set(key, S.imgCache.get(ck));
      if (tex.hasAlpha()) alphaTex.add(key);
    }
    return { images, alphaTex };
  }
  async function runRebuild() {
    if (!S.model) return;
    const model = S.model;
    const opts = readOptions();
    const problem = speedProblem();
    $('oSpeed').setAttribute('aria-invalid', problem ? 'true' : 'false');
    $('oSpeedMsg').hidden = !problem; $('oSpeedMsg').textContent = problem || '';
    if (problem) { setStatus(problem, 'err'); S.ok = false; $('dlBtn').disabled = true; $('copyBtn').disabled = true; return; }
    S.buildLog = [];
    setStatus('Building the preview...');
    const warnings = model.notes.slice();
    let built, plan;
    try {
      built = await buildPieces(model, opts, buildLog, warnings);
      S.warnBuild = warnings.slice();
      plan = await makeOutputs(model, built, opts, buildLog, warnings, true);
    } catch (e) { logAll(); showError(e); return; }
    if (model !== S.model) return;                       // a newer model was loaded meanwhile
    S.built = built; S.plan = plan; S.opts = opts; S.ok = true;
    if (viewer) {
      const { images, alphaTex } = sceneImages(opts);
      viewer.setScene({ pieces: built.pieces, wheels: built.wheels, flags: plan.flags, size: built.size, images, alphaTex, topSpeed: opts.topSpeed }, !S.newModel);
      // nothing to drive without wheels
      noWheelsHint = built.wheels.length === 0;
      if (noWheelsHint && viewer.drive) setDriving(false);
      driveBtn.disabled = noWheelsHint;
      driveBtn.title = noWheelsHint ? 'No wheels were found on this car. Turn on "Add tires" or name the wheel parts "wheel" or "tire".' : '';
      $('saveBtn').disabled = false;
      updateTag();
    }
    S.newModel = false;
    $('oName').placeholder = sanitize(model.name, 'Car');
    renderStats(built, plan);
    renderNotes(warnings);
    renderSafety([...warnings.filter((w) => w.startsWith('Safety: ')).map((w) => w.slice(8)), ...S.extraSafety]);
    $('scriptBox').hidden = !plan.lua;
    $('scriptText').value = plan.lua || '';
    $('copyBtn').disabled = !plan.lua;
    $('dlBtn').disabled = false;
    $('dlBtn').textContent = `Download ${plan.name}.zip`;
    const w = built.wheels.length;
    const tail = !viewer ? '' : w ? ' Press Drive to test it.' : ` No wheels found, so Drive is off.${opts.addTires ? '' : ' Turn on "Add tires" to generate some.'}`;
    setStatus(`${plan.name}: ${built.pieces.length} pieces, ${w} wheel${w === 1 ? '' : 's'}.${tail}`, 'ok');
    logAll();
  }
  function rebuild() {
    // one build at a time; if options change while building, run once more afterwards
    if (S.building) { S.again = true; return S.building; }
    S.building = (async () => {
      try { do { S.again = false; await runRebuild(); } while (S.again); }
      finally { S.building = null; }
    })();
    return S.building;
  }
  function scheduleRebuild() {
    if (!S.model) return;
    clearTimeout(S.timer);
    S.timer = setTimeout(() => { S.timer = 0; rebuild(); }, 200);
  }
  async function settle() {
    if (S.timer) { clearTimeout(S.timer); S.timer = 0; rebuild(); }
    while (S.building) await S.building;
  }

  // ---------- loading models
  function fillSelect(sel, items, first) {
    sel.textContent = '';
    if (first !== undefined) { const o = document.createElement('option'); o.value = ''; o.textContent = first; sel.appendChild(o); }
    for (const it of items) { const o = document.createElement('option'); o.value = it.value; o.textContent = it.label; sel.appendChild(o); }
  }
  function refreshBeamngPickers(keepVehicle) {
    const src = S.sources[S.srcIdx];
    const isBeam = src && src.kind === 'beamng' && S.store;
    $('vehWrap').hidden = true; $('cfgWrap').hidden = true;
    if (isBeam) {
      const info = beamngInfo(S.store);
      if (!keepVehicle) fillSelect($('vehSelect'), info.map((v) => ({ value: v.name, label: v.name })));
      const cur = info.find((v) => v.name === $('vehSelect').value) || info[0];
      $('vehWrap').hidden = info.length < 2;
      fillSelect($('cfgSelect'), (cur ? cur.configs : []).map((c) => ({ value: c, label: c })), 'Default parts');
      $('cfgWrap').hidden = !cur || cur.configs.length === 0;
    }
    $('srcRow').hidden = S.sources.length < 2 && $('vehWrap').hidden && $('cfgWrap').hidden;
    $('srcSelect').parentElement.hidden = S.sources.length < 2;
  }
  async function loadSelected(isNewSource) {
    const src = S.sources[S.srcIdx];
    if (!src) return;
    S.loadLog = []; S.buildLog = [];
    setStatus(`Reading ${src.label}...`);
    $('dlBtn').disabled = true; $('copyBtn').disabled = true;
    await new Promise((r) => setTimeout(r, 0));
    const opts = readOptions();
    try {
      const model = await loadModel(S.store, src, S.zipName, { source: 'auto', vehicle: opts.vehicle, config: opts.config, includeLod: opts.includeLod }, loadLog);
      S.model = model; S.newModel = true; S.imgCache = new Map();
      if (isNewSource) { S.paintTouched = [false, false, false]; $('oName').value = ''; }
    } catch (e) { logAll(); showError(e); return; }
    await rebuild();
  }

  async function ingest(items) {
    items = items.filter((it) => it && it.file);
    if (!items.length) { setStatus('Nothing to read there. Drop a .dff, a BeamNG .zip, a vehicle folder, or an .obj / .glb / .dae file.', 'err'); return; }
    setDriving(false);
    S.extraSafety = [];
    setStatus('Looking at the files...');
    try {
      // files that are never opened: shortcuts, programs, scripts, disguised double extensions
      const risky = new Map(scanFiles(items.map((it) => it.path)));
      const kept = items.filter((it) => !risky.has(it.path));
      if (risky.size) {
        const list = [...risky].slice(0, 4).map(([p, why]) => `${p.split('/').pop()} (${why})`).join(', ');
        S.extraSafety.push(`ignored ${risky.size} risky file(s), never opened or run: ${list}${risky.size > 4 ? ', ...' : ''}`);
      }
      const zips = kept.filter((it) => /\.zip$/i.test(it.path));
      const loose = kept.filter((it) => !/\.zip$/i.test(it.path));
      const opened = [];
      for (const z of zips) {
        try { opened.push({ it: z, store: await storeFromZip(new BlobSource(z.file)) }); }
        catch (e) { throw new ConversionError(`${z.file.name}: ${e && e.message ? e.message : e}`); }
      }
      let store, zipName = null;
      const beam = opened.find((o) => [...o.store.index.keys()].some((p) => p.endsWith('.jbeam')));
      if (beam) { store = beam.store; zipName = beam.it.file.name; }
      else {
        const entries = loose.map((it) => ({ path: it.path, read: async () => new Uint8Array(await it.file.arrayBuffer()) }));
        for (const o of opened) for (const e of o.store.index.values()) entries.push({ path: opened.length > 1 ? `${o.it.file.name.replace(/\.zip$/i, '')}/${e.path}` : e.path, read: e.read });
        store = new Store(entries);
      }
      const sources = detectSources(store, zipName);
      if (!sources.length) {
        throw new ConversionError('No car model found. Add a GTA San Andreas .dff (with its .txd), a BeamNG mod .zip or vehicle folder, or an .obj / .glb / .dae file.');
      }
      S.store = store; S.zipName = zipName; S.sources = sources; S.srcIdx = 0;
      fillSelect($('srcSelect'), sources.map((s, i) => ({ value: String(i), label: s.label })));
      $('srcSelect').value = '0';
      $('vehSelect').textContent = ''; $('cfgSelect').textContent = '';
      $('vehWrap').hidden = true; $('cfgWrap').hidden = true;
      refreshBeamngPickers(false);
    } catch (e) { showError(e); return; }
    await loadSelected(true);
  }

  // ---------- file intake: picker, folder picker, drag and drop
  const relPath = (f) => f.webkitRelativePath || f.name;
  $('pickFiles').addEventListener('click', () => $('fileInput').click());
  $('pickFolder').addEventListener('click', () => $('folderInput').click());
  for (const id of ['fileInput', 'folderInput']) {
    $(id).addEventListener('change', (e) => {
      const files = [...e.target.files].map((f) => ({ path: relPath(f), file: f }));
      e.target.value = '';
      ingest(files);
    });
  }
  async function walkEntry(entry, prefix, out, budget) {
    if (budget.n <= 0) return;
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ path: prefix + entry.name, file }); budget.n--;
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const c of batch) await walkEntry(c, prefix + entry.name + '/', out, budget);
      }
    }
  }
  const dropZone = $('drop');
  document.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('over'); });
  document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) dropZone.classList.remove('over'); });
  document.addEventListener('drop', async (e) => {
    e.preventDefault();
    dropZone.classList.remove('over');
    const dt = e.dataTransfer;
    if (!dt) return;
    const entries = [], plain = [];
    if (dt.items && dt.items.length && dt.items[0].webkitGetAsEntry) {
      for (const it of dt.items) {
        if (it.kind !== 'file') continue;
        const en = it.webkitGetAsEntry();
        if (en) entries.push(en); else { const f = it.getAsFile(); if (f) plain.push({ path: f.name, file: f }); }
      }
    } else for (const f of dt.files) plain.push({ path: f.name, file: f });
    const out = [...plain], budget = { n: 20000 };
    try { for (const en of entries) await walkEntry(en, '', out, budget); }
    catch (err) { showError(err); return; }
    ingest(out);
  });

  // ---------- controls
  $('srcSelect').addEventListener('change', () => { S.srcIdx = parseInt($('srcSelect').value, 10) || 0; refreshBeamngPickers(false); loadSelected(true); });
  $('vehSelect').addEventListener('change', () => { refreshBeamngPickers(true); loadSelected(false); });
  $('cfgSelect').addEventListener('change', () => loadSelected(false));
  $('oLod').addEventListener('change', () => { if (S.sources[S.srcIdx] && S.sources[S.srcIdx].kind === 'gta_sa') loadSelected(false); else scheduleRebuild(); });
  const rebuildIds = ['oName', 'oScale', 'oLength', 'oUnit', 'oFlip', 'oGlass', 'oInterior', 'oLights', 'oMaxTris', 'oTarget', 'oTex', 'oRig', 'oTires', 'oNewTires', 'oSpeed', 'oAChassis', 'oAChassisReal', 'oObj', 'axR', 'axU', 'axF'];
  for (const id of rebuildIds) $(id).addEventListener(id === 'oName' || id.startsWith('o') && $(id).type === 'number' ? 'input' : 'change', scheduleRebuild);
  for (const el of document.querySelectorAll('input[name=oDrive]')) el.addEventListener('change', scheduleRebuild);
  // the drive options only matter when the car is made drivable
  function syncDriveOpts() {
    const on = $('oRig').checked;
    for (const el of $('driveOpts').querySelectorAll('input')) el.disabled = !on;
    for (const el of $('driveOpts').querySelectorAll(':scope > label')) el.classList.toggle('dim', !on);
    $('oAChassis').disabled = !on;
    $('oAChassis').closest('label').classList.toggle('dim', !on);
    $('oAChassisReal').disabled = !on;
    $('oAChassisReal').closest('label').classList.toggle('dim', !on);
  }
  $('oRig').addEventListener('change', syncDriveOpts);
  // the real A-Chassis kit takes over from car2roblox's own rigs when it's ticked
  $('oAChassisReal').addEventListener('change', () => {
    if ($('oAChassisReal').checked) $('oAChassis').checked = false;
  });
  syncDriveOpts();
  $('oAxes').addEventListener('change', () => {
    const on = $('oAxes').checked;
    $('axesRow').hidden = !on;
    if (on && S.built && S.built.axes) { $('axR').value = S.built.axes.right; $('axU').value = S.built.axes.up; $('axF').value = S.built.axes.forward; }
    scheduleRebuild();
  });
  ['oPaint1', 'oPaint2', 'oPaint3'].forEach((id, i) => $(id).addEventListener('input', () => { S.paintTouched[i] = true; scheduleRebuild(); }));
  $('paintReset').addEventListener('click', () => {
    S.paintTouched = [false, false, false];
    $('oPaint1').value = '#2f6db5'; $('oPaint2').value = '#e8e8e8'; $('oPaint3').value = '#202020';
    scheduleRebuild();
  });
  $('sampleBtn').addEventListener('click', () => loadSample());

  // ---------- output: download and copy
  async function saveBlob(blob, filename) {
    let dl = null;
    const hosted = typeof window.claude === 'object' && window.claude && typeof window.claude.use === 'function';
    if (hosted) { try { dl = await window.claude.use('downloads'); } catch (_) { dl = null; } }
    if (dl) {
      try { await dl.save({ filename, data: blob }); toast(`Saved ${filename}`); }
      catch (e) {
        if (e && e.code === 'declined') return;
        throw new ConversionError(e && e.message ? `Could not save the file: ${e.message}` : 'Could not save the file here. Open the standalone page to download.');
      }
      return;
    }
    if (hosted) throw new ConversionError('Downloads are not available in this view. Open the standalone Car2Roblox.html page to download.');
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.rel = 'noopener';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(`Downloading ${filename}`);
  }
  $('dlBtn').addEventListener('click', async () => {
    const btn = $('dlBtn');
    btn.disabled = true;
    try {
      await settle();
      if (!S.ok || !S.built) return;
      setStatus('Packing the zip...');
      const out = await makeOutputs(S.model, S.built, S.opts, buildLog, S.warnBuild.slice(), false);
      // a picture of the converted car, like the desktop app writes (needs the 3D view; skipped without WebGL)
      let png = null;
      try { png = viewer ? await viewer.snapshot({ width: 960, height: 600, hero: true }) : null; } catch (e) { console.error(e); }
      if (png) out.files.push({ name: `${out.name}/preview.png`, data: png, store: true });
      else buildLog('preview.png was not added: this browser could not draw the 3D view');
      const blob = await buildZip(out.files);
      logAll();
      await saveBlob(blob, `${out.name}.zip`);
      setStatus(`${out.name}: ${out.files.length} files in ${out.name}.zip (${(blob.size / 1048576).toFixed(1)} MB).`, 'ok');
    } catch (e) { showError(e); }
    finally { btn.disabled = !S.ok; }
  });
  $('copyBtn').addEventListener('click', async () => {
    const text = S.plan && S.plan.lua;
    if (!text) return;
    try { await navigator.clipboard.writeText(text); toast('Studio script copied'); }
    catch (_) {
      $('scriptBox').hidden = false; $('scriptBox').open = true;
      const ta = $('scriptText'); ta.focus(); ta.select();
      toast('Copy blocked here. The script is selected: press Ctrl+C.');
    }
  });

  // ---------- start with the sample car so the first view is not empty
  function loadSample() {
    setDriving(false);
    S.store = null; S.zipName = null; S.sources = []; S.srcIdx = 0; S.extraSafety = []; S.loadLog = ['Sample car (built in)'];
    S.model = makeSampleModel(); S.newModel = true; S.imgCache = new Map(); S.paintTouched = [false, false, false];
    $('oName').value = '';
    $('srcRow').hidden = true; $('vehWrap').hidden = true; $('cfgWrap').hidden = true;
    return rebuild();
  }
  loadSample();
  // test hook, harmless in normal use
  window.__car2roblox = { S, viewer, API, ingest, rebuild, readOptions };
})();
