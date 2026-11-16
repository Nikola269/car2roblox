"""Synthetic input files for the tests. Everything is generated in code, so no game assets
are needed. The DFF/TXD writers follow the RenderWare layouts documented in rwparse.py."""
from __future__ import annotations

import io
import json
import struct
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

SA_VER = 0x1803FFFF


# --------------------------------------------------------------------------- shapes
def box(center, size):
    """24-vertex box with outward CCW triangles. Returns (pos, nrm, uv, tris)."""
    c = np.asarray(center, float)
    h = np.asarray(size, float) / 2.0
    pos, nrm, uv, tris = [], [], [], []
    for axis in range(3):
        for sign in (1.0, -1.0):
            n = np.zeros(3)
            n[axis] = sign
            u_axis, v_axis = (axis + 1) % 3, (axis + 2) % 3
            u, v = np.zeros(3), np.zeros(3)
            u[u_axis], v[v_axis] = 1.0, 1.0
            if np.dot(np.cross(u, v), n) < 0:
                u = -u
            base = len(pos)
            for su, sv, tu, tv in ((-1, -1, 0, 0), (1, -1, 1, 0), (1, 1, 1, 1), (-1, 1, 0, 1)):
                pos.append(c + n * h[axis] + u * abs(h[u_axis]) * su + v * abs(h[v_axis]) * sv)
                nrm.append(n)
                uv.append((tu, tv))
            tris += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    return (np.array(pos, np.float32), np.array(nrm, np.float32), np.array(uv, np.float32),
            np.array(tris, np.int32))


def cylinder_x(center, radius, width, segments=16):
    """Cylinder along the X axis (a wheel), outward CCW. Returns (pos, nrm, uv, tris)."""
    c = np.asarray(center, float)
    pos, uv, tris = [], [], []
    for side in (-1, 1):
        for i in range(segments):
            a = 2 * np.pi * i / segments
            pos.append(c + [side * width / 2, radius * np.cos(a), radius * np.sin(a)])
            uv.append((i / segments, 0.0 if side < 0 else 1.0))
    n = segments
    for i in range(n):
        j = (i + 1) % n
        a, b, c2, d = i, j, n + j, n + i
        tris += [(a, b, c2), (a, c2, d)]        # outward: caps below use the same sense
    pos.append(c + [-width / 2, 0, 0])
    pos.append(c + [width / 2, 0, 0])
    uv += [(0.5, 0.5), (0.5, 0.5)]
    for i in range(n):
        j = (i + 1) % n
        tris.append((2 * n, j, i))
        tris.append((2 * n + 1, n + i, n + j))
    pos = np.array(pos, np.float32)
    tris = np.array(tris, np.int32)
    # safety net: the winding must be outward (positive signed volume)
    p = pos.astype(np.float64)
    p0 = p - p.mean(axis=0)
    vol = np.einsum("ij,ij->i", p0[tris[:, 0]], np.cross(p0[tris[:, 1]], p0[tris[:, 2]])).sum()
    if vol < 0:
        tris = tris[:, ::-1].copy()
    from car2roblox.model import Mesh
    m = Mesh(pos, tris, np.zeros(len(tris), np.int32), None, np.array(uv, np.float32)).recompute_normals()
    return m.positions, m.normals, m.uvs, m.triangles


# --------------------------------------------------------------------------- RenderWare writers
def chunk(t: int, payload: bytes, ver: int = SA_VER) -> bytes:
    return struct.pack("<III", t, len(payload), ver) + payload


def rw_string(s: str) -> bytes:
    b = s.encode("latin-1") + b"\0"
    return chunk(2, b + b"\0" * ((4 - len(b) % 4) % 4))


def dff_material(color=(255, 255, 255, 255), tex: Optional[str] = None) -> bytes:
    st = struct.pack("<I4BII", 0, *color, 0, 1 if tex else 0) + struct.pack("<3f", 1, 1, 1)
    body = chunk(1, st)
    if tex:
        body += chunk(6, chunk(1, struct.pack("<2BH", 0x16, 0x11, 0)) + rw_string(tex)
                      + rw_string("") + chunk(3, b""))
    body += chunk(3, b"")
    return chunk(7, body)


def dff_geometry(pos, nrm, uv, tris, tri_mats, materials: Sequence[bytes], *, binmesh_only=False,
                 tristrip_flag=False) -> bytes:
    nv = len(pos)
    flags = 0x02 | 0x04 | 0x10 | (1 << 16) | (1 if tristrip_flag else 0)
    ntri = 0 if binmesh_only else len(tris)
    st = struct.pack("<IIII", flags, ntri, nv, 1)
    st += np.asarray(uv, "<f4").tobytes()
    if not binmesh_only:
        for (a, b, c), m in zip(tris, tri_mats):
            st += struct.pack("<4H", int(b), int(a), int(m), int(c))     # file order: v1, v0, mat, v2
    st += struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<II", 1, 1)
    st += np.asarray(pos, "<f4").tobytes() + np.asarray(nrm, "<f4").tobytes()
    ml = chunk(8, chunk(1, struct.pack("<I", len(materials)) + b"\xff\xff\xff\xff" * len(materials))
               + b"".join(materials))
    ext = b""
    if binmesh_only:
        by_mat: Dict[int, List[int]] = {}
        for (a, b, c), m in zip(tris, tri_mats):
            by_mat.setdefault(int(m), []).extend([int(a), int(b), int(c)])
        bm = struct.pack("<III", 0, len(by_mat), sum(len(v) for v in by_mat.values()))
        for m, idx in by_mat.items():
            bm += struct.pack("<II", len(idx), m) + struct.pack(f"<{len(idx)}I", *idx)
        ext = chunk(0x50E, bm)
    return chunk(0x0F, chunk(1, st) + ml + chunk(3, ext))


