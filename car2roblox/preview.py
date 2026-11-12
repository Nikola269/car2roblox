"""Software preview renderer (numpy + Pillow, no GPU, no extra packages).

Used for the desktop window's preview, for the window/header artwork, and for the ``preview.png``
that is written next to every conversion. It draws the *converted* pieces (Roblox axes, studs),
so what you see is what the Studio import will contain: size, wheels, colours, textures.

Cheap by design: per-triangle flat shading with a painter's-algorithm sort. Textures are sampled
once per triangle at its UV centre, so fine texture detail is not shown (it is a preview).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .geometry import decimate_to

Vec = np.ndarray

# sky / ground / grid colours (0-255 sRGB) per theme
THEMES: Dict[str, Dict[str, Tuple[float, ...]]] = {
    "dusk": {   # the app's look: indigo sky, warm horizon glow, dark blue ground with a cyan grid
        "top": (9, 13, 32), "mid": (41, 52, 112), "horizon": (214, 126, 146), "glow": (255, 170, 120),
        "ground": (15, 20, 38), "ground_far": (66, 62, 112), "grid": (70, 120, 210),
        "amb_sky": (0.58, 0.64, 0.95), "amb_gnd": (0.16, 0.17, 0.26), "sun": (1.0, 0.9, 0.78),
        "arrow": (110, 160, 255)},
    "day": {
        "top": (110, 160, 226), "mid": (170, 200, 238), "horizon": (232, 238, 246), "glow": (255, 244, 214),
        "ground": (198, 206, 218), "ground_far": (226, 232, 240), "grid": (150, 164, 188),
        "amb_sky": (0.80, 0.86, 0.96), "amb_gnd": (0.36, 0.36, 0.40), "sun": (1.0, 0.97, 0.9),
        "arrow": (30, 90, 216)},
}
SUN = np.array([-0.45, 0.85, 0.35], dtype=np.float32)
SUN /= np.linalg.norm(SUN)
FOV = 0.8                                    # vertical field of view, radians
NEAR = 0.3


@dataclass
class Scene:
    """A triangle soup ready to draw. All per-triangle arrays have one entry per triangle."""
    tri: np.ndarray            # (T,3,3) float32 world positions
    normal: np.ndarray         # (T,3)   float32 unit face normals
    color: np.ndarray          # (T,3)   float32 sRGB 0..1 (material x texture)
    alpha: np.ndarray          # (T,)    float32
    two_sided: np.ndarray      # (T,)    bool
    size: Tuple[float, float, float]     # width, height, length in studs
    group: Optional[np.ndarray] = None          # (T,) int32: -1 = body, i = belongs to wheel i
    wheel_hubs: Optional[np.ndarray] = None     # (n,3) hub centres
    wheel_radius: Optional[np.ndarray] = None   # (n,)
    wheel_front: Optional[np.ndarray] = None    # (n,) bool, on the front axle
    wheel_steer: Optional[np.ndarray] = None    # (n,) bool, turns with the steering

    def __post_init__(self) -> None:
        if self.group is None:
            self.group = np.full(len(self.tri), -1, dtype=np.int32)
        if self.wheel_hubs is None:
            self.wheel_hubs = np.zeros((0, 3), dtype=np.float64)
        n = len(self.wheel_hubs)
        if self.wheel_radius is None:
            self.wheel_radius = np.ones(n, dtype=np.float64)
        if self.wheel_front is None:
            self.wheel_front = np.zeros(n, dtype=bool)
        if self.wheel_steer is None:
            self.wheel_steer = np.zeros(n, dtype=bool)

    @property
    def tri_count(self) -> int:
        return int(len(self.tri))

    @property
    def wheel_count(self) -> int:
        return int(len(self.wheel_hubs))


def _texture_array(tex, limit: int = 128) -> np.ndarray:
    img = tex.image.convert("RGBA")
    if max(img.size) > limit:
        s = limit / max(img.size)
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.BOX)
    return np.asarray(img, dtype=np.uint8)


def _subdivide(v: np.ndarray, uv: np.ndarray, length: float, levels: int = 2):
    """Split triangles longer than a fraction of the car so texture colours vary across them."""
    for lvl in range(levels):
        e = np.maximum(np.maximum(np.linalg.norm(v[:, 1] - v[:, 0], axis=1),
                                  np.linalg.norm(v[:, 2] - v[:, 1], axis=1)),
                       np.linalg.norm(v[:, 0] - v[:, 2], axis=1))
        big = e > length / (14.0 / (lvl + 1))
        if not big.any() or int(big.sum()) * 4 + int((~big).sum()) > 120000:
            break
        a, b = v[big], uv[big]
        m = [(a[:, 0] + a[:, 1]) / 2, (a[:, 1] + a[:, 2]) / 2, (a[:, 2] + a[:, 0]) / 2]
        mu = [(b[:, 0] + b[:, 1]) / 2, (b[:, 1] + b[:, 2]) / 2, (b[:, 2] + b[:, 0]) / 2]
        nv = np.concatenate([np.stack([a[:, 0], m[0], m[2]], 1), np.stack([m[0], a[:, 1], m[1]], 1),
                             np.stack([m[2], m[1], a[:, 2]], 1), np.stack([m[0], m[1], m[2]], 1)])
        nu = np.concatenate([np.stack([b[:, 0], mu[0], mu[2]], 1), np.stack([mu[0], b[:, 1], mu[1]], 1),
                             np.stack([mu[2], mu[1], b[:, 2]], 1), np.stack([mu[0], mu[1], mu[2]], 1)])
        v = np.concatenate([v[~big], nv])
        uv = np.concatenate([uv[~big], nu])
    return v, uv


def _steer_flags(wheels: Sequence) -> list:
    """Which wheels turn with the steering wheel: the front axle of a car that also has a rear axle."""
    has_rear = any(not w.front for w in wheels)
    return [bool(w.front and (has_rear or len(wheels) <= 2)) for w in wheels]


def build_scene(pieces: Sequence, textures: Dict[str, object], size: Tuple[float, float, float],
                max_tris: int = 14000, wheels: Optional[Sequence] = None) -> Scene:
    """Flatten converted pieces into a drawable scene, simplifying heavy meshes to ``max_tris``.

    ``wheels`` (the pipeline's WheelGroup list) lets the scene move wheels separately for the test
    drive: every triangle of a wheel's pieces is tagged with that wheel's index.
    """
    if not pieces:
        raise ValueError("nothing to preview")
    wheels = list(wheels or [])
    piece_wheel: Dict[str, int] = {}
    for wi, w in enumerate(wheels):
        for name in w.pieces:
            piece_wheel[name] = wi
    total = sum(p.mesh.tri_count for p in pieces)
    tex_cache: Dict[str, np.ndarray] = {}
    tris, nrms, cols, alphas, sides, groups = [], [], [], [], [], []
    for pc in pieces:
        mesh = pc.mesh
        if total > max_tris and mesh.tri_count > 24:
            mesh = decimate_to(mesh, max(24, int(max_tris * mesh.tri_count / total)))
        if mesh.tri_count == 0:
            continue
        v = mesh.positions[mesh.triangles].astype(np.float32)               # (t,3,3)
        mat = pc.material
        key = mat.texture
        textured = bool(key and key in textures and mesh.uvs is not None)
        uv3 = mesh.uvs[mesh.triangles].astype(np.float32) if textured else None   # (t,3,2)
        if textured:
            v, uv3 = _subdivide(v, uv3, max(size[2], 1.0))
        n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-9
        if not keep.any():
            continue
        v, n, ln = v[keep], n[keep], ln[keep]
        n = n / ln[:, None]
        base = np.tile(np.array(mat.color[:3], dtype=np.float32), (len(v), 1))
        alpha = np.full(len(v), float(mat.color[3]), dtype=np.float32)
        if textured:
            if key not in tex_cache:
                tex_cache[key] = _texture_array(textures[key])
            arr = tex_cache[key]
            h, w = arr.shape[:2]
            uv = uv3[keep].mean(axis=1)                                        # image-space centre
            x = np.clip(((uv[:, 0] % 1.0) * w).astype(int), 0, w - 1)
            y = np.clip(((uv[:, 1] % 1.0) * h).astype(int), 0, h - 1)
            texel = arr[y, x].astype(np.float32) / 255.0
            base = base * texel[:, :3]
            if not mat.transparent:
                alpha = alpha * texel[:, 3]
        tris.append(v)
        nrms.append(n.astype(np.float32))
        cols.append(base)
        alphas.append(alpha)
        sides.append(np.full(len(v), bool(mat.double_sided or mat.transparent)))
        groups.append(np.full(len(v), piece_wheel.get(pc.name, -1), dtype=np.int32))
    if not tris:
        raise ValueError("nothing to preview")
    hubs = np.array([w.hub for w in wheels], dtype=np.float64).reshape(-1, 3)
    return Scene(np.concatenate(tris), np.concatenate(nrms), np.concatenate(cols),
                 np.concatenate(alphas), np.concatenate(sides), tuple(float(s) for s in size),
                 group=np.concatenate(groups), wheel_hubs=hubs,
                 wheel_radius=np.array([w.radius for w in wheels], dtype=np.float64),
                 wheel_front=np.array([bool(w.front) for w in wheels], dtype=bool),
                 wheel_steer=np.array(_steer_flags(wheels), dtype=bool))


def scene_from_build(pieces: Sequence, wheels: Sequence, textures: Dict[str, object],
                     size: Tuple[float, float, float], max_tris: int = 14000) -> Scene:
    """Scene for the window's preview / test drive from pipeline.build_pieces() results."""
    return build_scene(pieces, textures, size, max_tris=max_tris, wheels=wheels)


# --------------------------------------------------------------------------- camera
def _camera(size: Tuple[float, float, float], yaw: float, pitch: float, zoom: float):
    w, h, l = size
    dist = max(l * 1.7, w * 3.2, 12.0) / max(zoom, 0.2)
    target = np.array([0.0, h * 0.45, 0.0], dtype=np.float64)
    cp = math.cos(pitch)
    eye = target + dist * np.array([math.sin(yaw) * cp, math.sin(pitch), math.cos(yaw) * cp])
    return (eye,) + _basis(eye, target)


def _basis(eye, target):
    """(forward, right, up) unit vectors of a camera at ``eye`` looking at ``target``."""
    eye = np.asarray(eye, dtype=np.float64)
    f = np.asarray(target, dtype=np.float64) - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, [0.0, 1.0, 0.0])
    r /= max(np.linalg.norm(r), 1e-9)
    u = np.cross(r, f)
    return f, r, u


