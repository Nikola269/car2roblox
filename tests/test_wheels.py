"""Missing, fused, duplicated and unnamed wheels; BeamNG wheel placement; generated tyres."""
import numpy as np
import pytest

import fixtures as fx
import luaharness as lh
from car2roblox import wheels as wh
from car2roblox.model import Mesh
from car2roblox.pipeline import Options, convert

quiet = lambda s: None


def run(tmp_path, src, **kw):
    kw.setdefault("rig", False)
    kw.setdefault("formats", ("glb",))
    return convert(str(src), str(tmp_path / "out"), Options(**kw), log=quiet)


def wheel_info(res):
    return {w["slot"]: w for w in res.manifest["wheels"]}


# --------------------------------------------------------------------------- generic / OBJ
@pytest.mark.parametrize("mode", ["named", "unnamed", "fused", "dup"])
def test_four_wheels_from_every_layout(tmp_path, mode):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, mode)
    res = run(tmp_path, f)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"], mode
    assert not any(p["name"].startswith("tire_") for p in res.manifest["pieces"]), "nothing to generate"
    # every wheel is round and sits on the ground
    for slot, info in w.items():
        assert info["radius"] == pytest.approx(0.35 / 0.28, rel=0.02)
        assert info["hub"][1] - info["radius"] == pytest.approx(0.0, abs=0.02)
    assert (w["FL"]["hub"][0] < 0 < w["FR"]["hub"][0]) and (w["FL"]["hub"][2] < w["RL"]["hub"][2])


def test_fused_wheels_are_split_and_duplicates_dropped(tmp_path):
    f = tmp_path / "fused.obj"
    fx.write_obj_wheel_car(f, "fused")
    assert sum(p["kind"] == "wheel" for p in run(tmp_path, f).manifest["pieces"]) == 4
    f = tmp_path / "dup.obj"
    fx.write_obj_wheel_car(f, "dup")
    assert sum(p["kind"] == "wheel" for p in run(tmp_path, f).manifest["pieces"]) == 4   # one per wheel


def test_unnamed_round_parts_are_found_by_shape(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "unnamed")
    res = run(tmp_path, f)
    assert any("shape" in w for w in res.warnings)


def test_car_without_wheels_gets_tires(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "none")
    res = run(tmp_path, f)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"]
    names = [p["name"] for p in res.manifest["pieces"]]
    assert {"tire_FL", "rim_FL", "tire_RR", "rim_RR"} <= set(names)
    assert any("generated" in x for x in res.warnings)
    # the car now stands on the generated wheels: the lowest point of everything is the ground
    assert min(p["center"][1] - p["size"][1] / 2 for p in res.manifest["pieces"]) == pytest.approx(0.0, abs=0.02)
    for info in w.values():
        assert info["hub"][1] - info["radius"] == pytest.approx(0.0, abs=0.02)
        assert 0.8 <= info["radius"] <= 2.0
    # the wheels lie under the body, left and right of the centre line
    xs = sorted(i["hub"][0] for i in w.values())
    assert xs[0] < -1 and xs[-1] > 1


def test_add_tires_can_be_turned_off(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "none")
    res = run(tmp_path, f, add_tires=False, rig=True)
    assert res.wheels == 0
    assert any("No wheels were detected" in w for w in res.warnings)


def test_generated_wheels_are_valid_closed_meshes():
    for side in (1, -1):
        tire, rim = wh.make_wheel(1.2, 0.9, side)
        for m in (tire, rim):
            p, t = m.positions.astype(float), m.triangles
            vol = np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6
            assert vol > 0                                             # outward faces
            assert m.tri_count <= 700 and m.normals is not None
        lo, hi = tire.bounds()
        assert hi[1] == pytest.approx(1.2, abs=1e-3) and (hi[0] - lo[0]) == pytest.approx(0.9, abs=0.02)


def test_split_islands():
    t, r = wh.make_wheel(1, 1, 1)
    m = Mesh.concat([t, t.transformed(np.eye(3), [10, 0, 0]), r.transformed(np.eye(3), [0, 0, 20])])
    assert sorted(i.tri_count for i in wh.split_islands(m)) == sorted([t.tri_count, t.tri_count, r.tri_count])


def test_triangle_budget_is_respected_by_generated_tires(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "none")
    res = run(tmp_path, f, max_tris_per_mesh=300)
    assert all(p["triangles"] <= 300 for p in res.manifest["pieces"])


def test_arches_are_not_wheels():
    from car2roblox.classify import classify_part
    assert classify_part("wheel_arch_FL") == "body"
    assert classify_part("wheelwell_liner") == "body"
    assert classify_part("wheel_FL") == "wheel"
    assert classify_part("steering_wheel") == "interior"