def dff_frames(frames: Sequence[Tuple[str, Tuple[float, float, float], int]],
               rot: Optional[Dict[int, np.ndarray]] = None) -> bytes:
    st = struct.pack("<I", len(frames))
    for i, (name, pos, parent) in enumerate(frames):
        R = (rot or {}).get(i, np.eye(3))
        right, up, at = R[:, 0], R[:, 1], R[:, 2]
        st += struct.pack("<9f", *right, *up, *at) + struct.pack("<3f", *pos)
        st += struct.pack("<iI", parent, 0x20003)
    out = chunk(1, st)
    for name, _, _ in frames:
        out += chunk(3, chunk(0x253F2FE, name.encode("latin-1")))
    return chunk(0x0E, out)


def build_dff(frames, geometries: Sequence[bytes], atomics: Sequence[Tuple[int, int]],
              rot=None) -> bytes:
    gl = chunk(0x1A, chunk(1, struct.pack("<I", len(geometries))) + b"".join(geometries))
    at = b"".join(chunk(0x14, chunk(1, struct.pack("<4I", f, g, 5, 0)) + chunk(3, b""))
                  for f, g in atomics)
    return chunk(0x10, chunk(1, struct.pack("<III", len(atomics), 0, 0))
                 + dff_frames(frames, rot) + gl + at + chunk(3, b""))


def sa_test_car(binmesh_only=False, wheel=True) -> bytes:
    """A little SA-style car: chassis, bonnet (child frame), windscreen, a wheel model with four
    wheel dummies, plus a _dam and a _vlo variant that must be skipped."""
    mats_body = [dff_material((60, 255, 0, 255)), dff_material((255, 255, 255, 255), "bodytex")]
    p, n, u, t = box((0, 0, 0.6), (1.8, 4.4, 0.9))
    tm = np.zeros(len(t), int)
    tm[:4] = 1                                                    # a few triangles use the texture
    g_body = dff_geometry(p, n, u, t, tm, mats_body, binmesh_only=binmesh_only)
    p, n, u, t = box((0, 0, 0), (1.6, 1.2, 0.1))
    g_hood = dff_geometry(p, n, u, t, np.zeros(len(t), int), [dff_material((60, 255, 0, 255))])
    p, n, u, t = box((0, -0.3, 1.25), (1.5, 1.4, 0.5))
    g_glass = dff_geometry(p, n, u, t, np.zeros(len(t), int), [dff_material((10, 10, 10, 90))])
    p, n, u, t = cylinder_x((0, 0, 0), 0.35, 0.25)
    g_wheel = dff_geometry(p, n, u, t, np.zeros(len(t), int), [dff_material((30, 30, 30, 255))])
    p, n, u, t = box((0, 0, 0), (0.5, 0.5, 0.5))
    g_junk = dff_geometry(p, n, u, t, np.zeros(len(t), int), [dff_material((255, 0, 0, 255))])

    frames = [
        ("root", (0, 0, 0), -1),                     # 0
        ("chassis", (0, 0, 0.3), 0),                 # 1  (world z offset 0.3)
        ("bonnet_ok", (0, 1.4, 0.6), 1),             # 2  child of chassis
        ("windscreen_ok", (0, 0, 0.3), 0),           # 3
        ("wheel", (0, 0, 0), 0),                     # 4
        ("wheel_lf_dummy", (-0.9, 1.4, 0.35), 0),    # 5
        ("wheel_rf_dummy", (0.9, 1.4, 0.35), 0),     # 6
        ("wheel_lb_dummy", (-0.9, -1.4, 0.35), 0),   # 7
        ("wheel_rb_dummy", (0.9, -1.4, 0.35), 0),    # 8
        ("door_lf_dam", (0, 0, 0), 0),               # 9
        ("chassis_vlo", (0, 0, 0), 0),               # 10
    ]
    geoms = [g_body, g_hood, g_glass, g_wheel, g_junk]
    atomics = [(1, 0), (2, 1), (3, 2), (4, 3), (9, 4), (10, 4)]
    if not wheel:                                                 # a car file without the wheel model
        atomics.remove((4, 3))
    return build_dff(frames, geoms, atomics)


# --------------------------------------------------------------------------- TXD writer
def native_texture(name: str, w: int, h: int, kind: str, pixels: bytes, palette: bytes = b"") -> bytes:
    fmt = {"8888": (0x0500, 21, 32, 1), "565": (0x0200, 23, 16, 0), "dxt1": (0x0200, 0x31545844, 16, 9),
           "pal8": (0x2500, 0, 8, 1), "dxt5": (0x0500, 0x35545844, 32, 9)}[kind]
    raster, d3d, depth, prop = fmt
    hdr = struct.pack("<I2BH", 9, 0x16, 0x11, 0)
    hdr += name.encode().ljust(32, b"\0") + b"".ljust(32, b"\0")
    hdr += struct.pack("<IIHHBBB", raster, d3d, w, h, depth, 1, 4) + bytes([prop])
    body = hdr + palette + struct.pack("<I", len(pixels)) + pixels
    return chunk(0x15, chunk(1, body) + chunk(3, b""))


