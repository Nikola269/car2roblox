"""BeamNG.drive vehicle importer: mod ``.zip`` or an unpacked vehicle folder.

Reads the ``.dae`` meshes, resolves which meshes belong to the chosen part configuration
(``.pc`` file, or the default slot chain) through the JBeam ``flexbodies`` sections, and
picks up textures / paint flags from ``*.materials.json``.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

from . import jbeam
from .classify import NOT_WHEEL, is_lod_name, slot_from_name, tokens
from .collada import DaeScene, parse_collada
from .model import AxisSpec, ConversionError, Material, Mesh, Model, Part, Texture, WheelHint
from .safety import scan_files
from .textures import load_image_bytes, norm_name

_SKIP_NODE = re.compile(r"^(col(lision|mesh)?[-_]?\d*|start\d*|base\d*|bounds?|detail\d*|"
                        r"collision.*|colmesh.*|ground.*|shadow.*)$", re.I)
_TEX_KEYS = ("baseColorMap", "colorMap", "diffuseMap")
_IMG_EXT = (".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp")
# Blender gives every duplicated object a unique name ("Wheel", "Wheel.001", "Wheel.002", ...) even
# when all of them share one mesh - a modder who Shift+D's a wheel rim around the car ends up with
# exactly that. Collada exporters also routinely suffix the <geometry> id with "-mesh"/"_mesh". A
# mod's JBeam always refers to the plain name, so matching .dae node/geometry names against it
# exactly makes every duplicate past the first one invisible: it is dropped as "not in the archive",
# and if it was a wheel, that corner is left with only whatever hardware (caliper, hub...) shares its
# JBeam group - exactly the "fewer wheels than expected, debris at the arch" symptom. Strip the
# duplicate/export suffix only as a fallback, once an exact match has already failed, so this can
# only recover a mesh that direct matching would have missed.
_DUP_SUFFIX = re.compile(r"[._-](?:\d{3,4}|mesh|geom|geometry|shape)$", re.I)


def _normalize_mesh_key(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _DUP_SUFFIX.sub("", s)
    return s


class Store:
    """Read-only view over a zip file or a directory, with case-insensitive lookups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: Optional[zipfile.ZipFile] = None
        self.index: Dict[str, str] = {}            # lower-case posix path -> real name
        if path.is_dir():
            for p in path.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(path).as_posix()
                    self.index[rel.lower()] = rel
        elif zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
            for n in self._zip.namelist():
                if not n.endswith("/"):
                    self.index[n.replace("\\", "/").lower()] = n
        else:
            raise ConversionError(f"{path} is neither a folder nor a .zip file")
        self.by_base: Dict[str, List[str]] = defaultdict(list)
        for low in self.index:
            self.by_base[posixpath.basename(low)].append(low)
        self.prefixes: Set[str] = {""}

    def read(self, low_path: str) -> bytes:
        real = self.index[low_path]
        if self._zip is not None:
            return self._zip.read(real)
        return (self.path / real).read_bytes()

    def find(self, ref: str) -> Optional[str]:
        """Resolve a BeamNG resource path (rooted at the game's data folder) to a stored file."""
        p = ref.replace("\\", "/").strip().lstrip("/").lower()
        if not p:
            return None
        stem, ext = posixpath.splitext(p)
        variants = [p] + [stem + e for e in _IMG_EXT if e != ext]
        for pre in self.prefixes:
            for v in variants:
                if pre + v in self.index:
                    return pre + v
        base = posixpath.basename(p)
        for b in [base] + [posixpath.basename(stem) + e for e in _IMG_EXT]:
            hits = self.by_base.get(b)
            if hits:
                return sorted(hits, key=len)[0]
        return None


