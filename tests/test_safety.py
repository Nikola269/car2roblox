import json
import zipfile
from pathlib import Path

import pytest

from car2roblox.imp_beamng import import_beamng
from car2roblox.model import ConversionError
from car2roblox.pipeline import Options, convert, load_model
from car2roblox.safety import is_adult_name, risky_file, scan_files, strip_links
import fixtures as fx

quiet = lambda s: None


# ------------------------------------------------------------------ links
@pytest.mark.parametrize("text,expected", [
    ("wheel www.evil.com", "wheel"),
    ("http://bit.ly/abc door", "door"),
    ("body_https://example.org/x?y=1", "body"),
    ("bumper.com", ""),
    ("javascript:alert(1) hood", "hood"),
    ("downloaded from gta-mods.ru/cars", "downloaded from"),
    ("door.top", "door.top"),          # dotted part names are not links
    ("hood.001", "hood.001"),
    ("wheel_lf_dummy", "wheel_lf_dummy"),
])
def test_strip_links(text, expected):
    assert strip_links(text)[0] == expected


# ------------------------------------------------------------------ adult markers
@pytest.mark.parametrize("name", ["nude_decal", "NSFW-sticker", "r18_skin", "skin 18+", "xxx", "Hentai poster",
                                  "sexy_girl", "adult_only", "x-rated", "onlyfans_logo"])
def test_adult_names_are_flagged(name):
    assert is_adult_name(name)


@pytest.mark.parametrize("name", ["Essex", "cockpit", "Scunthorpe", "bodyDecal", "passenger_seat", "wheel_lf",
                                  "sextant", "grass_mat", "windshield", "adulthood", "r180", "door_18"])
def test_normal_names_are_not_flagged(name):
    assert not is_adult_name(name)


# ------------------------------------------------------------------ risky files
@pytest.mark.parametrize("path,flag", [
    ("mods/readme.url", True), ("setup.EXE", True), ("a/b/install.bat", True), ("skin.png.exe", True),
    ("icon.exe.png", True), ("ui/app.js", True), ("page.html", True), ("link.lnk", True),
    ("vehicles/car/car.dae", False), ("textures/body_d.dds", False), ("wheel.png", False),
    ("readme.txt", False), ("car.materials.json", False),
])
def test_risky_file(path, flag):
    assert bool(risky_file(path)) == flag


def test_scan_files_sorted():
    out = scan_files(["b.exe", "a.url", "ok.png"])
    assert [p for p, _ in out] == ["a.url", "b.exe"]


# ------------------------------------------------------------------ whole pipeline
def _obj_with_names(tmp_path: Path, body_name: str, roof_name: str, roof_mat: str = "roofmat") -> Path:
    p = tmp_path / "car.obj"
    fx.write_obj_car(p, "z")
    text = p.read_text().replace("o body", f"o {body_name}").replace("o roof", f"o {roof_name}")
    p.write_text(text)
    (tmp_path / "car.mtl").write_text(f"newmtl paint\nKd 0.8 0.1 0.1\nnewmtl {roof_mat}\nKd 0.2 0.2 0.2\n")
    p.write_text(p.read_text().replace("usemtl roofmat", f"usemtl {roof_mat}"))
    return p


def test_adult_part_is_deleted_and_link_removed(tmp_path):
    src = _obj_with_names(tmp_path, "body_www.evil.com", "nude_sticker")
    out = tmp_path / "out"
    res = convert(str(src), str(out), Options(rig=True), quiet)
    names = [p["name"] for p in res.manifest["pieces"]]
    assert not any("nude" in n.lower() for n in names)
    assert not any("evil" in n.lower() or "www" in n.lower() for n in names)
    assert any(n.startswith("body") for n in names)
    joined = "\n".join(res.warnings)
    assert "deleted a part (adult-content name)" in joined and "nude_sticker" not in joined and "removed a link" in joined
    # the removed link must not leak into any written text file; deleted parts leave no geometry
    for f in res.files:
        if f.suffix in (".json", ".lua", ".txt", ".obj", ".mtl"):
            t = f.read_text(errors="replace").lower()
            assert "evil.com" not in t, f.name
            if f.suffix in (".obj", ".mtl", ".lua"):
                assert "nude" not in t, f.name


def test_adult_material_deletes_its_triangles(tmp_path):
    src = _obj_with_names(tmp_path, "body", "roof", roof_mat="nsfw_skin")
    model = load_model(str(src), Options(), quiet)
    assert [p.name for p in model.parts] == ["body"]
    assert any("deleted part 'roof'" in n or "adult-content material" in n for n in model.notes)


def test_everything_adult_is_an_error(tmp_path):
    src = _obj_with_names(tmp_path, "nude_a", "porn_b")
    with pytest.raises(ConversionError, match="safety filter"):
        load_model(str(src), Options(), quiet)


def test_risky_files_in_beamng_zip_are_ignored(tmp_path):
    z = tmp_path / "mod.zip"
    fx.beamng_test_zip(z)
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("readme.url", "[InternetShortcut]\nURL=http://evil.example/\n")
        zf.writestr("vehicles/testcar/setup.exe", b"MZ not a real program")
        zf.writestr("vehicles/testcar/skin.png.bat", b"@echo off")
    model = import_beamng(str(z), log=quiet)
    note = next(n for n in model.notes if n.startswith("Safety: ignored"))
    assert "3 risky file" in note and "readme.url" in note and "setup.exe" in note
    res = convert(str(z), str(tmp_path / "out"), Options(), quiet)
    assert any("ignored 3 risky" in w for w in res.warnings)
    for f in res.files:
        assert f.suffix not in (".exe", ".bat", ".url")


def test_gltf_never_follows_remote_uris(tmp_path):
    from car2roblox.imp_generic import _find_file
    assert _find_file(tmp_path, "http://example.com/tex.png") is None
    assert _find_file(tmp_path, "HTTPS://example.com/tex.png") is None
