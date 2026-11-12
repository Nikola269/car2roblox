// ===================================================================== zip reader/writer + file store
class BytesSource {
  constructor(u8) { this.u8 = u8; this.size = u8.length; }
  async read(off, len) { return this.u8.subarray(off, off + len); }
}
class BlobSource {
  constructor(blob) { this.blob = blob; this.size = blob.size; }
  async read(off, len) { return new Uint8Array(await this.blob.slice(off, off + len).arrayBuffer()); }
}
function decodeZipName(raw) {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(raw); }
  catch (e) { return new TextDecoder('latin1').decode(raw); }
}

// Opens a zip from a random-access source. Only the central directory is read up front.
async function openZip(src) {
  const size = src.size;
  if (size < 22) throw new ConversionError('this is not a zip file (too small)');
  const tailLen = Math.min(size, 22 + 65535 + 24);
  const tail = await src.read(size - tailLen, tailLen);
  const td = dv(tail);
  let e = -1;
  for (let i = tail.length - 22; i >= 0; i--) if (td.getUint32(i, true) === 0x06054B50) { e = i; break; }
  if (e < 0) throw new ConversionError('this is not a zip file (no end-of-archive record)');
  let count = td.getUint16(e + 10, true), cdSize = td.getUint32(e + 12, true), cdOff = td.getUint32(e + 16, true);
  if (count === 0xFFFF || cdSize === 0xFFFFFFFF || cdOff === 0xFFFFFFFF) {
    if (e >= 20 && td.getUint32(e - 20, true) === 0x07064B50) {            // zip64
      const z64off = Number(td.getBigUint64(e - 20 + 8, true));
      const rec = dv(await src.read(z64off, 56));
      if (rec.getUint32(0, true) !== 0x06064B50) throw new ConversionError('damaged zip64 directory');
      count = Number(rec.getBigUint64(32, true));
      cdSize = Number(rec.getBigUint64(40, true));
      cdOff = Number(rec.getBigUint64(48, true));
    }
  }
  const cd = await src.read(cdOff, cdSize);
  const d = dv(cd);
  const entries = [];
  let p = 0;
  for (let i = 0; i < count && p + 46 <= cd.length; i++) {
    if (d.getUint32(p, true) !== 0x02014B50) throw new ConversionError('damaged zip directory');
    const flags = d.getUint16(p + 8, true), method = d.getUint16(p + 10, true);
    let csize = d.getUint32(p + 20, true), usize = d.getUint32(p + 24, true), lho = d.getUint32(p + 42, true);
    const nl = d.getUint16(p + 28, true), xl = d.getUint16(p + 30, true), cl = d.getUint16(p + 32, true);
    const name = decodeZipName(cd.subarray(p + 46, p + 46 + nl));
    if (usize === 0xFFFFFFFF || csize === 0xFFFFFFFF || lho === 0xFFFFFFFF) {
      let q = p + 46 + nl;
      const end = q + xl;
      while (q + 4 <= end) {
        const id = d.getUint16(q, true), len = d.getUint16(q + 2, true);
        if (id === 1) {
          let r = q + 4;
          if (usize === 0xFFFFFFFF) { usize = Number(d.getBigUint64(r, true)); r += 8; }
          if (csize === 0xFFFFFFFF) { csize = Number(d.getBigUint64(r, true)); r += 8; }
          if (lho === 0xFFFFFFFF) { lho = Number(d.getBigUint64(r, true)); r += 8; }
        }
        q += 4 + len;
      }
    }
    entries.push({ name, method, csize, usize, offset: lho, encrypted: (flags & 1) !== 0, dir: name.endsWith('/') });
    p += 46 + nl + xl + cl;
  }
  return {
    entries,
    async read(ent) {
      if (ent.encrypted) throw new ConversionError(`'${ent.name}' is password protected`);
      if (ent.usize > 1.9e9) throw new ConversionError(`'${ent.name}' is too large to open in a browser`);
      const lh = dv(await src.read(ent.offset, 30));
      if (lh.getUint32(0, true) !== 0x04034B50) throw new ConversionError('damaged zip entry');
      const start = ent.offset + 30 + lh.getUint16(26, true) + lh.getUint16(28, true);
      const raw = await src.read(start, ent.csize);
      if (ent.method === 0) return raw;
      if (ent.method === 8) return inflateRaw(raw);
      throw new ConversionError(`'${ent.name}' uses an unsupported zip compression (${ent.method})`);
    },
  };
}