def _vehicle_dirs(store: Store) -> Dict[str, Tuple[str, str]]:
    """{display name: (prefix, 'prefix + vehicles/<name>/')} found in the store."""
    found: Dict[str, Tuple[str, str]] = {}
    rx = re.compile(r"^(.*?/)?vehicles/([^/]+)/")
    for low in store.index:
        m = rx.match(low)
        if m and m.group(2) != "common":
            pre = m.group(1) or ""
            found.setdefault(m.group(2), (pre, f"{pre}vehicles/{m.group(2)}/"))
    return found


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _table(section: Any) -> List[dict]:
    """A JBeam table (header row, then rows, with ``{...}`` modifier rows in between) as dicts keyed
    by the header names. Modifier dicts persist for the rows after them; a dict at the end of a row
    applies to that row only. They are returned under the key ``_mods``."""
    if not isinstance(section, list):
        return []
    header: Optional[List[str]] = None
    sticky: Dict[str, Any] = {}
    out: List[dict] = []
    for row in section:
        if isinstance(row, dict):
            sticky.update(row)
            continue
        if not isinstance(row, list) or not row:
            continue
        if header is None:
            if all(isinstance(c, str) for c in row):
                header = [re.sub(r"[\[\]:]", "", c) for c in row]
            continue
        mods = dict(sticky)
        cells = row
        if isinstance(row[-1], dict):
            mods.update(row[-1])
            cells = row[:-1]
        rec = {h: v for h, v in zip(header, cells)}
        rec["_mods"] = mods
        out.append(rec)
    return out


def _resolve_default_parts(parts: Dict[str, dict]) -> List[str]:
    """Follow the slot chain from the main part, taking each slot's default part. Understands
    both ``slots`` (type, default) and the newer ``slots2`` (name, allowTypes, denyTypes, default)."""
    main = [n for n, p in parts.items() if isinstance(p, dict) and p.get("slotType") == "main"]
    chosen: List[str] = []
    stack = list(main)
    while stack:
        name = stack.pop()
        if name in chosen or name not in parts:
            continue
        chosen.append(name)
        for key in ("slots", "slots2"):
            for row in _table(parts[name].get(key)):
                d = row.get("default")
                if isinstance(d, str) and d in parts:
                    stack.append(d)
    return chosen


_ZERO3 = np.zeros(3, dtype=np.float64)


def _slot_offsets(parts_db: Dict[str, dict], active: List[str]) -> Dict[str, np.ndarray]:
    """A BeamNG slot can carry a ``nodeOffset`` (x/y/z) that every part plugged into it is built
    at, on top of that part's own node/flexbody coordinates - the standard way a reusable wheel or
    tyre part (written once, at x~=+-0.5 with y=z=0) is placed at the actual front or rear axle,
    so the same part can serve several track-width/offset presets. Skipping this, front and rear
    wheels built from such a part land at the exact same point (both read as y=z=0), which both
    hides half of them behind the other (dropped as a duplicate placement) and, for the half that
    does get drawn, puts it nowhere near its wheel arch. Collect, for every slot type an active
    part fills, that slot's offset - leaving an axis at 0 contribution when it is driven by a
    formula car2roblox can't evaluate (e.g. a variable track-width), same policy as elsewhere in
    this module, rather than guessing it."""
    offsets: Dict[str, np.ndarray] = {}
    for pn in active:
        part = parts_db.get(pn, {})
        for key in ("slots", "slots2"):
            for row in _table(part.get(key)):
                off = row["_mods"].get("nodeOffset")
                if not isinstance(off, dict):
                    continue
                vals = [_num(off.get(k)) for k in "xyz"]
                if not any(v is not None for v in vals):
                    continue
                vec = np.array([v or 0.0 for v in vals], dtype=np.float64)
                # "slots" rows are keyed (type, default, description) - "type" IS the slotType of
                # whatever part fills it. "slots2" rows are keyed (name, allowTypes, denyTypes,
                # default, description) - the slot's own "name" is an arbitrary label, not a
                # slotType, so the offset is registered under every slotType the slot will accept.
                t = row.get("type")
                if isinstance(t, str):
                    offsets[t] = vec
                for t in row.get("allowTypes") or []:
                    if isinstance(t, str):
                        offsets[t] = vec
    return offsets


def _is_wheel_group(g: str) -> bool:
    tk = tokens(g)
    if not tk or set(tk) & NOT_WHEEL:
        return False
    return tk[0].startswith("wheel") or tk[0] in {"tire", "tires", "tyre", "tyres", "rim", "rims",
                                                  "hubcap", "hubcaps"}