# --------------------------------------------------------------------------- BeamNG
def test_beamng_one_wheel_mesh_is_placed_four_times(tmp_path):
    z = tmp_path / "pickup.zip"
    fx.beamng_flexbody_zip(z)
    res = run(tmp_path, z)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"]
    assert not any(p["name"].startswith("tire_") for p in res.manifest["pieces"])     # the mod's own mesh
    s = 1 / 0.28
    assert w["FR"]["hub"][0] == pytest.approx(0.9 * s, rel=0.01)      # BeamNG right is -x -> Roblox +X
    assert w["FL"]["hub"][0] == pytest.approx(-0.9 * s, rel=0.01)
    assert w["RR"]["hub"][2] - w["FR"]["hub"][2] == pytest.approx(2.8 * s, rel=0.01)   # wheelbase from pos
    assert not res.warnings or not any("generated" in x for x in res.warnings)
    for info in w.values():
        assert info["hub"][1] - info["radius"] == pytest.approx(0.0, abs=0.02)


def test_beamng_wheels_from_jbeam_when_the_dae_has_no_wheel_mesh(tmp_path):
    z = tmp_path / "pickup.zip"
    fx.beamng_flexbody_zip(z, wheel_mesh=False)
    res = run(tmp_path, z)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"]
    assert {"tire_FL", "rim_FR"} <= {p["name"] for p in res.manifest["pieces"]}
    s = 1 / 0.28
    assert w["FR"]["hub"][0] == pytest.approx(0.9 * s, rel=0.01)
    assert w["FR"]["radius"] == pytest.approx(0.35 * s, rel=0.01)       # JBeam's own tyre radius
    assert w["FR"]["width"] == pytest.approx(0.25 * s, rel=0.05)
    assert w["RL"]["hub"][2] - w["FL"]["hub"][2] == pytest.approx(2.8 * s, rel=0.01)


def test_beamng_slots2_and_empty_wheel_slots(tmp_path):
    z = tmp_path / "pickup.zip"
    fx.beamng_flexbody_zip(z, empty_defaults=True)           # the config installs no wheels at all
    res = run(tmp_path, z)
    assert sorted(wheel_info(res)) == ["FL", "FR", "RL", "RR"]
    assert any(p["name"].startswith("pickup_wheel") for p in res.manifest["pieces"])


def test_beamng_duplicate_rows_do_not_stack(tmp_path):
    z = tmp_path / "pickup.zip"
    fx.beamng_flexbody_zip(z, duplicate_row=True)
    res = run(tmp_path, z)
    assert sum(p["kind"] == "wheel" for p in res.manifest["pieces"]) == 4


def test_beamng_euler_matrix():
    from car2roblox.imp_beamng import euler_matrix
    assert np.allclose(euler_matrix(np.array([0, 0, 180.0])), np.diag([-1, -1, 1]), atol=1e-9)
    R = euler_matrix(np.array([10.0, 20.0, 30.0]))
    assert np.allclose(R @ R.T, np.eye(3)) and np.linalg.det(R) == pytest.approx(1.0)


def test_beamng_table_parser_modifiers():
    from car2roblox.imp_beamng import _table
    rows = _table([["name", "node1:"], {"radius": 0.3}, ["a", "n1"], ["b", "n2", {"radius": 0.4}], ["c", "n3"]])
    assert [r["_mods"]["radius"] for r in rows] == [0.3, 0.4, 0.3]
    assert [r["node1"] for r in rows] == ["n1", "n2", "n3"]


# --------------------------------------------------------------------------- GTA SA
def test_sa_car_without_wheel_model_gets_tires_at_the_dummies(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car(wheel=False))
    res = run(tmp_path, tmp_path / "t.dff")
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"]
    assert any("generated" in x for x in res.warnings)
    assert w["FL"]["hub"][0] == pytest.approx(-0.9 / 0.28, rel=0.02)


# --------------------------------------------------------------------------- the Studio script
def test_rig_uses_cylinder_colliders_and_no_collision_pairs(tmp_path):
    if not lh.AVAILABLE:
        pytest.skip("Lua not available")
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "none")
    res = run(tmp_path, f, rig=True, name="W", top_speed=120)
    names = [p["name"] for p in res.manifest["pieces"]]
    ok, out = lh.run_rig((res.out_dir / "W_setup.lua").read_text(), names)
    assert ok, out
    r = lh.parse_results(out)
    assert r["colliders"] == "4"
    assert int(r["nocollide"]) >= 8                           # every wheel vs the body and the seat
    assert r["prompt"] == "true" and r["seat_collides"] == "false"
    assert float(r["maxspeed"]) == pytest.approx(120 / 3.6 / 0.28, abs=0.1)
    assert r["anchored"] == "0" and "WARN" not in out


