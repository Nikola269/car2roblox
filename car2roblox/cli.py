"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import ROBLOX_MAX_TRIANGLES, __version__
from .model import ConversionError
from .pipeline import Options, convert, inspect_model


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", help="GTA SA .dff | BeamNG .zip or vehicle folder | .obj / .glb / .gltf / .dae")
    p.add_argument("--source", choices=["auto", "gta_sa", "beamng", "generic"], default="auto",
                   help="force the input type (default: detect from the file)")
    p.add_argument("--txd", action="append", default=[], metavar="FILE",
                   help="GTA SA: extra texture dictionary (repeatable). The .txd next to the .dff "
                        "and vehicle.txd are used automatically")
    p.add_argument("--vehicle", help="BeamNG: vehicle folder name if the archive holds several")
    p.add_argument("--config", help="BeamNG: part configuration (.pc file name without extension)")
    p.add_argument("--include-lod", action="store_true", help="also export low-detail (LOD) meshes")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="car2roblox",
        description="Convert GTA San Andreas, GTA V (exported) and BeamNG car models for Roblox Studio.")
    ap.add_argument("--version", action="version", version=f"car2roblox {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("convert", help="convert a car")
    _add_common(c)
    c.add_argument("-o", "--out", default="roblox_out", help="output folder (default: roblox_out)")
    c.add_argument("--name", help="car name (default: file name)")
    c.add_argument("--scale", type=float, default=1.0,
                   help="size multiplier; 1.0 = real size (1 m = 3.57 studs)")
    c.add_argument("--length", type=float, metavar="STUDS", help="force the car length in studs")
    c.add_argument("--unit", choices=["m", "cm", "mm", "in", "ft"],
                   help="units of OBJ/GLB/DAE input (default: guessed)")
    c.add_argument("--axes", metavar="RIGHT,UP,FORWARD",
                   help="source axes for generic input, e.g. 'x,z,y' (X right, Z up, Y forward); "
                        "signs allowed ('-y')")
    c.add_argument("--flip-forward", action="store_true", help="turn the car around 180 degrees")
    c.add_argument("--paint", metavar="#RRGGBB", help="primary paint colour")
    c.add_argument("--paint2", metavar="#RRGGBB", help="secondary paint colour")
    c.add_argument("--paint3", metavar="#RRGGBB", help="tertiary paint colour")
    c.add_argument("--no-glass", action="store_true", help="leave out windows")
    c.add_argument("--no-interior", action="store_true", help="leave out the interior")
    c.add_argument("--no-lights", action="store_true", help="leave out light meshes")
    c.add_argument("--max-tris", type=int, default=ROBLOX_MAX_TRIANGLES,
                   help=f"triangles per MeshPart; bigger meshes are split (default {ROBLOX_MAX_TRIANGLES})")
    c.add_argument("--target-tris", type=int, metavar="N",
                   help="reduce the whole car to about N triangles (crude decimation)")
    c.add_argument("--max-texture", type=int, default=1024, help="longest texture side (default 1024)")
    c.add_argument("--format", default="glb,obj", help="outputs: glb, obj or glb,obj (default)")
    c.add_argument("--no-rig", action="store_true",
                   help="do not write the Studio driving script (the car is not made drivable)")
    c.add_argument("--no-tires", action="store_true",
                   help="do not generate tyres when the model has none")
    c.add_argument("--new-tires", action="store_true",
                   help="replace the model's wheels with clean generated tires at the same positions")
    c.add_argument("--top-speed", type=float, metavar="KMH",
                   help="top speed of the drivable car in km/h (default about 91)")
    c.add_argument("--drivetrain", choices=["awd", "rwd", "fwd"], default="awd")

    i = sub.add_parser("inspect", help="list the parts of a model without converting")
    _add_common(i)

    sub.add_parser("gui", help="open the graphical interface")
    return ap


def _options(a: argparse.Namespace) -> Options:
    axes = None
    if getattr(a, "axes", None):
        parts = [x.strip() for x in a.axes.split(",")]
        if len(parts) != 3:
            raise ConversionError("--axes needs three values: RIGHT,UP,FORWARD (e.g. x,z,y)")
        axes = tuple(parts)
    fmts = tuple(f.strip().lower() for f in getattr(a, "format", "glb,obj").split(",") if f.strip())
    for f in fmts:
        if f not in ("glb", "obj"):
            raise ConversionError(f"unknown output format '{f}' (use glb and/or obj)")
    return Options(
        source=a.source, name=getattr(a, "name", None), scale=getattr(a, "scale", 1.0),
        length=getattr(a, "length", None), unit=getattr(a, "unit", None), axes=axes,  # type: ignore
        flip_forward=getattr(a, "flip_forward", False), paint=getattr(a, "paint", None),
        paint2=getattr(a, "paint2", None), paint3=getattr(a, "paint3", None),
        include_lod=a.include_lod, include_glass=not getattr(a, "no_glass", False),
        include_interior=not getattr(a, "no_interior", False),
        include_lights=not getattr(a, "no_lights", False),
        max_tris_per_mesh=getattr(a, "max_tris", ROBLOX_MAX_TRIANGLES),
        target_tris=getattr(a, "target_tris", None), max_texture=getattr(a, "max_texture", 1024),
        formats=fmts or ("glb", "obj"), rig=not getattr(a, "no_rig", False),
        add_tires=not getattr(a, "no_tires", False),
        new_tires=getattr(a, "new_tires", False), top_speed=getattr(a, "top_speed", None),
        drivetrain=getattr(a, "drivetrain", "awd"), txd=tuple(a.txd), vehicle=a.vehicle,
        config=a.config)


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .gui import run_gui
        return run_gui()
    try:
        opts = _options(args)
        if args.cmd == "inspect":
            inspect_model(args.input, opts)
            return 0
        res = convert(args.input, args.out, opts)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    print(f"\nOutput folder: {res.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