def _flexbody_rows(part: dict, offset: np.ndarray = _ZERO3) -> List[dict]:
    """[{mesh, groups, pos, rot, scale}] for every mesh the part shows. ``offset`` is the owning
    slot's ``nodeOffset`` (see ``_slot_offsets``), added to the authored ``pos`` - the part's own
    coordinates stay local/reusable, the slot is what says where on the car it actually sits."""
    out = []
    for r in _table(part.get("flexbodies")):
        mesh = r.get("mesh")
        if not isinstance(mesh, str) or not mesh or mesh == "mesh":
            continue
        g = r.get("group")
        groups = [x for x in g if isinstance(x, str)] if isinstance(g, list) else (
            [g] if isinstance(g, str) and g else [])
        m = r["_mods"]

        def _known(v: Any) -> bool:
            if isinstance(v, dict):
                return all(_num(v.get(k)) is not None for k in "xyz")
            if isinstance(v, list):
                return len(v) >= 3 and all(_num(c) is not None for c in v[:3])
            return _num(v) is not None

        pos, rot, scale = m.get("pos"), m.get("rot"), m.get("scale")
        # a value made of variables ("$=$trackwidth/2", used for e.g. a mirrored left/right offset)
        # cannot be read, and falling back to its default can place or orient a part somewhere badly
        # wrong; "not given at all" is different (and fine) - it means no override, i.e. identity
        out.append({"mesh": mesh, "groups": groups, "pos": _vec3(pos, (0, 0, 0)) + offset,
                    "rot": _vec3(rot, (0, 0, 0)), "scale": _vec3(scale, (1, 1, 1)),
                    "has_pos": bool(_known(pos) if pos is not None else False) or bool(offset.any()),
                    "pos_bad": pos is not None and not _known(pos),
                    "rot_bad": rot is not None and not _known(rot),
                    "scale_bad": scale is not None and not _known(scale)})
    return out


def _flexbody_meshes(part: dict) -> List[str]:
    return [r["mesh"] for r in _flexbody_rows(part)]


def _vec3(v: Any, default: Tuple[float, float, float]) -> np.ndarray:
    if isinstance(v, dict):
        vals = [_num(v.get(k)) for k in "xyz"]
    elif isinstance(v, list) and len(v) >= 3:
        vals = [_num(c) for c in v[:3]]
    elif _num(v) is not None:
        vals = [_num(v)] * 3
    else:
        vals = [None, None, None]
    return np.array([d if x is None else x for x, d in zip(vals, default)], dtype=np.float64)


def euler_matrix(rot_deg: np.ndarray) -> np.ndarray:
    """BeamNG's flexbody ``rot`` (degrees): intrinsic rotations about Z, then X, then Y."""
    rx, ry, rz = (float(np.radians(a)) for a in rot_deg)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Rx @ Ry


def _with_wheel_parts(parts_db: Dict[str, dict], active: List[str]) -> Tuple[List[str], List[str]]:
    """Some configurations leave the wheel slots empty. Add one wheel part for every wheel slot that
    has none, so the car is not delivered without tyres. Returns (parts, names added)."""
    have = {parts_db[n].get("slotType") for n in active if n in parts_db}
    added: List[str] = []
    by_slot: Dict[str, List[str]] = defaultdict(list)
    for n, p in parts_db.items():
        st = p.get("slotType") if isinstance(p, dict) else None
        if isinstance(st, str) and re.search(r"wheel|tire|tyre", st, re.I) and n not in active:
            if any(_is_wheel_group(g) for r in _flexbody_rows(p) for g in r["groups"]):
                by_slot[st].append(n)
    for st, names in sorted(by_slot.items()):
        if st not in have:
            added.append(sorted(names, key=lambda x: (len(x), x))[0])
    return active + added, added


def _wheel_hints(parts_db: Dict[str, dict], active: List[str],
                 slot_offsets: Optional[Dict[str, np.ndarray]] = None) -> List[WheelHint]:
    slot_offsets = slot_offsets or {}
    nodes: Dict[str, Tuple[float, float, float]] = {}
    for pn in active:
        st = parts_db.get(pn, {}).get("slotType")
        off = slot_offsets.get(st, _ZERO3) if isinstance(st, str) else _ZERO3
        for r in _table(parts_db.get(pn, {}).get("nodes")):
            xyz = [_num(r.get(k)) for k in ("posX", "posY", "posZ")]
            if None not in xyz and r.get("id") is not None:
                nodes[str(r["id"])] = (xyz[0] + off[0], xyz[1] + off[1], xyz[2] + off[2])  # type: ignore[assignment]
    hints: List[WheelHint] = []
    for pn in active:
        for sec in ("pressureWheels", "hubWheels", "wheels"):
            for r in _table(parts_db.get(pn, {}).get(sec)):
                a, b = nodes.get(str(r.get("node1"))), nodes.get(str(r.get("node2")))
                if a is None or b is None:
                    continue
                m = r["_mods"]
                rad = _num(m.get("radius")) or _num(m.get("hubRadius"))
                wid = _num(m.get("tireWidth")) or _num(m.get("hubWidth"))
                if rad is not None and not (0.1 <= rad <= 1.2):
                    rad = None
                if wid is not None and not (0.05 <= wid <= 1.0):
                    wid = None
                c = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)
                hints.append(WheelHint(c, rad, wid, slot_from_name(str(r.get("name", "")))))
    return _name_hints(hints)


