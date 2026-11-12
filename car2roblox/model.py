"""Core data model shared by every importer and exporter.

Conventions used *inside* the program once an importer has produced a ``Model``:

* UV coordinates are stored in **image space** (v = 0 is the TOP row of the texture).
  GTA (DirectX) and glTF already use that; OBJ / Collada use v-up and are flipped on
  import/export.
* Triangles are counter-clockwise when seen from the front (outside) face.
* Positions are in the importer's *source* axes and units until the pipeline converts
  them to Roblox axes (X right, Y up, -Z forward) and studs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


class ConversionError(Exception):
    """Raised for unreadable / unsupported input. The message is shown to the user."""


# --------------------------------------------------------------------------- textures
@dataclass
class Texture:
    name: str
    image: "object"  # PIL.Image.Image, RGBA
    _alpha: Optional[bool] = field(default=None, repr=False)

    @property
    def has_alpha(self) -> bool:
        """True when any pixel is not fully opaque."""
        if self._alpha is None:
            a = np.asarray(self.image.convert("RGBA"))[..., 3]
            self._alpha = bool(a.size and a.min() < 250)
        return self._alpha


@dataclass
class Material:
    name: str
    color: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)  # sRGB 0..1
    texture: Optional[str] = None      # key into Model.textures (lower-case)
    paint: Optional[str] = None        # 'primary' | 'secondary' | 'tertiary' | 'quaternary'
    transparent: bool = False
    double_sided: bool = False

    def key(self) -> tuple:
        return (self.name, tuple(round(c, 4) for c in self.color), self.texture,
                self.paint, self.transparent, self.double_sided)


# --------------------------------------------------------------------------- meshes
def _pack_keys(cols: Sequence[np.ndarray]) -> np.ndarray:
    """Pack several non-negative index columns into a single int64 key when possible."""
    maxes = [int(c.max()) + 1 if c.size else 1 for c in cols]
    total_bits = sum(int(m).bit_length() for m in maxes)
    if total_bits <= 62:
        key = np.zeros(cols[0].shape, dtype=np.int64)
        for c, m in zip(cols, maxes):
            key = key * np.int64(m) + c.astype(np.int64)
        return key
    return np.stack(cols, axis=1)


@dataclass
class Mesh:
    positions: np.ndarray                    # (N,3) float32
    triangles: np.ndarray                    # (M,3) int32
    material_ids: np.ndarray                 # (M,)  int32  -> Model.materials
    normals: Optional[np.ndarray] = None     # (N,3) float32
    uvs: Optional[np.ndarray] = None         # (N,2) float32, image space

    def __post_init__(self) -> None:
        self.positions = np.ascontiguousarray(self.positions, dtype=np.float32).reshape(-1, 3)
        self.triangles = np.ascontiguousarray(self.triangles, dtype=np.int32).reshape(-1, 3)
        self.material_ids = np.ascontiguousarray(self.material_ids, dtype=np.int32).reshape(-1)
        if self.normals is not None:
            self.normals = np.ascontiguousarray(self.normals, dtype=np.float32).reshape(-1, 3)
            if len(self.normals) != len(self.positions):
                self.normals = None
        if self.uvs is not None:
            self.uvs = np.ascontiguousarray(self.uvs, dtype=np.float32).reshape(-1, 2)
            if len(self.uvs) != len(self.positions):
                self.uvs = None
        if len(self.material_ids) != len(self.triangles):
            raise ValueError("material_ids must have one entry per triangle")
        if len(self.triangles) and (self.triangles.min() < 0 or
                                    self.triangles.max() >= len(self.positions)):
            raise ValueError("triangle index out of range")

    # -- basic info
    @property
    def tri_count(self) -> int:
        return int(len(self.triangles))

    @property
    def vert_count(self) -> int:
        return int(len(self.positions))

    def bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        if not len(self.positions):
            z = np.zeros(3, dtype=np.float32)
            return z, z
        return self.positions.min(axis=0), self.positions.max(axis=0)

    def copy(self) -> "Mesh":
        return Mesh(self.positions.copy(), self.triangles.copy(), self.material_ids.copy(),
                    None if self.normals is None else self.normals.copy(),
                    None if self.uvs is None else self.uvs.copy())

    # -- builders
    @staticmethod
    def empty() -> "Mesh":
        return Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int32),
                    np.zeros((0,), np.int32))

    @staticmethod
    def build(pos: np.ndarray, ptri: np.ndarray, material_ids,
              uv: Optional[np.ndarray] = None, utri: Optional[np.ndarray] = None,
              nrm: Optional[np.ndarray] = None, ntri: Optional[np.ndarray] = None) -> "Mesh":
        """Build a mesh from attribute arrays that each have their *own* index list
        (OBJ / Collada style). Corners are de-duplicated into unified vertices."""
        pos = np.asarray(pos, dtype=np.float32).reshape(-1, 3)
        ptri = np.asarray(ptri, dtype=np.int64).reshape(-1, 3)
        material_ids = np.broadcast_to(np.asarray(material_ids, dtype=np.int32),
                                       (len(ptri),)).copy()
        if len(ptri) == 0:
            return Mesh.empty()
        cols = [ptri.reshape(-1)]
        use_uv = uv is not None and utri is not None and len(uv) > 0
        use_n = nrm is not None and ntri is not None and len(nrm) > 0
        if use_uv:
            cols.append(np.asarray(utri, dtype=np.int64).reshape(-1))
        if use_n:
            cols.append(np.asarray(ntri, dtype=np.int64).reshape(-1))
        # corners with a missing attribute index (-1) are mapped to 0
        cols = [np.maximum(c, 0) for c in cols]
        key = _pack_keys(cols) if len(cols) > 1 else cols[0]
        if key.ndim == 1:
            uniq, first, inverse = np.unique(key, return_index=True, return_inverse=True)
            inverse = inverse.reshape(-1)
            rep = np.stack([c[first] for c in cols], axis=1)
        else:
            uniq, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
            inverse = inverse.reshape(-1)
            rep = np.stack([c[first] for c in cols], axis=1)
        positions = pos[rep[:, 0]]
        i = 1
        uvs = normals = None
        if use_uv:
            uvs = np.asarray(uv, dtype=np.float32).reshape(-1, np.asarray(uv).shape[-1])[:, :2][rep[:, i]]
            i += 1
        if use_n:
            normals = np.asarray(nrm, dtype=np.float32).reshape(-1, 3)[rep[:, i]]
        return Mesh(positions, inverse.reshape(-1, 3).astype(np.int32), material_ids, normals, uvs)

    # -- operations
    def transformed(self, matrix: np.ndarray, translation=(0, 0, 0)) -> "Mesh":
        """Return ``x' = matrix @ x + translation``. Flips winding for mirroring matrices."""
        A = np.asarray(matrix, dtype=np.float64).reshape(3, 3)
        t = np.asarray(translation, dtype=np.float64).reshape(3)
        out = self.copy()
        out.positions = (self.positions.astype(np.float64) @ A.T + t).astype(np.float32)
        if self.normals is not None:
            try:
                n = self.normals.astype(np.float64) @ np.linalg.inv(A)  # (A^-T n) == n @ A^-1
            except np.linalg.LinAlgError:
                n = self.normals.astype(np.float64) @ A.T
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            ln[ln == 0] = 1.0
            out.normals = (n / ln).astype(np.float32)
        if np.linalg.det(A) < 0:
            out.triangles = out.triangles[:, ::-1].copy()
        return out

    def flipped(self) -> "Mesh":
        out = self.copy()
        out.triangles = out.triangles[:, ::-1].copy()
        if out.normals is not None:
            out.normals = -out.normals
        return out

    def select(self, mask_or_idx) -> "Mesh":
        """Keep only the given triangles and drop unused vertices."""
        tris = self.triangles[mask_or_idx]
        mats = self.material_ids[mask_or_idx]
        if len(tris) == 0:
            return Mesh.empty()
        used, inv = np.unique(tris.reshape(-1), return_inverse=True)
        return Mesh(self.positions[used], inv.reshape(-1, 3).astype(np.int32), mats,
                    None if self.normals is None else self.normals[used],
                    None if self.uvs is None else self.uvs[used])

    def compact(self) -> "Mesh":
        return self.select(slice(None))

    def drop_degenerate(self) -> "Mesh":
        t = self.triangles
        ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        if ok.all():
            return self
        return self.select(ok)

    def split_by_material(self) -> List[Tuple[int, "Mesh"]]:
        out = []
        for mid in np.unique(self.material_ids):
            out.append((int(mid), self.select(self.material_ids == mid)))
        return out

    @staticmethod
    def concat(meshes: Sequence["Mesh"]) -> "Mesh":
        meshes = [m for m in meshes if m.tri_count]
        if not meshes:
            return Mesh.empty()
        has_n = all(m.normals is not None for m in meshes)
        has_uv = all(m.uvs is not None for m in meshes)
        pos, tri, mat, nrm, uv = [], [], [], [], []
        base = 0
        for m in meshes:
            pos.append(m.positions)
            tri.append(m.triangles + base)
            mat.append(m.material_ids)
            if has_n:
                nrm.append(m.normals)
            if has_uv:
                uv.append(m.uvs)
            base += m.vert_count
        return Mesh(np.concatenate(pos), np.concatenate(tri), np.concatenate(mat),
                    np.concatenate(nrm) if has_n else None,
                    np.concatenate(uv) if has_uv else None)

    def face_normals(self) -> np.ndarray:
        p = self.positions.astype(np.float64)
        t = self.triangles
        return np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])

    def recompute_normals(self) -> "Mesh":
        """Smooth, area-weighted normals; vertices at identical positions are welded
        for the calculation so UV seams do not show up as hard edges."""
        out = self.copy()
        if self.tri_count == 0:
            out.normals = np.zeros_like(self.positions)
            return out
        fn = self.face_normals()
        q = np.round(self.positions.astype(np.float64) * 1e4).astype(np.int64)
        _, weld = np.unique(q, axis=0, return_inverse=True)
        weld = weld.reshape(-1)
        acc = np.zeros((weld.max() + 1, 3))
        for k in range(3):
            np.add.at(acc, weld[self.triangles[:, k]], fn)
        n = acc[weld]
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        out.normals = (n / ln).astype(np.float32)
        return out


