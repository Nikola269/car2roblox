"""Image helpers: DDS/PNG/JPG/TGA decoding through Pillow, resizing for Roblox."""
from __future__ import annotations

import io
import re
import struct
from typing import Optional

import numpy as np
from PIL import Image

from .model import ConversionError, Texture


def norm_name(name: str) -> str:
    """Normalised texture key: lower-case, no directory, no extension."""
    n = name.replace("\\", "/").split("/")[-1].strip().lower()
    return re.sub(r"\.(dds|png|jpg|jpeg|tga|bmp|tif|tiff|gif|webp)$", "", n)


def load_image_bytes(data: bytes) -> Image.Image:
    """Decode any image Pillow understands (including DXT/BC DDS) to RGBA."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # pragma: no cover - depends on Pillow build
        raise ConversionError(f"cannot decode image: {exc}") from exc
    return img.convert("RGBA")


def make_dds(width: int, height: int, fourcc: bytes, level0: bytes) -> bytes:
    """Wrap a raw DXT1/3/5 top mip level in a minimal DDS container so Pillow can decode it."""
    DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000       # caps|height|width|pixelformat|linearsize
    pixel_format = struct.pack("<II4sIIIII", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    header = struct.pack("<7I", 124, DDSD, height, width, len(level0), 0, 1)
    header += b"\0" * 44                             # reserved1[11]
    header += pixel_format
    header += struct.pack("<5I", 0x1000, 0, 0, 0, 0)  # caps, caps2..4, reserved2
    return b"DDS " + header + level0


def decode_dxt(width: int, height: int, fourcc: bytes, data: bytes) -> Image.Image:
    need = ((width + 3) // 4) * ((height + 3) // 4) * (8 if fourcc == b"DXT1" else 16)
    if len(data) < need:
        raise ConversionError("truncated DXT texture data")
    return load_image_bytes(make_dds(width, height, fourcc, data[:need]))


def fit_texture(img: Image.Image, max_size: int) -> Image.Image:
    """Downscale (never upscale) so the longest side is <= max_size."""
    if max_size and max(img.size) > max_size:
        s = max_size / max(img.size)
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))),
                         Image.LANCZOS)
    return img


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    # drop the alpha channel when it is fully opaque (smaller files, no BLEND surprises)
    a = np.asarray(img.convert("RGBA"))[..., 3]
    out = img.convert("RGBA") if a.min() < 255 else img.convert("RGB")
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def solid_texture(name: str, rgba=(255, 255, 255, 255), size: int = 4) -> Texture:
    return Texture(name, Image.new("RGBA", (size, size), tuple(int(c) for c in rgba)))


def parse_hex_color(value: str, default=(0.8, 0.1, 0.1)) -> tuple[float, float, float]:
    """'#aabbcc' / 'aabbcc' / 'r,g,b' (0-255) -> floats 0..1."""
    if value is None:
        return default
    s = str(value).strip()
    try:
        if "," in s:
            parts = [float(x) for x in s.split(",")][:3]
            return tuple(max(0.0, min(1.0, p / 255.0)) for p in parts)  # type: ignore
        s = s.lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) != 6:
            raise ValueError
        return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore
    except ValueError:
        raise ConversionError(f"bad colour '{value}' (use #rrggbb)") from None