def build_txd(natives: Sequence[bytes]) -> bytes:
    return chunk(0x16, chunk(1, struct.pack("<HH", len(natives), 0)) + b"".join(natives) + chunk(3, b""))


def sa_test_txd() -> bytes:
    # 4x4 body texture: left half red, right half green, 32-bit BGRA
    px = bytearray()
    for y in range(4):
        for x in range(4):
            px += bytes([0, 0, 255, 255]) if x < 2 else bytes([0, 255, 0, 255])
    return build_txd([native_texture("bodytex", 4, 4, "8888", bytes(px))])


# --------------------------------------------------------------------------- BeamNG archive
def _dae_geometry(gid: str, pos, nrm, uv, tris, symbol: str) -> str:
    def arr(name, a, stride, params):
        flat = " ".join(f"{x:.5f}" for x in np.asarray(a).reshape(-1))
        ps = "".join(f'<param name="{p}" type="float"/>' for p in params)
        return (f'<source id="{gid}-{name}"><float_array id="{gid}-{name}-a" count="{np.asarray(a).size}">'
                f'{flat}</float_array><technique_common><accessor source="#{gid}-{name}-a" '
                f'count="{len(a)}" stride="{stride}">{ps}</accessor></technique_common></source>')
    p_idx = " ".join(f"{i} {i} {i}" for tri in tris for i in tri)
    # collada uv is v-up
    uv = np.array(uv, float)
    uv[:, 1] = 1 - uv[:, 1]
    return (f'<geometry id="{gid}" name="{gid}"><mesh>'
            + arr("pos", pos, 3, "XYZ") + arr("nrm", nrm, 3, "XYZ") + arr("uv", uv, 2, "ST")
            + f'<vertices id="{gid}-vtx"><input semantic="POSITION" source="#{gid}-pos"/></vertices>'
            + f'<triangles count="{len(tris)}" material="{symbol}">'
            + f'<input semantic="VERTEX" source="#{gid}-vtx" offset="0"/>'
            + f'<input semantic="NORMAL" source="#{gid}-nrm" offset="1"/>'
            + f'<input semantic="TEXCOORD" source="#{gid}-uv" offset="2" set="0"/>'
            + f'<p>{p_idx}</p></triangles></mesh></geometry>')


