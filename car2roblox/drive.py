"""Test-drive model for the preview: a kinematic bicycle car. Pure Python, no numpy / Tk.

It mirrors the browser version's drive mode (same constants), so the two previews feel alike.
It is *not* Roblox physics: it only lets you check size, wheel placement and steering before
importing. Units are studs and seconds; positive ``steer`` / ``heading`` turn left.
The car faces -Z when ``heading`` is 0 (Roblox's forward).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence, Set, Tuple

STUDS_PER_METER = 1.0 / 0.28
KMH_PER_STUD_S = 0.28 * 3.6          # 1 stud/s = 1.008 km/h


@dataclass(frozen=True)
class Tuning:
    wheelbase: float = 9.0          # distance between the axles
    max_forward: float = 78.0       # studs / s
    max_reverse: float = 26.0
    steer_limit: float = 0.56       # radians at standstill, reduced with speed
    steer_rate: float = 2.6         # radians / s the front wheels can swing


@dataclass(frozen=True)
class Inputs:
    throttle: float = 0.0           # 0..1
    brake: float = 0.0              # 0..1 (also reverses when stopped)
    steer: float = 0.0              # -1 (right) .. +1 (left)
    handbrake: bool = False


@dataclass(frozen=True)
class CarState:
    x: float = 0.0
    z: float = 0.0
    heading: float = 0.0
    speed: float = 0.0              # + forward, studs / s
    steer: float = 0.0              # current front wheel angle, radians
    spin: float = 0.0               # studs rolled; wheels turn by spin / radius (negative = forward)
    pitch: float = 0.0
    roll: float = 0.0
    cam_heading: float = 0.0        # the chase camera lags behind the heading

    @property
    def kmh(self) -> float:
        return abs(self.speed) * KMH_PER_STUD_S


def reset() -> CarState:
    return CarState()


def wheelbase_from_hubs(hubs: Sequence[Sequence[float]], fronts: Sequence[bool],
                        length: float = 15.0) -> float:
    """Distance between the front and rear axle, from the wheel hubs; a guess from the car length
    (about 0.58 of it, like a typical car) when there are no two axles to measure."""
    front = [h[2] for h, f in zip(hubs, fronts) if f]
    rear = [h[2] for h, f in zip(hubs, fronts) if not f]
    if front and rear:
        wb = abs(sum(rear) / len(rear) - sum(front) / len(front))
        if wb > 1.0:
            return wb
    return max(2.0, 0.58 * float(length))


def tuning_for(hubs: Sequence[Sequence[float]], fronts: Sequence[bool], length: float,
               top_speed_kmh: Optional[float] = None) -> Tuning:
    t = Tuning(wheelbase=wheelbase_from_hubs(hubs, fronts, length))
    if top_speed_kmh and top_speed_kmh > 0:
        studs = float(top_speed_kmh) / KMH_PER_STUD_S
        t = replace(t, max_forward=max(15.0, min(studs, 400.0)))
    return t


_KEYS = {
    "w": "throttle", "up": "throttle", "s": "brake", "down": "brake",
    "a": "left", "left": "left", "d": "right", "right": "right", "space": "handbrake",
}


def inputs_from_keys(keys: Iterable[str]) -> Inputs:
    """Turn the set of pressed key names (Tk keysyms, any case) into drive inputs."""
    held = {_KEYS[k.lower()] for k in keys if k.lower() in _KEYS}
    return Inputs(throttle=1.0 if "throttle" in held else 0.0, brake=1.0 if "brake" in held else 0.0,
                  steer=(1.0 if "left" in held else 0.0) - (1.0 if "right" in held else 0.0),
                  handbrake="handbrake" in held)


def _sign(v: float) -> float:
    return (v > 0) - (v < 0)


def step(state: CarState, inp: Inputs, dt: float, tuning: Tuning = Tuning()) -> CarState:
    """Advance the car by ``dt`` seconds and return the new state."""
    dt = max(0.0, min(float(dt), 0.1))
    if dt == 0.0:
        return state
    throttle, brake, hand = max(0.0, inp.throttle), max(0.0, inp.brake), bool(inp.handbrake)
    speed = state.speed
    acc = 0.0
    if throttle > 0:
        acc += 46.0 * min(1.0, throttle) * (1.0 - max(0.0, speed) / tuning.max_forward)
    if brake > 0:
        acc -= 90.0 if speed > 1 else 40.0 * (1.0 - max(0.0, -speed) / tuning.max_reverse)
    if hand:
        acc -= _sign(speed) * 120.0
    coasting = not (throttle > 0 or brake > 0 or hand)
    if coasting:
        acc -= _sign(speed) * (9.0 + 0.004 * speed * speed)
    new_speed = speed + acc * dt
    if coasting and _sign(speed) != _sign(new_speed):
        new_speed = 0.0
    if hand and abs(new_speed) < 2:
        new_speed = 0.0
    new_speed = max(-tuning.max_reverse, min(tuning.max_forward, new_speed))

    limit = tuning.steer_limit / (1.0 + abs(new_speed) / 38.0)
    target = max(-1.0, min(1.0, inp.steer)) * limit
    swing = tuning.steer_rate * dt
    steer = state.steer + max(-swing, min(swing, target - state.steer))

    omega = new_speed / max(2.0, tuning.wheelbase) * math.tan(steer)
    heading = state.heading + omega * dt
    x = state.x - math.sin(heading) * new_speed * dt
    z = state.z - math.cos(heading) * new_speed * dt
    spin = state.spin - new_speed * dt

    accel = (new_speed - speed) / max(dt, 1e-4)
    k = min(1.0, dt * 6.0)
    pitch = state.pitch + (max(-0.05, min(0.05, accel * 0.0009)) - state.pitch) * k
    roll = state.roll + (max(-0.07, min(0.07, -omega * new_speed * 0.0007)) - state.roll) * k

    d = ((heading - state.cam_heading + math.pi * 3) % (math.pi * 2)) - math.pi
    cam = state.cam_heading + d * (1.0 - 0.92 ** (dt * 60.0))
    return CarState(x=x, z=z, heading=heading, speed=new_speed, steer=steer, spin=spin,
                    pitch=pitch, roll=roll, cam_heading=cam)


def chase_camera(state: CarState, size: Tuple[float, float, float], pitch: float = 0.26,
                 distance: Optional[float] = None) -> Tuple[Tuple[float, float, float],
                                                            Tuple[float, float, float]]:
    """(eye, target) of a camera that follows the car from behind. ``size`` = width, height, length."""
    width, height, length = size
    dist = distance if distance else max(length * 1.7, width * 3.2, 12.0)
    dist = min(dist, max(length * 1.5, 12.0))
    h = state.cam_heading
    elev = pitch * 0.6 + 0.12
    flat = math.cos(elev) * dist
    eye = (state.x + math.sin(h) * flat, height * 0.9 + math.sin(elev) * dist + 1.0,
           state.z + math.cos(h) * flat)
    ahead = length * 0.35
    target = (state.x - math.sin(state.heading) * ahead, height * 0.55,
              state.z - math.cos(state.heading) * ahead)
    return eye, target


def keys_text() -> str:
    return "W/S gas and brake  -  A/D steer  -  Space handbrake  -  R reset  -  Esc stop"