# --------------------------------------------------------------------------- axes
_AXIS = {"x": 0, "y": 1, "z": 2}


def parse_axis(token: str) -> np.ndarray:
    """'+x', '-y', 'z' -> unit vector."""
    t = token.strip().lower()
    sign = -1.0 if t.startswith("-") else 1.0
    t = t.lstrip("+-")
    if t not in _AXIS:
        raise ConversionError(f"bad axis '{token}' (use x, y or z with an optional sign)")
    v = np.zeros(3)
    v[_AXIS[t]] = sign
    return v


@dataclass(frozen=True)
class AxisSpec:
    """Which source axis points to the car's right / up / forward."""
    right: str = "+x"
    up: str = "+y"
    forward: str = "-z"

    def validate(self) -> None:
        vs = [parse_axis(a) for a in (self.right, self.up, self.forward)]
        axes = {int(np.argmax(np.abs(v))) for v in vs}
        if len(axes) != 3:
            raise ConversionError(
                f"right/up/forward must use three different axes (got {self.right}, "
                f"{self.up}, {self.forward})")

    def to_roblox(self) -> np.ndarray:
        """3x3 matrix R with ``p_roblox = R @ p_source`` (Roblox: +X right, +Y up, -Z forward)."""
        self.validate()
        r, u, f = (parse_axis(a) for a in (self.right, self.up, self.forward))
        return np.stack([r, u, -f])

    def with_flipped_forward(self) -> "AxisSpec":
        """Reverse front/back by turning the car 180 degrees about the vertical axis."""
        flip = lambda a: a[1:] if a.startswith("-") else "-" + a.lstrip("+")
        return AxisSpec(flip(self.right), self.up, flip(self.forward))