# --------------------------------------------------------------------------- wheels that are not on their hubs
def _hubs(res):
    return {k: np.array(v["hub"]) for k, v in wheel_info(res).items()}


@pytest.mark.parametrize("mode", ["template", "stacked"])
def test_wheel_left_in_the_middle_is_moved_to_the_four_hubs(tmp_path, mode):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, mode)
    res = run(tmp_path, f)
    h = _hubs(res)
    assert sorted(h) == ["FL", "FR", "RL", "RR"]
    for slot, hub in h.items():                    # out at the sides, front/back apart, nothing left at the centre
        assert abs(hub[0]) > 2.0, (slot, hub)
    assert h["FL"][0] < 0 < h["FR"][0] and h["FL"][2] < 0 < h["RL"][2]
    assert not any(p["name"] == "wheel" for p in res.manifest["pieces"]), "the middle wheel is gone"
    assert any("middle of the car" in w for w in res.warnings)


def test_middle_wheel_is_moved_even_with_add_tires_off(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "template")
    res = run(tmp_path, f, add_tires=False)
    assert sorted(wheel_info(res)) == ["FL", "FR", "RL", "RR"]
    assert not any(p["name"].startswith("tire_") for p in res.manifest["pieces"])


def test_only_some_wheels_the_rest_are_copied(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "partial")
    res = run(tmp_path, f)
    h = _hubs(res)
    assert sorted(h) == ["FL", "FR", "RL", "RR"]
    assert h["RL"][0] == pytest.approx(h["FL"][0], abs=0.01) and h["RR"][0] == pytest.approx(h["FR"][0], abs=0.01)
    assert h["RL"][2] > 0 > h["FL"][2]
    assert not any(p["name"].startswith("tire_") for p in res.manifest["pieces"]), "copied, not generated"
    # sitting on the ground like the originals
    for info in wheel_info(res).values():
        assert info["hub"][1] - info["radius"] == pytest.approx(0.0, abs=0.02)


def test_spare_tyre_in_the_middle_stays_body(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "spare")
    res = run(tmp_path, f)
    assert sorted(wheel_info(res)) == ["FL", "FR", "RL", "RR"]
    spare = next(p for p in res.manifest["pieces"] if p["name"] == "spare_wheel")
    assert spare["kind"] == "body"


def test_new_tires_replace_the_look_but_keep_the_positions(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "named")
    old = _hubs(run(tmp_path, f))
    res = run(tmp_path, f, new_tires=True)
    new = _hubs(res)
    assert sorted(new) == ["FL", "FR", "RL", "RR"]
    for k in old:
        assert np.allclose(old[k], new[k], atol=0.02), k
    names = {p["name"] for p in res.manifest["pieces"] if p["kind"] == "wheel"}
    assert names == {f"{k}_{s}" for k in ("tire", "rim") for s in ("FL", "FR", "RL", "RR")}


def test_new_tires_without_any_wheels_still_generates(tmp_path):
    f = tmp_path / "car.obj"
    fx.write_obj_wheel_car(f, "none")
    res = run(tmp_path, f, new_tires=True, add_tires=False)
    assert sorted(wheel_info(res)) == ["FL", "FR", "RL", "RR"]


def test_lr_names_are_rear_left():
    from car2roblox.classify import slot_from_name
    assert [slot_from_name(n) for n in ("wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr")] == ["FL", "FR", "RL", "RR"]


# --------------------------------------------------------------------------- BeamNG positions that cannot be read
def test_beamng_formula_positions_fall_back_to_the_hubs(tmp_path):
    z = tmp_path / "mod.zip"
    fx.beamng_flexbody_zip(z, variable_pos=True)
    res = run(tmp_path, z)
    h = _hubs(res)
    assert sorted(h) == ["FL", "FR", "RL", "RR"]
    assert all(abs(v[0]) > 2.0 for v in h.values()), h          # not piled up in the middle
    assert h["FL"][2] < 0 < h["RL"][2]


def test_beamng_hubs_without_names_get_their_slots_from_position(tmp_path):
    z = tmp_path / "mod.zip"
    fx.beamng_flexbody_zip(z, wheel_mesh=False, anon_hubs=True)
    res = run(tmp_path, z)
    assert sorted(wheel_info(res)) == ["FL", "FR", "RL", "RR"]


def test_beamng_hints_for_only_one_axle_still_finds_all_four_wheels(tmp_path):
    # real-world bug: the rear part's hub nodes can't be resolved (a 'wheels' table pointing at
    # node ids this trimmed export never defines), so only the front axle gets a hub hint even
    # though both axles have meshes on disk. The two should no longer make the hints the whole story.
    z = tmp_path / "mod.zip"
    fx.beamng_front_hints_only_zip(z)
    res = run(tmp_path, z)
    h = _hubs(res)
    assert sorted(h) == ["FL", "FR", "RL", "RR"], res.warnings
    assert h["FL"][2] < 0 < h["RL"][2]
    assert abs(h["FL"][0] - h["RL"][0]) < 0.3         # same track width front and rear


