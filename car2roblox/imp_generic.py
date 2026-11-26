"""Generic importers for models exported from other tools: OBJ, glTF/GLB and Collada.

This is the route for GTA V cars: open the vehicle in CodeWalker (export) / OpenIV, or in
Blender with the Sollumz add-on, and export OBJ, GLB or DAE - then convert that file here.
"""
from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .collada import parse_collada
from .model import AxisSpec, ConversionError, Material, Mesh, Model, Part, Texture
from .textures import load_image_bytes, norm_name


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.maximum(c, 0), 1 / 2.4) - 0.055)


def _find_file(base: Path, ref: str) -> Optional[Path]:
    """Resolve a texture reference relative to ``base`` (case-insensitive, any slashes)."""
    ref = ref.strip().strip('"').replace("\\", "/")
    if not ref or re.match(r"^[a-z][a-z0-9+.-]*://", ref, re.I):
        return None            # remote links are never followed
    cand = (base / ref)
    if cand.exists():
        return cand
    name = Path(ref).name.lower()
    stem = Path(ref).stem.lower()
    for p in sorted(base.rglob("*")):
        if p.is_file() and (p.name.lower() == name or (p.stem.lower() == stem and
                            p.suffix.lower() in (".png", ".jpg", ".jpeg", ".tga", ".dds", ".bmp"))):
            return p
    return None


# ===================================================================== OBJ
def _parse_mtl(path: Path) -> Dict[str, dict]:
    mats: Dict[str, dict] = {}
    cur = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return mats
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        key, _, rest = s.partition(" ")
        key = key.lower()
        rest = rest.strip()
        if key == "newmtl":
            cur = mats.setdefault(rest, {})
        elif cur is not None:
            if key == "kd":
                try:
                    cur["kd"] = [float(x) for x in rest.split()[:3]]
                except ValueError:
                    pass
            elif key == "d":
                try:
                    cur["d"] = float(rest.split()[0])
                except (ValueError, IndexError):
                    pass
            elif key == "tr":
                try:
                    cur["d"] = 1.0 - float(rest.split()[0])
                except (ValueError, IndexError):
                    pass
            elif key == "map_kd":
                toks = rest.split()
                # skip options like "-s 1 1 1": the file name is the last token(s)
                cur["map_kd"] = " ".join(toks[-1:]) if len(toks) > 1 and toks[0].startswith("-") else rest
    return mats


