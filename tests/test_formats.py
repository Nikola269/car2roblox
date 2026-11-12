import json
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from car2roblox import rwparse
from car2roblox.export import write_glb
from car2roblox.imp_beamng import import_beamng
from car2roblox.imp_generic import import_gltf, import_obj
from car2roblox.imp_gtasa import import_gta_sa
from car2roblox.model import ConversionError
from car2roblox.pipeline import Options, convert
from car2roblox.textures import make_dds
import fixtures as fx

quiet = lambda s: None


# ------------------------------------------------------------------ RenderWare
def test_dff_parse_structure():
    dff = rwparse.parse_dff(fx.sa_test_car())
    names = [f.name for f in dff.frames]
    assert names[:3] == ["root", "chassis", "bonnet_ok"]
    assert len(dff.geometries) == 5 and len(dff.atomics) == 6
    g = dff.geometries[0]
    assert len(g.positions) == 24 and len(g.triangles) == 12
    assert g.materials[0].color == (60, 255, 0, 255) and g.materials[1].texture == "bodytex"
    assert g.normals is not None and g.uvs is not None
    # world transform composes the parent chain: bonnet = chassis(0,0,.3) + (0,1.4,.6)
    R, t = dff.world(2)
    assert np.allclose(t, [0, 1.4, 0.9])


def test_dff_triangle_order_matches_file_layout():
    # one triangle (0,1,2) must come back as (0,1,2) in CCW order
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32)
    nrm = np.tile([0, 0, 1], (3, 1)).astype(np.float32)
    uv = np.zeros((3, 2), np.float32)
    g = fx.dff_geometry(pos, nrm, uv, np.array([[0, 1, 2]]), [0], [fx.dff_material()])
    data = fx.build_dff([("root", (0, 0, 0), -1)], [g], [(0, 0)])
    out = rwparse.parse_dff(data).geometries[0]
    assert out.triangles.tolist() == [[0, 1, 2]]


def test_dff_binmesh_fallback_when_struct_has_no_faces():
    dff = rwparse.parse_dff(fx.sa_test_car(binmesh_only=True))
    assert len(dff.geometries[0].triangles) == 12


def test_dff_binmesh_tristrip():
    # strip 0 1 2 3 -> triangles (0,1,2) and (2,1,3)
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], np.float32)
    nrm = np.tile([0, 0, 1], (4, 1)).astype(np.float32)
    st = struct.pack("<IIII", 0x02 | 0x04 | 0x10 | (1 << 16) | 1, 0, 4, 1) + np.zeros((4, 2), "<f4").tobytes()
    st += struct.pack("<4f", 0, 0, 0, 1) + struct.pack("<II", 1, 1) + pos.tobytes() + nrm.tobytes()
    bm = struct.pack("<III", 1, 1, 4) + struct.pack("<II", 4, 0) + struct.pack("<4I", 0, 1, 2, 3)
    geo = fx.chunk(0x0F, fx.chunk(1, st) + fx.chunk(8, fx.chunk(1, struct.pack("<Ii", 1, -1)) + fx.dff_material())
                   + fx.chunk(3, fx.chunk(0x50E, bm)))
    data = fx.build_dff([("root", (0, 0, 0), -1)], [geo], [(0, 0)])
    tris = rwparse.parse_dff(data).geometries[0].triangles.tolist()
    assert tris == [[0, 1, 2], [2, 1, 3]]


def test_dff_errors_are_clear():
    with pytest.raises(rwparse.RWError):
        rwparse.parse_dff(b"not a dff at all, definitely")
    with pytest.raises(rwparse.RWError):
        rwparse.parse_dff(fx.sa_test_car()[:200])          # truncated


def test_txd_8888_exact():
    imgs = rwparse.parse_txd(fx.sa_test_txd())
    img = np.asarray(imgs["bodytex"])
    assert img.shape == (4, 4, 4)
    assert tuple(img[0, 0]) == (255, 0, 0, 255) and tuple(img[0, 3]) == (0, 255, 0, 255)


