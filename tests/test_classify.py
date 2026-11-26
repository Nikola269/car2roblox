"""Regression tests for the "solid body panel renders as a translucent ghost double" bug: a
near-opaque (but not quite 255/1.0) alpha value on a material - routine exporter noise from
AO/sheen/dirt/decal baking, not an intentionally see-through part - was being read as "this is
glass" across every importer, and classify_part() would then wave almost anything through as
"glass" unless its name said "light". See the comments by each importer's alpha cut-off and by
classify._NOT_GLASS for the full story.
"""
import numpy as np

import fixtures as fx
from car2roblox.classify import classify_part
from car2roblox.imp_beamng import import_beamng
from car2roblox.imp_gtasa import import_gta_sa

quiet = lambda s: None


# --------------------------------------------------------------------------- classify_part itself
def test_a_body_part_name_is_never_swept_into_glass_by_a_stray_transparent_flag():
    for name in ("bonnet", "hood", "roof", "chassis", "cage", "door_FL", "front_bumper",
                 "wing_mirror", "exhaust_tip", "rear_spoiler", "engine_bay", "undertray"):
        assert classify_part(name, transparent=True) == "body", name


def test_glass_named_by_itself_is_unaffected():
    for name in ("windscreen", "window_FL", "glass_roof"):
        assert classify_part(name, transparent=True) == "glass", name
        assert classify_part(name, transparent=False) == "glass", name  # the name alone is enough


def test_an_explicit_glass_material_name_still_wins_even_on_a_body_named_part():
    assert classify_part("bonnet", material_names=["glass_tint_mat"], transparent=True) == "glass"


def test_an_unnamed_mesh_can_still_be_picked_up_as_glass_by_a_genuinely_low_alpha():
    # nothing in the name hints either way - the alpha heuristic is the only signal there is, and it
    # must still work for real unnamed windscreen meshes (a common real-world export).
    assert classify_part("mesh_014", transparent=True) == "glass"
    assert classify_part("mesh_014", transparent=False) == "body"


def test_a_light_lens_is_a_light_not_glass_even_though_its_alpha_is_often_low():
    assert classify_part("headlight_lens", transparent=True) == "light"
    assert classify_part("taillight", transparent=True) == "light"


# --------------------------------------------------------------------------- GTA SA importer
def _sa_car_with_bonnet_alpha(a: int) -> bytes:
    """A minimal SA car: a chassis, and a 'bonnet' part whose material has alpha ``a``/255."""
    p, n, u, t = fx.box((0, 0, 0.6), (1.8, 4.4, 0.9))
    g_body = fx.dff_geometry(p, n, u, t, np.zeros(len(t), int), [fx.dff_material((60, 255, 0, 255))])
    p, n, u, t = fx.box((0, 1.4, 0.6), (1.6, 1.2, 0.5))
    g_bonnet = fx.dff_geometry(p, n, u, t, np.zeros(len(t), int), [fx.dff_material((60, 255, 0, a))])
    frames = [("root", (0, 0, 0), -1), ("chassis", (0, 0, 0.3), 0), ("bonnet", (0, 0, 0), 1)]
    return fx.build_dff(frames, [g_body, g_bonnet], [(1, 0), (2, 1)])


def test_sa_bonnet_with_noisy_near_opaque_alpha_stays_an_opaque_body_part(tmp_path):
    # 200/255 (~0.78) used to be under the old 217 cut-off: a solid bonnet with this kind of
    # export noise on its alpha channel was misread as "glass" and rendered as a translucent ghost
    # double over the real body.
    dff = tmp_path / "car.dff"
    dff.write_bytes(_sa_car_with_bonnet_alpha(200))
    m = import_gta_sa(str(dff), log=quiet)
    bonnet = next(p for p in m.parts if p.name == "bonnet")
    mat = m.materials[bonnet.mesh.material_ids[0]]
    assert not mat.transparent
    assert classify_part(bonnet.name, transparent=mat.transparent) == "body"