def test_beamng_hardware_sharing_the_wheel_group_stays_body(tmp_path):
    # real-world bug: a brake caliper placed in the SAME JBeam group as the tyre ("wheel_FR") -
    # a common way BeamNG mods tie hardware to a corner's suspension travel - was swept in as if
    # it were the wheel itself and snapped onto the hub, rendering as mangled, body-painted debris
    # jammed into the wheel arch.
    z = tmp_path / "mod.zip"
    fx.beamng_wheel_group_hardware_zip(z)
    res = run(tmp_path, z)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"]
    calipers = [p for p in res.manifest["pieces"] if "caliper" in p["name"]]
    assert len(calipers) == 2, [p["name"] for p in res.manifest["pieces"]]
    for c in calipers:
        assert c["kind"] == "body", c
        assert c["wheel"] is None, c
    # no wheel group picked up a caliper as one of its own moving pieces
    for info in w.values():
        assert not any("caliper" in p for p in info["pieces"]), info
    # not snapped onto the hub: the calipers stay well clear of where the wheels actually are
    for c in calipers:
        nearest = min(float(np.hypot(c["center"][1] - info["hub"][1], c["center"][2] - info["hub"][2]))
                      for info in w.values())
        assert nearest > 0.3 / 0.28, (c, w)


def test_beamng_duplicated_wheel_mesh_keeps_all_four_wheels(tmp_path):
    # real-world bug (found from an actual BeamNG mod's exported .dae): a modder who Shift+D's one
    # wheel rim around the car ends up with Blender object names "wheel", "wheel.001", "wheel.002",
    # "wheel.003" in the export, while the JBeam refers to the plain "wheel" name for all four
    # placements. Matching .dae names against the JBeam mesh name exactly found only the
    # unsuffixed instance - three wheels silently vanished, each corner left with only its brake
    # caliper (which has its own, unsuffixed name) rendered loose in the empty arch.
    z = tmp_path / "mod.zip"
    fx.beamng_duplicate_wheel_mesh_zip(z)
    res = run(tmp_path, z)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"], res.warnings
    wheels = [p for p in res.manifest["pieces"] if p["kind"] == "wheel"]
    assert len(wheels) == 4, [p["name"] for p in res.manifest["pieces"]]
    # every wheel actually sits at its own corner, not stacked on top of one another
    centers = {tuple(round(c, 2) for c in p["center"]) for p in wheels}
    assert len(centers) == 4, wheels
    # the calipers are still correctly left out as hardware, not swept in as wheels
    calipers = [p for p in res.manifest["pieces"] if "caliper" in p["name"]]
    assert len(calipers) == 4, [p["name"] for p in res.manifest["pieces"]]
    for c in calipers:
        assert c["kind"] == "body", c


def test_beamng_wheel_part_placed_only_by_slot_offset_keeps_all_four(tmp_path):
    # real-world bug (found from an actual BeamNG mod, a Volkswagen Golf Mk2): the front and rear
    # wheel parts are each written with their own nodes and flexbody mesh entirely in local
    # coordinates (y=z=0) and rely on their owning SLOT's nodeOffset to land at the actual front
    # or rear axle - a standard pattern for a wheel/tyre part meant to serve several track-width
    # presets. Ignoring the slot and reading the flexbody's listed pos as the car's own coordinate
    # space made the front and rear wheel on each side collapse onto the exact same point: one of
    # every pair was dropped as "the same placement listed twice" (2 wheels instead of 4), and the
    # survivors sat bunched near the car's centre instead of under their wheel arches.
    z = tmp_path / "mod.zip"
    fx.beamng_shared_wheel_slot_offset_zip(z)
    res = run(tmp_path, z)
    w = wheel_info(res)
    assert sorted(w) == ["FL", "FR", "RL", "RR"], res.warnings
    wheels = [p for p in res.manifest["pieces"] if p["kind"] == "wheel"]
    assert len(wheels) == 4, [p["name"] for p in res.manifest["pieces"]]
    # front and rear wheels on the same side must not be stacked on top of one another
    centers = {tuple(round(c, 2) for c in p["center"]) for p in wheels}
    assert len(centers) == 4, wheels
    # the slot's nodeOffset actually moved them apart lengthwise (not just deduplicated in place)
    front_z = {round(w[s]["hub"][2], 2) for s in ("FL", "FR")}
    rear_z = {round(w[s]["hub"][2], 2) for s in ("RL", "RR")}
    assert front_z != rear_z, w