def test_txd_dxt1_solid_red():
    block = struct.pack("<HHI", 0xF800, 0x0000, 0)          # colour0 red, all indices 0
    txd = fx.build_txd([fx.native_texture("red", 4, 4, "dxt1", block)])
    img = np.asarray(rwparse.parse_txd(txd)["red"])
    assert img.shape == (4, 4, 4)
    assert (img[..., 0] >= 248).all() and (img[..., 1] <= 4).all() and (img[..., 2] <= 4).all()


def test_txd_dxt5_alpha():
    # alpha0 = 255, alpha1 = 0, all indices 0 -> opaque; colour red
    block = bytes([255, 0]) + b"\0" * 6 + struct.pack("<HHI", 0xF800, 0, 0)
    txd = fx.build_txd([fx.native_texture("a", 4, 4, "dxt5", block)])
    img = np.asarray(rwparse.parse_txd(txd)["a"])
    assert (img[..., 3] == 255).all() and (img[..., 0] >= 248).all()


def test_txd_pal8_and_565():
    palette = bytes([10, 20, 30, 255, 200, 100, 50, 255]) + b"\0" * (1024 - 8)
    idx = bytes([0, 1, 1, 0] * 4)
    px565 = struct.pack("<H", (31 << 11)) * 4              # pure red
    txd = fx.build_txd([fx.native_texture("p", 4, 4, "pal8", idx, palette),
                        fx.native_texture("r", 2, 2, "565", px565)])
    imgs = rwparse.parse_txd(txd)
    p = np.asarray(imgs["p"])
    assert tuple(p[0, 0]) == (10, 20, 30, 255) and tuple(p[0, 1]) == (200, 100, 50, 255)
    r = np.asarray(imgs["r"])
    assert tuple(r[0, 0]) == (255, 0, 0, 255)


def test_txd_rejects_console_platform():
    bad = fx.native_texture("x", 4, 4, "8888", b"\0" * 64)
    bad = bad.replace(struct.pack("<I2BH", 9, 0x16, 0x11, 0), struct.pack("<I2BH", 6, 0x16, 0x11, 0), 1)
    with pytest.raises(rwparse.RWError, match="platform"):
        rwparse.parse_txd(fx.build_txd([bad]))


def test_dds_wrapper_decodes_via_pillow():
    block = struct.pack("<HHI", 0x07E0, 0, 0)               # green
    from car2roblox.textures import load_image_bytes
    img = load_image_bytes(make_dds(4, 4, b"DXT1", block))
    assert img.size == (4, 4) and img.getpixel((0, 0))[1] >= 248


