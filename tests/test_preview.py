"""The software-rendered preview: it must draw something sensible, never crash on odd input,
and write preview.png next to the converted car."""
import numpy as np
import pytest

import fixtures as fx
from car2roblox import preview as pv
from car2roblox.pipeline import Options, convert, preview_model

quiet = lambda s: None


@pytest.fixture()
def sa_path(tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    return str(tmp_path / "t.dff")


def test_empty_scene_draws_the_backdrop():
    img = pv.render(None, 200, 120)
    assert img.size == (200, 120)
    a = np.asarray(img)
    assert a.std() > 5                       # sky and ground, not a flat fill


def test_car_changes_the_picture_and_views_differ(sa_path):
    scene, info = preview_model(sa_path, Options(), log=quiet)
    assert info["wheels"] == 4 and scene.size[2] > 8
    base = np.asarray(pv.render(None, 240, 150)).astype(int)
    a = np.asarray(pv.render(scene, 240, 150, yaw=0.5)).astype(int)
    b = np.asarray(pv.render(scene, 240, 150, yaw=2.4)).astype(int)
    assert np.abs(a - base).mean() > 1.0
    assert np.abs(a - b).mean() > 1.0


def test_day_and_dusk_look_different(sa_path):
    scene, _ = preview_model(sa_path, Options(), log=quiet)
    d = np.asarray(pv.render(scene, 200, 120, theme="day")).astype(int)
    k = np.asarray(pv.render(scene, 200, 120, theme="dusk")).astype(int)
    assert np.abs(d - k).mean() > 5


def test_drive_pose_moves_the_car(sa_path):
    from car2roblox import drive
    scene, _ = preview_model(sa_path, Options(), log=quiet)
    st = drive.reset()
    for _ in range(40):
        st = drive.step(st, drive.Inputs(throttle=1.0, steer=0.5), 0.05)
    assert st.kmh > 10
    still = np.asarray(pv.render(scene, 220, 140, camera=drive.chase_camera(drive.reset(), scene.size),
                                 pose=drive.reset())).astype(int)
    moved = np.asarray(pv.render(scene, 220, 140, camera=drive.chase_camera(st, scene.size),
                                 pose=st)).astype(int)
    assert np.abs(still - moved).mean() > 1.0


def test_png_bytes_and_artwork():
    for img in (pv.banner(600, 108), pv.window_backdrop(500, 300)):
        data = pv.png_bytes(img)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert pv.banner(600, 108).size == (600, 108)


def test_convert_writes_preview_png(sa_path, tmp_path):
    res = convert(sa_path, str(tmp_path / "o"), Options(name="P"), log=quiet)
    assert res.preview and res.preview.exists() and res.preview.name == "preview.png"
    assert res.preview.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert res.preview in res.files


def test_car_without_wheels_still_previews(tmp_path):
    p = tmp_path / "box.obj"
    p.write_text("o body\nv -1 0 -3\nv 1 0 -3\nv 1 1 -3\nv -1 1 -3\nv -1 0 3\nv 1 0 3\nv 1 1 3\nv -1 1 3\n"
                 "f 1 2 3 4\nf 8 7 6 5\nf 1 5 6 2\nf 2 6 7 3\nf 3 7 8 4\nf 4 8 5 1\n")
    scene, info = preview_model(str(p), Options(), log=quiet)
    assert info["wheels"] == 4                        # tyres were generated
    assert pv.render(scene, 160, 100).size == (160, 100)