def _name_hints(hints: List[WheelHint]) -> List[WheelHint]:
    """Give every hint a slot (FL FR RL RR ...) from where it is: BeamNG has the front at -y and the
    right side at -x. Hints that already carry a slot keep it."""
    if not hints:
        return hints
    axles: List[float] = []                          # axle positions along the car, 0.3 m apart or more
    for y in sorted(h.center[1] for h in hints):
        if not axles or y - axles[-1] > 0.3:
            axles.append(y)
    out: List[WheelHint] = []
    for h in hints:
        slot = h.slot
        if not slot:
            i = min(range(len(axles)), key=lambda k: abs(axles[k] - h.center[1]))
            letter = "F" if i == 0 else ("R" if i == len(axles) - 1 else "M")
            slot = letter + ("L" if h.center[0] > 0 else "R")
        out.append(WheelHint(h.center, h.radius, h.width, slot))
    return out


def _stage_value(stage: Any, keys) -> Optional[str]:
    if isinstance(stage, dict):
        for k in keys:
            v = stage.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return None


def _norm_mat(name: str) -> str:
    return re.sub(r"-material$", "", name.strip().lower())


def import_beamng(path: str, vehicle: Optional[str] = None, config: Optional[str] = None,
                  include_lod: bool = False, log: Callable[[str], None] = print) -> Model:
    src = Path(path)
    store = Store(src)
    vdirs = _vehicle_dirs(store)
    if vehicle:
        key = next((k for k in vdirs if k == vehicle.lower()), None)
        if key is None:
            raise ConversionError(f"vehicle '{vehicle}' not found; available: "
                                  f"{', '.join(sorted(vdirs)) or 'none'}")
        name = key
    elif vdirs:
        def weight(k: str) -> int:
            d = vdirs[k][1]
            return sum(1 for low in store.index if low.startswith(d) and low.endswith((".dae", ".jbeam")))
        name = max(vdirs, key=weight)
        if len(vdirs) > 1:
            log(f"  several vehicles in the archive ({', '.join(sorted(vdirs))}); using '{name}'")
    else:
        # archive/folder is the vehicle folder itself
        name, vdirs = src.stem, {src.stem: ("", "")}
    prefix, vdir = vdirs[name]
    store.prefixes |= {prefix}
    log(f"BeamNG vehicle '{name}' from {src.name}")

    files = [low for low in store.index if low.startswith(vdir)]
    daes = sorted(f for f in files if f.endswith(".dae"))
    jbeams = sorted(f for f in files if f.endswith(".jbeam"))
    pcs = sorted(f for f in files if f.endswith(".pc"))
    mat_files = sorted(f for f in store.index
                       if f.startswith(vdir) or f.startswith(prefix + "vehicles/common/")
                       if f.endswith("materials.json"))
    if not daes:
        raise ConversionError("no .dae mesh files found. BeamNG ships some vehicles as binary "
                              ".cdae caches only - export the car to .dae from Blender or use "
                              "the unpacked mod files.")

    model = Model(name=name, source="beamng", axes=AxisSpec("-x", "+z", "-y"), unit_meters=1.0)
    risky = scan_files(store.index.values())
    if risky:
        shown = ", ".join(f"{posixpath.basename(p)} ({why})" for p, why in risky[:6])
        more = f" and {len(risky) - 6} more" if len(risky) > 6 else ""
        model.notes.append(f"Safety: ignored {len(risky)} risky file(s) in the archive - never opened, "
                           f"run or copied: {shown}{more}")

    # ---------------- jbeam parts / configuration
    parts_db: Dict[str, dict] = {}
    for jf in jbeams:
        try:
            data = jbeam.load_bytes(store.read(jf))
        except (jbeam.JBeamError, KeyError) as exc:
            log(f"  ! skipped {posixpath.basename(jf)}: {exc}")
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    parts_db[k] = v
    active_parts: Optional[List[str]] = None
    pc_data: Optional[dict] = None
    if config:
        pc = next((p for p in pcs if posixpath.splitext(posixpath.basename(p))[0] == config.lower()), None)
        if pc is None:
            raise ConversionError(f"configuration '{config}' not found; available: "
                                  f"{', '.join(posixpath.splitext(posixpath.basename(p))[0] for p in pcs) or 'none'}")
        try:
            pc_data = jbeam.load_bytes(store.read(pc))
        except jbeam.JBeamError as exc:
            raise ConversionError(f"cannot read configuration {config}: {exc}") from exc
        pp = (pc_data or {}).get("parts", {})
        active_parts = [v for v in pp.values() if isinstance(v, str) and v]
        log(f"  configuration '{config}': {len(active_parts)} parts")
    elif parts_db:
        active_parts = _resolve_default_parts(parts_db)
        log(f"  default configuration: {len(active_parts)} parts")

    if active_parts and not config:
        active_parts, extra_parts = _with_wheel_parts(parts_db, active_parts)
        if extra_parts:
            log(f"  the default configuration has no wheels installed; added: {', '.join(extra_parts)}")

    # a slot's nodeOffset (see _slot_offsets) must be known before any node/flexbody position in
    # the part it holds is read, so it is resolved from the final active-part list up front
    slot_offsets = _slot_offsets(parts_db, active_parts or [])

    def _part_offset(pn: str) -> np.ndarray:
        st = parts_db.get(pn, {}).get("slotType")
        return slot_offsets.get(st, _ZERO3) if isinstance(st, str) else _ZERO3

    # every mesh a part shows, with its placement: one wheel mesh is listed once per wheel
    rows_by_mesh: Dict[str, List[dict]] = defaultdict(list)
    for pn in active_parts or []:
        for r in _flexbody_rows(parts_db.get(pn, {}), _part_offset(pn)):
            rows_by_mesh[r["mesh"].lower()].append(r)
    wanted: Optional[Set[str]] = None
    wanted_norm: Dict[str, str] = {}
    if rows_by_mesh:
        wanted = set(rows_by_mesh)
        for w in wanted:
            wanted_norm.setdefault(_normalize_mesh_key(w), w)
    if active_parts:
        model.wheel_hints = _wheel_hints(parts_db, active_parts, slot_offsets)
        if model.wheel_hints:
            log(f"  JBeam lists {len(model.wheel_hints)} wheels")

    # paint defaults from the .pc (BeamNG stores baseColor as [r,g,b,metallic])
    if pc_data and isinstance(pc_data.get("paints"), list):
        for slot, p in zip(("primary", "secondary", "tertiary"), pc_data["paints"]):
            bc = p.get("baseColor") if isinstance(p, dict) else None
            if isinstance(bc, list) and len(bc) >= 3:
                model.default_paint[slot] = tuple(max(0.0, min(1.0, float(c))) for c in bc[:3])

    # ---------------- meshes
    scenes: List[Tuple[str, DaeScene]] = []
    for d in daes:
        try:
            scenes.append((d, parse_collada(store.read(d))))
        except Exception as exc:  # noqa: BLE001 - report and continue with other files
            log(f"  ! skipped {posixpath.basename(d)}: {exc}")
    if not scenes:
        raise ConversionError("none of the .dae files could be read")

    found_names: Set[str] = set()

    def keep(inst) -> bool:
        node, geom = inst.node_name, inst.geometry_name
        if wanted is not None:
            nl, gl = node.lower(), geom.lower()
            hit_keys = {nl, gl} & wanted
            if not hit_keys:
                # a duplicated Blender object ("Wheel.001", from Shift+D'ing a wheel rim around the
                # car) or a geometry id with an exporter-added suffix ("wheel-mesh") won't match the
                # JBeam's plain mesh name exactly - recover it with that suffix stripped, rather than
                # silently dropping the mesh (which, for a wheel, looks like a missing wheel with
                # whatever hardware shares its JBeam group left jammed in the empty arch)
                for k in (nl, gl):
                    w = wanted_norm.get(_normalize_mesh_key(k))
                    if w is not None:
                        hit_keys = {w}
                        break
            if hit_keys:
                found_names.update(hit_keys)
                return True
            return False
        if _SKIP_NODE.match(node) or (not include_lod and is_lod_name(node)):
            return False
        return True

    selected: List[Tuple[DaeScene, Any]] = []
    for _, sc in scenes:
        for inst in sc.instances:
            if keep(inst):
                selected.append((sc, inst))
    if wanted is not None:
        missing = wanted - found_names
        if missing:                       # shared meshes often live in vehicles/common
            for d in sorted(f for f in store.index if f.startswith(prefix + "vehicles/common/") and f.endswith(".dae")):
                try:
                    sc = parse_collada(store.read(d))
                except Exception:        # noqa: BLE001
                    continue
                for inst in sc.instances:
                    if keep(inst):
                        selected.append((sc, inst))
            missing = wanted - found_names
        if not selected:
            log("  ! none of the configuration's flexbody meshes were found in the .dae files; "
                "exporting every visible mesh instead")
            wanted = None
            for _, sc in scenes:
                for inst in sc.instances:
                    if keep(inst):
                        selected.append((sc, inst))
        elif missing:
            model.notes.append(f"{len(missing)} flexbody meshes are not in the exported .dae files "
                               f"(e.g. {', '.join(sorted(missing)[:5])})")
    if not selected:
        raise ConversionError("no renderable meshes were found in the .dae files")

    # ---------------- materials
    matdb: Dict[str, dict] = {}
    for mf in mat_files:
        try:
            data = jbeam.load_bytes(store.read(mf))
        except jbeam.JBeamError as exc:
            log(f"  ! skipped {posixpath.basename(mf)}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            for key in (k, v.get("name"), v.get("mapTo")):
                if isinstance(key, str):
                    matdb.setdefault(_norm_mat(key), v)

    tex_cache: Dict[str, Optional[str]] = {}
    unresolved: Set[str] = set()

    def load_tex(ref: str) -> Optional[str]:
        if ref in tex_cache:
            return tex_cache[ref]
        key = None
        low = store.find(ref)
        if low is None:
            unresolved.add(ref)
        else:
            try:
                img = load_image_bytes(store.read(low))
                key = posixpath.splitext(low)[0]
                model.textures.setdefault(key, Texture(posixpath.basename(key), img))
            except Exception as exc:    # noqa: BLE001
                log(f"  ! texture {posixpath.basename(low)} could not be decoded: {exc}")
        tex_cache[ref] = key
        return key

    def _open_ref(ref: str) -> Optional[Image.Image]:
        low = store.find(ref)
        if low is None:
            unresolved.add(ref)
            return None
        try:
            return load_image_bytes(store.read(low))
        except Exception as exc:        # noqa: BLE001
            log(f"  ! texture {posixpath.basename(low)} could not be decoded: {exc}")
            return None

    merge_cache: Dict[Tuple, Optional[str]] = {}

    def load_layered_tex(base_ref: Optional[str], layer_refs: Tuple[str, ...],
                         paint_color: Optional[Tuple[float, float, float]]) -> Optional[str]:
        """BeamNG composites a material's 'Stages' into one look - a base paint colour or
        livery map with decals, dirt or damage layers painted on top of it using their own
        alpha. Using only stage 0 (the old behaviour) silently dropped every layer after it,
        which is what made a painted car lose its decals/stripes, or a livery lose its grime."""
        if not base_ref and not layer_refs:
            return None
        ck = (base_ref, layer_refs, paint_color)
        if ck in merge_cache:
            return merge_cache[ck]
        if not layer_refs:
            # the common case: one texture, nothing to merge - go through the simple cached path
            key = load_tex(base_ref) if base_ref else None
            merge_cache[ck] = key
            return key
        base_img = _open_ref(base_ref) if base_ref else None
        layer_imgs = [img for r in layer_refs if (img := _open_ref(r)) is not None]
        if base_img is None and paint_color is not None and layer_imgs:
            w, h = layer_imgs[0].size
            rgb = tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in paint_color)
            base_img = Image.new("RGBA", (w, h), rgb + (255,))
        if base_img is None and not layer_imgs:
            merge_cache[ck] = None
            return None
        out = base_img.convert("RGBA") if base_img is not None else layer_imgs[0].convert("RGBA")
        start = 1 if base_img is None else 0
        for limg in layer_imgs[start:]:
            if limg.size != out.size:
                limg = limg.resize(out.size, Image.LANCZOS)
            out = Image.alpha_composite(out, limg.convert("RGBA"))
        name = posixpath.basename(base_ref or layer_refs[0])
        key = f"__merged__/{len(model.textures)}/{name}"
        model.textures[key] = Texture(name, out)
        merge_cache[ck] = key
        return key

    mat_cache: Dict[Tuple[int, str], int] = {}

    def global_material(sc: DaeScene, local: str) -> int:
        ck = (id(sc), local)
        if ck in mat_cache:
            return mat_cache[ck]
        bm = matdb.get(_norm_mat(local))
        dm = sc.materials.get(local)
        color = (1.0, 1.0, 1.0, 1.0)
        tex = None
        paint = None
        transparent = False
        double = False
        if bm:
            stages = bm.get("Stages") or bm.get("stages") or []
            stage_list = [s for s in stages if isinstance(s, dict)] if isinstance(stages, list) else []
            st0 = stage_list[0] if stage_list else {}
            col = None
            for src_ in (st0, bm):
                if isinstance(src_, dict):
                    col = src_.get("baseColorFactor") or src_.get("diffuseColor") or col
            if isinstance(col, list) and len(col) >= 3:
                color = tuple(float(c) for c in (list(col) + [1.0])[:4])  # type: ignore
            instance = bool(st0.get("instanceDiffuse")) or bool(bm.get("instanceDiffuse"))
            base_ref = None if instance else (_stage_value(st0, _TEX_KEYS) or _stage_value(bm, _TEX_KEYS))
            # every stage after the base is a layer painted on top of it (decals, dirt, damage) -
            # merge them all instead of only ever looking at stage 0
            layer_refs = tuple(r for s in stage_list[1:] if (r := _stage_value(s, _TEX_KEYS)))
            if instance:
                paint = "primary"
                color = (1.0, 1.0, 1.0, color[3])
            if base_ref or layer_refs:
                tex = load_layered_tex(base_ref, layer_refs, color[:3] if instance else None)
            transparent = bool(bm.get("translucent"))
            double = bool(bm.get("doubleSided"))
        elif dm:
            if dm.color:
                color = dm.color
            if dm.texture_path:
                tex = load_tex(dm.texture_path)
            transparent = dm.transparent
        if color[3] < 0.98:
            transparent = True
        if "glass" in tokens(local):
            transparent = True
        idx = model.add_material(Material(name=re.sub(r"-material$", "", local) or "material",
                                          color=color, texture=tex, paint=paint,
                                          transparent=transparent, double_sided=double))
        mat_cache[ck] = idx
        return idx

    def hint_for(slot: Optional[str]) -> Optional[WheelHint]:
        return next((h for h in model.wheel_hints if slot and h.slot == slot), None)

    rows_by_mesh_norm: Dict[str, List[dict]] = defaultdict(list)
    for mesh_key, rows in rows_by_mesh.items():
        rows_by_mesh_norm[_normalize_mesh_key(mesh_key)].extend(rows)

    parts: List[Part] = []
    emitted: Set[tuple] = set()
    dropped_formula: List[str] = []
    for sc, inst in selected:
        gl = np.array([global_material(sc, n) for n in sc.material_names], dtype=np.int32)
        m = inst.mesh
        base = Mesh(m.positions, m.triangles, gl[m.material_ids], m.normals, m.uvs)
        if not base.tri_count:
            continue
        node = inst.node_name or inst.geometry_name
        nl, gl_name = inst.node_name.lower(), inst.geometry_name.lower()
        # as in `keep()` above: a duplicated object or a suffixed geometry id can match the JBeam
        # mesh name only once the duplicate/export suffix is stripped
        rows = (rows_by_mesh.get(nl) or rows_by_mesh.get(gl_name)
               or rows_by_mesh_norm.get(_normalize_mesh_key(nl)) or rows_by_mesh_norm.get(_normalize_mesh_key(gl_name))
               or [])
        node_tokens = set(tokens(node))
        # real mods commonly share one JBeam group (e.g. "wheel_FL") between the tyre/rim AND the
        # brake caliper, rotor, hub carrier etc. that live at that corner, so the group alone can't
        # tell the wheel from its hardware - a part whose own mesh name says what it is overrides it
        node_is_hardware = bool(node_tokens & NOT_WHEEL)
        name_wheel = bool({"wheel", "tire", "tyre", "rim"} & node_tokens) and not node_is_hardware
        if not rows:
            key = (node.lower(), base.tri_count, None)
            if key in emitted:
                continue
            emitted.add(key)
            parts.append(Part(node, base, wheel_slot=slot_from_name(node) if name_wheel else None,
                              kind_hint="wheel" if name_wheel and not slot_from_name(node) else None))
            continue
        for k, row in enumerate(rows):
            R = euler_matrix(row["rot"]) @ np.diag(row["scale"])
            mesh = base.transformed(R, row["pos"])
            wheel_groups = [g for g in row["groups"] if _is_wheel_group(g)]
            slot = next((slot_from_name(g) for g in wheel_groups if slot_from_name(g)), None)
            if (wheel_groups or name_wheel) and not slot:
                slot = slot_from_name(node)
            # sharing the corner's wheel group is not enough on its own: hardware named like a
            # brake/hub/suspension part stays out, even when it rides in the "wheel_FL" group -
            # that mismatch is what put a body-painted caliper or hub carrier jammed onto the tyre
            is_wheel = (bool(wheel_groups) or name_wheel) and not node_is_hardware
            # a part placed or oriented from a formula we can't evaluate (e.g. a mirrored side
            # offset) would otherwise fall back to a default that can be badly wrong - unlike a
            # wheel (which gets snapped onto its JBeam hub below), there is no hub to fall back on,
            # so it is better left out, with a warning, than drawn jammed through the bodywork
            if not is_wheel and (row["pos_bad"] or row["rot_bad"] or row["scale_bad"]):
                dropped_formula.append(node)
                continue
            h = hint_for(slot)
            if is_wheel and h is not None:
                lo, hi = mesh.bounds()
                ctr = (lo + hi) / 2.0
                one_wheel = h.radius is None or max(hi[1] - lo[1], hi[2] - lo[2]) <= 2.6 * h.radius
                # a wheel meant for its hub but far from it (modelled at the origin, or a position
                # that could not be read) goes onto the hub; a listed position is trusted more
                far = (0.5 if not row["has_pos"] else 1.5) * max(h.radius or 0.3, 0.3)
                if one_wheel and float(np.linalg.norm(ctr - np.array(h.center))) > far:
                    mesh = mesh.transformed(np.eye(3), np.array(h.center) - ctr)
            # a wheel part is commonly authored once and placed at every corner with identical
            # local pos/rot/scale (see _slot_offsets) - the resolved axle/side slot (FL/FR/RL/RR)
            # is what tells two such corners apart when their raw numbers alone cannot, so it is
            # part of the key for a wheel; without it, a front and a rear wheel built from the same
            # reusable part collide here and one of every pair is silently dropped as "the same
            # placement listed twice", which is what left only 2 of 4 wheels in the output
            key = (node.lower(), tuple(np.round(row["pos"], 4)), tuple(np.round(row["rot"], 3)),
                   tuple(np.round(row["scale"], 4)), base.tri_count, slot if is_wheel else None)
            if key in emitted:                 # the same placement listed twice would z-fight
                continue
            emitted.add(key)
            name = node if len(rows) == 1 else f"{node}_{slot or k + 1}"
            parts.append(Part(name, mesh, wheel_slot=slot if is_wheel else None,
                              kind_hint="wheel" if is_wheel and not slot else None))
    if unresolved:
        model.notes.append(f"{len(unresolved)} textures referenced by materials were not found "
                           f"in the archive (e.g. {sorted(unresolved)[0]}); flat colours used.")
    if dropped_formula:
        shown = ", ".join(sorted(set(dropped_formula))[:5])
        more = f" and {len(set(dropped_formula)) - 5} more" if len(set(dropped_formula)) > 5 else ""
        model.notes.append(f"{len(dropped_formula)} part(s) were left out because BeamNG places or "
                           f"orients them with a formula car2roblox can't compute (e.g. {shown}{more}); "
                           "drawing them with a default position/rotation could jam them through the "
                           "bodywork instead.")
    log(f"  {len(parts)} meshes, {len(model.materials)} materials, {len(model.textures)} textures")
    model.parts = parts
    return model