# ------------------------------------------------------------------ GTA SA importer
@pytest.fixture()
def sa_files(tmp_path):
    (tmp_path / "testcar.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "testcar.txd").write_bytes(fx.sa_test_txd())
    return tmp_path


def test_sa_import_filters_and_places_wheels(sa_files):
    m = import_gta_sa(str(sa_files / "testcar.dff"), log=quiet)
    names = sorted(p.name for p in m.parts)
    assert "door_lf_dam" not in names and "chassis_vlo" not in names
    assert {"chassis", "bonnet_ok", "windscreen_ok"} <= set(names)
    wheels = {p.wheel_slot: p for p in m.parts if p.wheel_slot}
    assert set(wheels) == {"FL", "FR", "RL", "RR"}
    # wheel hubs end up at the dummy positions
    for slot, (x, y) in {"FL": (-0.9, 1.4), "FR": (0.9, 1.4), "RL": (-0.9, -1.4), "RR": (0.9, -1.4)}.items():
        lo, hi = wheels[slot].mesh.bounds()
        c = (lo + hi) / 2
        assert np.allclose(c, [x, y, 0.35], atol=1e-3), slot
    # hierarchy: bonnet world z = 0.9
    hood = next(p for p in m.parts if p.name == "bonnet_ok")
    lo, hi = hood.mesh.bounds()
    assert abs((lo[2] + hi[2]) / 2 - 0.9) < 1e-4
    # textures + paint markers
    assert "bodytex" in m.textures
    assert any(mat.paint == "primary" for mat in m.materials)


def test_sa_right_wheels_are_turned_around(sa_files):
    m = import_gta_sa(str(sa_files / "testcar.dff"), log=quiet)
    w = {p.wheel_slot: p.mesh for p in m.parts if p.wheel_slot}

    # The wheel model's first cap centre (second-to-last vertex) sits at local -X. After placing it
    # on the left it must still face -X; on the right side it is turned around to face +X.
    def cap_side(mesh):
        lo, hi = mesh.bounds()
        return float(np.sign(mesh.positions[-2, 0] - (lo[0] + hi[0]) / 2))

    assert cap_side(w["FL"]) == -1.0 and cap_side(w["RL"]) == -1.0
    assert cap_side(w["FR"]) == 1.0 and cap_side(w["RR"]) == 1.0


def test_sa_missing_txd_is_reported(tmp_path):
    (tmp_path / "c.dff").write_bytes(fx.sa_test_car())
    m = import_gta_sa(str(tmp_path / "c.dff"), log=quiet)
    assert any("txd" in n.lower() for n in m.notes)


def test_sa_end_to_end(sa_files, tmp_path):
    out = tmp_path / "out"
    res = convert(str(sa_files / "testcar.dff"), str(out), Options(name="SATest"), log=quiet)
    root = out / "SATest"
    for f in ("SATest.glb", "manifest.json", "SATest_setup.lua", "IMPORT_INSTRUCTIONS.txt"):
        assert (root / f).exists(), f
    man = json.loads((root / "manifest.json").read_text())
    assert man["triangles"] == res.triangles > 0
    slots = {w["slot"]: w for w in man["wheels"]}
    assert set(slots) == {"FL", "FR", "RL", "RR"}
    # Roblox axes: forward = -Z, right = +X, ground at y = 0, centred
    assert slots["FL"]["hub"][2] < 0 < slots["RL"]["hub"][2]
    assert slots["FL"]["hub"][0] < 0 < slots["FR"]["hub"][0]
    assert slots["FL"]["steer"] and not slots["RL"]["steer"]
    assert min(p["center"][1] - p["size"][1] / 2 for p in man["pieces"]) == pytest.approx(0, abs=1e-3)
    assert man["size"]["length"] == pytest.approx(4.4 / 0.28, rel=0.02)      # 4.4 m -> studs
    # paint marker became the configured colour (default blue), not the magic green
    paint = [p for p in man["pieces"] if p["paint"] == "primary"]
    assert paint and paint[0]["color"][2] > paint[0]["color"][1]
    # textured part carries a PNG that exists
    tex = [p["texture"] for p in man["pieces"] if p["texture"]]
    assert tex and (root / tex[0]).exists()
    # windscreen is classified as glass and transparent
    assert any(p["kind"] == "glass" and p["transparent"] for p in man["pieces"])


def test_sa_paint_option(sa_files, tmp_path):
    res = convert(str(sa_files / "testcar.dff"), str(tmp_path / "o"),
                  Options(paint="#ff0000", rig=False, formats=("glb",)), log=quiet)
    p = [x for x in res.manifest["pieces"] if x["paint"] == "primary"][0]
    assert p["color"][:3] == pytest.approx([1.0, 0.0, 0.0], abs=1e-3)


def test_filters_remove_glass_and_interior(sa_files, tmp_path):
    res = convert(str(sa_files / "testcar.dff"), str(tmp_path / "o"),
                  Options(include_glass=False, rig=False, formats=("glb",)), log=quiet)
    assert not any(p["kind"] == "glass" for p in res.manifest["pieces"])


# ------------------------------------------------------------------ BeamNG
@pytest.fixture()
def bng_zip(tmp_path):
    p = tmp_path / "testcar_mod.zip"
    fx.beamng_test_zip(p)
    return p


def test_beamng_import_follows_default_config(bng_zip):
    m = import_beamng(str(bng_zip), log=quiet)
    names = sorted(p.name for p in m.parts)
    # unused + colmesh are not part of the default configuration
    assert names == ["testcar_body", "testcar_glass", "testcar_wheel_FL", "testcar_wheel_FR",
                     "testcar_wheel_RL", "testcar_wheel_RR"]
    mats = {x.name: x for x in m.materials}
    assert mats["body_mat"].paint == "primary"                       # instanceDiffuse
    assert mats["glass_mat"].transparent
    assert mats["wheel_mat"].texture and mats["wheel_mat"].texture in m.textures
    # the vehicle is rooted at z=0.9: body bbox centre
    body = next(p for p in m.parts if p.name == "testcar_body")
    lo, hi = body.mesh.bounds()
    assert np.allclose((lo + hi) / 2, [0, 0, 0.9], atol=1e-4)


def test_beamng_config_sets_paint(bng_zip):
    m = import_beamng(str(bng_zip), config="sport", log=quiet)
    assert m.default_paint["primary"] == pytest.approx((0.9, 0.1, 0.1))
    with pytest.raises(ConversionError, match="not found"):
        import_beamng(str(bng_zip), config="nope", log=quiet)


def test_beamng_end_to_end_axes(bng_zip, tmp_path):
    res = convert(str(bng_zip), str(tmp_path / "o"), Options(name="Bng"), log=quiet)
    man = res.manifest
    slots = {w["slot"]: w for w in man["wheels"]}
    assert set(slots) == {"FL", "FR", "RL", "RR"}
    # BeamNG front is -Y and left is +X: they must map to Roblox -Z (forward) and -X (left)
    assert slots["FL"]["hub"][2] < 0 < slots["RL"]["hub"][2]
    assert slots["FL"]["hub"][0] < 0 < slots["FR"]["hub"][0]
    assert slots["FL"]["pieces"] == ["testcar_wheel_FL"]
    wheel_piece = next(p for p in man["pieces"] if p["name"] == "testcar_wheel_FL")
    assert wheel_piece["texture"] and (res.out_dir / wheel_piece["texture"]).exists()


def test_beamng_folder_input_and_vehicle_choice(bng_zip, tmp_path):
    folder = tmp_path / "unpacked"
    with zipfile.ZipFile(bng_zip) as z:
        z.extractall(folder)
    m = import_beamng(str(folder), vehicle="testcar", log=quiet)
    assert len(m.parts) == 6
    with pytest.raises(ConversionError, match="not found"):
        import_beamng(str(folder), vehicle="ghost", log=quiet)


def test_beamng_part_with_unresolvable_formula_position_is_left_out(tmp_path):
    # a non-wheel part (e.g. a brake caliper) placed with a mirrored-side formula ("$=$track/2")
    # we cannot evaluate must be left out, with a warning, rather than drawn at a wrong default spot
    z = tmp_path / "mod.zip"
    fx.beamng_flexbody_zip(z, stray_formula_part=True)
    m = import_beamng(str(z), log=quiet)
    names = {p.name for p in m.parts}
    assert "pickup_caliper" not in names
    assert any("formula" in n and "pickup_caliper" in n for n in m.notes)
    # the rest of the car is unaffected
    assert {"pickup_body", "pickup_glass"} <= names
    assert sum(1 for n in names if "wheel" in n) == 4


def test_beamng_without_jbeam_skips_collision(tmp_path):
    p = tmp_path / "x.zip"
    fx.beamng_test_zip(p)
    # strip the jbeam so the importer has to fall back to name heuristics
    out = tmp_path / "y.zip"
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(out, "w") as zo:
        for n in zi.namelist():
            if not n.endswith(".jbeam"):
                zo.writestr(n, zi.read(n))
    m = import_beamng(str(out), log=quiet)
    names = {x.name for x in m.parts}
    assert "colmesh" not in names and "testcar_body" in names


# ------------------------------------------------------------------ generic
def test_obj_import_and_forward_detection(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_car(f, long_axis="x")                      # car lies along X
    m = import_obj(f, quiet)
    assert {p.name for p in m.parts} == {"body", "roof"}
    res = convert(str(f), str(tmp_path / "o"), Options(rig=False, formats=("glb",)), log=quiet)
    w, h, l = res.size_studs
    assert l > w and l == pytest.approx(4.4 / 0.28, rel=0.02)   # long axis became Z


def test_rpf_gets_a_clear_export_first_message(tmp_path):
    f = tmp_path / "vehicles.rpf"
    f.write_bytes(b"RPF7" + b"\x00" * 60)       # a real .rpf is a whole encrypted archive, not a model
    with pytest.raises(ConversionError, match="CodeWalker"):
        convert(str(f), str(tmp_path / "o"), Options(rig=False), log=quiet)


def test_obj_forward_axis_respected_when_already_z(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_car(f, long_axis="z")
    res = convert(str(f), str(tmp_path / "o"), Options(rig=False, formats=("glb",)), log=quiet)
    assert res.size_studs[2] > res.size_studs[0]


def test_explicit_axes_and_flip(sa_files, tmp_path):
    a = convert(str(sa_files / "testcar.dff"), str(tmp_path / "a"), Options(rig=False, formats=("glb",)), log=quiet)
    b = convert(str(sa_files / "testcar.dff"), str(tmp_path / "b"),
                Options(rig=False, formats=("glb",), flip_forward=True), log=quiet)
    ha = {w["slot"]: np.array(w["hub"]) for w in a.manifest["wheels"]}
    hb = {w["slot"]: np.array(w["hub"]) for w in b.manifest["wheels"]}
    # slots are re-derived from position, so after a 180-degree turn the old front-left wheel
    # is the new rear-right one: (x, y, z) -> (-x, y, -z)
    assert np.allclose(hb["RR"], ha["FL"] * [-1, 1, -1], atol=1e-3)
    assert np.allclose(hb["FL"], ha["RR"] * [-1, 1, -1], atol=1e-3)


def test_glb_roundtrip(sa_files, tmp_path):
    res = convert(str(sa_files / "testcar.dff"), str(tmp_path / "o"), Options(name="RT", rig=False), log=quiet)
    glb = res.out_dir / "RT.glb"
    m = import_gltf(glb, quiet)
    assert sum(p.mesh.tri_count for p in m.parts) == res.triangles
    assert len(m.parts) == res.pieces
    assert m.textures                                       # embedded PNG came back
    # GLB structure is valid
    raw = glb.read_bytes()
    assert raw[:4] == b"glTF" and struct.unpack_from("<I", raw, 8)[0] == len(raw)
    jlen = struct.unpack_from("<I", raw, 12)[0]
    js = json.loads(raw[20:20 + jlen])
    assert js["asset"]["version"] == "2.0"
    pos_acc = [a for a in js["accessors"] if a["type"] == "VEC3" and "min" in a]
    assert pos_acc and all(len(a["min"]) == 3 for a in pos_acc)


def test_dae_generic_import(tmp_path):
    nodes = [dict(name="body", geom=fx.box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s", material="m",
                  xform="<translate>0 0 1</translate>")]
    f = tmp_path / "c.dae"
    f.write_bytes(fx.make_dae(nodes))
    from car2roblox.imp_generic import import_dae_generic
    m = import_dae_generic(f, quiet)
    lo, hi = m.parts[0].mesh.bounds()
    assert np.allclose((lo + hi) / 2, [0, 0, 1], atol=1e-4)
    # Y-up files are rotated into Z-up
    f2 = tmp_path / "c2.dae"
    f2.write_bytes(fx.make_dae(nodes, up="Y_UP"))
    m2 = import_dae_generic(f2, quiet)
    lo, hi = m2.parts[0].mesh.bounds()
    assert np.allclose((lo + hi) / 2, [0, -1, 0], atol=1e-4)


def test_big_mesh_is_split_under_the_limit(tmp_path):
    f = tmp_path / "big.obj"
    p, n, u, t = fx.cylinder_x((0, 0, 0), 1, 4, segments=700)       # ~2800 tris
    lines = [f"v {a} {b} {c}" for a, b, c in p] + [f"f {a} {b} {c}" for a, b, c in t + 1]
    f.write_text("\n".join(lines))
    res = convert(str(f), str(tmp_path / "o"), Options(max_tris_per_mesh=500, rig=False, add_tires=False, formats=("glb",)), log=quiet)
    assert res.pieces > 1
    assert all(p["triangles"] <= 500 for p in res.manifest["pieces"])
    assert res.triangles == len(t)


def test_decimation_budget(tmp_path):
    f = tmp_path / "big.obj"
    p, n, u, t = fx.cylinder_x((0, 0, 0), 1, 4, segments=600)
    f.write_text("\n".join([f"v {a} {b} {c}" for a, b, c in p] + [f"f {a} {b} {c}" for a, b, c in t + 1]))
    res = convert(str(f), str(tmp_path / "o"), Options(target_tris=800, rig=False, add_tires=False, formats=("glb",)), log=quiet)
    assert 0 < res.triangles <= 1100                          # clustering gets close to the budget


def test_helpful_errors(tmp_path):
    with pytest.raises(ConversionError, match="does not exist"):
        convert(str(tmp_path / "nope.dff"), str(tmp_path / "o"), log=quiet)
    fbx = tmp_path / "a.fbx"
    fbx.write_bytes(b"Kaydara FBX Binary")
    with pytest.raises(ConversionError, match="Blender"):
        convert(str(fbx), str(tmp_path / "o"), log=quiet)
    yft = tmp_path / "a.yft"
    yft.write_bytes(b"RSC7")
    with pytest.raises(ConversionError, match="CodeWalker|Sollumz"):
        convert(str(yft), str(tmp_path / "o"), log=quiet)
    txd = tmp_path / "a.txd"
    txd.write_bytes(b"x")
    with pytest.raises(ConversionError, match=r"\.dff"):
        convert(str(txd), str(tmp_path / "o"), log=quiet)
    bad = tmp_path / "bad.dff"
    bad.write_bytes(b"\0" * 64)
    with pytest.raises(ConversionError):
        convert(str(bad), str(tmp_path / "o"), log=quiet)


def test_rerun_removes_stale_obj_and_png_files(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"), Options(name="Rerun"), log=lambda s: None)
    obj_dir = res.out_dir / "obj"
    stale = obj_dir / "OldPiece.obj"
    stale.write_text("o old\n")
    (obj_dir / "OldPiece.mtl").write_text("newmtl x\n")
    (res.out_dir / "textures" / "old_tex.png").write_bytes(b"x")
    keep = obj_dir / "notes.txt"
    keep.write_text("mine")
    convert(str(tmp_path / "t.dff"), str(tmp_path / "out"), Options(name="Rerun"), log=lambda s: None)
    assert not stale.exists() and not (obj_dir / "OldPiece.mtl").exists()
    assert not (res.out_dir / "textures" / "old_tex.png").exists()
    assert keep.exists()                       # only files the tool generates are removed


def test_instructions_use_the_documented_studio_wording(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"), Options(name="Words"), log=lambda s: None)
    text = (res.out_dir / "IMPORT_INSTRUCTIONS.txt").read_text()
    assert "File > Import" in text and "Import Only As Model" in text and "Scale Unit = Studs" in text
    assert "Home > Import 3D" not in text
