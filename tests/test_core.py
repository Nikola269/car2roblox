import numpy as np
import pytest

from car2roblox import jbeam
from car2roblox.geometry import auto_fix_winding, decimate_to, split_by_limit
from car2roblox.model import AxisSpec, ConversionError, Mesh
from car2roblox.textures import parse_hex_color
import fixtures as fx


def signed_volume(m: Mesh) -> float:
    p = m.positions.astype(np.float64)
    p = p - p.mean(axis=0)
    t = m.triangles
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6)


def test_fixture_box_is_outward():
    p, n, u, t = fx.box((1, 2, 3), (2, 3, 4))
    m = Mesh(p, t, np.zeros(len(t), int), n, u)
    assert signed_volume(m) > 0
    assert m.tri_count == 12
    # face normals agree with the stored vertex normals
    fn = m.face_normals()
    vn = m.normals[m.triangles].sum(axis=1)
    assert (np.einsum("ij,ij->i", fn, vn) > 0).all()


def test_fixture_cylinder_is_outward():
    p, n, u, t = fx.cylinder_x((0, 0, 0), 0.4, 0.3)
    m = Mesh(p, t, np.zeros(len(t), int), n, u)
    assert signed_volume(m) > 0


def test_build_dedupes_corners():
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    ptri = np.array([[0, 1, 2], [0, 2, 3]])
    m = Mesh.build(pos, ptri, 0, uv, ptri.copy())
    assert m.vert_count == 4 and m.tri_count == 2 and m.uvs is not None
    # different uv index on a shared position -> the vertex is duplicated
    utri = np.array([[0, 1, 2], [3, 2, 1]])
    m2 = Mesh.build(pos, ptri, 0, uv, utri)
    assert m2.vert_count > 4


def test_mirror_flips_winding_and_keeps_volume_positive():
    p, n, u, t = fx.box((0, 0, 0), (1, 2, 3))
    m = Mesh(p, t, np.zeros(len(t), int), n, u)
    mir = m.transformed(np.diag([-1, 1, 1]))
    assert signed_volume(mir) > 0           # still outward after the winding flip
    assert mir.positions[:, 0].max() <= m.positions[:, 0].max() + 1e-6


def test_axis_specs():
    R = AxisSpec("+x", "+z", "+y").to_roblox()          # GTA SA
    assert np.allclose(R @ [0, 1, 0], [0, 0, -1])       # forward -> Roblox -Z
    assert np.allclose(R @ [0, 0, 1], [0, 1, 0])        # up -> +Y
    assert np.linalg.det(R) > 0
    R = AxisSpec("-x", "+z", "-y").to_roblox()          # BeamNG
    assert np.allclose(R @ [0, -1, 0], [0, 0, -1])
    assert np.allclose(R @ [-1, 0, 0], [1, 0, 0])       # BeamNG right (-X) -> Roblox +X
    flipped = AxisSpec("+x", "+z", "+y").with_flipped_forward()
    assert flipped == AxisSpec("-x", "+z", "-y")
    with pytest.raises(ConversionError):
        AxisSpec("+x", "+x", "+y").validate()


def test_split_by_limit_is_lossless():
    p, n, u, t = fx.cylinder_x((0, 0, 0), 1, 4, segments=200)
    m = Mesh(p, t, np.zeros(len(t), int), n, u)
    parts = split_by_limit(m, 150)
    assert sum(x.tri_count for x in parts) == m.tri_count
    assert all(x.tri_count <= 150 for x in parts)
    assert len(parts) > 1


def test_decimate_reduces_and_stays_valid():
    # a subdivided plane-ish blob
    n = 40
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    pos = np.stack([xs.ravel(), ys.ravel(), 0.1 * np.sin(xs.ravel() * 6)], axis=1)
    tris = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            tris += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    m = Mesh(pos, np.array(tris), np.zeros(len(tris), int), None, pos[:, :2])
    d = decimate_to(m, 400)
    assert 0 < d.tri_count <= 400
    assert d.triangles.max() < d.vert_count


def test_auto_fix_winding():
    p, n, u, t = fx.box((0, 0, 0), (1, 1, 1))
    good = Mesh(p, t, np.zeros(len(t), int), n, u)
    same, flipped = auto_fix_winding(good)
    assert not flipped
    bad = Mesh(p, t[:, ::-1].copy(), np.zeros(len(t), int), n, u)
    fixed, flipped = auto_fix_winding(bad)
    assert flipped and signed_volume(fixed) > 0


def test_recompute_normals_point_outward():
    p, n, u, t = fx.box((0, 0, 0), (2, 2, 2))
    m = Mesh(p, t, np.zeros(len(t), int), None, u).recompute_normals()
    out = np.einsum("ij,ij->i", m.normals, m.positions)
    assert (out > 0).all()


def test_jbeam_leniency():
    text = """{ // comment
      "a": {"list": [1, 2.5, -3e2 "x"  true,], /* block */ "n": null,},
      "b": [["k", "v"] ["k2", "v2"],]
    }"""
    d = jbeam.loads(text)
    assert d["a"]["list"] == [1, 2.5, -300.0, "x", True]
    assert d["a"]["n"] is None
    assert d["b"] == [["k", "v"], ["k2", "v2"]]
    with pytest.raises(jbeam.JBeamError):
        jbeam.loads('{"a": [1, 2')


def test_parse_hex_color():
    assert parse_hex_color("#ff0000") == (1.0, 0.0, 0.0)
    assert parse_hex_color("0f0") == (0.0, 1.0, 0.0)
    assert parse_hex_color("255,0,0") == (1.0, 0.0, 0.0)
    with pytest.raises(ConversionError):
        parse_hex_color("nope")