def make_dae(nodes: Sequence[dict], up: str = "Z_UP") -> bytes:
    """nodes: dict(name, geom=(pos,nrm,uv,tris), symbol, material, xform=<xml>)"""
    geos, vnodes, mats = [], [], {}
    for i, nd in enumerate(nodes):
        gid = f"g{i}"
        geos.append(_dae_geometry(gid, *nd["geom"], symbol=nd["symbol"]))
        mats[nd["material"]] = 1
        vnodes.append(
            f'<node id="n{i}" name="{nd["name"]}" type="NODE">{nd.get("xform", "")}'
            f'<instance_geometry url="#{gid}"><bind_material><technique_common>'
            f'<instance_material symbol="{nd["symbol"]}" target="#{nd["material"]}"/>'
            f'</technique_common></bind_material></instance_geometry></node>')
    lib_mats = "".join(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>' for m in mats)
    lib_fx = "".join(f'<effect id="{m}-fx"><profile_COMMON><technique sid="c"><phong><diffuse>'
                     f'<color>0.7 0.7 0.7 1</color></diffuse></phong></technique></profile_COMMON></effect>'
                     for m in mats)
    xml = (f'<?xml version="1.0" encoding="utf-8"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" '
           f'version="1.4.1"><asset><unit meter="1"/><up_axis>{up}</up_axis></asset>'
           f'<library_effects>{lib_fx}</library_effects><library_materials>{lib_mats}</library_materials>'
           f'<library_geometries>{"".join(geos)}</library_geometries>'
           f'<library_visual_scenes><visual_scene id="s">{"".join(vnodes)}</visual_scene></library_visual_scenes>'
           f'<scene><instance_visual_scene url="#s"/></scene></COLLADA>')
    return xml.encode()


def png_bytes(color=(200, 40, 40, 255), size=8) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def checker_png_bytes(a=(200, 40, 40, 255), b=(0, 0, 0, 0), size=8) -> bytes:
    """Left half colour a (opaque), right half colour b - a decal-shaped test texture so a merge
    can be told apart from either layer alone (half transparent lets the base show through)."""
    img = Image.new("RGBA", (size, size), a)
    for x in range(size // 2, size):
        for y in range(size):
            img.putpixel((x, y), b)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def beamng_layered_material_zip(path) -> None:
    """A body material with TWO Stages: stage 0 is the paint colour (instanceDiffuse, like most
    BeamNG body paint) and stage 1 is a decal/stripe texture with its own alpha - a common way
    BeamNG layers decals, dirt or damage on top of the base colour. Only ever reading stage 0
    silently dropped the decal entirely."""
    def tr(x, y, z):
        return f"<translate>{x} {y} {z}</translate>"

    nodes = [
        dict(name="testcar_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
    ]
    jb = """{
  "testcar": {
    "slotType": "main",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["testcar_body", ["testcar_body"], []],
    ],
  },
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material",
                     "Stages": [{"instanceDiffuse": True},
                               {"baseColorMap": "vehicles/testcar/decal.png"}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/testcar/testcar.dae", make_dae(nodes))
        z.writestr("vehicles/testcar/testcar.jbeam", jb)
        z.writestr("vehicles/testcar/testcar.materials.json", json.dumps(materials))
        z.writestr("vehicles/testcar/decal.png", checker_png_bytes())


def beamng_test_zip(path, with_config: bool = True) -> None:
    """A tiny 'testcar' mod. BeamNG axes: +X left, +Y back, -Y front, +Z up."""
    def tr(x, y, z):
        return f"<translate>{x} {y} {z}</translate>"

    nodes = [
        dict(name="testcar_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
        dict(name="testcar_glass", geom=box((0, 0, 0), (1.5, 1.4, 0.5)), symbol="s2", material="glass_mat",
             xform=tr(0, 0.3, 1.5)),
        dict(name="testcar_wheel_FL", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat",
             xform=tr(0.9, -1.4, 0.35)),     # front (-y), left (+x)
        dict(name="testcar_wheel_FR", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat",
             xform=tr(-0.9, -1.4, 0.35)),
        dict(name="testcar_wheel_RL", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat",
             xform=tr(0.9, 1.4, 0.35)),
        dict(name="testcar_wheel_RR", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat",
             xform=tr(-0.9, 1.4, 0.35)),
        dict(name="testcar_unused", geom=box((0, 0, 0), (0.3, 0.3, 0.3)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 3)),
        dict(name="colmesh", geom=box((0, 0, 0), (5, 5, 5)), symbol="s1", material="body_mat"),
    ]
    jb = """{
  // main part
  "testcar": {
    "information": {"name": "Test Car", "authors": "me"},
    "slotType": "main",
    "slots": [
      ["type", "default", "description"],
      ["testcar_wheels", "testcar_wheels", "Wheels"],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["testcar_body", ["testcar_body"], []],
      ["testcar_glass", ["testcar_body"], []]      /* no comma problems */
    ],
  },
  "testcar_wheels": {
    "slotType": "testcar_wheels",
    "flexbodies": [
      ["mesh", "[group]:"],
      ["testcar_wheel_FL", ["FL"]], ["testcar_wheel_FR", ["FR"]]
      ["testcar_wheel_RL", ["RL"]], ["testcar_wheel_RR", ["RR"]],
    ]
  },
  "testcar_other": {"slotType": "other", "flexbodies": [["mesh"], ["testcar_unused", ["x"]]]}
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material",
                     "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "glass_mat": {"name": "glass_mat", "translucent": True,
                      "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 0.4]}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material",
                      "Stages": [{"baseColorMap": "vehicles/testcar/wheel_d.png"}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/testcar/testcar.dae", make_dae(nodes))
        z.writestr("vehicles/testcar/testcar.jbeam", jb)
        z.writestr("vehicles/testcar/testcar.materials.json", json.dumps(materials))
        z.writestr("vehicles/testcar/wheel_d.png", png_bytes((20, 120, 200, 255)))
        if with_config:
            z.writestr("vehicles/testcar/sport.pc", json.dumps({
                "mainPartName": "testcar",
                "parts": {"testcar": "testcar", "testcar_wheels": "testcar_wheels"},
                "paints": [{"baseColor": [0.9, 0.1, 0.1, 1.0]}]}))


# --------------------------------------------------------------------------- OBJ
def write_obj_car(path, long_axis: str = "x") -> None:
    """Y-up OBJ car whose long axis is X (or Z) - used to test forward-axis detection."""
    def place(c, s):
        return box(c, s)
    parts = {
        "body": box((0, 0.7, 0), (4.4, 0.9, 1.8) if long_axis == "x" else (1.8, 0.9, 4.4)),
        "roof": box((-0.3, 1.35, 0) if long_axis == "x" else (0, 1.35, 0.3),
                    (1.6, 0.5, 1.5) if long_axis == "x" else (1.5, 0.5, 1.6)),
    }
    lines, vo = ["mtllib car.mtl"], 0
    for name, (p, n, u, t) in parts.items():
        lines.append(f"o {name}")
        lines += [f"v {a:.4f} {b:.4f} {c:.4f}" for a, b, c in p]
        lines += [f"vt {a:.4f} {b:.4f}" for a, b in u]
        lines += [f"vn {a:.4f} {b:.4f} {c:.4f}" for a, b, c in n]
        lines.append("usemtl paint" if name == "body" else "usemtl roofmat")
        for a, b, c in t + vo + 1:
            lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
        vo += len(p)
    path.write_text("\n".join(lines) + "\n")
    (path.parent / "car.mtl").write_text("newmtl paint\nKd 0.8 0.1 0.1\nnewmtl roofmat\nKd 0.2 0.2 0.2\n")


def write_obj_wheel_car(path, mode: str) -> None:
    """Y-up OBJ car (long axis Z) in the wheel situations the converter has to cope with:
    none | named | unnamed | fused | dup | template | stacked | partial | spare."""
    parts = {"body": box((0, 0.7, 0), (1.8, 0.9, 4.4)), "roof": box((0, 1.35, 0.3), (1.5, 0.5, 1.6))}
    centres = [(sx * 0.9, 0.35, sz * 1.4) for sz in (-1, 1) for sx in (-1, 1)]
    cyl = lambda c: cylinder_x(c, 0.35, 0.25)
    if mode == "named":
        for i, c in enumerate(centres):
            parts[f"wheel_{i + 1}"] = cyl(c)
    elif mode == "unnamed":
        for i, c in enumerate(centres):
            parts[f"Object_{i + 1}"] = cyl(c)
    elif mode == "dup":
        for i, c in enumerate(centres):
            parts[f"wheel_{i + 1}"] = cyl(c)
            parts[f"tyre_{i + 1}"] = cyl(c)
    elif mode == "fused":
        ps, ns, us, ts, off = [], [], [], [], 0
        for c in centres:
            p, n, u, t = cyl(c)
            ps.append(p), ns.append(n), us.append(u), ts.append(t + off)
            off += len(p)
        parts["wheels"] = (np.concatenate(ps), np.concatenate(ns), np.concatenate(us), np.concatenate(ts))
    elif mode == "template":          # one wheel left at the middle of the car (never moved to the hubs)
        parts["wheel"] = cyl((0, 0.35, 0))
    elif mode == "stacked":           # four wheels exported at the origin of their bones (GTA V style)
        for n in ("wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr"):
            parts[n] = cyl((0, 0.35, 0))
    elif mode == "partial":           # only the two front wheels are there
        for i, c in enumerate(centres[:2]):
            parts[f"wheel_{i + 1}"] = cyl(c)
    elif mode == "spare":             # four good wheels and a spare in the middle of the boot
        for i, c in enumerate(centres):
            parts[f"wheel_{i + 1}"] = cyl(c)
        parts["spare_wheel"] = cylinder_x((0, 0.75, 1.5), 0.3, 0.2)
    elif mode != "none":
        raise ValueError(mode)
    lines, vo = ["mtllib car.mtl"], 0
    for name, (p, n, u, t) in parts.items():
        lines.append(f"o {name}")
        lines += [f"v {a:.4f} {b:.4f} {c:.4f}" for a, b, c in p]
        lines += [f"vt {a:.4f} {b:.4f}" for a, b in u]
        lines += [f"vn {a:.4f} {b:.4f} {c:.4f}" for a, b, c in n]
        lines.append("usemtl paint" if name == "body" else "usemtl roofmat")
        for a, b, c in t + vo + 1:
            lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
        vo += len(p)
    path.write_text("\n".join(lines) + "\n")
    (path.parent / "car.mtl").write_text("newmtl paint\nKd 0.8 0.1 0.1\nnewmtl roofmat\nKd 0.2 0.2 0.2\n")


def beamng_flexbody_zip(path, wheel_mesh: bool = True, empty_defaults: bool = False,
                        duplicate_row: bool = False, variable_pos: bool = False,
                        anon_hubs: bool = False, stray_formula_part: bool = False) -> None:
    """A BeamNG mod laid out like the real ones: ONE wheel mesh at the origin that the JBeam places
    four times with ``pos`` / ``rot``, ``slots2`` slot tables and hub nodes in ``pressureWheels``."""
    tr = lambda x, y, z: f"<translate>{x} {y} {z}</translate>"
    nodes = [
        dict(name="pickup_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
        dict(name="pickup_glass", geom=box((0, 0, 0), (1.5, 1.4, 0.5)), symbol="s2", material="glass_mat",
             xform=tr(0, 0.3, 1.5)),
    ]
    if wheel_mesh:
        nodes.append(dict(name="pickup_wheel", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3",
                          material="wheel_mat"))
    if stray_formula_part:
        # a small non-wheel part (e.g. a brake caliper) placed with a mirrored-side formula our
        # importer cannot evaluate; it must be left out rather than drawn at a wrong default spot
        nodes.append(dict(name="pickup_caliper", geom=box((0, 0, 0), (0.1, 0.2, 0.2)), symbol="s1",
                          material="body_mat"))
    dflt = lambda n: "" if empty_defaults else n
    jb = """{
  "pickup": {
    "slotType": "main",
    "slots2": [
      ["name", "allowTypes", "denyTypes", "default", "description"],
      ["wheels_F", ["pickup_wheel_F"], [], "%s", "Front wheels"],
      ["wheels_R", ["pickup_wheel_R"], [], "%s", "Rear wheels"],
    ],
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["fr1", -0.8, -1.4, 0.35], ["fr2", -1.0, -1.4, 0.35], ["fl1", 0.8, -1.4, 0.35], ["fl2", 1.0, -1.4, 0.35],
      ["rr1", -0.8, 1.4, 0.35], ["rr2", -1.0, 1.4, 0.35], ["rl1", 0.8, 1.4, 0.35], ["rl2", 1.0, 1.4, 0.35],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_body", ["pickup_body"], []],
      ["pickup_glass", ["pickup_body"], []],
      %s
    ],
  },
  "pickup_wheel_F_a": {
    "slotType": "pickup_wheel_F",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.9, "y": -1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": -1.4, "z": 0.35}}],
      %s
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["FR", "wheelhub_FR", "tire_FR", "fr1", "fr2", 9999, "fr1", 1],
      ["FL", "wheelhub_FL", "tire_FL", "fl1", "fl2", 9999, "fl1", -1],
    ],
  },
  "pickup_wheel_R_a": {
    "slotType": "pickup_wheel_R",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      {"rot": {"x": 0, "y": 0, "z": 180}},          // stays in force for the next row only if not overridden
      ["pickup_wheel", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.9, "y": 1.4, "z": 0.35}}],
      {"rot": {"x": 0, "y": 0, "z": 0}},
      ["pickup_wheel", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.9, "y": 1.4, "z": 0.35}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["RR", "wheelhub_RR", "tire_RR", "rr1", "rr2", 9999, "rr1", 1],
      ["RL", "wheelhub_RL", "tire_RL", "rl1", "rl2", 9999, "rl1", -1],
    ],
  },
}""" % (dflt("pickup_wheel_F_a"), dflt("pickup_wheel_R_a"),
        '["pickup_caliper", ["pickup_body"], [], {"pos": ["$=$track/2", -1.4, 0.35]}],' if stray_formula_part else "",
        '["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": -1.4, "z": 0.35}}],' if duplicate_row else "")
    if variable_pos:             # positions written as formulas cannot be read: the hubs must be used
        jb = jb.replace('"x": -0.9, "y": -1.4, "z": 0.35', '"x": "$=$track/2", "y": "$=$wb/2", "z": 0.35')
        jb = jb.replace('"x": 0.9, "y": -1.4, "z": 0.35', '"x": "$=$track/2", "y": "$=$wb/2", "z": 0.35')
        jb = jb.replace('"x": -0.9, "y": 1.4, "z": 0.35', '"x": "$=$track/2", "y": "$=$wb/2", "z": 0.35')
        jb = jb.replace('"x": 0.9, "y": 1.4, "z": 0.35', '"x": "$=$track/2", "y": "$=$wb/2", "z": 0.35')
    if anon_hubs:                # wheels in the JBeam that carry no FR/FL/RR/RL name
        for i, n in enumerate(("FR", "FL", "RR", "RL")):
            jb = jb.replace(f'["{n}", "wheelhub_{n}"', f'["{i + 1}", "wheelhub_{n}"')
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "glass_mat": {"name": "glass_mat", "translucent": True, "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 0.4]}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material", "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 1.0]}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/pickup/pickup.dae", make_dae(nodes))
        z.writestr("vehicles/pickup/pickup.jbeam", jb)
        z.writestr("vehicles/pickup/pickup.materials.json", json.dumps(materials))


def beamng_front_hints_only_zip(path) -> None:
    """A mod like real ones that only give usable hub hints for one axle: the front part's hub
    nodes are in the active 'nodes' table and its hub names match, so it gets a hint; the rear part
    uses a 'wheels' table whose node1/node2 point at suspension-part node ids this trimmed export
    never defines, so it gets none, and the rear wheel mesh itself is the only clue a rear axle
    exists at all. This reproduces a mod converting to only 2 wheels instead of 4."""
    tr = lambda x, y, z: f"<translate>{x} {y} {z}</translate>"
    nodes = [
        dict(name="pickup_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
        dict(name="pickup_wheel", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat"),
    ]
    jb = """{
  "pickup": {
    "slotType": "main",
    "slots2": [
      ["name", "allowTypes", "denyTypes", "default", "description"],
      ["wheels_F", ["pickup_wheel_F"], [], "pickup_wheel_F_a", "Front wheels"],
      ["wheels_R", ["pickup_wheel_R"], [], "pickup_wheel_R_a", "Rear wheels"],
    ],
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["fr1", -0.8, -1.4, 0.35], ["fr2", -1.0, -1.4, 0.35], ["fl1", 0.8, -1.4, 0.35], ["fl2", 1.0, -1.4, 0.35],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_body", ["pickup_body"], []],
    ],
  },
  "pickup_wheel_F_a": {
    "slotType": "pickup_wheel_F",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.9, "y": -1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": -1.4, "z": 0.35}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["FR", "wheelhub_FR", "tire_FR", "fr1", "fr2", 9999, "fr1", 1],
      ["FL", "wheelhub_FL", "tire_FL", "fl1", "fl2", 9999, "fl1", -1],
    ],
  },
  "pickup_wheel_R_a": {
    "slotType": "pickup_wheel_R",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.9, "y": 1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.9, "y": 1.4, "z": 0.35}}],
    ],
    "wheels": [
      ["name", "node1", "node2"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["RR", "susp_RR_hub", "susp_RR_rim"],
      ["RL", "susp_RL_hub", "susp_RL_rim"],
    ],
  },
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material", "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 1.0]}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/pickup/pickup.dae", make_dae(nodes))
        z.writestr("vehicles/pickup/pickup.jbeam", jb)
        z.writestr("vehicles/pickup/pickup.materials.json", json.dumps(materials))


def beamng_duplicate_wheel_mesh_zip(path) -> None:
    """A real-world Blender export pattern: the modder places one wheel rim with Shift+D (a real
    duplicate, not a linked one) at all four corners. Blender keeps object names unique, so the
    .dae ends up with the mesh exported as four differently-named nodes - "pickup_wheel",
    "pickup_wheel.001", "pickup_wheel.002", "pickup_wheel.003" - while the JBeam (which only cares
    about the one physical mesh) refers to the plain "pickup_wheel" for all four placements.
    Matching .dae names against the JBeam mesh name exactly finds only the first, unsuffixed
    instance; the other three are dropped as "not in the archive" - three wheels silently
    disappear, each corner left with whatever hardware (a caliper, here) shares its JBeam group."""
    tr = lambda x, y, z: f"<translate>{x} {y} {z}</translate>"
    corners = [("pickup_wheel", "FR", -0.9, -1.4), ("pickup_wheel.001", "FL", 0.9, -1.4),
              ("pickup_wheel.002", "RR", -0.9, 1.4), ("pickup_wheel.003", "RL", 0.9, 1.4)]
    nodes = [dict(name="pickup_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
                 xform=tr(0, 0, 0.9))]
    for node_name, slot, x, y in corners:
        nodes.append(dict(name=node_name, geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3",
                          material="wheel_mat", xform=tr(x, y, 0.35)))
        nodes.append(dict(name=f"pickup_caliper_{slot}", geom=box((0, 0, 0), (0.08, 0.12, 0.12)),
                          symbol="s1", material="body_mat", xform=tr(x * 0.8, y * 1.05 + (0.4 if y < 0 else -0.4), 0.55)))
    jb = """{
  "pickup": {
    "slotType": "main",
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["fr1", -0.8, -1.4, 0.35], ["fr2", -1.0, -1.4, 0.35], ["fl1", 0.8, -1.4, 0.35], ["fl2", 1.0, -1.4, 0.35],
      ["rr1", -0.8, 1.4, 0.35], ["rr2", -1.0, 1.4, 0.35], ["rl1", 0.8, 1.4, 0.35], ["rl2", 1.0, 1.4, 0.35],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_body", ["pickup_body"], []],
      ["pickup_wheel", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.9, "y": -1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": -1.4, "z": 0.35}}],
      ["pickup_wheel", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.9, "y": 1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.9, "y": 1.4, "z": 0.35}}],
      ["pickup_caliper_FR", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.72, "y": -1.0, "z": 0.75}}],
      ["pickup_caliper_FL", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.72, "y": -1.0, "z": 0.75}}],
      ["pickup_caliper_RR", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.72, "y": 1.86, "z": 0.75}}],
      ["pickup_caliper_RL", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.72, "y": 1.86, "z": 0.75}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["FR", "wheelhub_FR", "tire_FR", "fr1", "fr2", 9999, "fr1", 1],
      ["FL", "wheelhub_FL", "tire_FL", "fl1", "fl2", 9999, "fl1", -1],
      ["RR", "wheelhub_RR", "tire_RR", "rr1", "rr2", 9999, "rr1", 1],
      ["RL", "wheelhub_RL", "tire_RL", "rl1", "rl2", 9999, "rl1", -1],
    ],
  },
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material", "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 1.0]}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/pickup/pickup.dae", make_dae(nodes))
        z.writestr("vehicles/pickup/pickup.jbeam", jb)
        z.writestr("vehicles/pickup/pickup.materials.json", json.dumps(materials))