def import_obj(path: Path, log: Callable[[str], None]) -> Model:
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        raise ConversionError(f"cannot read {path}: {exc}") from exc
    pos: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    nrm: List[Tuple[float, float, float]] = []
    mtl_defs: Dict[str, dict] = {}
    model = Model(name=path.stem, source="generic", axes=AxisSpec("+x", "+y", "-z"),
                  forward_auto=True)
    mat_ids: Dict[str, int] = {}

    def material_for(name: str) -> int:
        if name in mat_ids:
            return mat_ids[name]
        d = mtl_defs.get(name, {})
        kd = d.get("kd", [0.8, 0.8, 0.8])
        alpha = d.get("d", 1.0)
        tex = None
        ref = d.get("map_kd")
        if ref:
            f = _find_file(path.parent, ref)
            if f is None:
                model.notes.append(f"texture '{ref}' referenced by the MTL was not found")
            else:
                key = norm_name(f.name)
                try:
                    if key not in model.textures:
                        model.textures[key] = Texture(key, load_image_bytes(f.read_bytes()))
                    tex = key
                except ConversionError as exc:
                    model.notes.append(f"texture {f.name}: {exc}")
        paint = None
        # 0.75, not 0.85: an MTL 'd' well under 1 is routinely left by exporters for subtle
        # sheen/AO blending, not an intentionally see-through material - see the note by the glTF
        # alphaMode check below for what over-eager "transparent" detection breaks. classify_part()
        # also refuses to turn a part named like a body panel into glass from this flag alone.
        idx = model.add_material(Material(name=name or "material",
                                          color=(kd[0], kd[1], kd[2], alpha), texture=tex,
                                          paint=paint, transparent=alpha < 0.75))
        mat_ids[name] = idx
        return idx

    # object name -> corner lists
    objs: Dict[str, dict] = {}
    cur_obj = path.stem
    cur_mat = ""
    for raw in text.splitlines():
        if not raw or raw[0] == "#":
            continue
        parts = raw.split()
        if not parts:
            continue
        k = parts[0]
        if k == "v":
            pos.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif k == "vt":
            uvs.append((float(parts[1]), 1.0 - (float(parts[2]) if len(parts) > 2 else 0.0)))
        elif k == "vn":
            nrm.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif k == "f":
            corners = []
            for tok in parts[1:]:
                a = tok.split("/")
                vi = int(a[0])
                vi = vi - 1 if vi > 0 else len(pos) + vi
                ti = -1
                if len(a) > 1 and a[1]:
                    t = int(a[1])
                    ti = t - 1 if t > 0 else len(uvs) + t
                ni = -1
                if len(a) > 2 and a[2]:
                    n = int(a[2])
                    ni = n - 1 if n > 0 else len(nrm) + n
                corners.append((vi, ti, ni))
            o = objs.setdefault(cur_obj, {"p": [], "u": [], "n": [], "m": []})
            mid = material_for(cur_mat)
            for j in range(1, len(corners) - 1):
                tri = (corners[0], corners[j], corners[j + 1])
                o["p"].append([c[0] for c in tri])
                o["u"].append([c[1] for c in tri])
                o["n"].append([c[2] for c in tri])
                o["m"].append(mid)
        elif k in ("o", "g"):
            cur_obj = " ".join(parts[1:]) or path.stem
        elif k == "usemtl":
            cur_mat = " ".join(parts[1:])
        elif k == "mtllib":
            for name in " ".join(parts[1:]).split(","):
                mf = _find_file(path.parent, name.strip())
                if mf is not None:
                    mtl_defs.update(_parse_mtl(mf))
    if not pos or not objs:
        raise ConversionError("the OBJ file contains no faces")
    P = np.asarray(pos, dtype=np.float32)
    U = np.asarray(uvs, dtype=np.float32) if uvs else None
    N = np.asarray(nrm, dtype=np.float32) if nrm else None
    for name, o in objs.items():
        ptri = np.asarray(o["p"], dtype=np.int64)
        utri = np.asarray(o["u"], dtype=np.int64)
        ntri = np.asarray(o["n"], dtype=np.int64)
        has_u = U is not None and (utri >= 0).any()
        has_n = N is not None and (ntri >= 0).any()
        if ptri.min() < 0 or ptri.max() >= len(P):
            raise ConversionError("OBJ face refers to a vertex that does not exist")
        mesh = Mesh.build(P, ptri, np.asarray(o["m"], dtype=np.int32),
                          U if has_u else None, utri if has_u else None,
                          N if has_n else None, ntri if has_n else None)
        if mesh.tri_count:
            model.parts.append(Part(name, mesh))
    log(f"OBJ: {len(model.parts)} objects, {sum(p.mesh.tri_count for p in model.parts)} triangles")
    return model


# ===================================================================== glTF / GLB
_CT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class _Gltf:
    def __init__(self, path: Path) -> None:
        raw = path.read_bytes()
        self.dir = path.parent
        self.bin: Optional[bytes] = None
        if raw[:4] == b"glTF":
            _magic, _ver, _length = struct.unpack_from("<4sII", raw, 0)
            off = 12
            js = None
            while off + 8 <= len(raw):
                clen, ctype = struct.unpack_from("<I4s", raw, off)
                body = raw[off + 8: off + 8 + clen]
                if ctype == b"JSON":
                    js = body
                elif ctype == b"BIN\0":
                    self.bin = body
                off += 8 + clen
            if js is None:
                raise ConversionError("GLB has no JSON chunk")
            self.j = json.loads(js.decode("utf-8"))
        else:
            try:
                self.j = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConversionError(f"not a glTF file: {exc}") from exc
        self._buffers: Dict[int, bytes] = {}

    def buffer(self, i: int) -> bytes:
        if i in self._buffers:
            return self._buffers[i]
        b = self.j["buffers"][i]
        uri = b.get("uri")
        if uri is None:
            data = self.bin or b""
        elif uri.startswith("data:"):
            data = base64.b64decode(uri.split(",", 1)[1])
        else:
            f = _find_file(self.dir, uri.replace("%20", " "))
            if f is None:
                raise ConversionError(f"glTF buffer file '{uri}' is missing")
            data = f.read_bytes()
        self._buffers[i] = data
        return data

    def view_bytes(self, vi: int) -> bytes:
        v = self.j["bufferViews"][vi]
        buf = self.buffer(v["buffer"])
        o = v.get("byteOffset", 0)
        return buf[o:o + v["byteLength"]]

    def accessor(self, ai: int) -> np.ndarray:
        a = self.j["accessors"][ai]
        dt = np.dtype(_CT[a["componentType"]])
        nc = _NC[a["type"]]
        count = a["count"]
        if "bufferView" in a:
            v = self.j["bufferViews"][a["bufferView"]]
            buf = self.buffer(v["buffer"])
            base = v.get("byteOffset", 0) + a.get("byteOffset", 0)
            stride = v.get("byteStride") or dt.itemsize * nc
            arr = np.ndarray((count, nc), dtype=dt, buffer=buf, offset=base,
                             strides=(stride, dt.itemsize)).copy()
        else:
            arr = np.zeros((count, nc), dtype=dt)
        if a.get("normalized") and dt.kind in "iu":
            arr = np.maximum(arr.astype(np.float64) / float(np.iinfo(dt).max), -1.0)
        return arr


