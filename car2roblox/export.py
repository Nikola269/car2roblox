"""Writers: glTF binary (.glb), Wavefront OBJ/MTL, Lua tables."""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import __version__
from .imp_generic import srgb_to_linear
from .model import Material, Piece

# A texture ready for export: (file name without folder, PNG bytes, has_alpha)
TexFile = Tuple[str, bytes, bool]


def sanitize(name: str, fallback: str = "Part") -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return s or fallback


# --------------------------------------------------------------------------- GLB
class _Bin:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.views: List[dict] = []
        self.accessors: List[dict] = []

    def view(self, data: bytes, target: Optional[int] = None) -> int:
        while len(self.buf) % 4:
            self.buf.append(0)
        v = {"buffer": 0, "byteOffset": len(self.buf), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.buf.extend(data)
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, arr: np.ndarray, comp: int, typ: str, target: Optional[int],
                 minmax: bool = False) -> int:
        vi = self.view(arr.tobytes(), target)
        a = {"bufferView": vi, "componentType": comp, "count": int(arr.shape[0]), "type": typ}
        if minmax:
            a["min"] = [float(x) for x in arr.min(axis=0)]
            a["max"] = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(a)
        return len(self.accessors) - 1


def _pbr(mat: Material) -> Tuple[float, float]:
    n = mat.name.lower()
    if mat.transparent or "glass" in n or "window" in n:
        return 0.0, 0.05
    if mat.paint:
        return 0.3, 0.35
    if "chrome" in n or "metal" in n:
        return 0.9, 0.25
    return 0.0, 0.75


def write_glb(path: Path, pieces: Sequence[Piece], textures: Dict[str, TexFile], name: str) -> None:
    b = _Bin()
    gltf: dict = {"asset": {"version": "2.0", "generator": f"car2roblox {__version__}"},
                  "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
                  "materials": [], "images": [], "textures": [],
                  "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]}
    tex_index: Dict[str, int] = {}
    mat_index: Dict[tuple, int] = {}

    def texture_idx(key: str) -> Optional[int]:
        if key not in textures:
            return None
        if key not in tex_index:
            fname, png, _ = textures[key]
            vi = b.view(png)
            gltf["images"].append({"bufferView": vi, "mimeType": "image/png", "name": fname})
            gltf["textures"].append({"sampler": 0, "source": len(gltf["images"]) - 1})
            tex_index[key] = len(gltf["textures"]) - 1
        return tex_index[key]

    def material_idx(m: Material) -> int:
        k = m.key()
        if k in mat_index:
            return mat_index[k]
        lin = srgb_to_linear(m.color[:3])
        metal, rough = _pbr(m)
        pbr = {"baseColorFactor": [float(lin[0]), float(lin[1]), float(lin[2]), float(m.color[3])],
               "metallicFactor": metal, "roughnessFactor": rough}
        gm = {"name": m.name, "pbrMetallicRoughness": pbr}
        ti = texture_idx(m.texture) if m.texture else None
        if ti is not None:
            pbr["baseColorTexture"] = {"index": ti}
            if textures[m.texture][2] and not m.transparent:
                gm["alphaMode"], gm["alphaCutoff"] = "MASK", 0.5
        if m.transparent:
            gm["alphaMode"] = "BLEND"
        if m.double_sided:
            gm["doubleSided"] = True
        gltf["materials"].append(gm)
        mat_index[k] = len(gltf["materials"]) - 1
        return mat_index[k]

    for pc in pieces:
        m = pc.mesh
        attrs = {"POSITION": b.accessor(m.positions.astype("<f4"), 5126, "VEC3", 34962, True)}
        if m.normals is not None:
            attrs["NORMAL"] = b.accessor(m.normals.astype("<f4"), 5126, "VEC3", 34962)
        if m.uvs is not None:
            attrs["TEXCOORD_0"] = b.accessor(m.uvs.astype("<f4"), 5126, "VEC2", 34962)
        if m.vert_count < 65535:
            idx = b.accessor(m.triangles.reshape(-1, 1).astype("<u2"), 5123, "SCALAR", 34963)
        else:
            idx = b.accessor(m.triangles.reshape(-1, 1).astype("<u4"), 5125, "SCALAR", 34963)
        gltf["meshes"].append({"name": pc.name, "primitives": [
            {"attributes": attrs, "indices": idx, "material": material_idx(pc.material), "mode": 4}]})
        extras = {"kind": pc.kind}
        if pc.wheel_slot:
            extras["wheel"] = pc.wheel_slot
        gltf["nodes"].append({"name": pc.name, "mesh": len(gltf["meshes"]) - 1, "extras": extras})
        gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]) - 1)

    for k in ("images", "textures", "materials"):
        if not gltf[k]:
            del gltf[k]
    if "textures" not in gltf:
        del gltf["samplers"]
    gltf["bufferViews"] = b.views
    gltf["accessors"] = b.accessors
    while len(b.buf) % 4:
        b.buf.append(0)
    gltf["buffers"] = [{"byteLength": len(b.buf)}]
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(b.buf)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON"))
        f.write(js)
        f.write(struct.pack("<I4s", len(b.buf), b"BIN\0"))
        f.write(bytes(b.buf))


