"""Wheel helpers for the pipeline: repair wheel parts that came in fused or duplicated, find
wheels that carry no name, and build tyres + rims for models that have none.

Everything here works on finished ``Piece`` objects, i.e. after the model has been turned into
Roblox axes (X = right, Y = up, -Z = forward) and studs, with the lowest point of the car at y = 0.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .model import Material, Mesh, Piece, WheelHint

TIRE_MATERIAL = Material("tire_rubber", (0.055, 0.055, 0.060, 1.0))
RIM_MATERIAL = Material("rim_metal", (0.72, 0.74, 0.78, 1.0))

# (x / width, r / radius) outlines, outer side of the wheel at +x
_TIRE_PROFILE = [(-0.46, 0.60), (-0.50, 0.72), (-0.50, 0.88), (-0.42, 0.985), (-0.25, 1.0),
                 (0.25, 1.0), (0.42, 0.985), (0.50, 0.88), (0.50, 0.72), (0.46, 0.60),
                 (0.0, 0.57)]
_RIM_PROFILE = [(-0.36, 0.0), (-0.36, 0.50), (-0.44, 0.62), (0.44, 0.62), (0.48, 0.66),
                (0.40, 0.60), (0.30, 0.46), (0.26, 0.30), (0.34, 0.20), (0.34, 0.0)]


# --------------------------------------------------------------------------- generating
def _lathe(profile: Sequence[Tuple[float, float]], width: float, radius: float, segs: int,
           closed: bool) -> Mesh:
    n = len(profile)
    ang = np.linspace(0.0, 2.0 * math.pi, segs, endpoint=False)
    ca, sa = np.cos(ang), np.sin(ang)
    pos = np.zeros((n * segs, 3))
    for i, (x, r) in enumerate(profile):
        pos[i * segs:(i + 1) * segs, 0] = x * width
        pos[i * segs:(i + 1) * segs, 1] = r * radius * ca
        pos[i * segs:(i + 1) * segs, 2] = r * radius * sa
    tris = []
    last = n if closed else n - 1
    for i in range(last):
        a, b = i, (i + 1) % n
        for j in range(segs):
            k = (j + 1) % segs
            p00, p01 = a * segs + j, a * segs + k
            p10, p11 = b * segs + j, b * segs + k
            tris.append((p00, p10, p11))
            tris.append((p00, p11, p01))
    m = Mesh(pos, np.array(tris, dtype=np.int32), np.zeros(len(tris), np.int32)).drop_degenerate()
    p = m.positions.astype(np.float64)
    t = m.triangles
    vol = float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum())
    if vol < 0:                                  # make the faces point outwards
        m = m.flipped()
    return m.recompute_normals()


def make_wheel(radius: float, width: float, side: int, segs: int = 28) -> Tuple[Mesh, Mesh]:
    """(tyre, rim) centred on the origin with the axle along X. ``side`` is +1 when the outside
    of the wheel faces +X (the right-hand wheels) and -1 for the left-hand ones."""
    tire = _lathe(_TIRE_PROFILE, width, radius, segs, closed=True)
    rim = _lathe(_RIM_PROFILE, width, radius, segs, closed=False)
    if side < 0:
        mirror = np.diag([-1.0, 1.0, 1.0])
        tire, rim = tire.transformed(mirror), rim.transformed(mirror)
    return tire, rim


def plan_wheels(lo: np.ndarray, hi: np.ndarray, hints: Sequence[Tuple[np.ndarray, Optional[float],
                                                                       Optional[float]]]
                ) -> List[dict]:
    """Where to put wheels: ``hints`` are (hub, radius, width) tuples already in stud space.
    Without hints the wheels go where an ordinary car has them (about a fifth of the length
    in from each end)."""
    size = hi - lo
    length, width_c, height = float(size[2]), float(size[0]), float(size[1])
    r_def = max(min(0.072 * length, 0.24 * height), 0.2)
    plan: List[dict] = []
    if hints:
        for hub, r, w in hints:
            r = float(r) if r and r > 0 else r_def
            plan.append({"hub": np.array(hub, dtype=np.float64), "radius": r,
                         "width": float(w) if w and w > 0 else 0.7 * r})
        return plan
    tw = 0.7 * r_def
    xo = max(0.5 * width_c - 0.5 * tw - 0.06 * r_def, 0.5 * tw)
    zf = float(lo[2]) + 0.21 * length
    zr = float(hi[2]) - 0.22 * length
    y = float(lo[1]) + 0.55 * r_def
    cx = float((lo[0] + hi[0]) / 2.0)
    for z in (zf, zr):
        for sx in (-1.0, 1.0):
            plan.append({"hub": np.array([cx + sx * xo, y, z]), "radius": r_def, "width": tw})
    return plan


def wheel_pieces(plan: Sequence[dict], taken: set, max_tris: int = 20000,
                 budget: Optional[int] = None) -> List[Piece]:
    """Turn a plan into named tyre + rim pieces (kind 'wheel')."""
    out: List[Piece] = []
    segs = int(max(6, min(28, max_tris // 22)))     # 22 triangles per segment in the tyre
    if budget:                                      # a triangle budget for the whole car was requested
        segs = int(max(6, min(segs, budget // (40 * max(len(plan), 1)))))
    zs = sorted({round(float(p["hub"][2]), 3) for p in plan})
    cx = 0.0                                   # the car is centred on x = 0
    for p in sorted(plan, key=lambda q: (float(q["hub"][2]), float(q["hub"][0]))):
        hub = p["hub"]
        ai = zs.index(round(float(hub[2]), 3))
        letter = "F" if ai == 0 else ("R" if ai == len(zs) - 1 else "M")
        left = hub[0] < cx
        slot = letter + ("L" if left else "R")
        base, k = slot, 2
        while slot in taken:
            slot = f"{base}{k}"
            k += 1
        taken.add(slot)
        tire, rim = make_wheel(p["radius"], p["width"], -1 if left else 1, segs)
        for kind, mesh, mat in (("tire", tire, TIRE_MATERIAL), ("rim", rim, RIM_MATERIAL)):
            m = mesh.transformed(np.eye(3), hub)
            out.append(Piece(f"{kind}_{slot}", f"{kind}_{slot}", "wheel", m, mat, slot))
    return out


# --------------------------------------------------------------------------- repairing
def split_islands(mesh: Mesh) -> List[Mesh]:
    """Split a mesh into its connected pieces (vertices at the same position count as shared)."""
    if mesh.tri_count == 0:
        return []
    q = np.round(mesh.positions.astype(np.float64) * 1e3).astype(np.int64)
    _, weld = np.unique(q, axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    tri = weld[mesh.triangles]
    n = int(weld.max()) + 1
    parent = np.arange(n)

    def root(a: np.ndarray) -> np.ndarray:
        while True:
            b = parent[a]
            if (b == a).all():
                return a
            a = parent[b]

    for _ in range(200):
        r0, r1, r2 = root(tri[:, 0]), root(tri[:, 1]), root(tri[:, 2])
        lowest = np.minimum(np.minimum(r0, r1), r2)
        changed = False
        for r in (r0, r1, r2):
            hit = parent[r] > lowest
            if hit.any():
                np.minimum.at(parent, r, lowest)
                changed = True
        if not changed:
            break
    label = root(tri[:, 0])
    _, first = np.unique(label, return_index=True)       # islands in order of first appearance
    return [mesh.select(np.flatnonzero(label == lab)) for lab in label[np.sort(first)]]


def split_fused_wheels(pieces: List[Piece], lo: np.ndarray, hi: np.ndarray) -> int:
    """A 'wheels' mesh holding all four wheels (or one axle) becomes one piece per wheel.
    Returns how many pieces were added. Works in place on ``pieces``."""
    size = hi - lo
    added = 0
    for pc in list(pieces):
        if pc.kind != "wheel":
            continue
        sx, sy, sz = (float(v) for v in pc.size)
        if not (sx > 0.6 * size[0] or max(sy, sz) > 0.3 * size[2]):
            continue
        parts = [m for m in split_islands(pc.mesh) if m.tri_count >= 6]
        if len(parts) < 2:
            continue
        idx = pieces.index(pc)
        pieces.remove(pc)
        for k, m in enumerate(parts):
            pieces.insert(idx + k, Piece(f"{pc.name}_w{k + 1}", pc.part, "wheel", m, pc.material,
                                         None))
        added += len(parts) - 1
    return added


def find_round_parts(pieces: List[Piece], lo: np.ndarray, hi: np.ndarray) -> int:
    """Models whose wheels have no telling name: mark parts that look like wheels (round when seen
    from the side, narrower than they are tall, touching the ground, left and right matched).
    Returns how many pieces were marked."""
    size = hi - lo
    length, width = float(size[2]), float(size[0])
    cx = float((lo[0] + hi[0]) / 2.0)
    cand = []
    for pc in pieces:
        if pc.kind not in ("body", "other"):
            continue
        sx, sy, sz = (float(v) for v in pc.size)
        dia = max(sy, sz)
        if dia <= 0 or not (0.07 * length <= dia <= 0.26 * length):
            continue
        if min(sy, sz) / dia < 0.82 or not (0.12 * dia <= sx <= 0.85 * dia):
            continue
        c = pc.center
        if float(pc.mesh.bounds()[0][1]) > 0.35 * dia + float(lo[1]):
            continue
        if abs(float(c[0]) - cx) < 0.2 * width:
            continue
        cand.append((pc, c, dia))
    marked = set()
    for i, (a, ca, da) in enumerate(cand):
        for b, cb, db in cand[i + 1:]:
            if (ca[0] - cx) * (cb[0] - cx) < 0 and abs(ca[1] - cb[1]) < 0.25 * da \
                    and abs(ca[2] - cb[2]) < 0.25 * da and abs(da - db) < 0.15 * max(da, db):
                marked.add(id(a))
                marked.add(id(b))
    n = 0
    for pc, _, _ in cand:
        if id(pc) in marked:
            pc.kind = "wheel"
            n += 1
    return n


def drop_duplicate_wheels(pieces: List[Piece]) -> int:
    """Two wheel pieces that sit on top of each other (the same mesh listed twice) z-fight and look
    like flickering tyres; keep one of each. Returns how many were removed."""
    wheels = [p for p in pieces if p.kind == "wheel"]
    gone = set()
    for i, a in enumerate(wheels):
        if id(a) in gone:
            continue
        for b in wheels[i + 1:]:
            if id(b) in gone:
                continue
            r = max(float(a.size[1]), float(a.size[2])) / 2.0 or 1.0
            if np.linalg.norm(a.center - b.center) < 0.03 * r and np.all(
                    np.abs(a.size - b.size) <= 0.06 * np.maximum(a.size, b.size) + 1e-6):
                gone.add(id(b))
    if gone:
        pieces[:] = [p for p in pieces if id(p) not in gone]
    return len(gone)


def hint_to_stud(h: WheelHint, R: np.ndarray, s: float, shift: np.ndarray
                 ) -> Tuple[np.ndarray, Optional[float], Optional[float]]:
    hub = (R @ np.asarray(h.center, dtype=np.float64)) * s + shift
    return hub, (h.radius * s if h.radius else None), (h.width * s if h.width else None)


# --------------------------------------------------------------------------- placing wheels that are not on their hubs
def centre_line_wheels(pieces: List[Piece]) -> List[Piece]:
    """Wheel pieces sitting on the middle of the car. Real wheels are out at the sides, so these are
    an unplaced template wheel (or four wheels left at the origin of their bones), or a spare tyre."""
    body = [p for p in pieces if p.kind != "wheel"]
    if not body:
        return []
    lo = np.min([p.bbox()[0] for p in body], axis=0)
    hi = np.max([p.bbox()[1] for p in body], axis=0)
    cx, bw = float(lo[0] + hi[0]) / 2.0, float(hi[0] - lo[0])
    return [p for p in pieces if p.kind == "wheel" and abs(float(p.center[0]) - cx) < 0.2 * bw]


def corner_targets(wheels: Sequence, lo: np.ndarray, hi: np.ndarray, hints: Sequence) -> List[dict]:
    """Where a car's wheels should be, as plan entries (hub, radius, width). Real mods often give
    hints for only some of the wheels (a rear axle built from node names we can't resolve, a part
    that didn't export), so hints and the wheels already found are combined per axle/side rather
    than trusting either alone: a side with no data is mirrored from its axle-mate, and an axle with
    no data at all is added at the usual spot, sized like the nearest axle that has data. Falls back
    to the usual spots for the whole car when there is nothing to go on."""
    size = hi - lo
    length, width_c, height = float(size[2]), float(size[0]), float(size[1])
    r_def = max(min(0.072 * length, 0.24 * height), 0.2)
    cx = float((lo[0] + hi[0]) / 2.0)

    known: List[dict] = []
    for hub, r, w in hints:
        rad = float(r) if r else r_def
        known.append({"hub": np.asarray(hub, dtype=np.float64), "radius": rad,
                      "width": float(w) if w else 0.7 * rad, "src": 0})
    for g in wheels:
        known.append({"hub": np.asarray(g.hub, dtype=np.float64), "radius": float(g.radius),
                      "width": float(g.width) if g.width > 0 else 0.7 * float(g.radius), "src": 1})
    if not known:
        return plan_wheels(lo, hi, [])

    rmax = max(e["radius"] for e in known)
    zs: List[float] = []
    for z in sorted(e["hub"][2] for e in known):
        if not zs or z - zs[-1] > 0.5 * rmax:
            zs.append(z)
    axles: List[dict] = []
    for z in zs:
        members = [e for e in known if abs(float(e["hub"][2]) - z) <= 0.5 * rmax]
        sides: Dict[int, dict] = {}
        for e in sorted(members, key=lambda e: e["src"]):           # hints (src 0) win ties
            sides.setdefault(-1 if float(e["hub"][0]) < cx else 1, e)
        axles.append({"z": float(np.mean([e["hub"][2] for e in members])), "sides": sides})

    if len(axles) == 1:
        a = axles[0]
        new_z = (float(hi[2]) - 0.22 * length if a["z"] < float(lo[2] + hi[2]) / 2.0
                else float(lo[2]) + 0.21 * length)
        axles.append({"z": new_z, "sides": {}})

    out: List[dict] = []
    for a in axles:
        sides = a["sides"]
        if not sides:
            ref_axle = min((x for x in axles if x["sides"]), key=lambda x: abs(x["z"] - a["z"]),
                           default=None)
            ref = next(iter(ref_axle["sides"].values())) if ref_axle else None
            ref_r = float(ref["radius"]) if ref else r_def
            ref_w = float(ref["width"]) if ref else 0.7 * r_def
            ref_y = float(ref["hub"][1]) if ref else float(lo[1]) + 0.55 * r_def
            ref_xo = abs(float(ref["hub"][0]) - cx) if ref else 0.0
            xo = ref_xo or max(0.5 * width_c - 0.5 * ref_w - 0.06 * ref_r, 0.5 * ref_w)
            for s in (-1.0, 1.0):
                out.append({"hub": np.array([cx + s * xo, ref_y, a["z"]]), "radius": ref_r, "width": ref_w})
            continue
        if len(sides) == 1:
            s0, e0 = next(iter(sides.items()))
            sides = dict(sides)
            sides[-s0] = {"hub": np.array([2 * cx - float(e0["hub"][0]), e0["hub"][1], e0["hub"][2]]),
                          "radius": e0["radius"], "width": e0["width"]}
        for s in (-1, 1):
            e = sides[s]
            out.append({"hub": np.array([float(e["hub"][0]), float(e["hub"][1]), a["z"]]),
                       "radius": e["radius"], "width": e["width"]})
    return out


def target_labels(plan: Sequence[dict]) -> List[str]:
    """FL / FR / RL / RR ... for each plan entry (same naming as wheel_pieces)."""
    zs = sorted({round(float(p["hub"][2]), 3) for p in plan})
    out = []
    for p in plan:
        ai = zs.index(round(float(p["hub"][2]), 3))
        out.append(("F" if ai == 0 else ("R" if ai == len(zs) - 1 else "M")) + ("L" if p["hub"][0] < 0 else "R"))
    return out


def copy_wheel(src: Sequence[Piece], src_hub: Sequence[float], dst_hub: Sequence[float], flip: bool,
               tag: str) -> List[Piece]:
    """Copies of a wheel's pieces moved onto another hub; ``flip`` turns them half a turn about the
    vertical axis so the outside of the wheel faces outwards on the other side of the car."""
    M = np.diag([-1.0, 1.0, -1.0]) if flip else np.eye(3)
    t = np.asarray(dst_hub, dtype=np.float64) - M @ np.asarray(src_hub, dtype=np.float64)
    return [Piece(f"{p.name}_{tag}", p.part, "wheel", p.mesh.transformed(M, t), p.material, None)
            for p in src]


def union_centre(pieces: Sequence[Piece]) -> np.ndarray:
    lo = np.min([p.bbox()[0] for p in pieces], axis=0)
    hi = np.max([p.bbox()[1] for p in pieces], axis=0)
    return (lo + hi) / 2.0