def beamng_shared_wheel_slot_offset_zip(path) -> None:
    """A real-world BeamNG authoring pattern (seen in production mods: a wheel/tyre part meant to
    serve several track-width presets is written once, entirely in LOCAL coordinates - nodes and
    flexbody mesh both at x=+-half-track, y=0, z=0 - and is placed on the car only by the *slot*
    that holds it, via that slot's ``nodeOffset``. The front and rear wheel slots are filled by two
    different parts of this kind, so a front wheel part and a rear wheel part can (and commonly do)
    author numerically identical flexbody pos/rot/scale and node positions - only the owning slot's
    nodeOffset actually tells them apart. Reading a flexbody's listed pos as if it were already the
    car's own coordinate space - ignoring the slot - makes the front and rear wheel on each side
    land on exactly the same point: the later one is dropped as "the same placement listed twice"
    (2 wheels instead of 4), and whichever survives sits near the car's centre, not its wheel
    arch."""
    tr = lambda x, y, z: f"<translate>{x} {y} {z}</translate>"
    nodes = [
        dict(name="pickup_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
        dict(name="pickup_wheel", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat"),
    ]
    jb = """{
  "pickup": {
    "slotType": "main",
    "slots2": [
      ["name", "allowTypes", "denyTypes", "default", "description"],
      ["wheels_F", ["pickup_wheel_F"], [], "pickup_wheel_F_a", "Front wheels", {"nodeOffset":{"y":-1.4, "z":0.35}}],
      ["wheels_R", ["pickup_wheel_R"], [], "pickup_wheel_R_a", "Rear wheels", {"nodeOffset":{"y":1.4, "z":0.35}}],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_body", ["pickup_body"], []],
    ],
  },
  "pickup_wheel_F_a": {
    "slotType": "pickup_wheel_F",
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["fr1", -0.8, 0, 0], ["fr2", -1.0, 0, 0], ["fl1", 0.8, 0, 0], ["fl2", 1.0, 0, 0],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.9, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": 0, "z": 0}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["FR", "wheelhub_FR", "tire_FR", "fr1", "fr2", 9999, "fr1", 1],
      ["FL", "wheelhub_FL", "tire_FL", "fl1", "fl2", 9999, "fl1", -1],
    ],
  },
  "pickup_wheel_R_a": {
    "slotType": "pickup_wheel_R",
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["rr1", -0.8, 0, 0], ["rr2", -1.0, 0, 0], ["rl1", 0.8, 0, 0], ["rl2", 1.0, 0, 0],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.9, "y": 0, "z": 0}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.9, "y": 0, "z": 0}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["RR", "wheelhub_RR", "tire_RR", "rr1", "rr2", 9999, "rr1", 1],
      ["RL", "wheelhub_RL", "tire_RL", "rl1", "rl2", 9999, "rl1", -1],
    ],
  },
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material", "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 1.0]}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/pickup/pickup.dae", make_dae(nodes))
        z.writestr("vehicles/pickup/pickup.jbeam", jb)
        z.writestr("vehicles/pickup/pickup.materials.json", json.dumps(materials))


def beamng_wheel_group_hardware_zip(path) -> None:
    """A real-world BeamNG authoring pattern: the front brake calipers are placed in the SAME
    JBeam group as the tyre ("wheel_FR"/"wheel_FL") because that is how BeamNG keeps hardware
    moving with its corner's suspension travel - the group name alone says nothing about which
    flexbody is the tyre and which is a caliper. A caliper modelled well clear of the wheel hub
    must stay a body part and must NOT be snapped onto the hub, or it renders as mangled debris
    jammed into the wheel arch."""
    tr = lambda x, y, z: f"<translate>{x} {y} {z}</translate>"
    nodes = [
        dict(name="pickup_body", geom=box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1", material="body_mat",
             xform=tr(0, 0, 0.9)),
        dict(name="pickup_wheel", geom=cylinder_x((0, 0, 0), 0.35, 0.25), symbol="s3", material="wheel_mat"),
        dict(name="pickup_brake_caliper", geom=box((0, 0, 0), (0.08, 0.12, 0.12)), symbol="s1",
             material="body_mat"),
    ]
    jb = """{
  "pickup": {
    "slotType": "main",
    "slots2": [
      ["name", "allowTypes", "denyTypes", "default", "description"],
      ["wheels_F", ["pickup_wheel_F"], [], "pickup_wheel_F_a", "Front wheels"],
      ["wheels_R", ["pickup_wheel_R"], [], "pickup_wheel_R_a", "Rear wheels"],
    ],
    "nodes": [
      ["id", "posX", "posY", "posZ"],
      ["fr1", -0.8, -1.4, 0.35], ["fr2", -1.0, -1.4, 0.35], ["fl1", 0.8, -1.4, 0.35], ["fl2", 1.0, -1.4, 0.35],
      ["rr1", -0.8, 1.4, 0.35], ["rr2", -1.0, 1.4, 0.35], ["rl1", 0.8, 1.4, 0.35], ["rl2", 1.0, 1.4, 0.35],
    ],
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_body", ["pickup_body"], []],
    ],
  },
  "pickup_wheel_F_a": {
    "slotType": "pickup_wheel_F",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.9, "y": -1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.9, "y": -1.4, "z": 0.35}}],
      ["pickup_brake_caliper", ["wheel_FR", "wheelhub_FR"], [], {"pos": {"x": -0.72, "y": -1.9, "z": 0.85}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_brake_caliper", ["wheel_FL", "wheelhub_FL"], [], {"pos": {"x": 0.72, "y": -1.9, "z": 0.85}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["FR", "wheelhub_FR", "tire_FR", "fr1", "fr2", 9999, "fr1", 1],
      ["FL", "wheelhub_FL", "tire_FL", "fl1", "fl2", 9999, "fl1", -1],
    ],
  },
  "pickup_wheel_R_a": {
    "slotType": "pickup_wheel_R",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["pickup_wheel", ["wheel_RR", "wheelhub_RR"], [], {"pos": {"x": -0.9, "y": 1.4, "z": 0.35}, "rot": {"x": 0, "y": 0, "z": 180}}],
      ["pickup_wheel", ["wheel_RL", "wheelhub_RL"], [], {"pos": {"x": 0.9, "y": 1.4, "z": 0.35}}],
    ],
    "pressureWheels": [
      ["name", "hubGroup", "group", "node1:", "node2:", "nodeS", "nodeArm", "wheelDir"],
      {"radius": 0.35, "tireWidth": 0.25},
      ["RR", "wheelhub_RR", "tire_RR", "rr1", "rr2", 9999, "rr1", 1],
      ["RL", "wheelhub_RL", "tire_RL", "rl1", "rl2", 9999, "rl1", -1],
    ],
  },
}"""
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}, {}, {}, {}]},
        "wheel_mat": {"name": "wheel_mat", "class": "Material", "Stages": [{"diffuseColor": [0.1, 0.1, 0.1, 1.0]}, {}, {}, {}]},
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("vehicles/pickup/pickup.dae", make_dae(nodes))
        z.writestr("vehicles/pickup/pickup.jbeam", jb)
        z.writestr("vehicles/pickup/pickup.materials.json", json.dumps(materials))