# --------------------------------------------------------------------------- OBJ
def write_obj(path: Path, piece: Piece, tex_file: Optional[str]) -> None:
    m = piece.mesh
    mat = piece.material
    mtl_name = sanitize(mat.name, "material")
    lines = [f"# car2roblox {__version__}", f"mtllib {path.stem}.mtl", f"o {piece.name}"]
    lines += [f"v {x:.5f} {y:.5f} {z:.5f}" for x, y, z in m.positions]
    has_uv, has_n = m.uvs is not None, m.normals is not None
    if has_uv:
        lines += [f"vt {u:.5f} {1.0 - v:.5f}" for u, v in m.uvs]        # image space -> OBJ v-up
    if has_n:
        lines += [f"vn {x:.4f} {y:.4f} {z:.4f}" for x, y, z in m.normals]
    lines.append(f"usemtl {mtl_name}")
    if has_uv and has_n:
        lines += [f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}" for a, b, c in (m.triangles + 1).tolist()]
    elif has_uv:
        lines += [f"f {a}/{a} {b}/{b} {c}/{c}" for a, b, c in (m.triangles + 1).tolist()]
    elif has_n:
        lines += [f"f {a}//{a} {b}//{b} {c}//{c}" for a, b, c in (m.triangles + 1).tolist()]
    else:
        lines += [f"f {a} {b} {c}" for a, b, c in (m.triangles + 1).tolist()]
    path.write_text("\n".join(lines) + "\n")
    r, g, bl, a = mat.color
    mtl = [f"newmtl {mtl_name}", "Ka 1.000 1.000 1.000", f"Kd {r:.4f} {g:.4f} {bl:.4f}",
           "Ks 0.000 0.000 0.000", f"d {a:.4f}", "illum 2"]
    if tex_file:
        mtl.append(f"map_Kd {tex_file}")
    path.with_suffix(".mtl").write_text("\n".join(mtl) + "\n")


# --------------------------------------------------------------------------- Lua
def to_lua(obj, indent: int = 0) -> str:
    pad = "\t" * indent
    if obj is None:
        return "nil"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, np.integer)):
        return str(int(obj))
    if isinstance(obj, (float, np.floating)):
        return repr(round(float(obj), 4))
    if isinstance(obj, str):
        return '"' + obj.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(obj, (list, tuple, np.ndarray)):
        items = list(obj)
        if all(isinstance(x, (int, float, np.number)) and not isinstance(x, bool) for x in items):
            return "{" + ", ".join(to_lua(x) for x in items) + "}"
        inner = ",\n".join(f"{pad}\t{to_lua(x, indent + 1)}" for x in items)
        return "{\n" + inner + f"\n{pad}}}" if items else "{}"
    if isinstance(obj, dict):
        inner = []
        for k, v in obj.items():
            key = k if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) else f'["{k}"]'
            inner.append(f"{pad}\t{key} = {to_lua(v, indent + 1)}")
        return "{\n" + ",\n".join(inner) + f"\n{pad}}}" if inner else "{}"
    raise TypeError(f"cannot serialise {type(obj)} to Lua")