// files: [{ name, data: Uint8Array, store?: bool }] -> Blob
async function buildZip(files) {
  const parts = [], central = [];
  let offset = 0;
  const now = new Date();
  const dosTime = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const dosDate = (((now.getFullYear() - 1980) & 127) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  const enc = new TextEncoder();
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    let data = f.data, method = 0;
    if (!f.store && f.data.length > 128) {
      const z = await deflateRaw(f.data);
      if (z.length < f.data.length * 0.95) { data = z; method = 8; }
    }
    const lh = new Uint8Array(30 + name.length), d = dv(lh);
    d.setUint32(0, 0x04034B50, true); d.setUint16(4, 20, true); d.setUint16(6, 0x0800, true);
    d.setUint16(8, method, true); d.setUint16(10, dosTime, true); d.setUint16(12, dosDate, true);
    d.setUint32(14, crc, true); d.setUint32(18, data.length, true); d.setUint32(22, f.data.length, true);
    d.setUint16(26, name.length, true);
    lh.set(name, 30);
    const ch = new Uint8Array(46 + name.length), c = dv(ch);
    c.setUint32(0, 0x02014B50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
    c.setUint16(10, method, true); c.setUint16(12, dosTime, true); c.setUint16(14, dosDate, true);
    c.setUint32(16, crc, true); c.setUint32(20, data.length, true); c.setUint32(24, f.data.length, true);
    c.setUint16(28, name.length, true); c.setUint32(42, offset, true);
    ch.set(name, 46);
    central.push(ch);
    parts.push(lh, data);
    offset += lh.length + data.length;
  }
  let cdSize = 0;
  for (const c of central) { parts.push(c); cdSize += c.length; }
  const end = new Uint8Array(22), e = dv(end);
  e.setUint32(0, 0x06054B50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true);
  e.setUint32(12, cdSize, true); e.setUint32(16, offset, true);
  parts.push(end);
  if (offset + cdSize > 0xFFFFFFFF) throw new ConversionError('the output is larger than 4 GB');
  return new Blob(parts, { type: 'application/zip' });
}

// ---------------------------------------------------------------- Store: case-insensitive view over files
const IMG_EXT = ['.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp'];
class Store {
  // entries: [{ path, read: async () => Uint8Array }]
  constructor(entries) {
    this.index = new Map();                       // lower-case posix path -> entry
    for (const e of entries) {
      const rel = e.path.replace(/\\/g, '/').replace(/^\.?\//, '');
      if (!rel || rel.endsWith('/')) continue;
      this.index.set(rel.toLowerCase(), { path: rel, read: e.read });
    }
    this.byBase = new Map();
    for (const low of this.index.keys()) {
      const b = low.split('/').pop();
      if (!this.byBase.has(b)) this.byBase.set(b, []);
      this.byBase.get(b).push(low);
    }
    this.prefixes = new Set(['']);
  }
  async read(low) {
    const e = this.index.get(low);
    if (!e) throw new Error(`missing ${low}`);
    return e.read();
  }
  paths() { return [...this.index.values()].map((e) => e.path); }
  // Resolve a resource path (rooted at the game's data folder) to a stored file.
  find(ref) {
    const p = ref.replace(/\\/g, '/').trim().replace(/^\/+/, '').toLowerCase();
    if (!p) return null;
    const m = /^(.*?)(\.[^./]*)?$/.exec(p);
    const stem = m[1], ext = m[2] || '';
    const variants = [p, ...IMG_EXT.filter((e) => e !== ext).map((e) => stem + e)];
    for (const pre of this.prefixes) for (const v of variants) if (this.index.has(pre + v)) return pre + v;
    const base = p.split('/').pop();
    const stemBase = stem.split('/').pop();
    for (const b of [base, ...IMG_EXT.map((e) => stemBase + e)]) {
      const hits = this.byBase.get(b);
      if (hits && hits.length) return [...hits].sort((a, c) => a.length - c.length)[0];
    }
    return null;
  }
}
async function storeFromZip(src) {
  const z = await openZip(src);
  const entries = z.entries.filter((e) => !e.dir).map((ent) => ({ path: ent.name, read: () => z.read(ent) }));
  return new Store(entries);
}
