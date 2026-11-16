"""Writes the shared test files (same generators as the Python tests) and the Python reference output."""
import sys, json, shutil
from pathlib import Path
root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / "tests"))
import fixtures as fx
from car2roblox.pipeline import Options, convert

out = Path(sys.argv[1]); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
(out / "sa").mkdir(); (out / "sa" / "testcar.dff").write_bytes(fx.sa_test_car()); (out / "sa" / "testcar.txd").write_bytes(fx.sa_test_txd())
(out / "sa_bin").mkdir(); (out / "sa_bin" / "testcar.dff").write_bytes(fx.sa_test_car(binmesh_only=True)); (out / "sa_bin" / "testcar.txd").write_bytes(fx.sa_test_txd())
(out / "beam").mkdir(); fx.beamng_test_zip(out / "beam" / "mod.zip")
for ax in ("x", "z"):
    (out / f"obj_{ax}").mkdir(); fx.write_obj_car(out / f"obj_{ax}" / "car.obj", ax)
# the wheel situations: a car that has none, unnamed round parts, wheels fused into one mesh, doubled meshes
for mode in ("named", "unnamed", "fused", "dup", "none", "template", "stacked", "partial", "spare"):
    d = out / f"wobj_{mode}"; d.mkdir(); fx.write_obj_wheel_car(d / "car.obj", mode)
for k, kw in (("bflex", {}), ("bflex_nomesh", dict(wheel_mesh=False)), ("bflex_empty", dict(empty_defaults=True)),
              ("bflex_dup", dict(duplicate_row=True)), ("bflex_var", dict(variable_pos=True)),
              ("bflex_anon", dict(wheel_mesh=False, anon_hubs=True))):
    d = out / k; d.mkdir(); fx.beamng_flexbody_zip(d / "mod.zip", **kw)
(out / "bflex_frontonly").mkdir(); fx.beamng_front_hints_only_zip(out / "bflex_frontonly" / "mod.zip")
(out / "bflex_caliper").mkdir(); fx.beamng_wheel_group_hardware_zip(out / "bflex_caliper" / "mod.zip")
(out / "bflex_decal").mkdir(); fx.beamng_layered_material_zip(out / "bflex_decal" / "mod.zip")
(out / "bflex_slotoffset").mkdir(); fx.beamng_shared_wheel_slot_offset_zip(out / "bflex_slotoffset" / "mod.zip")
(out / "sa_nowheel").mkdir(); (out / "sa_nowheel" / "testcar.dff").write_bytes(fx.sa_test_car(wheel=False))
ref = out / "ref"
q = lambda s: None
cases = {
  "sa": (out / "sa" / "testcar.dff", Options()),
  "sa_bin": (out / "sa_bin" / "testcar.dff", Options()),
  "beam": (out / "beam" / "mod.zip", Options()),
  "beam_sport": (out / "beam" / "mod.zip", Options(config="sport", paint="#c0392b")),
  "obj_x": (out / "obj_x" / "car.obj", Options()),
  "obj_z": (out / "obj_z" / "car.obj", Options(flip_forward=True)),
}
for mode in ("named", "unnamed", "fused", "dup", "none", "template", "stacked", "partial", "spare"):
    cases[f"wobj_{mode}"] = (out / f"wobj_{mode}" / "car.obj", Options())
cases["wobj_template_notires"] = (out / "wobj_template" / "car.obj", Options(add_tires=False))
cases["wobj_named_new"] = (out / "wobj_named" / "car.obj", Options(new_tires=True))
cases["wobj_partial_new"] = (out / "wobj_partial" / "car.obj", Options(new_tires=True))
for k in ("bflex", "bflex_nomesh", "bflex_empty", "bflex_dup", "bflex_var", "bflex_anon"):
    cases[k] = (out / k / "mod.zip", Options())
cases["bflex_frontonly"] = (out / "bflex_frontonly" / "mod.zip", Options())
cases["bflex_caliper"] = (out / "bflex_caliper" / "mod.zip", Options())
cases["bflex_decal"] = (out / "bflex_decal" / "mod.zip", Options())
cases["bflex_slotoffset"] = (out / "bflex_slotoffset" / "mod.zip", Options())
cases["sa_nowheel"] = (out / "sa_nowheel" / "testcar.dff", Options())
cases["sa_speed"] = (out / "sa" / "testcar.dff", Options(top_speed=120))
cases["sa_achassis"] = (out / "sa" / "testcar.dff", Options(advanced_chassis=True))
cases["wobj_none_notires"] = (out / "wobj_none" / "car.obj", Options(add_tires=False))
for k, (p, o) in cases.items():
    o.name = k
    convert(str(p), str(ref), o, q)
# a GLB written by the Python exporter, read back as a generic model
(out / "glb").mkdir(); shutil.copy(ref / "obj_x" / "obj_x.glb", out / "glb" / "car.glb")
r = convert(str(out / "glb" / "car.glb"), str(ref), Options(name="glb"), q)
print("fixtures ok:", sorted(p.name for p in ref.iterdir()))