def test_sa_bonnet_with_a_genuinely_low_alpha_is_still_kept_as_an_opaque_body_part(tmp_path):
    # even if some future exporter's noise dips below the new cut-off too, a part plainly named
    # like a body panel must never turn into glass - the name guard is the real fix, the tighter
    # cut-off is only the first line of defence.
    dff = tmp_path / "car.dff"
    dff.write_bytes(_sa_car_with_bonnet_alpha(100))
    m = import_gta_sa(str(dff), log=quiet)
    bonnet = next(p for p in m.parts if p.name == "bonnet")
    mat = m.materials[bonnet.mesh.material_ids[0]]
    assert mat.transparent                                   # alpha is genuinely low here
    assert classify_part(bonnet.name, transparent=mat.transparent) == "body"  # but the name wins


def test_sa_real_glass_is_still_classified_as_glass(tmp_path):
    dff = tmp_path / "car.dff"
    dff.write_bytes(fx.sa_test_car())
    m = import_gta_sa(str(dff), log=quiet)
    windscreen = next(p for p in m.parts if p.name == "windscreen_ok")
    mat = m.materials[windscreen.mesh.material_ids[0]]
    assert mat.transparent
    assert classify_part(windscreen.name, transparent=mat.transparent) == "glass"


# --------------------------------------------------------------------------- BeamNG importer
def test_beamng_chassis_mesh_with_noisy_near_opaque_alpha_stays_opaque(tmp_path):
    def tr(x, y, z):
        return f"<translate>{x} {y} {z}</translate>"

    nodes = [
        dict(name="testcar_body", geom=fx.box((0, 0, 0), (1.8, 4.4, 0.9)), symbol="s1",
             material="body_mat", xform=tr(0, 0, 0.9)),
        dict(name="testcar_cage", geom=fx.box((0, 0, 0), (1.4, 3.8, 0.7)), symbol="s2",
             material="cage_mat", xform=tr(0, 0, 0.9)),
    ]
    jb = """{
  "testcar": {
    "slotType": "main",
    "flexbodies": [
      ["mesh", "[group]:", "nonFlexMaterials"],
      ["testcar_body", ["testcar_body"], []],
      ["testcar_cage", ["testcar_cage"], []],
    ],
  },
}"""
    # 0.8 used to be under the old 0.85 cut-off: a cage mesh with this kind of noise alpha was
    # misread as "glass" and rendered as a translucent ghost double, as the imp_beamng.py comment
    # describes.
    materials = {
        "body_mat": {"name": "body_mat", "class": "Material", "Stages": [{"instanceDiffuse": True}]},
        "cage_mat": {"name": "cage_mat", "class": "Material",
                     "Stages": [{"diffuseColor": [0.5, 0.5, 0.5, 0.8]}]},
    }
    p = tmp_path / "mod.zip"
    import json
    import zipfile
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("vehicles/testcar/testcar.dae", fx.make_dae(nodes))
        z.writestr("vehicles/testcar/testcar.jbeam", jb)
        z.writestr("vehicles/testcar/testcar.materials.json", json.dumps(materials))
    m = import_beamng(str(p), log=quiet)
    cage = next(part for part in m.parts if part.name == "testcar_cage")
    mat = m.materials[cage.mesh.material_ids[0]]
    assert not mat.transparent
    assert classify_part(cage.name, transparent=mat.transparent) == "body"


def test_beamng_real_glass_is_still_classified_as_glass(tmp_path):
    p = tmp_path / "mod.zip"
    fx.beamng_test_zip(p)
    m = import_beamng(str(p), log=quiet)
    glass = next(part for part in m.parts if part.name == "testcar_glass")
    mat = m.materials[glass.mesh.material_ids[0]]
    assert mat.transparent
    assert classify_part(glass.name, transparent=mat.transparent) == "glass"
