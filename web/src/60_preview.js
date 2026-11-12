// ===================================================================== 3D preview + drive mode (WebGL)
const M4 = {
  ident() { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; },
  mul(a, b) {                                   // column-major: a * b
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  },
  persp(fovy, aspect, n, f) {
    const t = 1 / Math.tan(fovy / 2), m = new Float32Array(16);
    m[0] = t / aspect; m[5] = t; m[10] = (f + n) / (n - f); m[11] = -1; m[14] = 2 * f * n / (n - f);
    return m;
  },
  lookAt(e, c, up) {
    let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
    let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    const m = new Float32Array(16);
    m[0] = xx; m[1] = yx; m[2] = zx; m[4] = xy; m[5] = yy; m[6] = zy; m[8] = xz; m[9] = yz; m[10] = zz;
    m[12] = -(xx * e[0] + xy * e[1] + xz * e[2]); m[13] = -(yx * e[0] + yy * e[1] + yz * e[2]); m[14] = -(zx * e[0] + zy * e[1] + zz * e[2]);
    m[15] = 1;
    return m;
  },
  translate(x, y, z) { const m = M4.ident(); m[12] = x; m[13] = y; m[14] = z; return m; },
  rotX(a) { const m = M4.ident(), c = Math.cos(a), s = Math.sin(a); m[5] = c; m[6] = s; m[9] = -s; m[10] = c; return m; },
  rotY(a) { const m = M4.ident(), c = Math.cos(a), s = Math.sin(a); m[0] = c; m[2] = -s; m[8] = s; m[10] = c; return m; },
  rotZ(a) { const m = M4.ident(), c = Math.cos(a), s = Math.sin(a); m[0] = c; m[1] = s; m[4] = -s; m[5] = c; return m; },
};
const hexToRgb = (s, d = [0.5, 0.5, 0.5]) => {
  const m = /^#?([0-9a-f]{6})$/i.exec((s || '').trim());
  return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255) : d;
};

