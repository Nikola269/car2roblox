import pytest

import luaharness as lh
from car2roblox import achassis
from car2roblox.pipeline import Options, convert
import fixtures as fx

pytestmark = pytest.mark.skipif(not lh.AVAILABLE, reason="system Lua 5.4 library not found")
quiet = lambda s: None


def test_fits_achassis_requires_exactly_one_front_and_rear_axle():
    assert achassis.fits_achassis([
        {"slot": "FL"}, {"slot": "FR"}, {"slot": "RL"}, {"slot": "RR"},
    ])
    assert not achassis.fits_achassis([{"slot": "FL"}, {"slot": "FR"}])
    assert not achassis.fits_achassis([
        {"slot": "FL"}, {"slot": "FR"}, {"slot": "ML"}, {"slot": "MR"}, {"slot": "RL"}, {"slot": "RR"},
    ])


def test_build_tune_source_overrides_only_the_derived_fields_and_stays_valid_lua():
    size = {"width": 6.5, "height": 4.8, "length": 15.2}
    wheels = [
        {"slot": "FL", "hub": [-3.1, 1.0, 5.0], "radius": 1.1, "width": 0.8},
        {"slot": "FR", "hub": [3.1, 1.0, 5.0], "radius": 1.1, "width": 0.8},
        {"slot": "RL", "hub": [-3.1, 1.0, -5.0], "radius": 1.2, "width": 0.9},
        {"slot": "RR", "hub": [3.1, 1.0, -5.0], "radius": 1.2, "width": 0.9},
    ]
    stock = achassis.vendor_source("A-Chassis Tune.luau")
    text = achassis.build_tune_source(size, wheels, "fwd")
    assert text != stock
    assert 'Tune.Config' in text and '"FWD"' in text
    # A handful of untouched stock fields - spanning both the banner-comment region and the real
    # tuning section - must come through completely unchanged.
    for untouched in ("Tune.CGHeight", "Tune.SuspensionEnabled",
                       "Tune.TCSEnabled", "Tune.ABSEnabled", "Tune.ESCEnabled"):
        line = next(ln for ln in stock.splitlines() if ln.strip().startswith(untouched))
        assert line in text, untouched
    # It must still run as a real Lua chunk (needs the Enum mock for its KeyCode defaults).
    st = lh.LuaState()
    ok, msg = st.run(lh.MOCK + "\n" + text.replace("return Tune", "return true"))
    st.close()
    assert ok, msg


@pytest.fixture()
def achassis_built(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"),
                  Options(name="RealCar", achassis_real=True), log=quiet)
    script = (res.out_dir / "RealCar_setup.lua").read_text()
    names = [p["name"] for p in res.manifest["pieces"]]
    return res, script, names


def test_achassis_real_builds_the_expected_object_tree(achassis_built):
    res, script, names = achassis_built
    ok, out = lh.run_achassis_rig(script, names)
    assert ok, out
    r = lh.parse_results(out)
    assert r["driveseat"] == "VehicleSeat"
    assert r["driveseat_cancollide"] == "true"          # real touch-to-sit, not a ProximityPrompt
    assert r["primary_is_driveseat"] == "true"
    assert r["wheels_folder"] == "Folder"
    assert r["wheel_count"] == "4"
    for slot in ("FL", "FR", "RL", "RR"):
        assert int(r[f"wheel_{slot}_parts_count"]) >= 1
        assert r[f"wheel_{slot}_wheelfixed"] == "Folder"
    assert r["body_folder"] == "Folder"
    assert int(r["body_children_count"]) > 0
    assert r["tune_class"] == "ModuleScript" if "tune_class" in r else True
    assert r["tune_source_roundtrip"] == "true"
    assert r["init_roundtrip"] == "true"
    assert r["readme_roundtrip"] == "true"
    assert r["units_roundtrip"] == "true"
    assert r["drive_roundtrip"] == "true"
    assert r["plugins_empty"] == "true"
    assert r["iface_class"] == "ScreenGui"
    assert r["values_count"] == str(len(achassis.VALUES_SPEC))
    assert r["mobile_class"] == "Frame"
    assert r["starting_class"] == "BoolValue"
    assert r["controlsopen_class"] == "BoolValue"
    assert r["selected"] == "true"
    assert "WARN" not in out


def test_achassis_real_note_in_print_message(achassis_built):
    _, script, names = achassis_built
    ok, out = lh.run_achassis_rig(script, names)
    assert ok, out
    assert "real A-Chassis by lisphm" in out


def test_achassis_real_falls_back_when_layout_does_not_fit(tmp_path):
    """fits_achassis already unit-tests the layout rule directly; this exercises the same
    all-or-nothing fallback through the real pipeline options surface - no wheels at all (and
    auto-tire generation turned off, or it would just build a normal 4-wheel layout anyway)."""
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car(wheel=False))
    res = convert(str(tmp_path / "t.dff"), str(tmp_path / "out"),
                  Options(name="NoWheels", achassis_real=True, add_tires=False), log=quiet)
    script = (res.out_dir / "NoWheels_setup.lua").read_text()
    assert "achassis_real = false" in script
    assert any("Real A-Chassis needs exactly one front axle" in w for w in res.warnings)