# --------------------------------------------------------------------------- model
@dataclass
class Part:
    name: str
    mesh: Mesh
    wheel_slot: Optional[str] = None   # 'FL' 'FR' 'RL' 'RR' 'ML' 'MR' when known
    kind_hint: Optional[str] = None    # importer's opinion: 'wheel', 'glass', ...


@dataclass
class WheelHint:
    """Where the source file says a wheel is (BeamNG JBeam hub nodes, GTA SA wheel dummies).
    Coordinates are in the model's own axes and units, like the part meshes."""
    center: Tuple[float, float, float]
    radius: Optional[float] = None
    width: Optional[float] = None
    slot: Optional[str] = None


@dataclass
class Model:
    name: str
    source: str                                # 'gta_sa' | 'beamng' | 'generic'
    parts: List[Part] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    textures: Dict[str, Texture] = field(default_factory=dict)
    axes: AxisSpec = field(default_factory=AxisSpec)
    unit_meters: float = 1.0                   # metres per source unit
    forward_auto: bool = False                 # let the pipeline find the forward axis
    default_paint: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    wheel_hints: List[WheelHint] = field(default_factory=list)

    def add_material(self, m: Material) -> int:
        k = m.key()
        for i, e in enumerate(self.materials):
            if e.key() == k:
                return i
        self.materials.append(m)
        return len(self.materials) - 1

    def tri_count(self) -> int:
        return sum(p.mesh.tri_count for p in self.parts)


# --------------------------------------------------------------------------- pipeline output
@dataclass
class Piece:
    """One exported MeshPart: a single material, already in Roblox axes and studs."""
    name: str                       # unique, [A-Za-z0-9_]
    part: str                       # source part name
    kind: str                       # body | wheel | glass | interior | light | other
    mesh: Mesh
    material: Material
    wheel_slot: Optional[str] = None

    def bbox(self) -> Tuple[np.ndarray, np.ndarray]:
        return self.mesh.bounds()

    @property
    def center(self) -> np.ndarray:
        lo, hi = self.bbox()
        return (lo + hi) / 2.0

    @property
    def size(self) -> np.ndarray:
        lo, hi = self.bbox()
        return hi - lo


@dataclass
class WheelGroup:
    slot: str                       # FL FR RL RR ML MR (or W1.. when unknown)
    hub: Tuple[float, float, float]
    radius: float
    width: float
    pieces: List[str]
    primary: str
    front: bool = False
