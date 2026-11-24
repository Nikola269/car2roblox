"""The real lisphm/A-Chassis (MPL-2.0) vehicle kit, vendored and wired onto a converted car.

car2roblox's own rig (see rigscript.py) is a small from-scratch drivetrain. This module instead
builds the exact object tree the real, popular community chassis at
https://github.com/lisphm/A-Chassis expects - DriveSeat, a Wheels folder with FL/FR/RL/RR parts,
a Body folder, and an "A-Chassis Tune" module tree - and embeds its real scripts verbatim
(car2roblox/vendor/achassis/, MPL-2.0, see NOTICE.md there for provenance). The structure below was
read directly out of that kit's Initialize.luau and Drive.luau, not guessed from the README:

* ``car.DriveSeat`` - a VehicleSeat players sit in normally (walk/click, not a ProximityPrompt -
  A-Chassis reacts to Roblox's own "SeatWeld" child, which only appears from a real seat-touch).
* ``car.Wheels`` - a Folder whose children are BasePart named exactly FL/FR/RL/RR. Initialize.luau
  unconditionally does ``for _, a in v.Parts:GetDescendants()`` and
  ``for _, a in v.WheelFixed:GetDescendants()`` on every wheel with no FindFirstChild guard, so
  both must exist (we ship them empty except ``Parts``, which holds the visible wheel/tyre mesh -
  ``ModelWeld(v.Parts, v)`` welds whatever is inside it to the wheel).
* ``car.Body`` - a Folder; ``ModelWeld(car.Body, car.DriveSeat)`` welds everything inside it to the
  seat (this is also where Initialize parents the auto-sized ballast brick).
* ``car["A-Chassis Tune"]`` - the Tune ModuleScript, with direct children ``Initialize`` (Script),
  ``README`` (ModuleScript, itself holding a ``Units`` ModuleScript child), ``Plugins`` (an empty
  Folder - Initialize iterates and then destroys it, so empty is fine) and ``"A-Chassis Interface"``
  (a ScreenGui cloned into the driver's PlayerGui on sit, holding ``Car`` (ObjectValue), ``Values``
  (Folder, see VALUES_SPEC), ``Mobile`` (a Frame - Drive.luau sets ``.Visible`` on it on every input
  type change, Tune.Mobile or not, so it must be a real GuiObject), ``IsOn``/``ControlsOpen``
  (BoolValue) and ``Drive`` (the LocalScript)).

``MiscWeld.luau`` and ``CustomEngine.luau`` are vendored for completeness but never instantiated:
both are optional, user-opt-in modules upstream only runs if required, and Initialize.luau
auto-requires every ModuleScript parented directly under itself - so a parented-but-unused
MiscWeld would have its ``car:WaitForChild("Misc")`` block the whole car forever, since nothing
here creates a "Misc" folder.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .export import to_lua

VENDOR_DIR = Path(__file__).parent / "vendor" / "achassis"

# The only wheel layout the real A-Chassis (car mode) understands: exactly one front and one rear
# axle, each with a left and a right wheel.
REQUIRED_SLOTS = {"FL", "FR", "RL", "RR"}

# (name, Roblox ClassName) for every instance Drive.luau reads out of the Values folder, in the
# order Initialize/Drive first touch them. Confirmed by grepping every ``values.X`` and
# ``Value.X.Value = <literal>`` in the vendored Drive.luau - the literal on the right of each
# assignment is what fixes the ClassName (a CFrame -> CFrameValue, a Vector3 -> Vector3Value, a
# bare true/false -> BoolValue, and so on).
VALUES_SPEC: List[Tuple[str, str]] = [
    ("Clutch", "NumberValue"), ("Gear", "IntValue"), ("TransmissionMode", "StringValue"),
    ("CurrentRatio", "NumberValue"), ("RPM", "NumberValue"),
    ("Boost", "NumberValue"), ("BoostTurbo", "NumberValue"), ("BoostSuper", "NumberValue"),
    ("HpNatural", "NumberValue"), ("HpElectric", "NumberValue"), ("HpTurbo", "NumberValue"),
    ("HpSuper", "NumberValue"), ("HpBoosted", "NumberValue"), ("Horsepower", "NumberValue"),
    ("TqNatural", "NumberValue"), ("TqElectric", "NumberValue"), ("TqTurbo", "NumberValue"),
    ("TqSuper", "NumberValue"), ("TqBoosted", "NumberValue"), ("Torque", "NumberValue"),
    ("Throttle", "NumberValue"), ("InputThrottle", "NumberValue"),
    ("Brake", "NumberValue"), ("InputBrake", "NumberValue"),
    ("SteerC", "NumberValue"), ("SteerT", "NumberValue"),
    ("PBrake", "BoolValue"),
    ("Velocity", "Vector3Value"), ("AverageRotSpeed", "NumberValue"), ("Acceleration", "Vector3Value"),
    ("Center", "CFrameValue"),
    ("TCS", "BoolValue"), ("TCSActive", "BoolValue"), ("TCSAmt", "NumberValue"),
    ("ABS", "BoolValue"), ("ABSActive", "BoolValue"), ("ABSAmt", "NumberValue"),
    ("ESC", "BoolValue"), ("ESCActive", "BoolValue"), ("ESCAmt", "NumberValue"),
    ("CS", "BoolValue"), ("CSActive", "BoolValue"), ("CSAmt", "NumberValue"),
    ("AutoClutch", "BoolValue"), ("MouseSteerOn", "BoolValue"),
    # Populated at runtime with one NumberValue per wheel by Drive.luau itself - stay empty here.
    ("TCSTracker", "Folder"), ("ABSTracker", "Folder"), ("ESCTracker", "Folder"),
    ("WheelSpeeds", "Folder"),
]


def fits_achassis(wheels: Sequence[dict]) -> bool:
    """True if a converted car's wheel layout is exactly one front + one rear axle (L/R each) -
    the only shape the real A-Chassis (car mode, not its separate bike rig) can drive."""
    slots = {w["slot"] for w in wheels}
    return slots == REQUIRED_SLOTS


_VENDOR_CACHE: Dict[str, str] = {}


def _read(name: str) -> str:
    if name not in _VENDOR_CACHE:
        _VENDOR_CACHE[name] = (VENDOR_DIR / name).read_text(encoding="utf-8")
    return _VENDOR_CACHE[name]


def lua_str(text: str) -> str:
    """A Lua double-quoted string literal that safely holds ``text`` verbatim, however large or
    whatever it contains - reuses export.to_lua's escaping (backslash/quote/newline) rather than
    Lua's leveled long-bracket strings, which would need scanning the content for a safe
    ``]==]``-style delimiter first; a handful of these real scripts are tens of thousands of
    characters, so a short-string literal with full escaping is the simpler, unconditionally
    correct choice."""
    return to_lua(text)


def derive_tune_overrides(size: Dict[str, float], wheels: Sequence[dict], drivetrain: str
                          ) -> Dict[str, str]:
    """A handful of Tune fields that can honestly be derived from the converted car's real
    geometry, as Lua source snippets ready to splice into the stock Tune module. Everything else
    (engine power, gearing, spring rates, ...) is left at A-Chassis's own stock defaults - there is
    no way to know a mod's real engine from its mesh, and those are exactly the numbers the Tune
    module exists for the user to hand-tune in Studio afterwards.
    """
    width, height, length = float(size["width"]), float(size["height"]), float(size["length"])
    m = 0.28  # studs -> metres

    fronts = [w for w in wheels if w["slot"] in ("FL", "FR")]
    rears = [w for w in wheels if w["slot"] in ("RL", "RR")]
    avg_radius = sum(w["radius"] for w in wheels) / max(1, len(wheels))
    avg_width = sum(w["width"] for w in wheels) / max(1, len(wheels))

    # Weight: scale a typical compact car's stock 1440 lb by this car's bounding-box volume
    # against a ~1.7 x 1.5 x 4.3 m reference box, then pad generously. Initialize.luau only adds a
    # ballast brick (and Drive.luau unconditionally indexes it - see NOTICE.md) when the converted
    # mesh's own computed mass is still under the target, so erring high is the safe direction.
    ref_volume = 1.7 * 1.5 * 4.3
    volume = max(0.3, width * m) * max(0.3, height * m) * max(0.3, length * m)
    weight_lb = 1440.0 * (volume / ref_volume) * 1.8
    weight_lb = max(900.0, min(weight_lb, 9000.0))

    # Weight-brick size, in the Tune module's own millimetre convention (1 stud = 280 mm),
    # sized as a fraction of the car's own footprint so it always fits inside the Body.
    wb_w = max(0.25 * width, 2.0) * 280.0
    wb_h = max(0.18 * height, 1.5) * 280.0
    wb_l = max(0.35 * length, 3.0) * 280.0

    # Wheel weight (kg): A-Chassis derives wheel density FROM this number and the wheel part's own
    # size, so it is really "how heavy is a wheel this size", scaled off a ~0.33 m radius /
    # 0.2 m wide reference wheel at the stock 22/23 kg.
    def wheel_kg(group: Sequence[dict], stock: float) -> float:
        if not group:
            return stock
        r = sum(w["radius"] for w in group) / len(group) * m
        w_ = sum(w["width"] for w in group) / len(group) * m
        scale = (r / 0.33) * (w_ / 0.20)
        return max(4.0, min(stock * scale, 90.0))

    # Structural axle hardware: scales with wheel size so bigger wheels get sturdier (and better-
    # fitting) axles; stock is 1.5 studs for a ~0.33 m-radius reference wheel.
    axle_size = max(0.6, min(1.5 * (avg_radius * m / 0.33), 3.0))

    overrides = {
        "Weight": f"{weight_lb:.0f}",
        "WeightBSize": "{" + f"{wb_w:.0f}, {wb_h:.0f}, {wb_l:.0f}" + "}",
        "FWheelWeight": f"{wheel_kg(fronts, 22.0):.1f}",
        "RWheelWeight": f"{wheel_kg(rears, 23.0):.1f}",
        "AxleSize": f"{axle_size:.2f}",
        # No mobile touch-control UI is generated (see module docstring on ``Mobile``), so keep
        # A-Chassis from reaching for the ModeSwitch/Jump buttons it would otherwise expect.
        "Mobile": "false",
    }
    dt = (drivetrain or "").strip().upper()
    if dt in ("FWD", "RWD", "AWD"):
        overrides["Config"] = f'"{dt}"'

    # Rough front/rear weight distribution from the actual axle spread, when both axles are present.
    if fronts and rears:
        fz = sum(w["hub"][2] for w in fronts) / len(fronts)
        rz = sum(w["hub"][2] for w in rears) / len(rears)
        wheelbase = abs(fz - rz)
        if wheelbase > 0.05:
            # the car's own longitudinal centre sits at the average of every wheel hub
            cz = sum(w["hub"][2] for w in wheels) / len(wheels)
            dist = (cz - rz) / (fz - rz) if fz != rz else 0.5
            dist = max(0.30, min(dist, 0.70)) * 100.0
            overrides["WeightDist"] = f"{dist:.0f}"
    return overrides


# Matches a single-line "Tune.Field = <anything up to the end of the line>" assignment.
_FIELD_RE = r"(?m)^(\tTune\.{field}\s*=\s*)[^\n]*(--.*)?$"
# The one multi-line field (a brace table spanning several lines, each with its own comment).
_WEIGHT_B_SIZE_RE = re.compile(r"(?s)Tune\.WeightBSize\s*=\s*\{.*?\}")


def build_tune_source(size: Dict[str, float], wheels: Sequence[dict], drivetrain: str) -> str:
    """The real "A-Chassis Tune.luau" text, byte-identical to upstream except for the handful of
    geometry-derived fields from derive_tune_overrides (kept to single-line replacements so the
    rest of the file - and every comment in it - stays exactly as shipped)."""
    text = _read("A-Chassis Tune.luau")
    overrides = derive_tune_overrides(size, wheels, drivetrain)
    note = "  -- car2roblox: derived from your model's measured size/wheels"
    if "WeightBSize" in overrides:
        text = _WEIGHT_B_SIZE_RE.sub(
            f"Tune.WeightBSize = {overrides['WeightBSize']}{note}", text, count=1)
    for field, value in overrides.items():
        if field == "WeightBSize":
            continue
        pattern = re.compile(_FIELD_RE.format(field=re.escape(field)))
        new_text, n = pattern.subn(lambda m_: m_.group(1) + value + note, text, count=1)
        if n == 0:  # pragma: no cover - defensive: every field above is a real stock Tune key
            raise ValueError(f"A-Chassis Tune.luau has no 'Tune.{field} = ...' line to override")
        text = new_text
    return text


def vendor_source(name: str) -> str:
    """The exact, unmodified vendored file text for ``name`` (e.g. "Initialize.luau")."""
    return _read(name)
