"""Collada (``.dae``) reader: scene graph, triangulated geometry, materials and textures.

The result is expressed in a **Z-up, metre** space (the file's ``up_axis`` and ``unit`` are
applied), with node transforms baked into the vertices.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .model import ConversionError, Mesh


@dataclass
class DaeMaterial:
    name: str
    color: Optional[Tuple[float, float, float, float]] = None
    texture_path: Optional[str] = None
    transparent: bool = False


@dataclass
class DaeInstance:
    node_name: str
    geometry_name: str
    mesh: Mesh                       # material_ids index DaeScene.material_names
    # (names of the materials used are looked up through scene.material_names)


@dataclass
class DaeScene:
    up_axis: str = "Z_UP"
    unit_meters: float = 1.0
    material_names: List[str] = field(default_factory=list)
    materials: Dict[str, DaeMaterial] = field(default_factory=dict)
    instances: List[DaeInstance] = field(default_factory=list)


def _strip_ns(root: ET.Element) -> None:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _floats(text: Optional[str]) -> np.ndarray:
    if not text or not text.strip():
        return np.zeros(0)
    return np.array(text.split(), dtype=np.float64)


def _ints(text: Optional[str]) -> np.ndarray:
    if not text or not text.strip():
        return np.zeros(0, dtype=np.int64)
    return np.array(text.split(), dtype=np.int64)


def _ref(url: Optional[str]) -> str:
    return (url or "").lstrip("#")


def _node_matrix(node: ET.Element) -> np.ndarray:
    M = np.eye(4)
    for ch in node:
        t = ch.tag
        if t == "matrix":
            v = _floats(ch.text)
            if len(v) == 16:
                M = M @ v.reshape(4, 4)
        elif t == "translate":
            v = _floats(ch.text)
            if len(v) == 3:
                T = np.eye(4)
                T[:3, 3] = v
                M = M @ T
        elif t == "rotate":
            v = _floats(ch.text)
            if len(v) == 4:
                axis = v[:3]
                nrm = np.linalg.norm(axis)
                if nrm > 0:
                    x, y, z = axis / nrm
                    a = np.radians(v[3])
                    c, s = np.cos(a), np.sin(a)
                    R = np.array([
                        [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                        [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                        [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])
                    T = np.eye(4)
                    T[:3, :3] = R
                    M = M @ T
        elif t == "scale":
            v = _floats(ch.text)
            if len(v) == 3:
                M = M @ np.diag([v[0], v[1], v[2], 1.0])
    return M


class _Geom:
    """Parsed (still un-instanced) geometry: arrays + per-symbol primitive groups."""
    def __init__(self) -> None:
        self.name = ""
        self.pos = np.zeros((0, 3))
        self.uv: Optional[np.ndarray] = None
        self.nrm: Optional[np.ndarray] = None
        self.ptri = np.zeros((0, 3), np.int64)
        self.utri: Optional[np.ndarray] = None
        self.ntri: Optional[np.ndarray] = None
        self.symbol_ids = np.zeros(0, np.int32)
        self.symbols: List[str] = []


def _source_array(mesh_el: ET.Element, src_id: str, want: int) -> np.ndarray:
    for src in mesh_el.findall("source"):
        if src.get("id") == src_id:
            arr = src.find("float_array")
            if arr is None:
                continue
            data = _floats(arr.text)
            stride = 0
            acc = src.find("technique_common/accessor")
            if acc is not None and acc.get("stride"):
                stride = int(acc.get("stride"))
            stride = stride or want
            if stride < want:
                raise ConversionError(f"Collada source '{src_id}' has too few components")
            return data[: (len(data) // stride) * stride].reshape(-1, stride)[:, :want]
    raise ConversionError(f"Collada source '{src_id}' not found")


def _parse_geometry(g_el: ET.Element) -> Optional[_Geom]:
    mesh = g_el.find("mesh")
    if mesh is None:
        return None
    vertices_map: Dict[str, str] = {}
    for v in mesh.findall("vertices"):
        for inp in v.findall("input"):
            if inp.get("semantic") == "POSITION":
                vertices_map[v.get("id", "")] = _ref(inp.get("source"))
    out = _Geom()
    out.name = g_el.get("name") or g_el.get("id") or ""
    pos_src = None
    ptri_parts, utri_parts, ntri_parts, sym_parts = [], [], [], []
    have_uv = have_n = False
    uv_arr = n_arr = None
    symbols: List[str] = []

    prims = [p for p in mesh if p.tag in ("triangles", "polylist", "polygons")]
    for pi, prim in enumerate(prims):
        inputs = prim.findall("input")
        if not inputs:
            continue
        stride = max(int(i.get("offset", 0)) for i in inputs) + 1
        off_v = off_n = off_t = None
        t_set = 1 << 30
        for i in inputs:
            sem, off, src = i.get("semantic"), int(i.get("offset", 0)), _ref(i.get("source"))
            if sem == "VERTEX":
                off_v = off
                pos_id = vertices_map.get(src, src)
                if pos_src is None:
                    pos_src = pos_id
                    out.pos = _source_array(mesh, pos_id, 3)
            elif sem == "NORMAL" and off_n is None:
                off_n = off
                if n_arr is None:
                    n_arr = _source_array(mesh, src, 3)
            elif sem == "TEXCOORD":
                s = int(i.get("set", 0))
                if off_t is None or s < t_set:
                    off_t, t_set = off, s
                    uv_arr = _source_array(mesh, src, 2)
        if off_v is None:
            continue

        # gather the index stream as (corners, stride)
        if prim.tag == "triangles":
            p = _ints(prim.findtext("p"))
            idx = p[: (len(p) // stride) * stride].reshape(-1, stride)
            corner_tris = (len(idx) // 3) * 3
            idx = idx[:corner_tris]
            tri_corner = np.arange(corner_tris).reshape(-1, 3)
        else:
            if prim.tag == "polylist":
                vcount = _ints(prim.findtext("vcount"))
                p = _ints(prim.findtext("p"))
                idx = p[: (len(p) // stride) * stride].reshape(-1, stride)
                if len(vcount) and (vcount == 3).all():
                    tri_corner = np.arange(int(vcount.sum())).reshape(-1, 3)
                else:
                    starts = np.concatenate([[0], np.cumsum(vcount)[:-1]])
                    rows = []
                    for s0, n in zip(starts, vcount):
                        for k in range(1, int(n) - 1):
                            rows.append((s0, s0 + k, s0 + k + 1))
                    tri_corner = np.array(rows, dtype=np.int64).reshape(-1, 3)
            else:  # polygons: one <p> per polygon
                chunks, rows, base = [], [], 0
                for ptxt in prim.findall("p"):
                    q = _ints(ptxt.text)
                    q = q[: (len(q) // stride) * stride].reshape(-1, stride)
                    for k in range(1, len(q) - 1):
                        rows.append((base, base + k, base + k + 1))
                    chunks.append(q)
                    base += len(q)
                idx = np.concatenate(chunks) if chunks else np.zeros((0, stride), np.int64)
                tri_corner = np.array(rows, dtype=np.int64).reshape(-1, 3)
        if len(tri_corner) == 0:
            continue
        ptri_parts.append(idx[tri_corner, off_v])
        if off_t is not None:
            utri_parts.append(idx[tri_corner, off_t])
        else:
            utri_parts.append(np.zeros_like(tri_corner))
        if off_n is not None:
            ntri_parts.append(idx[tri_corner, off_n])
        else:
            ntri_parts.append(np.zeros_like(tri_corner))
        have_uv |= off_t is not None
        have_n |= off_n is not None
        sym = prim.get("material") or f"__{pi}"
        if sym not in symbols:
            symbols.append(sym)
        sym_parts.append(np.full(len(tri_corner), symbols.index(sym), np.int32))

    if not ptri_parts:
        return None
    out.ptri = np.concatenate(ptri_parts)
    out.symbol_ids = np.concatenate(sym_parts)
    out.symbols = symbols
    if have_uv and uv_arr is not None:
        out.uv, out.utri = uv_arr, np.concatenate(utri_parts)
    if have_n and n_arr is not None:
        out.nrm, out.ntri = n_arr, np.concatenate(ntri_parts)
    return out


def _parse_effects(root: ET.Element) -> Dict[str, DaeMaterial]:
    images: Dict[str, str] = {}
    for im in root.findall("library_images/image"):
        init = im.findtext("init_from") or ""
        images[im.get("id", "")] = init.strip()
        if im.get("name"):
            images.setdefault(im.get("name"), init.strip())

    effects: Dict[str, dict] = {}
    for eff in root.findall("library_effects/effect"):
        info: dict = {"color": None, "tex": None, "transparent": False}
        newparams = {np_.get("sid"): np_ for np_ in eff.iter("newparam")}
        for shader in ("phong", "blinn", "lambert", "constant"):
            sh = eff.find(f".//{shader}")
            if sh is None:
                continue
            d = sh.find("diffuse") if shader != "constant" else sh.find("emission")
            if d is not None:
                col = d.find("color")
                if col is not None and col.text:
                    v = _floats(col.text)
                    if len(v) >= 3:
                        info["color"] = (float(v[0]), float(v[1]), float(v[2]),
                                         float(v[3]) if len(v) > 3 else 1.0)
                tx = d.find("texture")
                if tx is not None:
                    ref = tx.get("texture", "")
                    path = None
                    node = newparams.get(ref)
                    if node is not None:
                        src = node.findtext("sampler2D/source")
                        surf = newparams.get(src) if src else None
                        if surf is not None:
                            img_id = (surf.findtext("surface/init_from") or "").strip()
                            path = images.get(img_id, img_id or None)
                    if path is None:
                        path = images.get(ref)
                    info["tex"] = path
            tr = sh.find("transparency/float")
            if tr is not None and tr.text:
                try:
                    if float(tr.text) < 0.99 and (sh.find("transparent") is not None):
                        info["transparent"] = True
                except ValueError:
                    pass
            break
        effects[eff.get("id", "")] = info

    mats: Dict[str, DaeMaterial] = {}
    for m in root.findall("library_materials/material"):
        mid = m.get("id", "")
        name = m.get("name") or re.sub(r"-material$", "", mid)
        eff = effects.get(_ref(m.find("instance_effect").get("url")) if m.find("instance_effect") is not None else "", {})
        dm = DaeMaterial(name=name, color=eff.get("color"), texture_path=eff.get("tex"),
                         transparent=eff.get("transparent", False))
        mats[mid] = dm
    return mats


def parse_collada(data: bytes) -> DaeScene:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConversionError(f"not a valid Collada file: {exc}") from exc
    _strip_ns(root)
    if root.tag != "COLLADA":
        raise ConversionError("not a Collada (.dae) file")

    scene = DaeScene()
    asset = root.find("asset")
    if asset is not None:
        scene.up_axis = (asset.findtext("up_axis") or "Z_UP").strip()
        u = asset.find("unit")
        if u is not None and u.get("meter"):
            try:
                scene.unit_meters = float(u.get("meter"))
            except ValueError:
                pass

    mats_by_id = _parse_effects(root)
    geoms: Dict[str, _Geom] = {}
    for g in root.findall("library_geometries/geometry"):
        pg = _parse_geometry(g)
        if pg is not None:
            geoms[g.get("id", "")] = pg
    controllers: Dict[str, str] = {}
    for c in root.findall("library_controllers/controller"):
        skin = c.find("skin")
        if skin is not None:
            controllers[c.get("id", "")] = _ref(skin.get("source"))
    lib_nodes = {n.get("id", ""): n for n in root.findall("library_nodes/node")}

    def mat_index(name: str) -> int:
        if name not in scene.material_names:
            scene.material_names.append(name)
        return scene.material_names.index(name)

    def emit(node: ET.Element, world: np.ndarray, inst: ET.Element, gid: str) -> None:
        g = geoms.get(gid)
        if g is None:
            return
        symmap: Dict[str, str] = {}
        for im in inst.findall(".//instance_material"):
            target = _ref(im.get("target"))
            dm = mats_by_id.get(target)
            symmap[im.get("symbol", "")] = dm.name if dm else re.sub(r"-material$", "", target)
            if dm and dm.name not in scene.materials:
                scene.materials[dm.name] = dm
        local_ids = np.array([mat_index(symmap.get(s, s)) for s in g.symbols], dtype=np.int32)
        mesh = Mesh.build(g.pos, g.ptri, local_ids[g.symbol_ids], g.uv, g.utri, g.nrm, g.ntri)
        if mesh.uvs is not None:
            mesh.uvs[:, 1] = 1.0 - mesh.uvs[:, 1]          # Collada v-up -> image space
        A, t = world[:3, :3], world[:3, 3]
        mesh = mesh.transformed(A, t)
        name = node.get("name") or node.get("id") or g.name
        scene.instances.append(DaeInstance(name, g.name, mesh))

    def walk(node: ET.Element, parent: np.ndarray, depth: int = 0) -> None:
        if depth > 64:
            return
        world = parent @ _node_matrix(node)
        for inst in node.findall("instance_geometry"):
            emit(node, world, inst, _ref(inst.get("url")))
        for inst in node.findall("instance_controller"):
            gid = controllers.get(_ref(inst.get("url")), "")
            emit(node, world, inst, gid)
        for inst in node.findall("instance_node"):
            ref = lib_nodes.get(_ref(inst.get("url")))
            if ref is not None:
                walk(ref, world, depth + 1)
        for ch in node.findall("node"):
            walk(ch, world, depth + 1)

    visual = root.find("library_visual_scenes/visual_scene")
    if visual is None:
        raise ConversionError("Collada file has no visual scene")
    for n in visual.findall("node"):
        walk(n, np.eye(4))

    # Fold up-axis / unit into the vertices: result is Z-up metres.
    up = scene.up_axis.upper()
    if up == "Y_UP":
        A = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float64)   # (x,y,z)->(x,-z,y)
    elif up == "X_UP":
        A = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.float64)    # (x,y,z)->(y,z,x)
    else:
        A = np.eye(3)
    for inst in scene.instances:
        inst.mesh = inst.mesh.transformed(A * scene.unit_meters)
    scene.up_axis = "Z_UP"
    scene.unit_meters = 1.0
    if not scene.instances:
        raise ConversionError("Collada file contains no triangle geometry")
    return scene
