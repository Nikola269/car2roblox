"""The conversion pipeline: import -> Roblox axes/scale -> pieces -> files."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import ROBLOX_MAX_TRIANGLES, STUDS_PER_METER, __version__
from . import achassis
from .classify import classify_part, slot_from_name
from .export import TexFile, sanitize, write_glb, write_obj
from .geometry import auto_fix_winding, decimate_to, split_by_limit
from .model import (AxisSpec, ConversionError, Material, Mesh, Model, Piece, WheelGroup,
                    parse_axis)
from . import wheels as wh
from .rigscript import make_rig_script
from .safety import scrub_model
from .textures import fit_texture, parse_hex_color, png_bytes

Log = Callable[[str], None]

UNITS = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048}
DEFAULT_PAINT = {"primary": "#2F6DB5", "secondary": "#E8E8E8", "tertiary": "#202020",
                 "quaternary": "#202020"}


@dataclass
class Options:
    source: str = "auto"                  # auto | gta_sa | beamng | generic
    name: Optional[str] = None
    scale: float = 1.0                    # multiplier on real size (1 m = 3.571 studs)
    length: Optional[float] = None        # force the car's length in studs
    unit: Optional[str] = None            # generic files: m cm mm in ft (default: guess)
    axes: Optional[Tuple[str, str, str]] = None   # (right, up, forward) override
    flip_forward: bool = False
    paint: Optional[str] = None           # '#rrggbb' for the primary paint
    paint2: Optional[str] = None
    paint3: Optional[str] = None
    include_lod: bool = False
    include_glass: bool = True
    include_interior: bool = True
    include_lights: bool = True
    max_tris_per_mesh: int = ROBLOX_MAX_TRIANGLES
    target_tris: Optional[int] = None     # crude decimation budget for the whole car
    max_texture: int = 1024
    formats: Tuple[str, ...] = ("glb", "obj")
    rig: bool = True                      # 'Make drivable': write the Studio driving script
    add_tires: bool = True                # generate tires when the model has no (or too few) wheels
    new_tires: bool = False               # always replace the model's wheels with clean generated tires
    top_speed: Optional[float] = None     # km/h for the driving script (None = 90 studs/s, about 91 km/h)
    drivetrain: str = "awd"               # awd | rwd | fwd
    advanced_chassis: bool = False        # real per-wheel suspension + an RPM/torque-curve drivetrain,
                                           # in the spirit of the community "A-Chassis" rigs, instead of
                                           # the simple fixed-hinge wheels
    achassis_real: bool = False           # bundle the REAL lisphm/A-Chassis kit (MPL-2.0) instead of
                                           # car2roblox's own rig; needs exactly one front + one rear
                                           # axle (overrides advanced_chassis when both are set)
    txd: Tuple[str, ...] = ()
    vehicle: Optional[str] = None         # BeamNG: which vehicle inside a multi-vehicle archive
    config: Optional[str] = None          # BeamNG: .pc configuration name
    strip_links: bool = True              # cut a link out of a part/material/texture/model name.
                                           # (cosmetic only - deleting adult-content-named parts and
                                           # refusing risky archive files are not optional, see safety.py)


@dataclass
class Result:
    out_dir: Path
    files: List[Path] = field(default_factory=list)
    pieces: int = 0
    triangles: int = 0
    size_studs: Tuple[float, float, float] = (0, 0, 0)   # width, height, length
    wheels: int = 0
    warnings: List[str] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    preview: Optional[Path] = None        # preview.png (None when Pillow/numpy could not draw it)


# --------------------------------------------------------------------------- loading
def detect_source(path: Path) -> str:
    if path.is_dir():
        if any(path.rglob("*.jbeam")) or any(path.rglob("*.dae")):
            return "beamng"
        if any(path.glob("*.dff")):
            return "gta_sa"
        raise ConversionError("this folder has no .dff, .jbeam or .dae files")
    ext = path.suffix.lower()
    if ext == ".dff":
        return "gta_sa"
    if ext == ".zip":
        return "beamng"
    if ext in (".obj", ".glb", ".gltf", ".dae", ".fbx", ".yft", ".ydr", ".odr", ".oft", ".rpf"):
        return "generic"
    if ext == ".txd":
        raise ConversionError("select the .dff model - the matching .txd is picked up automatically")
    raise ConversionError(f"don't know how to read '{path.name}' (supported: .dff, BeamNG .zip/"
                          "folder, .obj, .glb/.gltf, .dae)")


def load_model(path: str, opts: Options, log: Log) -> Model:
    from .imp_beamng import import_beamng
    from .imp_generic import import_generic
    from .imp_gtasa import import_gta_sa

    p = Path(path)
    if not p.exists():
        raise ConversionError(f"'{path}' does not exist")
    src = opts.source if opts.source != "auto" else detect_source(p)
    if src == "gta_sa":
        if p.is_dir():
            dffs = sorted(p.glob("*.dff"))
            if not dffs:
                raise ConversionError("no .dff in that folder")
            p = dffs[0]
        model = import_gta_sa(str(p), opts.txd, opts.include_lod, log)
    elif src == "beamng":
        model = import_beamng(str(p), opts.vehicle, opts.config, opts.include_lod, log)
    elif src == "generic":
        model = import_generic(str(p), log)
    else:
        raise ConversionError(f"unknown source type '{src}'")
    # links and adult-content names are removed here (notes -> warnings); strip_links only
    # affects the link part - see Options.strip_links / safety.scrub_model
    scrub_model(model, strip_links=opts.strip_links)
    return model


# --------------------------------------------------------------------------- axes
def _token(v: np.ndarray) -> str:
    i = int(np.argmax(np.abs(v)))
    return ("-" if v[i] < 0 else "+") + "xyz"[i]


def auto_axes(model: Model, log: Log) -> AxisSpec:
    """Find the forward axis of a generic model: the longest horizontal extent."""
    spec = model.axes
    pts = np.concatenate([p.mesh.positions for p in model.parts])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    ext = hi - lo
    up_v = parse_axis(spec.up)
    up_i = int(np.argmax(np.abs(up_v)))
    horiz = [i for i in range(3) if i != up_i]
    fwd_i = max(horiz, key=lambda i: ext[i])
    cur_i = int(np.argmax(np.abs(parse_axis(spec.forward))))
    if fwd_i == cur_i:
        return spec
    # sign guess: a car's roof sits behind the middle, so the front points away from the roof
    h = pts[:, up_i] * up_v[up_i]
    top = h > h.min() + 0.85 * (h.max() - h.min())
    sign = 1.0
    if top.any():
        c = pts[top, fwd_i].mean() - (lo[fwd_i] + hi[fwd_i]) / 2.0
        if abs(c) > 0.02 * ext[fwd_i]:
            sign = -1.0 if c > 0 else 1.0
    fwd_v = np.zeros(3)
    fwd_v[fwd_i] = sign
    right_v = np.cross(fwd_v, up_v)
    new = AxisSpec(_token(right_v), spec.up, _token(fwd_v))
    log(f"  detected forward axis {new.forward} (up {new.up}); if the car faces backwards, "
        "re-run with --flip-forward")
    return new


# --------------------------------------------------------------------------- helpers
def _union_bbox(meshes: Sequence[Mesh]) -> Tuple[np.ndarray, np.ndarray]:
    los, his = zip(*(m.bounds() for m in meshes))
    return np.min(los, axis=0), np.max(his, axis=0)


def _final_material(m: Material, paints: Dict[str, Tuple[float, float, float]]) -> Material:
    if m.paint and m.paint in paints and not m.texture:
        r, g, b = paints[m.paint]
        return Material(m.name, (r, g, b, m.color[3]), None, m.paint, m.transparent, m.double_sided)
    return m


def _group_wheels(pieces: List[Piece], drivetrain: str, log: Log
                  ) -> Tuple[List[WheelGroup], Optional[float]]:
    """Cluster wheel pieces into wheels. Returns (wheels, y of the ground under them)."""
    cand = [p for p in pieces if p.kind == "wheel"]
    if not cand:
        return [], None
    items = sorted(cand, key=lambda p: -max(p.size[1], p.size[2]))
    clusters: List[dict] = []
    for pc in items:
        c = pc.center
        for cl in clusters:
            d = c - cl["c"]
            # same axle position (tyre, rim, nuts, discs...) - the other side of the car is far in x
            if float(np.hypot(d[1], d[2])) < 0.6 * cl["r"] and abs(float(d[0])) < 0.9 * cl["r"]:
                cl["m"].append(pc)
                break
        else:
            clusters.append({"c": c, "r": max(pc.size[1], pc.size[2]) / 2.0, "m": [pc]})
    groups = []
    for cl in clusters:
        # the hub comes from the tyre-sized pieces only, so brake discs or stray bolts cannot pull it
        tyres = [p for p in cl["m"] if max(p.size[1], p.size[2]) / 2.0 >= 0.8 * cl["r"]]
        lo, hi = _union_bbox([p.mesh for p in tyres])
        radius = float(max(hi[1] - lo[1], hi[2] - lo[2]) / 2.0)
        groups.append({"lo": lo, "hi": hi, "hub": (lo + hi) / 2.0, "r": radius, "m": cl["m"],
                       "width": float(hi[0] - lo[0])})
    # the ground is where at least two wheels bottom out together; anything clearly higher is a
    # spare tyre or a prop and is not a driven wheel
    rmax = max(g["r"] for g in groups)
    lows = sorted(float(g["lo"][1]) for g in groups)
    ground_y = lows[0]
    for lv in lows:
        if sum(1 for x in lows if x <= lv + 0.25 * rmax) >= min(2, len(lows)):
            ground_y = lv
            break
    on_ground = [bool(g["lo"][1] < ground_y + 0.45 * g["r"] + 0.05) for g in groups]
    ground = [g for g, ok in zip(groups, on_ground) if ok]
    dropped = [g for g, ok in zip(groups, on_ground) if not ok]
    for g in dropped:
        for p in g["m"]:
            p.kind = "body"
            p.wheel_slot = None
    if dropped:
        log(f"  {len(dropped)} wheel-like parts are off the ground (spare/props): treated as body")
    if not ground:
        return [], None

    zs = sorted(g["hub"][2] for g in ground)
    rmax = max(g["r"] for g in ground)
    axles: List[float] = []
    for z in zs:
        if not axles or abs(z - axles[-1]) > 0.5 * rmax:
            axles.append(z)
    out: List[WheelGroup] = []
    used: Dict[str, int] = {}
    for g in sorted(ground, key=lambda g: (g["hub"][2], g["hub"][0])):
        ai = min(range(len(axles)), key=lambda i: abs(axles[i] - g["hub"][2]))
        letter = "F" if ai == 0 else ("R" if ai == len(axles) - 1 else "M")
        if len(axles) == 1:
            letter = "F"
        slot = letter + ("L" if g["hub"][0] < 0 else "R")
        used[slot] = used.get(slot, 0) + 1
        if used[slot] > 1:
            slot += str(used[slot])
        primary = max(g["m"], key=lambda p: float(np.prod(p.size)))
        for p in g["m"]:
            p.wheel_slot = slot
        out.append(WheelGroup(slot=slot, hub=tuple(float(x) for x in g["hub"]),
                              radius=g["r"], width=g["width"],
                              pieces=[p.name for p in g["m"]], primary=primary.name,
                              front=letter == "F" and len(axles) > 1 or len(axles) == 1))
    log(f"  {len(out)} wheels in {len(axles)} axle(s): " + ", ".join(w.slot for w in out))
    return out, float(min(g["lo"][1] for g in ground))


def _shift_pieces(pieces: List[Piece], wheels: List[WheelGroup], dy: float) -> List[WheelGroup]:
    d = np.array([0.0, dy, 0.0])
    for p in pieces:
        p.mesh = p.mesh.transformed(np.eye(3), d)
    return [WheelGroup(w.slot, (w.hub[0], w.hub[1] + dy, w.hub[2]), w.radius, w.width, w.pieces,
                       w.primary, w.front) for w in wheels]


def _pick_unit(model: Model, extent_src: float, opts: Options, warnings: List[str]) -> float:
    if opts.unit:
        if opts.unit not in UNITS:
            raise ConversionError(f"unknown unit '{opts.unit}' (use {', '.join(UNITS)})")
        return UNITS[opts.unit]
    if model.source != "generic":
        return model.unit_meters
    e = extent_src * model.unit_meters
    if e >= 1000:
        u, label = 0.001, "millimetres"
    elif e >= 100:
        u, label = 0.01, "centimetres"
    elif e >= 20:
        u, label = 0.0254, "inches"
    else:
        return model.unit_meters
    warnings.append(f"The model is {e:.0f} units long, so I assumed {label}. Use --unit "
                    "m/cm/mm/in/ft if that is wrong.")
    return u


# --------------------------------------------------------------------------- the pipeline
def build_pieces(model: Model, opts: Options, log: Log, warnings: List[str]):
    """Returns (pieces, wheels, materials-by-piece handled inside, size tuple)."""
    if not model.parts:
        raise ConversionError("nothing to convert: the model has no geometry")

    # ---- axes
    if opts.axes:
        axes = AxisSpec(*opts.axes)
    elif model.forward_auto:
        axes = auto_axes(model, log)
    else:
        axes = model.axes
    if opts.flip_forward:
        axes = axes.with_flipped_forward()
    R = axes.to_roblox()
    flips = np.linalg.det(R) < 0

    meshes = [p.mesh.transformed(R) for p in model.parts]
    lo, hi = _union_bbox(meshes)
    extent = hi - lo
    unit = _pick_unit(model, float(extent[2]), opts, warnings)
    if opts.length:
        if extent[2] <= 0:
            raise ConversionError("cannot derive a scale: the model has no length")
        s = opts.length / float(extent[2])
    else:
        s = unit * STUDS_PER_METER * opts.scale
    meshes = [m.transformed(np.eye(3) * s) for m in meshes]
    lo, hi = _union_bbox(meshes)
    shift = np.array([-(lo[0] + hi[0]) / 2.0, -lo[1], -(lo[2] + hi[2]) / 2.0])
    meshes = [m.transformed(np.eye(3), shift) for m in meshes]
    lo, hi = _union_bbox(meshes)
    size = hi - lo
    log(f"  size: {size[0]:.1f} wide x {size[1]:.1f} tall x {size[2]:.1f} long studs "
        f"({size[2] * 0.28:.2f} m)")
    if not (4 <= size[2] <= 60):
        warnings.append(f"The car came out {size[2]:.1f} studs long (typical: 12-30). "
                        "Check the units: --unit cm / --length 18.")
    if max(size) > 2048:
        warnings.append("The car is larger than Roblox's 2048-stud mesh limit; use --length.")

    # ---- paints
    paints = {k: parse_hex_color(v) for k, v in DEFAULT_PAINT.items()}
    for k, v in model.default_paint.items():
        paints[k] = v
    for k, v in (("primary", opts.paint), ("secondary", opts.paint2), ("tertiary", opts.paint3)):
        if v:
            paints[k] = parse_hex_color(v)
    final_mats = [_final_material(m, paints) for m in model.materials]

    # ---- parts -> single-material pieces
    entries = []
    for part, mesh in zip(model.parts, meshes):
        mesh, fixed = auto_fix_winding(mesh)
        if fixed:
            log(f"  fixed inside-out winding on '{part.name}'")
        mesh = mesh.drop_degenerate()
        if mesh.tri_count == 0:
            continue
        if mesh.normals is None:
            mesh = mesh.recompute_normals()
        used = sorted(set(int(i) for i in mesh.material_ids))
        mats = [final_mats[i] for i in used]
        transparent = all(m.transparent for m in mats)
        kind = classify_part(part.name, [m.name for m in mats], transparent, part.kind_hint)
        if part.wheel_slot:
            kind = "wheel"
        if kind == "glass" and not opts.include_glass:
            continue
        if kind == "interior" and not opts.include_interior:
            continue
        if kind == "light" and not opts.include_lights:
            continue
        entries.append((part, kind, mesh))
    if not entries:
        raise ConversionError("every part was filtered out - nothing left to export")

    total = sum(e[2].tri_count for e in entries)
    ratio = 1.0
    if opts.target_tris and total > opts.target_tris:
        ratio = opts.target_tris / total
        log(f"  decimating {total} -> about {opts.target_tris} triangles (ratio {ratio:.2f})")

    pieces: List[Piece] = []
    names: set = set()

    def unique(base: str) -> str:
        n, k = base, 2
        while n.lower() in names:
            n = f"{base}_{k}"
            k += 1
        names.add(n.lower())
        return n

    for part, kind, mesh in entries:
        by_mat = mesh.split_by_material()
        for mid, sub in by_mat:
            mat = final_mats[mid]
            if ratio < 1.0 and sub.tri_count > 24:
                sub = decimate_to(sub, max(24, int(sub.tri_count * ratio)))
            base = sanitize(part.name)
            if len(by_mat) > 1:
                base += "__" + sanitize(mat.name, "mat")
            chunks = split_by_limit(sub, opts.max_tris_per_mesh)
            for ci, ch in enumerate(chunks):
                nm = unique(base if len(chunks) == 1 else f"{base}_p{ci + 1}")
                ch = Mesh(ch.positions, ch.triangles, np.zeros(ch.tri_count, np.int32),
                          ch.normals, ch.uvs)
                pieces.append(Piece(nm, part.name, kind, ch, mat, part.wheel_slot))
    wheels = _finish_wheels(model, opts, pieces, R, s, shift, log, warnings)
    return pieces, wheels, (float(size[0]), float(size[1]), float(size[2]))


def _unique_names(new: List[Piece], pieces: List[Piece]) -> None:
    names = {p.name.lower() for p in pieces}
    for e in new:
        k, base = 2, e.name
        while e.name.lower() in names:
            e.name = f"{base}_{k}"
            k += 1
        names.add(e.name.lower())
        e.part = e.name


def _near_wheel(wheels: List[WheelGroup], hub: np.ndarray, radius: Optional[float]) -> bool:
    return any(float(np.linalg.norm(np.array(g.hub) - hub)) < 0.8 * max(g.radius, radius or 1.0)
               for g in wheels)


def _finish_wheels(model: Model, opts: Options, pieces: List[Piece], R: np.ndarray, s: float,
                   shift: np.ndarray, log: Log, warnings: List[str]) -> List[WheelGroup]:
    """Repair what the importers produced, group it into wheels, put wheels where they are missing
    (move a wheel that was left in the middle of the car, copy the wheels there are, or generate
    tyres), and stand the car on its wheels."""
    lo, hi = _union_bbox([p.mesh for p in pieces])
    n_split = wh.split_fused_wheels(pieces, lo, hi)
    if n_split:
        log(f"  split {n_split} fused wheel meshes into separate wheels")
    if not any(p.kind == "wheel" for p in pieces):
        n = wh.find_round_parts(pieces, lo, hi)
        if n:
            log(f"  {n} unnamed round parts look like wheels")
            warnings.append(f"{n} parts without 'wheel' in their name were treated as wheels "
                            "because of their shape.")
    n_dup = wh.drop_duplicate_wheels(pieces)
    if n_dup:
        log(f"  removed {n_dup} duplicate wheel meshes (they flicker when stacked)")

    # wheels sitting in the middle of the car are not on their hubs: set them aside for now
    mid = wh.centre_line_wheels(pieces)
    for p in mid:
        p.kind = "other"
    wheels, ground = _group_wheels(pieces, opts.drivetrain, (lambda *_a: None) if mid else log)

    make = opts.add_tires or opts.new_tires
    hints = [wh.hint_to_stud(h, R, s, shift) for h in model.wheel_hints] if (make or mid) else []
    new: List[Piece] = []
    if make or mid:
        mid_ids = {id(p) for p in mid}
        t_lo, t_hi = _union_bbox([p.mesh for p in pieces if id(p) not in mid_ids] or [p.mesh for p in pieces])
        targets = wh.corner_targets(wheels, t_lo, t_hi, hints)
        labels = wh.target_labels(targets)
        need = [i for i, t in enumerate(targets) if not _near_wheel(wheels, t["hub"], t["radius"])]
        if len(wheels) >= len(targets):
            need = []
        by_id = {p.name: p for p in pieces}
        moved = copied = made_n = 0
        if need and wheels:
            # the model has some wheels: copy the nearest one onto each empty hub
            for i in need:
                t = targets[i]
                g = min(wheels, key=lambda w: (0 if (w.hub[0] < 0) == (t["hub"][0] < 0) else 1,
                                               float(np.linalg.norm(np.array(w.hub) - t["hub"]))))
                src = [by_id[n] for n in g.pieces if n in by_id]
                new += wh.copy_wheel(src, g.hub, t["hub"], (g.hub[0] < 0) != (t["hub"][0] < 0), labels[i])
                copied += 1
            for p in mid:
                p.kind = "body"
            pieces[:] = [p for p in pieces if id(p) not in mid_ids]
        elif need and mid:
            # nothing is on a hub: the wheel(s) in the middle are the only source
            groups: dict = {}
            plain: List[Piece] = []
            for p in mid:
                sl = slot_from_name(p.name) or slot_from_name(p.part)
                (groups.setdefault(sl, []) if sl else plain).append(p)
            if len(groups) >= 2:        # one wheel per name (wheel_lf, wheel_rf ...)
                spent: set = set()
                for i in need:
                    t = targets[i]
                    if labels[i] in groups:
                        g = groups[labels[i]]
                        d = np.asarray(t["hub"]) - wh.union_centre(g)
                        for p in g:
                            p.mesh = p.mesh.transformed(np.eye(3), d)
                            p.kind = "wheel"
                            spent.add(id(p))
                        moved += 1
                    else:
                        sl, g = next(iter(groups.items()))
                        c = wh.union_centre(g)
                        new += wh.copy_wheel(g, c, t["hub"], (sl[1] == "L") != (t["hub"][0] < 0), labels[i])
                        copied += 1
                for p in mid:
                    if id(p) not in spent:
                        p.kind = "body"
                pieces[:] = [p for p in pieces if id(p) not in mid_ids or id(p) in spent]
            else:                       # one template wheel for all four corners
                tmpl = mid
                big = max(tmpl, key=lambda p: float(max(p.size[1], p.size[2])))
                r0 = float(max(big.size[1], big.size[2])) / 2.0
                tmpl = [p for p in tmpl if float(np.linalg.norm(p.center - big.center)) < 0.6 * r0]
                c = wh.union_centre(tmpl)
                sl = next(iter(groups), None)
                for i in need:
                    t = targets[i]
                    left_src = bool(sl and sl[1] == "L")
                    new += wh.copy_wheel(tmpl, c, t["hub"], left_src != (t["hub"][0] < 0), labels[i])
                    moved += 1
                pieces[:] = [p for p in pieces if id(p) not in mid_ids]
        elif need and make:
            plan = [{**targets[i]} for i in need]
            taken = {w.slot for w in wheels}
            new += wh.wheel_pieces(plan, taken, opts.max_tris_per_mesh,
                                   int(0.2 * opts.target_tris) if opts.target_tris else None)
            made_n = len(plan)
        else:
            for p in mid:               # a spare tyre in the boot, not a wheel
                p.kind = "body"
        if mid and not need:
            log(f"  {len(mid)} wheel part(s) in the middle of the car look like a spare: kept as body")
        if new:
            _unique_names(new, pieces)
            pieces.extend(new)
            if moved:
                log(f"  a wheel was sitting in the middle of the car: moved to {moved} wheel positions")
                warnings.append("A wheel was sitting in the middle of the car instead of on its hub, so it "
                                f"was moved onto {moved} wheel position(s). Check the wheels in the preview.")
            if copied:
                log(f"  only some wheels were there: copied them to {copied} empty wheel position(s)")
                warnings.append(f"Only some wheels were found, so {copied} wheel(s) were copied from the "
                                "ones that exist.")
            if made_n:
                how = "from the JBeam hub nodes" if hints else (
                    "at the usual spots for a car of this size" if not wheels else "from the wheels that exist")
                had = "no tyres" if not wheels else "tyres on only some wheels"
                log(f"  {had}: generated {made_n} tyre + rim pair(s) {how}")
                warnings.append(f"The model had {had}, so {made_n} tyre + rim pair(s) were generated "
                                f"{how}. If the body already has wheels built into it, turn off "
                                "'Add tires' and convert again.")
            wheels, ground = _group_wheels(pieces, opts.drivetrain, log)
        elif mid and wheels:
            wheels, ground = _group_wheels(pieces, opts.drivetrain, log)

    if opts.new_tires and wheels:
        # always use clean tyres: keep where the wheels are, replace what they look like
        plan = [{"hub": np.array(g.hub, dtype=np.float64), "radius": float(g.radius),
                 "width": float(g.width) if g.width > 0 else 0.7 * float(g.radius)} for g in wheels]
        pieces[:] = [p for p in pieces if p.kind != "wheel"]
        extra = wh.wheel_pieces(plan, set(), opts.max_tris_per_mesh,
                                int(0.2 * opts.target_tris) if opts.target_tris else None)
        _unique_names(extra, pieces)
        pieces.extend(extra)
        log(f"  replaced the model's wheels with {len(plan)} clean generated tyre + rim pairs")
        warnings.append(f"The wheels were replaced by {len(plan)} generated tyre + rim pairs at the same "
                        "positions ('Always use new tires').")
        wheels, ground = _group_wheels(pieces, opts.drivetrain, log)

    if wheels and ground is not None and abs(ground) > 0.01:
        # stand the car on its wheels (an under-body plate or shadow mesh must not hold it up)
        wheels = _shift_pieces(pieces, wheels, -ground)
    return wheels


def _drive_flags(wheels: List[WheelGroup], drivetrain: str) -> List[Tuple[bool, bool]]:
    """(steer, drive) per wheel."""
    out = []
    has_rear = any(not w.front for w in wheels)
    for w in wheels:
        steer = w.front and has_rear or (w.front and len(wheels) <= 2)
        if drivetrain == "fwd" and any(x.front for x in wheels):
            drive = w.front
        elif drivetrain == "rwd" and has_rear:
            drive = not w.front
        else:
            drive = True
        out.append((steer, drive))
    return out


def convert(path: str, out_dir: str, opts: Optional[Options] = None, log: Log = print) -> Result:
    opts = opts or Options()
    warnings: List[str] = []
    model = load_model(path, opts, log)
    for n in model.notes:
        warnings.append(n)
    name = sanitize(opts.name or model.name, "Car")
    log(f"Converting '{name}' ({model.source}), {model.tri_count()} source triangles")

    pieces, wheels, size = build_pieces(model, opts, log, warnings)

    root = Path(out_dir) / name
    root.mkdir(parents=True, exist_ok=True)
    _clean_previous_output(root)
    result = Result(out_dir=root, warnings=warnings, size_studs=size)

    # ---- textures
    tex_files: Dict[str, TexFile] = {}
    used_names: set = set()
    for pc in pieces:
        key = pc.material.texture
        if key and key not in tex_files and key in model.textures:
            tex = model.textures[key]
            base = sanitize(tex.name, "texture")
            fn = base
            k = 2
            while fn.lower() in used_names:
                fn = f"{base}_{k}"
                k += 1
            used_names.add(fn.lower())
            img = fit_texture(tex.image, opts.max_texture)
            tex_files[key] = (fn + ".png", png_bytes(img), tex.has_alpha)
    if tex_files:
        tdir = root / "textures"
        tdir.mkdir(exist_ok=True)
        for fn, data, _ in tex_files.values():
            (tdir / fn).write_bytes(data)
            result.files.append(tdir / fn)

    # ---- model files
    fmts = {f.lower() for f in opts.formats}
    glb_name = f"{name}.glb"
    if "glb" in fmts:
        write_glb(root / glb_name, pieces, tex_files, name)
        result.files.append(root / glb_name)
    if "obj" in fmts:
        odir = root / "obj"
        odir.mkdir(exist_ok=True)
        for pc in pieces:
            tf = tex_files.get(pc.material.texture) if pc.material.texture else None
            write_obj(odir / f"{pc.name}.obj", pc, tf[0] if tf else None)
            result.files += [odir / f"{pc.name}.obj", odir / f"{pc.name}.mtl"]
        for fn, data, _ in tex_files.values():
            (odir / fn).write_bytes(data)

    # ---- manifest + rig
    non_wheel = [p for p in pieces if p.kind != "wheel"] or list(pieces)
    body_like = [p for p in non_wheel if p.kind in ("body", "light", "other")] or non_wheel
    c_lo, c_hi = _union_bbox([p.mesh for p in body_like])
    avg_r = float(np.mean([w.radius for w in wheels])) if wheels else 0.0
    c_lo = c_lo.copy()
    c_lo[1] = max(c_lo[1], 0.6 * avg_r)
    shrink = 0.96
    c_size = (c_hi - c_lo) * shrink
    c_center = (c_hi + c_lo) / 2.0
    body_piece = max(body_like, key=lambda p: float(np.prod(p.size)))
    seat = [float(c_center[0] - 0.22 * size[0]), float(c_lo[1] + 0.38 * (c_hi[1] - c_lo[1])),
            float(c_center[2] + 0.06 * size[2])]
    # a front passenger seat mirrors the driver's; rear seats are added only when there is clearly
    # enough car behind the front seats for them (a rough guess, not a real interior layout)
    passenger_seats = [[float(2 * c_center[0] - seat[0]), seat[1], seat[2]]]
    rear_room = float(c_hi[2]) - seat[2]
    if rear_room > max(0.30 * size[2], 3.5):
        rear_z = seat[2] + 0.55 * rear_room
        passenger_seats += [[seat[0], seat[1], rear_z], [float(2 * c_center[0] - seat[0]), seat[1], rear_z]]
    flags = _drive_flags(wheels, opts.drivetrain)

    manifest = {
        "generator": f"car2roblox {__version__}",
        "name": name, "source": model.source, "units": "studs",
        "size": {"width": size[0], "height": size[1], "length": size[2]},
        "triangles": int(sum(p.mesh.tri_count for p in pieces)),
        "max_triangles_per_mesh": opts.max_tris_per_mesh,
        "body": body_piece.name,
        "pieces": [{
            "name": p.name, "part": p.part, "kind": p.kind, "material": p.material.name,
            "triangles": p.mesh.tri_count,
            "center": [round(float(x), 4) for x in p.center],
            "size": [round(float(x), 4) for x in p.size],
            "color": [round(float(x), 4) for x in p.material.color],
            "paint": p.material.paint, "transparent": p.material.transparent,
            "texture": ("textures/" + tex_files[p.material.texture][0])
            if p.material.texture in tex_files else None,
            "wheel": p.wheel_slot} for p in pieces],
        "wheels": [{"slot": w.slot, "hub": [round(x, 4) for x in w.hub],
                    "radius": round(w.radius, 4), "width": round(w.width, 4),
                    "pieces": w.pieces, "primary": w.primary,
                    "steer": st, "drive": dr} for w, (st, dr) in zip(wheels, flags)],
        "seat": [round(x, 4) for x in seat],
        "passenger_seats": [[round(x, 4) for x in p] for p in passenger_seats],
        "collider": {"center": [round(float(x), 4) for x in c_center],
                     "size": [round(float(x), 4) for x in c_size]},
        "warnings": warnings,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    result.files.append(root / "manifest.json")

    if opts.rig:
        # km/h -> studs per second (a stud is 0.28 m)
        top_speed = round(opts.top_speed / 3.6 / 0.28, 1) if opts.top_speed and opts.top_speed > 0 else None
        if not wheels:
            warnings.append("No wheels were detected, so the rig script builds a body + seat "
                            "only. Wheel parts need 'wheel', 'tire' or 'rim' in their names.")
        rig_wheels = [{"slot": w.slot, "hub": [round(x, 4) for x in w.hub], "radius": round(w.radius, 4),
                       "width": round(w.width, 4), "pieces": w.pieces, "primary": w.primary, "steer": st, "drive": dr}
                      for w, (st, dr) in zip(wheels, flags)]
        achassis_real = opts.achassis_real and achassis.fits_achassis(rig_wheels)
        achassis_extra: dict = {}
        if opts.achassis_real and not achassis_real:
            warnings.append("Real A-Chassis needs exactly one front axle and one rear axle "
                            "(a left and right wheel on each); this car's wheel layout doesn't "
                            "match that, so the simple drivable rig was used instead.")
        if achassis_real:
            size_whl = {"width": float(size[0]), "height": float(size[1]), "length": float(size[2])}
            achassis_extra = {"achassis": {
                "tune": achassis.build_tune_source(size_whl, rig_wheels, opts.drivetrain),
                "initialize": achassis.vendor_source("Initialize.luau"),
                "readme": achassis.vendor_source("README.luau"),
                "units": achassis.vendor_source("Units.luau"),
                "drive": achassis.vendor_source("Drive.luau"),
                "values": [[n, c] for n, c in achassis.VALUES_SPEC],
            }}
        rig = make_rig_script({
            "name": name, "drivetrain": opts.drivetrain, "body": body_piece.name,
            "pieces": [{"name": p.name, "kind": p.kind, "center": [round(float(x), 4) for x in p.center],
                        "size": [round(float(x), 4) for x in p.size],
                        "paint": p.material.paint} for p in pieces],
            "wheels": rig_wheels,
            "collider": manifest["collider"], "seat": manifest["seat"],
            "passenger_seats": manifest["passenger_seats"],
            "advanced_chassis": opts.advanced_chassis,
            "achassis_real": achassis_real,
            **achassis_extra,
            **({"top_speed": top_speed} if top_speed else {}),
        }, name, glb_name if "glb" in fmts else "the .obj files")
        (root / f"{name}_setup.lua").write_text(rig)
        result.files.append(root / f"{name}_setup.lua")

    (root / "IMPORT_INSTRUCTIONS.txt").write_text(_instructions(name, opts, fmts, wheels, size))
    result.files.append(root / "IMPORT_INSTRUCTIONS.txt")

    try:                                  # a picture of the converted car, handy for the Roblox upload page
        from . import preview as pv
        scene = pv.build_scene(pieces, model.textures, size, max_tris=30000)
        (root / "preview.png").write_bytes(pv.png_bytes(pv.render(scene, 960, 600, ssaa=2)))
        result.files.append(root / "preview.png")
        result.preview = root / "preview.png"
    except Exception as exc:  # noqa: BLE001 - the preview is optional, never fail the conversion for it
        warnings.append(f"preview.png was not written ({exc})")

    result.pieces = len(pieces)
    result.triangles = manifest["triangles"]
    result.wheels = len(wheels)
    result.manifest = manifest
    biggest = max(p.mesh.tri_count for p in pieces)
    log(f"Done: {len(pieces)} pieces, {result.triangles} triangles (largest piece {biggest}), "
        f"{len(wheels)} wheels -> {root}")
    for w in warnings:
        log(f"  warning: {w}")
    return result


def preview_model(path: str, opts: Optional[Options] = None, log: Log = print,
                  max_tris: int = 14000):
    """Load and build the pieces exactly like convert() but write nothing.

    Returns ``(scene, info)``: a ``preview.Scene`` ready for ``preview.render`` and a dict with the
    name, source, size in studs, piece / triangle / wheel counts and the warnings (incl. Safety: lines).
    """
    from .preview import build_scene
    opts = opts or Options()
    warnings: List[str] = []
    model = load_model(path, opts, log)
    warnings.extend(model.notes)
    pieces, wheels, size = build_pieces(model, opts, log, warnings)
    scene = build_scene(pieces, model.textures, size, max_tris=max_tris)
    info = {"name": sanitize(opts.name or model.name, "Car"), "source": model.source,
            "size": tuple(float(x) for x in size), "pieces": len(pieces),
            "triangles": int(sum(p.mesh.tri_count for p in pieces)), "wheels": len(wheels),
            "warnings": warnings}
    return scene, info


def _clean_previous_output(root: Path) -> None:
    """Remove files this tool wrote on an earlier run so stale pieces cannot end up in a bulk import."""
    for sub, patterns in (("obj", ("*.obj", "*.mtl", "*.png")), ("textures", ("*.png",))):
        d = root / sub
        if d.is_dir():
            for pat in patterns:
                for f in d.glob(pat):
                    try:
                        f.unlink()
                    except OSError:
                        pass


def _instructions(name: str, opts: Options, fmts: set, wheels, size) -> str:
    lines = [f"{name} - import into Roblox Studio", "=" * 44, ""]
    if "glb" in fmts:
        lines += [
            "OPTION A (recommended): one file",
            f"  1. In Studio choose File > Import and pick {name}.glb.",
            "  2. In the import window set Scale Unit = Studs (the default) and tick "
            "'Import Only As Model', then import.",
            "     Textures travel inside the .glb.",
            "",
        ]
    if "obj" in fmts:
        lines += [
            "OPTION B: separate OBJ pieces" + (" (fallback)" if "glb" in fmts else ""),
            "  1. File > Import, then select every .obj in the 'obj' folder at once "
            "(keep the .mtl and .png files next to them).",
            "  2. Scale Unit = Studs, 'Import Only As Model' ticked.",
            "  Roblox's docs do not say whether the .mtl textures are applied for OBJ. If the parts "
            "arrive untextured, upload the PNGs from the 'textures' folder as images and paste their "
            "ids into each MeshPart's TextureID.",
            "",
        ]
    if opts.rig:
        lines += [
            "BUILD THE CAR",
            "  1. Select the imported Model in the Explorer.",
            f"  2. Open {name}_setup.lua, copy everything and paste it into the Command Bar "
            "(View tab > Command Bar), press Enter.",
            "  3. Press Play, walk up to the car and press E at a seat to get in (\"Drive\" for the "
            "driver's seat, \"Sit\" for any passenger seats; the car spawns a couple of studs above "
            "the ground). The body does not collide, so you can walk straight through it to a seat.",
            "  Car drives backwards -> set DriveSign to 1.  Steering inverted -> SteerSign to 1.",
            "  Both values (and top speed) are also attributes on the finished Model.",
            "",
        ]
    lines += [
        f"Car size: {size[0]:.1f} wide, {size[1]:.1f} tall, {size[2]:.1f} long studs; "
        f"{len(wheels)} wheel(s) detected.",
        "Front of the car points to -Z (Roblox's forward). If the model imported facing the other "
        "way, re-run the converter with --flip-forward.",
        "manifest.json lists every piece (position, size, material, texture).",
        "",
        "Only convert models you made or are licensed to use: GTA and BeamNG assets belong to "
        "their creators, and Roblox's rules apply to anything you upload.",
    ]
    return "\n".join(lines) + "\n"


def inspect_model(path: str, opts: Optional[Options] = None, log: Log = print) -> str:
    """Load a model and describe it without writing any files."""
    opts = opts or Options()
    model = load_model(path, opts, lambda s: None)
    out = [f"{model.name}  [{model.source}]  {len(model.parts)} parts, {model.tri_count()} triangles, "
           f"{len(model.materials)} materials, {len(model.textures)} textures"]
    for p in model.parts:
        lo, hi = p.mesh.bounds()
        out.append(f"  {p.name:<32} {p.mesh.tri_count:>7} tris  size "
                   f"{hi[0]-lo[0]:.2f} x {hi[1]-lo[1]:.2f} x {hi[2]-lo[2]:.2f}")
    for n in model.notes:
        out.append(f"  note: {n}")
    text = "\n".join(out)
    log(text)
    return text