// ---- procedural sample car (so the page opens with something to look at and drive)
function boxMesh(c, s, mat) {
  const faces = [[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]], [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [0, 1, 0], [1, 0, 0]]];
  const P = [], N = [], U = [], T = [];
  for (const [n, u, v] of faces) {
    const base = P.length / 3;
    for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      for (let k = 0; k < 3; k++) P.push(c[k] + n[k] * s[k] / 2 + u[k] * a * s[k] / 2 + v[k] * b * s[k] / 2);
      N.push(...n); U.push((a + 1) / 2, (b + 1) / 2);
    }
    T.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return new Mesh(Float32Array.from(P), Int32Array.from(T), new Int32Array(T.length / 3).fill(mat), Float32Array.from(N), Float32Array.from(U));
}
function cylinderXMesh(c, r, w, mat, seg = 20) {
  const P = [], N = [], U = [], T = [];
  for (let i = 0; i < seg; i++) {
    const a0 = i / seg * Math.PI * 2, a1 = (i + 1) / seg * Math.PI * 2, base = P.length / 3;
    for (const [a, x] of [[a0, -1], [a1, -1], [a1, 1], [a0, 1]]) {
      P.push(c[0] + x * w / 2, c[1] + Math.cos(a) * r, c[2] + Math.sin(a) * r);
      N.push(0, Math.cos(a), Math.sin(a)); U.push((a / (Math.PI * 2)), (x + 1) / 2);
    }
    T.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  for (const sx of [1, -1]) {
    const cb = P.length / 3;
    P.push(c[0] + sx * w / 2, c[1], c[2]); N.push(sx, 0, 0); U.push(0.5, 0.5);
    for (let i = 0; i <= seg; i++) {
      const a = i / seg * Math.PI * 2;
      P.push(c[0] + sx * w / 2, c[1] + Math.cos(a) * r, c[2] + Math.sin(a) * r); N.push(sx, 0, 0);
      U.push(0.5 + Math.cos(a) / 2, 0.5 + Math.sin(a) / 2);
    }
    for (let i = 0; i < seg; i++) T.push(...(sx > 0 ? [cb, cb + 1 + i, cb + 2 + i] : [cb, cb + 2 + i, cb + 1 + i]));
  }
  return new Mesh(Float32Array.from(P), Int32Array.from(T), new Int32Array(T.length / 3).fill(mat), Float32Array.from(N), Float32Array.from(U));
}
function makeSampleModel() {
  const m = newModel('SampleCar', 'sample', axisSpec('+x', '+y', '-z'), { unitMeters: 1.0 });
  const paint = addMaterial(m, makeMaterial({ name: 'paint_primary', color: [1, 1, 1, 1], paint: 'primary' }));
  const trim = addMaterial(m, makeMaterial({ name: 'trim', color: [0.12, 0.12, 0.13, 1] }));
  const glass = addMaterial(m, makeMaterial({ name: 'glass', color: [0.35, 0.5, 0.6, 0.45], transparent: true }));
  const tyre = addMaterial(m, makeMaterial({ name: 'tyre', color: [0.07, 0.07, 0.08, 1] }));
  const rim = addMaterial(m, makeMaterial({ name: 'rim', color: [0.72, 0.74, 0.78, 1] }));
  const lamp = addMaterial(m, makeMaterial({ name: 'lamp', color: [1.0, 0.92, 0.65, 1] }));
  const tail = addMaterial(m, makeMaterial({ name: 'tail', color: [0.8, 0.08, 0.08, 1] }));
  const part = (name, mesh) => m.parts.push({ name, mesh, wheelSlot: null, kindHint: null });
  part('body', boxMesh([0, 0.62, 0], [1.8, 0.62, 4.2], paint));
  part('roof', boxMesh([0, 1.08, 0.25], [1.5, 0.38, 2.0], paint));
  part('windows', boxMesh([0, 1.08, 0.25], [1.52, 0.3, 2.02], glass));
  part('bumper_front', boxMesh([0, 0.42, -2.1], [1.82, 0.22, 0.14], trim));
  part('bumper_rear', boxMesh([0, 0.42, 2.1], [1.82, 0.22, 0.14], trim));
  part('headlight_L', boxMesh([-0.62, 0.7, -2.1], [0.34, 0.14, 0.06], lamp));
  part('headlight_R', boxMesh([0.62, 0.7, -2.1], [0.34, 0.14, 0.06], lamp));
  part('taillight_L', boxMesh([-0.62, 0.7, 2.1], [0.34, 0.14, 0.06], tail));
  part('taillight_R', boxMesh([0.62, 0.7, 2.1], [0.34, 0.14, 0.06], tail));
  for (const [slot, x, z] of [['FL', -0.88, -1.32], ['FR', 0.88, -1.32], ['RL', -0.88, 1.32], ['RR', 0.88, 1.32]]) {
    part(`wheel_${slot}_tyre`, cylinderXMesh([x, 0.36, z], 0.36, 0.26, tyre));
    part(`wheel_${slot}_rim`, cylinderXMesh([x + (x < 0 ? -0.02 : 0.02), 0.36, z], 0.24, 0.26, rim));
  }
  return m;
}

// ---- the viewer
const VS = `attribute vec3 aPos; attribute vec3 aNrm; attribute vec2 aUv;
uniform mat4 uVP; uniform mat4 uModel; varying vec3 vN; varying vec2 vUv; varying vec3 vW;
void main(){ vec4 w = uModel * vec4(aPos, 1.0); vW = w.xyz; vN = mat3(uModel) * aNrm; vUv = aUv; gl_Position = uVP * w; }`;
const FS = `precision mediump float;
uniform vec4 uColor; uniform float uHasTex; uniform sampler2D uTex; uniform float uAlphaCut; uniform float uUnlit;
uniform vec3 uSky; uniform vec3 uGnd; uniform vec3 uSun; varying vec3 vN; varying vec2 vUv; varying vec3 vW;
void main(){
  vec4 base = uColor;
  if (uHasTex > 0.5) { vec4 t = texture2D(uTex, vUv); base = vec4(t.rgb * uColor.rgb, t.a * uColor.a); }
  if (uAlphaCut > 0.0 && base.a < uAlphaCut) discard;
  if (uUnlit > 0.5) { gl_FragColor = base; return; }
  vec3 b = pow(base.rgb, vec3(2.2));
  vec3 n = normalize(vN); if (!gl_FrontFacing) n = -n;
  vec3 amb = mix(uGnd, uSky, n.y * 0.5 + 0.5);
  float d = max(dot(n, normalize(uSun)), 0.0);
  vec3 c = b * (amb * 0.8 + vec3(1.0, 0.97, 0.9) * d * 0.7);
  gl_FragColor = vec4(pow(c, vec3(1.0 / 2.2)), base.a);
}`;
// the backdrop: a full-screen pass that draws the sky, the horizon glow and a perspective grid floor from
// each pixel's view ray (same idea as the page background and the desktop app's preview)
const BVS = `attribute vec2 aP; varying vec2 vP; void main(){ vP = aP; gl_Position = vec4(aP, 0.0, 1.0); }`;
const BFS = `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec3 uRight; uniform vec3 uUp; uniform vec3 uFwd; uniform vec3 uEye;
uniform vec3 uTop; uniform vec3 uMid; uniform vec3 uHor; uniform vec3 uGlow;
uniform vec3 uGround; uniform vec3 uGroundFar; uniform vec3 uGrid;
uniform float uTanY; uniform float uAspect; uniform vec2 uPixClip;
varying vec2 vP;
vec3 rayDir(vec2 v) { return normalize(uFwd + v.x * uAspect * uTanY * uRight + v.y * uTanY * uUp); }
vec3 groundHit(vec2 v) { vec3 d = rayDir(v); float t = -uEye.y / min(d.y, -0.0002); return vec3((uEye + d * t).xz, t); }
// 1 on a grid line, 0 between lines; the line is one pixel wide wherever it is seen and fades out before it can shimmer
float axisLine(float p, float s, float fw) {
  float d = abs(mod(p + 0.5 * s, s) - 0.5 * s);
  return (1.0 - smoothstep(fw * 0.5, fw * 1.5 + 0.004, d)) * (1.0 - smoothstep(0.3, 0.9, fw / s));
}
void main(){
  vec3 d = rayDir(vP);
  vec3 col;
  if (d.y > -0.0008) {
    float t = clamp(d.y, 0.0, 1.0);
    col = mix(uHor, uMid, smoothstep(0.0, 0.17, t));
    col = mix(col, uTop, smoothstep(0.1, 0.55, t));
    float g = pow(max(dot(d, normalize(vec3(-0.55, 0.10, 0.45))), 0.0), 14.0);
    col += uGlow * (g * 0.30 + exp(-t * 9.0) * 0.12);
  } else {
    vec3 h = groundHit(vP);
    vec3 hx = groundHit(vP + vec2(uPixClip.x, 0.0));
    vec3 hy = groundHit(vP + vec2(0.0, uPixClip.y));
    vec2 fw = max(abs(hx.xy - h.xy), abs(hy.xy - h.xy));
    float tt = h.z;
    col = mix(uGroundFar, uGround, exp(-tt / 230.0));
    float g = max(max(axisLine(h.x, 4.0, fw.x), axisLine(h.y, 4.0, fw.y)) * 0.5, max(axisLine(h.x, 20.0, fw.x), axisLine(h.y, 20.0, fw.y)));
    col = mix(col, uGrid, g * (1.0 - smoothstep(60.0, 420.0, tt)) * 0.85);
  }
  gl_FragColor = vec4(col, 1.0);
}`;

// piece colours for the "Pieces" view mode (sRGB)
const KIND_COLORS = { body: '#4f86f7', wheel: '#f0a04b', glass: '#5ed0e0', interior: '#a98af5', light: '#f6d65b', other: '#9aa3b5' };

class Viewer {
  constructor(canvas, hooks = {}) {
    this.canvas = canvas;
    this.hooks = hooks;
    this.scene = null;
    this.gpu = [];
    this.textures = new Map();
    this.yaw = 2.5; this.pitch = 0.24; this.dist = 30; this.target = [0, 2, 0];
    this.drive = false; this.autoRotate = false; this.mode = 'shaded'; this.noArrow = false; this.fixedSize = null;
    this.maxFwd = 78; this.wheelbase = 9;
    this.keys = new Set(); this.touch = { throttle: 0, brake: 0, steer: 0 };
    this.car = this.freshCar();
    this.dirty = true;
    this.last = performance.now();
    this.colors = {};
    this.ok = this.initGL();
    if (this.ok) { this.bindInput(); this.readTheme(); requestAnimationFrame((t) => this.frame(t)); }
  }
  freshCar() { return { x: 0, z: 0, heading: 0, speed: 0, steer: 0, spin: 0, pitch: 0, roll: 0, camHeading: 0, camOffset: 0, lastAcc: 0 }; }
  initGL() {
    const opt = { antialias: true, alpha: false, preserveDrawingBuffer: false };
    let gl = null;
    try { gl = this.canvas.getContext('webgl2', opt) || this.canvas.getContext('webgl', opt) || this.canvas.getContext('experimental-webgl', opt); } catch (e) { gl = null; }
    if (!gl) return false;
    this.gl = gl;
    this.isGl2 = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext;
    this.uint = this.isGl2 || !!gl.getExtension('OES_element_index_uint');
    const prog = (vs, fs) => {
      const mk = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
      const p = gl.createProgram();
      gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      const u = {};
      const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); u[info.name] = gl.getUniformLocation(p, info.name); }
      return { p, u, a: { pos: gl.getAttribLocation(p, 'aPos'), nrm: gl.getAttribLocation(p, 'aNrm'), uv: gl.getAttribLocation(p, 'aUv') } };
    };
    try { this.main = prog(VS, FS); this.back = prog(BVS, BFS); } catch (e) { console.error(e); return false; }
    this.backBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.backBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    this.back.a.p = gl.getAttribLocation(this.back.p, 'aP');
    this.white = this.makeTexture({ w: 1, h: 1, data: new Uint8Array([255, 255, 255, 255]) });
    return true;
  }
  readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const get = (n, d) => hexToRgb(cs.getPropertyValue(n), d);
    this.colors = {
      bg: get('--scene-bg', [0.85, 0.88, 0.92]), grid: get('--scene-grid', [0.56, 0.62, 0.73]),
      sky: get('--scene-sky', [0.7, 0.78, 0.9]), gnd: get('--scene-gnd', [0.35, 0.35, 0.38]),
      top: get('--scene-top', [0.43, 0.63, 0.89]), mid: get('--scene-mid', [0.67, 0.78, 0.93]),
      hor: get('--scene-hor', [0.91, 0.93, 0.96]), glow: get('--scene-glow', [1, 0.96, 0.84]),
      ground: get('--scene-ground', [0.78, 0.81, 0.85]), groundFar: get('--scene-ground-far', [0.89, 0.91, 0.94]),
      wire: get('--scene-wire', [0.06, 0.11, 0.23]), accent: get('--accent', [0.1, 0.4, 0.85]),
    };
    this.dirty = true;
  }
  makeTexture(img) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, img.w, img.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, img.data);
    const pot = (n) => (n & (n - 1)) === 0;
    if (this.isGl2 || (pot(img.w) && pot(img.h))) {
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    } else {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return t;
  }
  freeScene() {
    const gl = this.gl;
    for (const g of this.gpu) { gl.deleteBuffer(g.vbo); gl.deleteBuffer(g.ibo); if (g.lines) gl.deleteBuffer(g.lines.ibo); }
    for (const t of this.textures.values()) gl.deleteTexture(t);
    this.gpu = []; this.textures = new Map();
  }
  // scene: { pieces, wheels, flags, size, images: Map(key -> image) }
  setScene(scene, keepCamera) {
    if (!this.ok) return;
    const gl = this.gl;
    this.freeScene();
    this.scene = scene;
    for (const [k, img] of scene.images) this.textures.set(k, this.makeTexture(img));
    const wheelIndex = new Map();
    scene.wheels.forEach((w, i) => wheelIndex.set(w.slot, i));
    let pieceIndex = 0;
    for (const pc of scene.pieces) {
      const m = pc.mesh, nv = m.vertCount;
      const data = new Float32Array(nv * 8);
      for (let i = 0; i < nv; i++) {
        data[i * 8] = m.pos[i * 3]; data[i * 8 + 1] = m.pos[i * 3 + 1]; data[i * 8 + 2] = m.pos[i * 3 + 2];
        if (m.nrm) { data[i * 8 + 3] = m.nrm[i * 3]; data[i * 8 + 4] = m.nrm[i * 3 + 1]; data[i * 8 + 5] = m.nrm[i * 3 + 2]; }
        if (m.uv) { data[i * 8 + 6] = m.uv[i * 2]; data[i * 8 + 7] = m.uv[i * 2 + 1]; }
      }
      const vbo = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, vbo); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      const wide = nv > 65535;
      if (wide && !this.uint) continue;
      const ibo = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, wide ? Uint32Array.from(m.tri) : Uint16Array.from(m.tri), gl.STATIC_DRAW);
      const mat = pc.material, tex = mat.texture && this.textures.has(mat.texture) ? mat.texture : null;
      const alpha = tex && scene.alphaTex && scene.alphaTex.has(mat.texture) && !mat.transparent;
      const box = pieceBox(pc);
      this.gpu.push({ vbo, ibo, count: m.tri.length, type: wide ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, mat, tex, alphaCut: alpha ? 0.5 : 0,
        wheel: pc.wheelSlot && wheelIndex.has(pc.wheelSlot) ? wheelIndex.get(pc.wheelSlot) : -1, center: box.center,
        kind: pc.kind || 'body', index: pieceIndex++, tri: m.tri, wide, lines: null });
    }
    const [w, h, l] = scene.size;
    this.carLen = l; this.carH = h; this.carW = w;
    // wheelbase from the front and rear axles (like the desktop app), otherwise a typical share of the length
    const fz = scene.wheels.filter((x) => x.front).map((x) => x.hub[2]), rz = scene.wheels.filter((x) => !x.front).map((x) => x.hub[2]);
    const avg = (a) => a.reduce((p, q) => p + q, 0) / a.length;
    this.wheelbase = fz.length && rz.length && Math.abs(avg(rz) - avg(fz)) > 1 ? Math.abs(avg(rz) - avg(fz)) : Math.max(2, 0.58 * l);
    this.maxFwd = scene.topSpeed > 0 ? Math.max(15, Math.min(scene.topSpeed / 1.008, 400)) : 78;   // km/h -> studs per second
    this.wheelSpin = scene.wheels.map(() => 0);
    this.buildDecals();
    if (!keepCamera) this.resetCamera();
    this.car = this.freshCar();
    this.dirty = true;
  }
  buildDecals() {
    const gl = this.gl, len = this.carLen, w = this.carW;
    const mk = (verts) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.STATIC_DRAW); return { buf: b, n: verts.length / 8 }; };
    if (this.arrow) gl.deleteBuffer(this.arrow.buf);
    if (this.shadow) gl.deleteBuffer(this.shadow.buf);
    const z0 = -(len / 2 + 2.5), aw = Math.max(1.2, w * 0.32), al = Math.max(2.4, len * 0.14);
    const V = (x, z) => [x, 0.03, z, 0, 1, 0, 0, 0];
    const arrow = [...V(-aw * 0.35, z0 + al), ...V(aw * 0.35, z0 + al), ...V(aw * 0.35, z0 + al * 1.9), ...V(-aw * 0.35, z0 + al), ...V(aw * 0.35, z0 + al * 1.9), ...V(-aw * 0.35, z0 + al * 1.9),
      ...V(-aw, z0 + al), ...V(aw, z0 + al), ...V(0, z0)];
    this.arrow = mk(arrow);
    const sh = [], seg = 28;
    for (let i = 0; i < seg; i++) {
      const a0 = i / seg * Math.PI * 2, a1 = (i + 1) / seg * Math.PI * 2, rx = w * 0.62, rz = len * 0.58;
      sh.push(...[0, 0.02, 0, 0, 1, 0, 0, 0], ...[Math.cos(a0) * rx, 0.02, Math.sin(a0) * rz, 0, 1, 0, 0, 0], ...[Math.cos(a1) * rx, 0.02, Math.sin(a1) * rz, 0, 1, 0, 0, 0]);
    }
    this.shadow = mk(sh);
  }
  resetCamera() {
    const d = Math.max(this.carLen * 1.7, this.carW * 3.2, 12);
    this.yaw = 2.55; this.pitch = 0.26; this.dist = d; this.target = [0, this.carH * 0.45, 0];
  }
  resetCar() { this.car = this.freshCar(); this.dirty = true; }
  setDrive(on) {
    this.drive = on;
    if (on) { this.car = this.freshCar(); this.keys.clear(); }
    else this.resetCamera();
    this.dirty = true;
  }

  // ---- input
  bindInput() {
    const c = this.canvas;
    const pts = new Map();
    let pinch = 0;
    c.addEventListener('pointerdown', (e) => { c.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); pinch = 0; c.focus(); });
    c.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId)) return;
      const p = pts.get(e.pointerId);
      if (pts.size === 1) {
        const dx = e.clientX - p[0], dy = e.clientY - p[1];
        if (this.drive) this.car.camOffset -= dx * 0.008; else this.yaw -= dx * 0.008;
        this.pitch = Math.max(-0.05, Math.min(1.45, this.pitch + dy * 0.006));
      }
      p[0] = e.clientX; p[1] = e.clientY;
      if (pts.size === 2) {
        const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) this.dist = Math.max(4, Math.min(400, this.dist * pinch / d));
        pinch = d;
      }
      this.dirty = true;
    });
    const up = (e) => { pts.delete(e.pointerId); pinch = 0; };
    c.addEventListener('pointerup', up); c.addEventListener('pointercancel', up);
    c.addEventListener('wheel', (e) => { e.preventDefault(); this.dist = Math.max(4, Math.min(400, this.dist * Math.exp(e.deltaY * 0.0012))); this.dirty = true; }, { passive: false });
    c.addEventListener('dblclick', () => { if (!this.drive) this.resetCamera(); this.dirty = true; });
    const map = { ArrowUp: 'up', KeyW: 'up', ArrowDown: 'down', KeyS: 'down', ArrowLeft: 'left', KeyA: 'left', ArrowRight: 'right', KeyD: 'right', Space: 'brake' };
    window.addEventListener('keydown', (e) => {
      if (!this.drive) return;
      const t = e.target && e.target.tagName;
      if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') return;
      if (e.code in map) { this.keys.add(map[e.code]); e.preventDefault(); }
      else if (e.code === 'KeyR') this.resetCar();
    });
    window.addEventListener('keyup', (e) => { if (e.code in map) this.keys.delete(map[e.code]); });
    window.addEventListener('blur', () => this.keys.clear());
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => { this.dirty = true; }).observe(c);
  }

  // ---- simulation (kinematic bicycle model: fast, stable and good enough to judge size, wheels and steering)
  step(dt) {
    const c = this.car, k = this.keys, T = this.touch;
    const throttle = (k.has('up') ? 1 : 0) + T.throttle, brake = (k.has('down') ? 1 : 0) + T.brake;
    const steerIn = (k.has('left') ? 1 : 0) - (k.has('right') ? 1 : 0) + T.steer;
    const hand = k.has('brake');
    const maxFwd = this.maxFwd, maxRev = 26;
    let acc = 0;
    if (throttle > 0) acc += 46 * Math.min(1, throttle) * (1 - Math.max(0, c.speed) / maxFwd);
    if (brake > 0) acc -= c.speed > 1 ? 90 : 40 * (1 - Math.max(0, -c.speed) / maxRev);
    if (hand) acc -= Math.sign(c.speed) * 120;
    if (!throttle && !brake && !hand) acc -= Math.sign(c.speed) * (9 + 0.004 * c.speed * c.speed);
    const prev = c.speed;
    c.speed += acc * dt;
    if (!throttle && !brake && !hand && Math.sign(prev) !== Math.sign(c.speed)) c.speed = 0;
    if (hand && Math.abs(c.speed) < 2) c.speed = 0;
    c.speed = Math.max(-maxRev, Math.min(maxFwd, c.speed));
    const lim = 0.56 / (1 + Math.abs(c.speed) / 38);
    const target = Math.max(-1, Math.min(1, steerIn)) * lim;
    c.steer += Math.max(-2.6 * dt, Math.min(2.6 * dt, target - c.steer));
    const wb = Math.max(2, this.wheelbase);
    const omega = c.speed / wb * Math.tan(c.steer);
    c.heading += omega * dt;
    c.x += -Math.sin(c.heading) * c.speed * dt;
    c.z += -Math.cos(c.heading) * c.speed * dt;
    c.spin += -c.speed * dt;
    const accel = (c.speed - prev) / Math.max(dt, 1e-4);
    c.pitch += (Math.max(-0.05, Math.min(0.05, accel * 0.0009)) - c.pitch) * Math.min(1, dt * 6);
    c.roll += (Math.max(-0.07, Math.min(0.07, -omega * c.speed * 0.0007)) - c.roll) * Math.min(1, dt * 6);
    if (this.hooks.onHud) this.hooks.onHud(Math.abs(c.speed));
  }

  // ---- drawing
  frame(now) {
    requestAnimationFrame((t) => this.frame(t));
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    if (!this.ok || !this.scene) return;
    if (this.drive) { this.step(dt); this.dirty = true; }
    else if (this.autoRotate) { this.yaw += dt * 0.35; this.dirty = true; }
    if (!this.dirty) return;
    this.dirty = false;
    this.draw(dt);
  }
  resize() {
    const c = this.canvas, dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.fixedSize ? this.fixedSize[0] : Math.max(1, Math.round(c.clientWidth * dpr));
    const h = this.fixedSize ? this.fixedSize[1] : Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  }
  camera(dt = 1 / 60) {
    const c = this.car;
    if (this.drive) {
      let d = ((c.heading - c.camHeading + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      c.camHeading += d * (1 - Math.pow(0.92, dt * 60));
      c.camOffset *= Math.pow(0.96, dt * 60);
      const h = c.camHeading + c.camOffset;
      const dist = Math.min(this.dist, Math.max(this.carLen * 1.5, 12));
      const flat = Math.cos(this.pitch * 0.6 + 0.12) * dist;
      const eye = [c.x + Math.sin(h) * flat, this.carH * 0.9 + Math.sin(this.pitch * 0.6 + 0.12) * dist + 1, c.z + Math.cos(h) * flat];
      const ahead = this.carLen * 0.35;
      return { eye, tgt: [c.x - Math.sin(c.heading) * ahead, this.carH * 0.55, c.z - Math.cos(c.heading) * ahead] };
    }
    const cp = Math.cos(this.pitch);
    const t = this.target;
    return { eye: [t[0] + this.dist * Math.sin(this.yaw) * cp, t[1] + this.dist * Math.sin(this.pitch), t[2] + this.dist * Math.cos(this.yaw) * cp], tgt: t };
  }
  bindAttribs(prog, vbo, stride) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.enableVertexAttribArray(prog.a.pos); gl.vertexAttribPointer(prog.a.pos, 3, gl.FLOAT, false, stride, 0);
    if (prog.a.nrm >= 0) { gl.enableVertexAttribArray(prog.a.nrm); gl.vertexAttribPointer(prog.a.nrm, 3, gl.FLOAT, false, stride, 12); }
    if (prog.a.uv >= 0) { gl.enableVertexAttribArray(prog.a.uv); gl.vertexAttribPointer(prog.a.uv, 2, gl.FLOAT, false, stride, 24); }
  }
  carMatrix() {
    const c = this.car;
    if (!this.drive) return M4.ident();
    return M4.mul(M4.translate(c.x, 0, c.z), M4.mul(M4.rotY(c.heading), M4.mul(M4.rotX(c.pitch), M4.rotZ(c.roll))));
  }
  wheelMatrix(i, carM) {
    const w = this.scene.wheels[i], f = this.scene.flags[i], c = this.car;
    if (!this.drive) return carM;
    const [hx, hy, hz] = w.hub;
    let m = M4.mul(M4.rotX(c.spin / Math.max(w.radius, 0.1)), M4.translate(-hx, -hy, -hz));
    if (f && f[0]) m = M4.mul(M4.rotY(c.steer), m);
    return M4.mul(carM, M4.mul(M4.translate(hx, hy, hz), m));
  }
  setMode(mode) {
    this.mode = mode === 'wire' || mode === 'pieces' ? mode : 'shaded';
    this.dirty = true;
  }
  // edge list of a piece for the wireframe view, built the first time it is needed
  lineBuffer(o) {
    if (o.lines) return o.lines;
    const gl = this.gl, t = o.tri, nv = o.wide ? 4294967295 : 65535, seen = new Set(), out = [];
    for (let i = 0; i < t.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const a = t[i + k], b = t[i + (k + 1) % 3], key = a < b ? a * (nv + 1) + b : b * (nv + 1) + a;
        if (seen.has(key)) continue;
        seen.add(key); out.push(a, b);
      }
    }
    const ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, o.wide ? Uint32Array.from(out) : Uint16Array.from(out), gl.STATIC_DRAW);
    o.lines = { ibo, count: out.length };
    return o.lines;
  }
  pieceColor(o) {
    const base = hexToRgb(KIND_COLORS[o.kind] || KIND_COLORS.other), k = 0.84 + 0.16 * (((o.index * 37) % 7) / 6);
    return [base[0] * k, base[1] * k, base[2] * k, o.kind === 'glass' ? 0.55 : 1];
  }
  draw(dt = 1 / 60) {
    const gl = this.gl, col = this.colors, mode = this.mode;
    this.resize();
    const W = this.canvas.width, H = this.canvas.height;
    gl.viewport(0, 0, W, H);
    gl.clearColor(col.bg[0], col.bg[1], col.bg[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const { eye, tgt } = this.camera(dt);
    const aspect = W / H, FOVY = 0.8;
    const P = M4.persp(FOVY, aspect, 0.3, 4000), V = M4.lookAt(eye, tgt, [0, 1, 0]);
    const VP = M4.mul(P, V);
    // backdrop: sky + horizon glow + grid floor
    {
      const b = this.back;
      let fx = tgt[0] - eye[0], fy = tgt[1] - eye[1], fz = tgt[2] - eye[2];
      let l = Math.hypot(fx, fy, fz) || 1; fx /= l; fy /= l; fz /= l;
      let rx = fy * 0 - fz * 1, ry = fz * 0 - fx * 0, rz = fx * 1 - fy * 0;        // forward x up(0,1,0)
      l = Math.hypot(rx, ry, rz) || 1; rx /= l; ry /= l; rz /= l;
      const ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;  // right x forward
      gl.disable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND);
      gl.useProgram(b.p);
      gl.uniform3f(b.u.uRight, rx, ry, rz); gl.uniform3f(b.u.uUp, ux, uy, uz); gl.uniform3f(b.u.uFwd, fx, fy, fz);
      gl.uniform3fv(b.u.uEye, eye);
      gl.uniform3fv(b.u.uTop, col.top); gl.uniform3fv(b.u.uMid, col.mid); gl.uniform3fv(b.u.uHor, col.hor); gl.uniform3fv(b.u.uGlow, col.glow);
      gl.uniform3fv(b.u.uGround, col.ground); gl.uniform3fv(b.u.uGroundFar, col.groundFar); gl.uniform3fv(b.u.uGrid, col.grid);
      gl.uniform1f(b.u.uTanY, Math.tan(FOVY / 2)); gl.uniform1f(b.u.uAspect, aspect); gl.uniform2f(b.u.uPixClip, 2 / W, 2 / H);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.backBuf);
      gl.enableVertexAttribArray(b.a.p); gl.vertexAttribPointer(b.a.p, 2, gl.FLOAT, false, 8, 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.disableVertexAttribArray(b.a.p);
    }
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
    // cars
    const m = this.main;
    gl.useProgram(m.p);
    gl.uniformMatrix4fv(m.u.uVP, false, VP);
    gl.uniform3fv(m.u.uSky, col.sky); gl.uniform3fv(m.u.uGnd, col.gnd); gl.uniform3f(m.u.uSun, -0.45, 0.85, 0.35);
    gl.uniform1i(m.u.uTex, 0);
    const carM = this.carMatrix();
    // shadow + front arrow
    gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.disable(gl.CULL_FACE); gl.depthMask(false);
    gl.uniform1f(m.u.uUnlit, 1); gl.uniform1f(m.u.uHasTex, 0); gl.uniform1f(m.u.uAlphaCut, 0);
    gl.uniformMatrix4fv(m.u.uModel, false, this.drive ? M4.mul(M4.translate(this.car.x, 0, this.car.z), M4.rotY(this.car.heading)) : M4.ident());
    gl.uniform4f(m.u.uColor, 0, 0, 0, 0.28);
    this.bindAttribs(m, this.shadow.buf, 32); gl.drawArrays(gl.TRIANGLES, 0, this.shadow.n);
    if (!this.drive && !this.noArrow) {
      gl.uniformMatrix4fv(m.u.uModel, false, M4.ident());
      gl.uniform4f(m.u.uColor, col.accent[0], col.accent[1], col.accent[2], 0.9);
      this.bindAttribs(m, this.arrow.buf, 32); gl.drawArrays(gl.TRIANGLES, 0, this.arrow.n);
    }
    gl.depthMask(true); gl.uniform1f(m.u.uUnlit, 0);
    const isGlass = (o) => (mode === 'pieces' ? o.kind === 'glass' : o.mat.transparent);
    const opaque = [], blend = [];
    for (const o of this.gpu) (isGlass(o) ? blend : opaque).push(o);
    const modelOf = (o) => (o.wheel >= 0 ? this.wheelMatrix(o.wheel, carM) : carM);
    const drawList = (list, transparent) => {
      if (transparent) { gl.enable(gl.BLEND); gl.depthMask(false); } else { gl.disable(gl.BLEND); gl.depthMask(true); }
      for (const o of list) {
        gl.uniformMatrix4fv(m.u.uModel, false, modelOf(o));
        const c = mode === 'pieces' ? this.pieceColor(o) : o.mat.color, tex = mode === 'shaded' && o.tex;
        gl.uniform4f(m.u.uColor, c[0], c[1], c[2], c[3]);
        gl.uniform1f(m.u.uHasTex, tex ? 1 : 0);
        gl.uniform1f(m.u.uAlphaCut, mode === 'shaded' ? o.alphaCut : 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, tex ? this.textures.get(o.tex) : this.white);
        if (o.mat.doubleSided) gl.disable(gl.CULL_FACE); else { gl.enable(gl.CULL_FACE); gl.cullFace(gl.BACK); }
        this.bindAttribs(m, o.vbo, 32);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, o.ibo);
        gl.drawElements(gl.TRIANGLES, o.count, o.type, 0);
      }
    };
    const sortFar = (list) => list.sort((a, b) => {
      const da = Math.hypot(a.center[0] - eye[0], a.center[1] - eye[1], a.center[2] - eye[2]);
      const db = Math.hypot(b.center[0] - eye[0], b.center[1] - eye[1], b.center[2] - eye[2]);
      return db - da;
    });
    if (mode === 'wire') {
      // hidden lines are removed by filling the surfaces with the scene colour first
      gl.uniform1f(m.u.uUnlit, 1); gl.uniform1f(m.u.uHasTex, 0); gl.uniform1f(m.u.uAlphaCut, 0);
      gl.disable(gl.BLEND); gl.depthMask(true); gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
      const fill = [col.bg[0] * 0.92 + col.wire[0] * 0.08, col.bg[1] * 0.92 + col.wire[1] * 0.08, col.bg[2] * 0.92 + col.wire[2] * 0.08];
      gl.disable(gl.CULL_FACE);
      for (const o of opaque) {
        gl.uniformMatrix4fv(m.u.uModel, false, modelOf(o));
        gl.uniform4f(m.u.uColor, fill[0], fill[1], fill[2], 1);
        this.bindAttribs(m, o.vbo, 32);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, o.ibo);
        gl.drawElements(gl.TRIANGLES, o.count, o.type, 0);
      }
      gl.disable(gl.POLYGON_OFFSET_FILL);
      gl.enable(gl.BLEND);
      gl.uniform4f(m.u.uColor, col.wire[0], col.wire[1], col.wire[2], 0.9);
      for (const o of this.gpu) {
        const ln = this.lineBuffer(o);
        gl.uniformMatrix4fv(m.u.uModel, false, modelOf(o));
        this.bindAttribs(m, o.vbo, 32);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ln.ibo);
        gl.drawElements(gl.LINES, ln.count, o.type, 0);
      }
    } else {
      drawList(opaque, false);
      drawList(sortFar(blend), true);
    }
    gl.depthMask(true); gl.disable(gl.BLEND);
  }

  // A still picture of the car. Draws once at the given size (the page layout is not affected) and returns PNG bytes.
  //   hero: use the default 3/4 view and shaded mode, no front arrow, not the driving pose (for preview.png in the zip)
  async snapshot({ width = 960, height = 600, hero = false } = {}) {
    if (!this.ok || !this.scene) return null;
    const keep = { drive: this.drive, yaw: this.yaw, pitch: this.pitch, dist: this.dist, target: this.target.slice(), mode: this.mode, noArrow: this.noArrow, fixed: this.fixedSize, car: this.car };
    let pending;
    try {
      if (hero) { this.drive = false; this.resetCamera(); this.mode = 'shaded'; this.noArrow = true; }
      this.fixedSize = [width, height];
      this.draw();
      pending = new Promise((res) => this.canvas.toBlob(res, 'image/png'));   // the bitmap is copied right here
    } finally {
      Object.assign(this, { drive: keep.drive, yaw: keep.yaw, pitch: keep.pitch, dist: keep.dist, target: keep.target, mode: keep.mode, noArrow: keep.noArrow, fixedSize: keep.fixed, car: keep.car });
      this.dirty = true;
    }
    const blob = await pending;
    return blob ? new Uint8Array(await blob.arrayBuffer()) : null;
  }
}