def _smooth(a: np.ndarray, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: Sequence[float], b: Sequence[float], t: np.ndarray) -> np.ndarray:
    a_, b_ = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return a_ + (b_ - a_) * t[..., None]


def _backdrop(W: int, H: int, eye, f, r, u, theme: Dict, size: Tuple[float, float, float],
              shadow: bool, shadow_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> np.ndarray:
    """Sky + ground (grid, fog, soft shadow) for every pixel by intersecting the view rays with y=0."""
    tanf = math.tan(FOV / 2)
    aspect = W / H
    xs = ((np.arange(W, dtype=np.float32) + 0.5) / W * 2 - 1) * aspect * tanf
    ys = (1 - (np.arange(H, dtype=np.float32) + 0.5) / H * 2) * tanf
    d = (f[None, None, :] + xs[None, :, None] * r[None, None, :] + ys[:, None, None] * u[None, None, :]).astype(np.float32)
    d /= np.linalg.norm(d, axis=2, keepdims=True)
    dy = d[..., 1]
    # sky: horizon colour rising through mid to the top, plus a warm glow around the sun direction
    e = np.clip(dy, 0, 1)
    sky = _lerp(theme["horizon"], theme["mid"], np.clip(e / 0.22, 0, 1) ** 0.7)
    sky = _lerp(sky, theme["top"], np.clip((e - 0.12) / 0.6, 0, 1) ** 0.8)
    sun = np.array([-0.35, 0.10, -1.0], dtype=np.float32)
    sun /= np.linalg.norm(sun)
    glow = np.clip((d * sun).sum(axis=2), 0, 1) ** 24
    sky = sky + glow[..., None] * np.asarray(theme["glow"], dtype=np.float32) * 0.45
    # ground
    below = dy < -1e-4
    t = np.where(below, -eye[1] / np.where(below, dy, -1.0), 1e6).astype(np.float32)
    px = (eye[0] + d[..., 0] * t).astype(np.float32)
    pz = (eye[2] + d[..., 2] * t).astype(np.float32)
    dist = np.sqrt((px - eye[0]) ** 2 + (pz - eye[2]) ** 2)

    def line(p: np.ndarray, s: float, w: np.ndarray) -> np.ndarray:
        dd = np.abs(np.mod(p + 0.5 * s, s) - 0.5 * s)
        return 1.0 - _smooth(w, w * 2.2, dd)

    # size of one pixel on the ground: lines are widened with it and fade out before they alias
    foot = dist * dist / max(float(eye[1]), 0.5) * (2.0 * tanf / H)
    w = np.minimum(0.03 + 0.35 * foot, 1.2)
    minor = np.maximum(line(px, 4.0, w), line(pz, 4.0, w)) * (1.0 - _smooth(0.4, 1.4, foot)) * 0.5
    major = np.maximum(line(px, 20.0, w * 1.6), line(pz, 20.0, w * 1.6)) * (1.0 - _smooth(1.5, 5.0, foot))
    g = np.maximum(minor, major) * (1.0 - _smooth(90.0, 420.0, dist))
    ground = _lerp(theme["ground"], theme["ground_far"], _smooth(10.0, 600.0, dist))
    ground = ground + (np.asarray(theme["grid"], dtype=np.float32) - ground) * g[..., None]
    if shadow:
        wd, _, ln = size
        sx0, sz0, hd = shadow_pose
        dx, dz = px - sx0, pz - sz0
        cs, sn = math.cos(hd), math.sin(hd)
        lx, lz = cs * dx - sn * dz, sn * dx + cs * dz            # world -> car space (undo the heading)
        q = (lx / (wd * 0.62)) ** 2 + (lz / (ln * 0.58)) ** 2
        ground = ground * (1.0 - 0.55 * np.exp(-(q ** 1.6)))[..., None]
    fog = 1.0 - np.exp(-dist / 150.0)
    ground = _lerp(ground, theme["horizon"], np.clip(fog * 0.85, 0, 1))
    return np.where(below[..., None], ground, sky)


def _rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def _rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def _rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def posed_geometry(scene: Scene, pose) -> Tuple[np.ndarray, np.ndarray]:
    """Triangle corners and face normals after applying a drive pose.

    ``pose`` is any object with x, z, heading, pitch, roll, steer and spin (see drive.CarState):
    wheels roll about X (spin, in studs travelled) and the steering wheels turn about Y at the hub,
    then the whole car is pitched, rolled, turned to its heading and moved to (x, z).
    """
    V = scene.tri.astype(np.float64).copy()
    N = scene.normal.astype(np.float64).copy()
    for i in range(scene.wheel_count):
        sel = scene.group == i
        if not sel.any():
            continue
        hub = scene.wheel_hubs[i]
        rot = _rot_x(float(pose.spin) / max(float(scene.wheel_radius[i]), 0.1))
        if scene.wheel_steer[i]:
            rot = _rot_y(float(pose.steer)) @ rot
        V[sel] = (V[sel] - hub) @ rot.T + hub
        N[sel] = N[sel] @ rot.T
    car = _rot_y(float(pose.heading)) @ _rot_x(float(pose.pitch)) @ _rot_z(float(pose.roll))
    V = V @ car.T + np.array([float(pose.x), 0.0, float(pose.z)])
    N = N @ car.T
    return V, N


def render(scene: Optional[Scene], width: int = 720, height: int = 450, yaw: float = 2.55,
           pitch: float = 0.26, zoom: float = 1.0, theme: str = "dusk", ssaa: int = 2,
           arrow: bool = True, pose=None, camera=None) -> Image.Image:
    """Draw the scene (or only the empty backdrop when ``scene`` is None) as an RGB image.

    ``pose`` (see ``posed_geometry``) moves the car and turns its wheels for the test drive;
    ``camera`` = (eye, target) overrides the orbit camera (use drive.chase_camera).
    """
    th = THEMES.get(theme, THEMES["dusk"])
    size = scene.size if scene is not None else (7.0, 4.5, 15.0)
    ss = max(1, int(ssaa))
    W, H = int(width) * ss, int(height) * ss
    if camera is not None:
        eye = np.asarray(camera[0], dtype=np.float64)
        f, r, u = _basis(eye, camera[1])
    else:
        eye, f, r, u = _camera(size, yaw, pitch, zoom)
    shadow_pose = (float(pose.x), float(pose.z), float(pose.heading)) if pose is not None else (0.0, 0.0, 0.0)
    img = Image.fromarray(np.clip(_backdrop(W, H, eye, f, r, u, th, size, scene is not None, shadow_pose),
                                  0, 255).astype(np.uint8))
    if scene is not None:
        draw = ImageDraw.Draw(img, "RGBA")
        tanf = math.tan(FOV / 2)
        aspect = W / H

        def project(p: np.ndarray):
            q = p - eye
            z = q @ f
            x = q @ r
            y = q @ u
            zz = np.maximum(z, 1e-6)
            sx = (x / (zz * tanf * aspect) * 0.5 + 0.5) * W
            sy = (0.5 - y / (zz * tanf) * 0.5) * H
            return sx, sy, z

        if arrow and pose is None:
            wd, _, ln = size
            aw, al, z0 = max(1.2, wd * 0.32), max(2.4, ln * 0.14), -(ln / 2 + 2.5)
            poly = [(-aw * .35, z0 + al), (aw * .35, z0 + al), (aw * .35, z0 + al * 1.9), (-aw * .35, z0 + al * 1.9)]
            head = [(-aw, z0 + al), (aw, z0 + al), (0.0, z0)]
            for shape in (poly, head):
                p3 = np.array([[x, 0.03, z] for x, z in shape])
                sx, sy, z = project(p3)
                if (z > NEAR).all():
                    draw.polygon(list(zip(sx.tolist(), sy.tolist())), fill=tuple(th["arrow"]) + (235,))
        if pose is not None:
            V, normal = posed_geometry(scene, pose)
        else:
            V, normal = scene.tri.astype(np.float64), scene.normal
        cen = V.mean(axis=1)
        facing = ((eye[None, :] - V[:, 0]) * normal).sum(axis=1)
        nrm = np.where((facing < 0)[:, None], -normal, normal)        # two-sided shading
        visible = (scene.two_sided | (facing > 0)) & (scene.alpha > 0.35)
        sx, sy, z = project(V.reshape(-1, 3))
        sx, sy, z = sx.reshape(-1, 3), sy.reshape(-1, 3), z.reshape(-1, 3)
        visible &= (z > NEAR).all(axis=1)
        # shade in linear light: hemisphere ambient + one sun
        lin = np.power(np.clip(scene.color, 0, 1), 2.2)
        hemi = _lerp(th["amb_gnd"], th["amb_sky"], (nrm[:, 1] * 0.5 + 0.5).astype(np.float32))
        diff = np.clip(nrm @ SUN, 0, 1)[:, None] * np.asarray(th["sun"], dtype=np.float32)
        lit = np.clip(lin * (hemi * 0.9 + diff * 0.75), 0, 1)
        rgb = (np.power(lit, 1 / 2.2) * 255).astype(np.uint8)
        depth = ((cen - eye) @ f)
        order = np.argsort(-depth)
        glass = scene.alpha < 0.99
        for pass_glass in (False, True):
            idx = order[(visible & (glass == pass_glass))[order]]
            xs, ys, cs, al = sx[idx].tolist(), sy[idx].tolist(), rgb[idx].tolist(), scene.alpha[idx].tolist()
            for (a, b, c), (d, e, g), col, a_ in zip(xs, ys, cs, al):
                fill = (col[0], col[1], col[2], int(a_ * 255)) if pass_glass else (col[0], col[1], col[2])
                draw.polygon([(a, d), (b, e), (c, g)], fill=fill, outline=None if pass_glass else fill)
    if ss > 1:
        img = img.resize((int(width), int(height)), Image.BOX)
    return img


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


_BANNER_BASE: Dict[str, Image.Image] = {}


def banner(width: int, height: int, theme: str = "dusk") -> Image.Image:
    """Wide header artwork: the backdrop seen low over the grid, darkened on the left for text."""
    w0, h0 = 1280, 420
    base = _BANNER_BASE.get(theme)
    if base is None:
        base = _BANNER_BASE[theme] = render(None, w0, h0, yaw=3.55, pitch=0.11, zoom=0.45,
                                            theme=theme, ssaa=1)
    width, height = max(2, int(width)), max(2, int(height))
    scale = width / w0
    full_h = max(height, round(h0 * scale))
    img = base.resize((width, full_h), Image.LANCZOS)
    horizon = full_h * (0.5 - math.tan(0.11) / (2.0 * math.tan(FOV / 2)))     # row of the horizon line
    top = max(0, min(full_h - height, int(horizon - height * 0.42)))
    img = img.crop((0, top, width, top + height))
    shade = np.linspace(0.62, 0.0, width, dtype=np.float32)[None, :, None] ** 1.3
    arr = np.asarray(img, dtype=np.float32) * (1.0 - 0.7 * shade)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def window_backdrop(width: int, height: int, theme: str = "dusk") -> Image.Image:
    """Full-window background art (sky above, grid floor below, a little dimmed)."""
    img = render(None, max(64, width // 2), max(64, height // 2), yaw=3.55, pitch=0.16, zoom=0.9,
                 theme=theme, ssaa=1).resize((max(64, width), max(64, height)), Image.BICUBIC)
    return Image.fromarray(np.clip(np.asarray(img, dtype=np.float32) * 0.8, 0, 255).astype(np.uint8))
