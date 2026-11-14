import json

import pytest

import luaharness as lh
from car2roblox.pipeline import Options, convert
import fixtures as fx

pytestmark = pytest.mark.skipif(not lh.AVAILABLE, reason="system Lua 5.4 library not found")
quiet = lambda s: None


@pytest.fixture()
def built(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"), Options(name="RigCar"), log=quiet)
    script = (res.out_dir / "RigCar_setup.lua").read_text()
    names = [p["name"] for p in res.manifest["pieces"]]
    return res, script, names


def test_script_runs_and_builds_a_car(built):
    res, script, names = built
    ok, out = lh.run_rig(script, names)
    assert ok, out
    r = lh.parse_results(out)
    assert r["seats"] == "4" and r["scripts"] == "1"
    assert r["drive"] == "4"                       # AWD default: every wheel is driven
    assert r["steer"] == "2"                       # front axle steers
    assert r["anchored"] == "0"                    # everything released at the end
    assert r["primary"] == "ChassisCollider"
    assert r["selected"] == "true"
    # every non-wheel piece is welded to the collider (+ wheel sub-parts + seat)
    n_nonwheel = sum(1 for p in res.manifest["pieces"] if p["kind"] != "wheel")
    assert int(r["welds"]) >= n_nonwheel
    assert "WARN" not in out


def test_steering_axis_points_up(built):
    _, script, names = built
    ok, out = lh.run_rig(script, names)
    assert ok, out
    ax = [float(x) for x in lh.parse_results(out)["steer_axis"].split(",")]
    assert ax == pytest.approx([0.0, 1.0, 0.0], abs=1e-6)


def test_driver_signs_and_scaling(built):
    res, script, names = built
    ok, out = lh.run_rig(script, names)
    assert ok, out
    r = lh.parse_results(out)
    radius = res.manifest["wheels"][0]["radius"]
    w = float(r["drive_w_Wheel_FL"])
    assert w == pytest.approx(-90.0 / radius, rel=1e-3)      # throttle 1 -> DriveSign(-1) * speed / radius
    assert float(r["steer_angle_Steer_FL"]) == pytest.approx(-30.0)   # steer right -> SteerSign(-1)
    assert float(r["drive_torque_Wheel_FL"]) > 0


def test_importer_name_variants_and_missing_parts(built):
    res, script, names = built
    body = res.manifest["body"]
    ok, out = lh.run_rig(script, names, rename={body: body + "_1"}, drop=(names[-1],))
    assert ok, out
    assert "WARN" in out and names[-1] in out              # missing piece reported, not fatal
    r = lh.parse_results(out)
    assert r["primary"] == "ChassisCollider" and r["seats"] == "4"


def test_clear_error_without_selection(built):
    _, script, names = built
    ok, out = lh.run_rig(script, names, select=False)
    assert not ok and "select the imported car" in out


def test_error_when_nothing_matches(built):
    _, script, _ = built
    ok, out = lh.run_rig(script, ["Totally", "Unrelated"])
    assert not ok and "none of the expected parts" in out


def test_drivetrain_options(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    for mode, drive, steer in (("rwd", "2", "2"), ("fwd", "2", "2"), ("awd", "4", "2")):
        res = convert(str(tmp_path / "t.dff"), str(tmp_path / mode), Options(name="D", drivetrain=mode), log=quiet)
        names = [p["name"] for p in res.manifest["pieces"]]
        ok, out = lh.run_rig((res.out_dir / "D_setup.lua").read_text(), names)
        assert ok, out
        r = lh.parse_results(out)
        assert (r["drive"], r["steer"]) == (drive, steer), mode
        drive_slots = {w["slot"] for w in res.manifest["wheels"] if w["drive"]}
        if mode == "rwd":
            assert drive_slots == {"RL", "RR"}
        if mode == "fwd":
            assert drive_slots == {"FL", "FR"}


def test_importer_node_suffix_is_matched(built):
    """Studio may rename a glTF mesh to '<Name>_Node' when node and mesh share a name."""
    res, script, names = built
    body = res.manifest["body"]
    ok, out = lh.run_rig(script, names, rename={body: body + "_Node"})
    assert ok, out
    assert "WARN" not in out
    assert lh.parse_results(out)["parts"] != "0"


def test_renamed_parts_are_matched_by_size(built):
    """If the importer renames parts beyond recognition, sizes from the manifest still identify them."""
    res, script, names = built
    sizes = {p["name"]: p["size"] for p in res.manifest["pieces"]}
    by_name = {p["name"]: p for p in res.manifest["pieces"]}
    # rename the distinctive (non-wheel) pieces to meaningless names
    victims = [p["name"] for p in res.manifest["pieces"] if p["kind"] != "wheel"]
    rename = {n: f"Mesh_{i}" for i, n in enumerate(victims)}
    ok, out = lh.run_rig(script, names, rename=rename, sizes=sizes)
    assert ok, out
    assert "WARN" not in out and "matched by size" in out
    r = lh.parse_results(out)
    for n in victims:
        cx, cy, cz = by_name[n]["center"]
        assert r["pos_" + rename[n]] == f"{cx:.3f},{cy + 2:.3f},{cz:.3f}", n   # SpawnHeight = 2


def test_no_size_match_when_sizes_differ(built):
    res, script, names = built
    sizes = {n: [50, 50, 50] for n in names}      # nothing like the manifest
    ok, out = lh.run_rig(script, names, rename={n: f"X{i}" for i, n in enumerate(names)}, sizes=sizes)
    assert not ok and "none of the expected parts" in out


def test_simple_chassis_has_no_suspension_parts(built):
    """The default (advanced_chassis=False) rig keeps its original fixed-hinge wheels."""
    _, script, names = built
    ok, out = lh.run_rig(script, names)
    assert ok, out
    r = lh.parse_results(out)
    assert r["uprights"] == "0" and r["suspensions"] == "0" and r["springs"] == "0"
    assert r["advanced_chassis"] == "false"


def test_advanced_chassis_builds_suspension_and_drivetrain(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"), Options(name="ACar", advanced_chassis=True),
                  log=quiet)
    names = [p["name"] for p in res.manifest["pieces"]]
    script = (res.out_dir / "ACar_setup.lua").read_text()
    ok, out = lh.run_rig(script, names)
    assert ok, out
    r = lh.parse_results(out)
    n_wheels = len(res.manifest["wheels"])
    # one suspension upright + one PrismaticConstraint + one SpringConstraint per wheel
    assert int(r["uprights"]) == n_wheels
    assert int(r["suspensions"]) == n_wheels
    assert int(r["springs"]) == n_wheels
    assert r["advanced_chassis"] == "true"
    assert int(r["knuckles"]) == 2                 # front axle still steers, same as the simple rig
    # the rig still builds normally: same seat/weld/selection behaviour as the simple chassis
    assert r["seats"] == "4" and r["scripts"] == "1" and r["selected"] == "true"
    # the drivetrain sim has taken over: an "unreachable" target speed in the right direction,
    # with the real driving force coming from MotorMaxTorque (computed from the torque curve)
    w = float(r["drive_w_Wheel_FL"])
    assert abs(w) >= 1000                          # nowhere near a real wheel speed - it's a target only
    assert w < 0                                   # DriveSign=-1, throttle=1 -> same sign convention as before
    assert float(r["drive_torque_Wheel_FL"]) > 0
    assert float(r["engine_rpm"]) >= 900           # at least idle
    assert r["gear"] == "1"                        # starts in first gear