def import_gltf(path: Path, log: Callable[[str], None]) -> Model:
    g = _Gltf(path)
    j = g.j
    model = Model(name=path.stem, source="generic", axes=AxisSpec("+x", "+y", "-z"),
                  forward_auto=True)

    # textures
    tex_keys: Dict[int, Optional[str]] = {}

    def texture_for(tex_index: int) -> Optional[str]:
        if tex_index in tex_keys:
            return tex_keys[tex_index]
        key = None
        try:
            src = j["textures"][tex_index]["source"]
            im = j["images"][src]
            if "bufferView" in im:
                data = g.view_bytes(im["bufferView"])
            elif im.get("uri", "").startswith("data:"):
                data = base64.b64decode(im["uri"].split(",", 1)[1])
            else:
                f = _find_file(g.dir, im["uri"].replace("%20", " "))
                if f is None:
                    raise ConversionError(f"image '{im.get('uri')}' not found")
                data = f.read_bytes()
            key = norm_name(im.get("name") or f"texture_{src}") + f"_{src}"
            if key not in model.textures:
                model.textures[key] = Texture(key, load_image_bytes(data))
        except (KeyError, ConversionError, IndexError) as exc:
            model.notes.append(f"glTF texture {tex_index} skipped: {exc}")
        tex_keys[tex_index] = key
        return key

    mat_map: Dict[int, int] = {}

    def material_for(mi: Optional[int]) -> int:
        k = -1 if mi is None else mi
        if k in mat_map:
            return mat_map[k]
        color = (0.8, 0.8, 0.8, 1.0)
        tex = None
        name = "material"
        transparent = False
        double = False
        if mi is not None and mi < len(j.get("materials", [])):
            m = j["materials"][mi]
            name = m.get("name") or f"material_{mi}"
            pbr = m.get("pbrMetallicRoughness", {})
            f = pbr.get("baseColorFactor")
            if f:
                rgb = linear_to_srgb(f[:3])
                color = (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(f[3]))
            else:
                color = (1.0, 1.0, 1.0, 1.0)
            if "baseColorTexture" in pbr:
                tex = texture_for(pbr["baseColorTexture"]["index"])
            # glTF's own spec says alpha is only meaningful in "BLEND" mode - in the default
            # "OPAQUE" mode a renderer must ignore it, but real exporters (CodeWalker, Sollumz,
            # Blender) routinely leave a baseColorFactor alpha well under 1 on an OPAQUE material
            # anyway (AO/sheen blending baked into the same channel). Trusting that stray value too
            # readily is what mis-classified solid chassis/cage/trim parts as glass - they'd pick up
            # the Roblox Glass material and render as a translucent ghost double on top of the real
            # body. Only a clearly see-through alpha (<0.75) overrides that for a sloppy exporter that
            # skipped alphaMode entirely but still meant real glass; classify_part() also refuses to
            # turn a part named like a body panel into glass from this flag alone.
            transparent = m.get("alphaMode") == "BLEND" or color[3] < 0.75
            double = bool(m.get("doubleSided"))
        mat_map[k] = model.add_material(Material(name=name, color=color, texture=tex,
                                                 transparent=transparent, double_sided=double))
        return mat_map[k]

    def node_matrix(n: dict) -> np.ndarray:
        if "matrix" in n:
            return np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T
        M = np.eye(4)
        if "translation" in n:
            M[:3, 3] = n["translation"]
        R = np.eye(4)
        if "rotation" in n:
            x, y, z, w = n["rotation"]
            R[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
        S = np.diag(list(n.get("scale", [1, 1, 1])) + [1.0])
        return M @ R @ S

    def primitive_mesh(prim: dict) -> Optional[Mesh]:
        attrs = prim.get("attributes", {})
        if "POSITION" not in attrs:
            return None
        P = g.accessor(attrs["POSITION"]).astype(np.float32)
        N = g.accessor(attrs["NORMAL"]).astype(np.float32) if "NORMAL" in attrs else None
        U = g.accessor(attrs["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in attrs else None
        if "indices" in prim:
            idx = g.accessor(prim["indices"]).reshape(-1).astype(np.int64)
        else:
            idx = np.arange(len(P), dtype=np.int64)
        mode = prim.get("mode", 4)
        if mode == 4:
            tris = idx[: (len(idx) // 3) * 3].reshape(-1, 3)
        elif mode == 5:
            a, b, c = idx[:-2], idx[1:-1], idx[2:]
            odd = (np.arange(len(a)) % 2) == 1
            tris = np.stack([np.where(odd, b, a), np.where(odd, a, b), c], axis=1)
        elif mode == 6:
            tris = np.stack([np.full(len(idx) - 2, idx[0]), idx[1:-1], idx[2:]], axis=1)
        else:
            return None
        if len(tris) == 0:
            return None
        mid = material_for(prim.get("material"))
        return Mesh(P, tris, np.full(len(tris), mid, np.int32), N, U)

    nodes = j.get("nodes", [])
    scenes = j.get("scenes", [])
    roots = scenes[j.get("scene", 0)].get("nodes", []) if scenes else list(range(len(nodes)))

    def walk(ni: int, parent: np.ndarray, depth: int = 0) -> None:
        if depth > 64:
            return
        n = nodes[ni]
        world = parent @ node_matrix(n)
        if "mesh" in n:
            msh = j["meshes"][n["mesh"]]
            subs = [m for m in (primitive_mesh(p) for p in msh.get("primitives", [])) if m]
            if subs:
                mesh = Mesh.concat(subs).transformed(world[:3, :3], world[:3, 3])
                model.parts.append(Part(n.get("name") or msh.get("name") or f"mesh{n['mesh']}", mesh))
        for c in n.get("children", []):
            walk(c, world, depth + 1)

    for r in roots:
        walk(r, np.eye(4))
    if not model.parts:
        raise ConversionError("the glTF file contains no triangle meshes")
    log(f"glTF: {len(model.parts)} meshes, {sum(p.mesh.tri_count for p in model.parts)} triangles")
    return model


# ===================================================================== Collada (generic)
def import_dae_generic(path: Path, log: Callable[[str], None]) -> Model:
    scene = parse_collada(path.read_bytes())
    # Z-up after parsing; typical Blender/GTA-V exports have the car's front along +Y.
    model = Model(name=path.stem, source="generic", axes=AxisSpec("+x", "+z", "+y"),
                  forward_auto=True)
    local: Dict[Tuple[str, int], int] = {}
    for inst in scene.instances:
        ids = []
        for nm in scene.material_names:
            dm = scene.materials.get(nm)
            tex = None
            color = (0.8, 0.8, 0.8, 1.0)
            if dm is not None:
                if dm.color:
                    color = dm.color
                if dm.texture_path:
                    f = _find_file(path.parent, dm.texture_path.replace("file://", ""))
                    if f is not None:
                        key = norm_name(f.name)
                        try:
                            if key not in model.textures:
                                model.textures[key] = Texture(key, load_image_bytes(f.read_bytes()))
                            tex = key
                        except ConversionError as exc:
                            model.notes.append(f"texture {f.name}: {exc}")
            # same 0.75 cut-off as the other importers - see the glTF alphaMode note above.
            ids.append(model.add_material(Material(name=nm, color=color, texture=tex,
                                                   transparent=color[3] < 0.75)))
        m = inst.mesh
        gl = np.asarray(ids, dtype=np.int32) if ids else np.zeros(1, np.int32)
        model.parts.append(Part(inst.node_name or inst.geometry_name,
                                Mesh(m.positions, m.triangles, gl[m.material_ids], m.normals, m.uvs)))
    log(f"DAE: {len(model.parts)} meshes")
    return model


def import_generic(path: str, log: Callable[[str], None] = print) -> Model:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".obj":
        return import_obj(p, log)
    if ext in (".glb", ".gltf"):
        return import_gltf(p, log)
    if ext == ".dae":
        return import_dae_generic(p, log)
    if ext == ".fbx":
        raise ConversionError("FBX is a closed binary format and cannot be read directly. "
                              "Open it in Blender and export OBJ, GLB or DAE, then convert that.")
    if ext == ".rpf":
        raise ConversionError(
            "'.rpf' is a GTA V game archive (it can hold thousands of files in a compressed, "
            "partly encrypted format), not a single car model, so car2roblox cannot read it "
            "directly - that would mean reimplementing Rockstar's RAGE engine format. Open it in "
            "CodeWalker (free) or OpenIV, find the car's .yft inside, export it as OBJ, GLB or DAE "
            "with its textures, and convert that file instead.")
    if ext in (".yft", ".ydr", ".ytd", ".ydd", ".odr", ".oft"):
        raise ConversionError(
            f"'{ext}' is a GTA V / RAGE game format. Open it in CodeWalker or OpenIV (or Blender "
            "with the Sollumz add-on), export it as OBJ / GLB / DAE and convert that file. "
            "Textures must be exported next to it as .png or .dds.")
    raise ConversionError(f"unsupported file type '{ext}'")
