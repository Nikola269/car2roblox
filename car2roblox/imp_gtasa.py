"""GTA San Andreas (and III / Vice City PC) vehicle importer: ``.dff`` + ``.txd``."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from .classify import NOT_WHEEL, is_lod_name, tokens
from .model import AxisSpec, ConversionError, Material, Mesh, Model, Part, Texture, WheelHint
from .rwparse import Dff, DffGeometry, parse_dff, parse_txd, RWError

# GTA marks repaintable surfaces with these magic material colours.
PAINT_MARKERS = {
    (60, 255, 0): "primary",
    (255, 0, 175): "secondary",
    (0, 255, 255): "tertiary",
    (255, 255, 0): "quaternary",
}
WHEEL_DUMMIES = {
    "wheel_lf_dummy": "FL", "wheel_rf_dummy": "FR",
    "wheel_lb_dummy": "RL", "wheel_rb_dummy": "RR",
    "wheel_lm_dummy": "ML", "wheel_rm_dummy": "MR",
}
# Right-hand wheels are turned 180 degrees about the vertical (Z) axis.
_FLIP_Z = np.diag([-1.0, -1.0, 1.0])


def _txd_candidates(dff_path: Path, given: Sequence[str]) -> List[Path]:
    out: List[Path] = [Path(p) for p in given]
    for name in (dff_path.stem + ".txd", "vehicle.txd", "generic.txd"):
        p = dff_path.with_name(name)
        if p.exists() and p not in out:
            out.append(p)
    return out


def _load_textures(paths: Sequence[Path], log: Callable[[str], None]) -> Dict[str, Texture]:
    textures: Dict[str, Texture] = {}
    for p in paths:
        try:
            imgs = parse_txd(p.read_bytes())
        except (OSError, RWError) as exc:
            log(f"  ! could not read {p.name}: {exc}")
            continue
        log(f"  {p.name}: {len(imgs)} textures")
        for name, img in imgs.items():
            textures.setdefault(name, Texture(name, img))   # earlier files win
    return textures


def _geometry_mesh(g: DffGeometry, matmap: List[int]) -> Mesh:
    if matmap:
        mats = np.asarray(matmap, dtype=np.int32)[np.clip(g.tri_materials, 0, len(matmap) - 1)]
    else:
        mats = np.zeros(len(g.triangles), dtype=np.int32)
    normals = g.normals
    if normals is not None and not np.any(normals):
        normals = None
    return Mesh(g.positions, g.triangles, mats, normals, g.uvs)


def import_gta_sa(dff_path: str, txd_paths: Sequence[str] = (), include_lod: bool = False,
                  log: Callable[[str], None] = print) -> Model:
    path = Path(dff_path)
    try:
        dff: Dff = parse_dff(path.read_bytes())
    except OSError as exc:
        raise ConversionError(f"cannot read {path}: {exc}") from exc

    model = Model(name=path.stem, source="gta_sa", axes=AxisSpec("+x", "+z", "+y"), unit_meters=1.0)
    log(f"Reading {path.name}: {len(dff.frames)} frames, {len(dff.geometries)} geometries, "
        f"{len(dff.atomics)} atomics")

    txd_files = _txd_candidates(path, txd_paths)
    if not txd_files:
        model.notes.append("No .txd found next to the .dff - exporting flat colours only. "
                           "Pass --txd to add textures.")
    model.textures = _load_textures(txd_files, log)

    # ---- materials (per geometry -> global ids)
    geom_matmaps: List[List[int]] = []
    missing: set = set()
    for g in dff.geometries:
        ids: List[int] = []
        for dm in g.materials:
            r, gg, b, a = dm.color
            paint = PAINT_MARKERS.get((r, gg, b)) if not dm.texture else None
            tex = dm.texture.lower() if dm.texture else None
            if tex and tex not in model.textures:
                missing.add(tex)
                tex = None
            name = tex or (f"paint_{paint}" if paint else f"color_{r:02x}{gg:02x}{b:02x}")
            # a < 191 (~0.75): real SA glass sits around 90-180; values above that (up to 255) are
            # export/quantisation noise or deliberate near-opaque shading (AO/dirt/decal layers on a
            # solid panel), not an intentionally see-through material. The old cut-off (217) still
            # let a lot of that noise through as "transparent", which is what mis-classified solid
            # chassis/cage/trim parts as "glass" - they inherit the Roblox Glass material and render
            # as a translucent ghost double over the real body. classify_part() also refuses to turn
            # a part named like a body panel into glass from this flag alone, as a second line of
            # defence if a model's noise alpha is still low enough to slip under this cut-off.
            ids.append(model.add_material(Material(
                name=name, color=(r / 255, gg / 255, b / 255, a / 255), texture=tex,
                paint=paint, transparent=a < 191)))
        geom_matmaps.append(ids)
    if missing:
        model.notes.append("Textures referenced but not found in any TXD: "
                           + ", ".join(sorted(missing)))

    # ---- parts
    wheel_template = None
    have_wheel_geo = set()
    parts: List[Part] = []
    skipped = 0
    for frame_idx, geom_idx in dff.atomics:
        if not (0 <= frame_idx < len(dff.frames)) or not (0 <= geom_idx < len(dff.geometries)):
            continue
        fname = dff.frames[frame_idx].name
        low = fname.lower()
        if low.endswith("_dam") or (not include_lod and is_lod_name(low)):
            skipped += 1
            continue
        mesh = _geometry_mesh(dff.geometries[geom_idx], geom_matmaps[geom_idx])
        if mesh.tri_count == 0:
            continue
        R, t = dff.world(frame_idx)
        # a detailed car's own "wheel_brake_disc" or "wheel_hub_f" is hardware next to the wheel,
        # not the wheel itself - swallowing it into the wheel template put it on every wheel dummy
        if low == "wheel" or (low.startswith("wheel") and not low.endswith("_dummy")
                              and low not in WHEEL_DUMMIES and not (set(tokens(low)) & NOT_WHEEL)):
            if wheel_template is None or low == "wheel":
                wheel_template = (mesh, R)
            continue
        slot = WHEEL_DUMMIES.get(low)
        if slot:
            have_wheel_geo.add(slot)
        parts.append(Part(fname, mesh.transformed(R, t), wheel_slot=slot,
                          kind_hint="wheel" if slot else None))
    if skipped:
        log(f"  skipped {skipped} damage/LOD objects")

    # ---- instance the single 'wheel' model at each wheel dummy
    hubs = {}
    for i, f in enumerate(dff.frames):
        slot = WHEEL_DUMMIES.get(f.name.lower())
        if slot:
            hubs[slot] = dff.world(i)[1]
    if hubs:                           # where the wheels belong, used if the wheel model is missing
        radius = width = None
        if wheel_template is not None:
            lo, hi = wheel_template[0].transformed(wheel_template[1]).bounds()
            dims = sorted(float(v) for v in (hi - lo))
            radius, width = dims[2] / 2.0, dims[0]
        for slot, hub in sorted(hubs.items()):
            model.wheel_hints.append(WheelHint(tuple(float(x) for x in hub), radius, width, slot))
    if wheel_template is not None and hubs:
        wmesh, wR = wheel_template
        for slot, hub in sorted(hubs.items()):
            if slot in have_wheel_geo:
                continue
            side = _FLIP_Z if slot.endswith("R") else np.eye(3)
            parts.append(Part(f"wheel_{slot}", wmesh.transformed(side @ wR, hub),
                              wheel_slot=slot, kind_hint="wheel"))
        log(f"  placed wheel model at {len(hubs)} wheel dummies")
    elif wheel_template is not None:
        parts.append(Part("wheel", wheel_template[0].transformed(wheel_template[1]),
                          kind_hint="wheel"))
        model.notes.append("No wheel_*_dummy frames: wheel model exported once, unplaced.")
    elif hubs and not have_wheel_geo:
        model.notes.append("Wheel dummies found but no 'wheel' model to place on them.")

    if not parts:
        raise ConversionError("the DFF contains no usable geometry after filtering")
    model.parts = parts
    return model
