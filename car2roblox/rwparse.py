"""Parser for RenderWare binary streams used by GTA III / Vice City / San Andreas:
``.dff`` (models) and ``.txd`` (texture dictionaries).

Layouts follow the public RenderWare documentation and were cross-checked against the
open-source DragonFF Blender add-on (triangle order, frame struct, native texture header).
Only PC (Direct3D 8 / 9) data is supported; console-native geometry/textures raise a clear
error.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image

from .model import ConversionError
from .textures import decode_dxt

# chunk ids
ID_STRUCT, ID_STRING, ID_EXTENSION = 0x01, 0x02, 0x03
ID_TEXTURE, ID_MATERIAL, ID_MATLIST = 0x06, 0x07, 0x08
ID_FRAMELIST, ID_GEOMETRY, ID_CLUMP = 0x0E, 0x0F, 0x10
ID_ATOMIC, ID_TEXNATIVE, ID_TEXDICT = 0x14, 0x15, 0x16
ID_GEOMLIST = 0x1A
ID_BINMESH = 0x50E
ID_FRAMENAME = 0x253F2FE

GEO_TRISTRIP, GEO_TEXTURED, GEO_PRELIT = 0x1, 0x4, 0x8
GEO_NORMALS, GEO_TEXTURED2, GEO_NATIVE = 0x10, 0x80, 0x01000000
LOCK_FLAG = 0x01000000


class RWError(ConversionError):
    pass


@dataclass
class Chunk:
    type: int
    size: int
    version: int       # decoded RenderWare version (e.g. 0x36003)
    start: int         # offset of chunk *data*
    @property
    def end(self) -> int:
        return self.start + self.size


def rw_version(lib_id: int) -> int:
    if lib_id & 0xFFFF0000:
        return ((lib_id >> 14) & 0x3FF00) + 0x30000 | ((lib_id >> 16) & 0x3F)
    return lib_id << 8


def read_chunk(buf: bytes, off: int, limit: int) -> Chunk:
    if off + 12 > limit:
        raise RWError("unexpected end of data (truncated file?)")
    t, size, lib = struct.unpack_from("<III", buf, off)
    start = off + 12
    # some modded files set a "lock" bit in the size of Struct/Texture chunks
    if size > limit - start and t in (ID_STRUCT, ID_TEXTURE) and (size & ~LOCK_FLAG) <= limit - start:
        size &= ~LOCK_FLAG
    if size > limit - start:
        size = limit - start      # be forgiving about slightly broken sizes
    return Chunk(t, size, rw_version(lib), start)


def iter_chunks(buf: bytes, start: int, end: int) -> Iterator[Chunk]:
    off = start
    while off + 12 <= end:
        c = read_chunk(buf, off, end)
        yield c
        off = c.end


def read_string_chunk(buf: bytes, c: Chunk) -> str:
    raw = bytes(buf[c.start:c.end])
    raw = raw.split(b"\0", 1)[0]
    return raw.decode("latin-1")


# ===================================================================== DFF
@dataclass
class DffMaterial:
    color: Tuple[int, int, int, int] = (255, 255, 255, 255)
    texture: Optional[str] = None
    mask: Optional[str] = None


@dataclass
class DffGeometry:
    positions: np.ndarray
    normals: Optional[np.ndarray]
    uvs: Optional[np.ndarray]
    triangles: np.ndarray          # (M,3) CCW
    tri_materials: np.ndarray      # (M,)
    materials: List[DffMaterial]
    flags: int = 0


@dataclass
class DffFrame:
    name: str
    rotation: np.ndarray           # 3x3, column-vector convention (x' = R @ x + pos)
    position: np.ndarray
    parent: int


@dataclass
class Dff:
    frames: List[DffFrame] = field(default_factory=list)
    geometries: List[DffGeometry] = field(default_factory=list)
    atomics: List[Tuple[int, int]] = field(default_factory=list)   # (frame, geometry)

    def world(self, idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """World rotation/translation of a frame (composes the parent chain root-first)."""
        chain = []
        i = idx
        seen = set()
        while 0 <= i < len(self.frames) and i not in seen:
            seen.add(i)
            chain.append(self.frames[i])
            i = self.frames[i].parent
        R = np.eye(3)
        t = np.zeros(3)
        for f in reversed(chain):               # root first
            t = R @ f.position + t
            R = R @ f.rotation
        return R, t


def _parse_frame_list(buf: bytes, c: Chunk) -> List[DffFrame]:
    kids = list(iter_chunks(buf, c.start, c.end))
    if not kids or kids[0].type != ID_STRUCT:
        raise RWError("frame list without struct")
    s = kids[0]
    (count,) = struct.unpack_from("<I", buf, s.start)
    if s.start + 4 + count * 56 > len(buf):
        raise RWError("frame list is truncated")
    frames: List[DffFrame] = []
    for i in range(count):
        o = s.start + 4 + i * 56
        m = struct.unpack_from("<9f", buf, o)
        pos = struct.unpack_from("<3f", buf, o + 36)
        parent = struct.unpack_from("<i", buf, o + 48)[0]
        # file stores right, up, at vectors -> they are the *columns* of R
        R = np.array(m, dtype=np.float64).reshape(3, 3).T
        frames.append(DffFrame(f"frame{i}", R, np.array(pos, dtype=np.float64), parent))
    exts = [k for k in kids[1:] if k.type == ID_EXTENSION]
    for i, ext in enumerate(exts[:count]):
        for sub in iter_chunks(buf, ext.start, ext.end):
            if sub.type == ID_FRAMENAME:
                frames[i].name = read_string_chunk(buf, sub) or frames[i].name
    return frames


def _parse_texture(buf: bytes, c: Chunk) -> Tuple[Optional[str], Optional[str]]:
    name = mask = None
    strings = [k for k in iter_chunks(buf, c.start, c.end) if k.type == ID_STRING]
    if strings:
        name = read_string_chunk(buf, strings[0])
    if len(strings) > 1:
        mask = read_string_chunk(buf, strings[1])
    return name, mask


def _parse_material_list(buf: bytes, c: Chunk) -> List[DffMaterial]:
    kids = list(iter_chunks(buf, c.start, c.end))
    if not kids or kids[0].type != ID_STRUCT:
        return []
    (count,) = struct.unpack_from("<I", buf, kids[0].start)
    indices = list(struct.unpack_from(f"<{count}i", buf, kids[0].start + 4)) if count else []
    mats: List[DffMaterial] = []
    raw = [k for k in kids[1:] if k.type == ID_MATERIAL]
    ri = 0
    for idx in indices:
        if idx >= 0 and idx < len(mats):      # instanced (reuse an earlier material)
            mats.append(mats[idx])
            continue
        if ri >= len(raw):
            mats.append(DffMaterial())
            continue
        mc = raw[ri]
        ri += 1
        m = DffMaterial()
        for sub in iter_chunks(buf, mc.start, mc.end):
            if sub.type == ID_STRUCT and sub.size >= 16:
                _flags, r, g, b, a, _unused, _textured = struct.unpack_from("<I4BII", buf, sub.start)
                m.color = (r, g, b, a)
            elif sub.type == ID_TEXTURE:
                m.texture, m.mask = _parse_texture(buf, sub)
        mats.append(m)
    return mats


def _read_binmesh(buf: bytes, c: Chunk, num_verts: int) -> Tuple[np.ndarray, np.ndarray]:
    flags, mesh_count, total = struct.unpack_from("<III", buf, c.start)
    strip = flags == 1
    body = c.size - 12 - mesh_count * 8
    wide = body >= total * 4          # 32-bit indices on PC, 16-bit on some ports
    off = c.start + 12
    tris, mats = [], []
    for _ in range(mesh_count):
        n, mat = struct.unpack_from("<II", buf, off)
        off += 8
        dt = "<u4" if wide else "<u2"
        idx = np.frombuffer(buf, dtype=dt, count=n, offset=off).astype(np.int64)
        off += n * (4 if wide else 2)
        if strip:
            if n < 3:
                continue
            a, b, cc = idx[:-2], idx[1:-1], idx[2:]
            odd = (np.arange(len(a)) % 2) == 1
            t = np.stack([np.where(odd, b, a), np.where(odd, a, b), cc], axis=1)
            ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
            t = t[ok]
        else:
            t = idx[: (n // 3) * 3].reshape(-1, 3)
        tris.append(t)
        mats.append(np.full(len(t), mat, dtype=np.int64))
    if not tris:
        return np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    t = np.concatenate(tris)
    m = np.concatenate(mats)
    keep = (t < num_verts).all(axis=1)
    return t[keep], m[keep]


def _parse_geometry(buf: bytes, c: Chunk) -> DffGeometry:
    kids = list(iter_chunks(buf, c.start, c.end))
    if not kids or kids[0].type != ID_STRUCT:
        raise RWError("geometry without struct")
    s = kids[0]
    flags, ntri, nvert, _nmorph = struct.unpack_from("<IIII", buf, s.start)
    if flags & GEO_NATIVE:
        raise RWError("this DFF contains console-native geometry (PS2/Xbox); only PC DFFs "
                      "are supported")
    pos = s.start + 16
    if c.version < 0x34000:
        pos += 12                                     # ambient/diffuse/specular floats
    if pos > s.end:
        raise RWError("geometry struct is truncated")

    if flags & GEO_PRELIT:
        pos += 4 * nvert
    nsets = (flags >> 16) & 0xFF
    if nsets == 0:
        nsets = 2 if flags & GEO_TEXTURED2 else (1 if flags & GEO_TEXTURED else 0)
    uvs = None
    if nsets:
        uvs = np.frombuffer(buf, "<f4", count=nvert * 2, offset=pos).reshape(nvert, 2).copy()
        pos += nsets * nvert * 8
    tri_arr = np.zeros((0, 4), np.uint16)
    if ntri:
        tri_arr = np.frombuffer(buf, "<u2", count=ntri * 4, offset=pos).reshape(ntri, 4)
        pos += ntri * 8
    pos += 16                                          # bounding sphere
    has_v, has_n = struct.unpack_from("<II", buf, pos)
    pos += 8
    if not has_v:
        raise RWError("geometry has no vertices")
    positions = np.frombuffer(buf, "<f4", count=nvert * 3, offset=pos).reshape(nvert, 3).copy()
    pos += nvert * 12
    normals = None
    if has_n:
        normals = np.frombuffer(buf, "<f4", count=nvert * 3, offset=pos).reshape(nvert, 3).copy()

    # file order is (v[1], v[0], material, v[2])
    triangles = tri_arr[:, [1, 0, 3]].astype(np.int64) if ntri else np.zeros((0, 3), np.int64)
    tri_mats = tri_arr[:, 2].astype(np.int64) if ntri else np.zeros(0, np.int64)

    materials: List[DffMaterial] = []
    binmesh = None
    for k in kids[1:]:
        if k.type == ID_MATLIST:
            materials = _parse_material_list(buf, k)
        elif k.type == ID_EXTENSION:
            for sub in iter_chunks(buf, k.start, k.end):
                if sub.type == ID_BINMESH:
                    binmesh = sub
    if len(triangles) == 0 and binmesh is not None:
        triangles, tri_mats = _read_binmesh(buf, binmesh, nvert)
    if len(triangles) and triangles.max() >= nvert:
        keep = (triangles < nvert).all(axis=1)
        triangles, tri_mats = triangles[keep], tri_mats[keep]
    return DffGeometry(positions, normals, uvs, triangles, tri_mats, materials, flags)


def parse_dff(data: bytes) -> Dff:
    buf = data
    if len(buf) < 12:
        raise RWError("file is too small to be a DFF")
    top = read_chunk(buf, 0, len(buf))
    if top.type != ID_CLUMP:
        raise RWError("not a RenderWare DFF (no clump chunk at the start)")
    dff = Dff()
    for c in iter_chunks(buf, top.start, top.end):
        if c.type == ID_FRAMELIST:
            dff.frames = _parse_frame_list(buf, c)
        elif c.type == ID_GEOMLIST:
            for g in iter_chunks(buf, c.start, c.end):
                if g.type == ID_GEOMETRY:
                    dff.geometries.append(_parse_geometry(buf, g))
        elif c.type == ID_ATOMIC:
            kids = list(iter_chunks(buf, c.start, c.end))
            if kids and kids[0].type == ID_STRUCT:
                if kids[0].size >= 16:
                    frame, geom, _fl, _un = struct.unpack_from("<4I", buf, kids[0].start)
                else:
                    frame, _fl, _un = struct.unpack_from("<3I", buf, kids[0].start)
                    geom = 0
                dff.atomics.append((frame, geom))
    if not dff.geometries:
        raise RWError("the DFF has no geometry")
    return dff


# ===================================================================== TXD
def _decode_raw(pixels: bytes, w: int, h: int, fmt_type: int, depth: int,
                d3d_fmt: int, platform: int) -> np.ndarray:
    """Uncompressed pixel formats -> RGBA uint8 (h, w, 4)."""
    a = np.frombuffer(pixels, dtype=np.uint8)
    n = w * h

    def le16() -> np.ndarray:
        if len(pixels) < n * 2:
            raise RWError("truncated 16-bit texture")
        return np.frombuffer(pixels, "<u2", count=n).astype(np.uint32)

    def scale(v: np.ndarray, bits: int) -> np.ndarray:
        return (v * 255 // ((1 << bits) - 1)).astype(np.uint8)

    if platform == 9 and d3d_fmt:
        kind = {21: "8888", 22: "888", 23: "565", 24: "555", 25: "1555", 26: "4444",
                50: "L8", 51: "A8L8"}.get(d3d_fmt)
    else:
        kind = {0x1: "1555", 0x2: "565", 0x3: "4444", 0x4: "L8", 0x5: "8888",
                0x6: "888", 0xA: "555"}.get(fmt_type)
    if kind is None:
        raise RWError(f"unsupported raster format (type {fmt_type}, d3d {d3d_fmt})")

    out = np.zeros((n, 4), np.uint8)
    if kind in ("8888", "888"):
        if len(pixels) < n * 4:
            raise RWError("truncated 32-bit texture")
        b = a[: n * 4].reshape(n, 4)
        out[:, 0], out[:, 1], out[:, 2] = b[:, 2], b[:, 1], b[:, 0]
        out[:, 3] = b[:, 3] if kind == "8888" else 255
    elif kind == "565":
        v = le16()
        out[:, 0], out[:, 1], out[:, 2] = scale((v >> 11) & 31, 5), scale((v >> 5) & 63, 6), scale(v & 31, 5)
        out[:, 3] = 255
    elif kind == "555":
        v = le16()
        out[:, 0], out[:, 1], out[:, 2] = scale((v >> 10) & 31, 5), scale((v >> 5) & 31, 5), scale(v & 31, 5)
        out[:, 3] = 255
    elif kind == "1555":
        v = le16()
        out[:, 3] = ((v >> 15) & 1) * 255
        out[:, 0], out[:, 1], out[:, 2] = scale((v >> 10) & 31, 5), scale((v >> 5) & 31, 5), scale(v & 31, 5)
    elif kind == "4444":
        v = le16()
        out[:, 3] = scale((v >> 12) & 15, 4)
        out[:, 0], out[:, 1], out[:, 2] = scale((v >> 8) & 15, 4), scale((v >> 4) & 15, 4), scale(v & 15, 4)
    elif kind == "L8":
        l = a[:n]
        out[:, 0] = out[:, 1] = out[:, 2] = l
        out[:, 3] = 255
    elif kind == "A8L8":
        b = a[: n * 2].reshape(n, 2)
        out[:, 0] = out[:, 1] = out[:, 2] = b[:, 0]
        out[:, 3] = b[:, 1]
    return out.reshape(h, w, 4)


def parse_native_texture(buf: bytes, s: Chunk) -> Tuple[str, Image.Image]:
    o = s.start
    platform, _fil, _uv, _unk = struct.unpack_from("<I2BH", buf, o)
    if platform not in (8, 9):
        raise RWError(f"texture uses unsupported platform id {platform:#x} "
                      "(only PC Direct3D8/9 TXDs are supported)")
    name = buf[o + 8:o + 40].split(b"\0", 1)[0].decode("latin-1")
    raster_flags, d3d_fmt, w, h, depth, levels, _rtype = struct.unpack_from("<IIHHBBB", buf, o + 72)
    prop = buf[o + 87]
    p = o + 88
    fmt_type = (raster_flags >> 8) & 0xF
    pal_type = (raster_flags >> 13) & 0x3
    palette = b""
    if pal_type:
        pal_len = 1024 if pal_type == 1 else (64 if depth == 4 else 128)
        palette = bytes(buf[p:p + pal_len])
        p += pal_len
    (size,) = struct.unpack_from("<I", buf, p)
    pixels = bytes(buf[p + 4:p + 4 + size])

    if platform == 9:
        has_alpha = bool(prop & 1)
        fourcc = struct.pack("<I", d3d_fmt) if d3d_fmt > 0xFFFF else b""
    else:
        has_alpha = bool(d3d_fmt)
        fourcc = {1: b"DXT1", 2: b"DXT3", 3: b"DXT3", 4: b"DXT5", 5: b"DXT5"}.get(prop, b"")
    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        return name, decode_dxt(w, h, fourcc, pixels)
    if fourcc in (b"DXT2", b"DXT4"):
        return name, decode_dxt(w, h, b"DXT3" if fourcc == b"DXT2" else b"DXT5", pixels)

    if pal_type:
        n = w * h
        if pal_type == 1:
            idx = np.frombuffer(pixels, np.uint8, count=n)
        else:
            raw = np.frombuffer(pixels, np.uint8)
            hi, lo = (raw >> 4) & 0xF, raw & 0xF
            idx = np.stack([hi, lo], axis=1).reshape(-1)[:n]
        pal = np.frombuffer(palette, np.uint8).reshape(-1, 4)
        rgba = pal[np.minimum(idx, len(pal) - 1)].reshape(h, w, 4).copy()
        if not has_alpha:
            rgba[..., 3] = 255
    else:
        rgba = _decode_raw(pixels, w, h, fmt_type, depth, d3d_fmt if platform == 9 else 0, platform)
    return name, Image.fromarray(rgba, "RGBA")


def parse_txd(data: bytes) -> Dict[str, Image.Image]:
    """Return {lower-case texture name: RGBA image}."""
    buf = data
    top = read_chunk(buf, 0, len(buf))
    if top.type != ID_TEXDICT:
        raise RWError("not a RenderWare TXD (no texture dictionary chunk)")
    out: Dict[str, Image.Image] = {}
    errors: List[str] = []
    for c in iter_chunks(buf, top.start, top.end):
        if c.type != ID_TEXNATIVE:
            continue
        for k in iter_chunks(buf, c.start, c.end):
            if k.type == ID_STRUCT:
                try:
                    name, img = parse_native_texture(buf, k)
                    out[name.lower()] = img
                except RWError as exc:
                    errors.append(str(exc))
                except (struct.error, ValueError) as exc:
                    errors.append(f"damaged texture ({exc})")
                break
    if not out and errors:
        raise RWError(errors[0])
    return out
