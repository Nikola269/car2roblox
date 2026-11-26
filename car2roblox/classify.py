"""Name based part classification shared by all importers."""
from __future__ import annotations

import re
from typing import Iterable, Optional

_WHEEL = {"wheel", "wheels", "tire", "tires", "tyre", "tyres", "rim", "rims", "hubcap",
          "hubcaps"}
# names that mention a wheel but are not the wheel itself (arches, liners, ...), or hardware that
# sits right at the hub but isn't the tire/rim (brakes, suspension...): they stay body parts. Real
# mods commonly name these "wheel_hub_f", "wheel_brake_disc", "front_wheel_knuckle" and so on, so a
# plain "contains 'wheel'" check sweeps them in as if they were the tire - that is what put a
# body-painted brake caliper or hub carrier right on top of the tire in the Studio/browser preview.
_NOT_WHEEL = {"arch", "arches", "well", "wells", "house", "housing", "fender", "fenders",
              "guard", "guards", "liner", "liners", "lining", "flare", "flares", "mudguard",
              "mudflap", "flap", "flaps", "splash", "skirt", "skirts", "cutout", "opening",
              "brake", "brakes", "caliper", "calipers", "disc", "discs", "rotor", "rotors",
              "pad", "pads", "drum", "drums", "hub", "hubs", "knuckle", "knuckles", "spindle",
              "spindles", "upright", "uprights", "carrier", "carriers", "bearing", "bearings",
              "strut", "struts", "shock", "shocks", "damper", "dampers", "arm", "arms",
              "axle", "axles", "driveshaft", "driveshafts"}
NOT_WHEEL = _NOT_WHEEL         # shared with the format-specific importers
_GLASS = {"glass", "window", "windows", "windscreen", "windshield", "windscreens"}
_INTERIOR = {"interior", "seat", "seats", "dash", "dashboard", "steering", "cabin",
             "cockpit", "ped", "headliner"}
_LIGHT = {"light", "lights", "lamp", "lamps", "headlight", "headlights", "taillight",
          "taillights", "indicator", "indicators", "foglight"}
# a part named like one of these is never *meant* to be see-through, whatever its material's alpha
# says - every importer's "transparent" flag is a heuristic read off a colour/alpha channel that
# real exporters routinely leave a little under fully opaque for AO/sheen/dirt/decal baking, not to
# signal real glass (see the "0.85" comments in imp_gtasa.py / imp_beamng.py / imp_generic.py). That
# false "transparent" reading is what swept an unrelated body panel, the chassis or a cage mesh into
# "glass" below and made it render as a translucent ghost double over the solid body underneath.
_NOT_GLASS = {"bumper", "bumpers", "bonnet", "bonnets", "hood", "hoods", "roof", "roofs", "door",
              "doors", "fender", "fenders", "wing", "wings", "mirror", "mirrors", "trim", "trims",
              "grille", "grill", "grilles", "grills", "spoiler", "spoilers", "exhaust", "exhausts",
              "chassis", "cage", "cages", "frame", "frames", "subframe", "crossmember", "shell",
              "panel", "panels", "skirt", "skirts", "bootlid", "boot", "boots", "trunk", "tailgate",
              "hatch", "hatches", "pillar", "pillars", "sill", "sills", "bar", "bars", "guard",
              "guards", "plate", "plates", "badge", "badges", "handle", "handles", "wiper", "wipers",
              "antenna", "antennas", "aerial", "vent", "vents", "scoop", "scoops", "splitter",
              "splitters", "diffuser", "diffusers", "undertray", "underbody", "engine", "engines",
              "radiator", "radiators", "intercooler", "turbo", "turbos", "bay", "bays", "floor",
              "floors", "rail", "rails", "mount", "mounts", "bracket", "brackets", "decal", "decals",
              "sticker", "stickers", "livery"}
_SLOT_TOKENS = {"fl": "FL", "fr": "FR", "rl": "RL", "rr": "RR", "lf": "FL", "rf": "FR",
                "lb": "RL", "lr": "RL", "rb": "RR", "bl": "RL", "br": "RR", "lm": "ML", "rm": "MR"}

KINDS = ("body", "wheel", "glass", "interior", "light", "other")


def tokens(name: str) -> list[str]:
    # split camelCase and separators: "pickup_wheelFR" -> pickup wheel fr
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^a-zA-Z]+", s.lower()) if t]


def is_lod_name(name: str) -> bool:
    n = name.lower()
    return bool(re.search(r"(_vlo|_lod\d*|_lo|lod\d+)$", n)) or n.startswith("lod")


def classify_part(name: str, material_names: Iterable[str] = (), transparent: bool = False,
                  hint: Optional[str] = None) -> str:
    if hint in KINDS:
        return hint  # type: ignore[return-value]
    tk = set(tokens(name))
    if tk & _INTERIOR:
        return "interior"
    if tk & _WHEEL:
        return "body" if tk & _NOT_WHEEL else "wheel"
    if tk & _GLASS:
        return "glass"
    mt = set()
    for m in material_names:
        mt |= set(tokens(m))
    if transparent and (mt & _GLASS or not (tk & _LIGHT or tk & _NOT_GLASS)):
        return "glass"
    if mt & _GLASS:
        return "glass"
    if tk & _LIGHT:
        return "light"
    return "body"


def slot_from_name(name: str) -> Optional[str]:
    for t in tokens(name):
        if t in _SLOT_TOKENS:
            return _SLOT_TOKENS[t]
    return None
