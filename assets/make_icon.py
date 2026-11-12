"""Generates assets/icon.ico: the Car2Roblox app icon (used by the Windows .exe and the
installer). Re-run this after changing the design; nothing else depends on its intermediate
state, only the committed .ico file.
"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path(__file__).resolve().parent / "icon.ico"

# same palette as the app GUI (car2roblox/gui.py PALETTE)
BG = (11, 16, 34, 255)          # #0b1022
PANEL = (26, 35, 71, 255)       # #1a2347
ACCENT = (110, 160, 255, 255)   # #6ea0ff
ACCENT_DK = (74, 115, 199, 255)
WARM = (255, 170, 120, 255)     # #ffaa78 headlight
WHITE = (233, 237, 255, 255)
RIM = (154, 167, 216, 255)      # #9aa7d8 muted


def draw_car(size: int, tile: bool = True) -> Image.Image:
    """The car artwork. With tile=True (the .ico use) it sits on its own rounded background
    card. With tile=False the card is left out, so the car can be composited onto another
    background (used for the installer's wizard images) without a visible seam."""
    s = size * 8  # supersample, then downscale for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if tile:
        pad = s * 0.035
        d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.23, fill=BG)
        d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.23, outline=PANEL,
                            width=max(1, int(s * 0.012)))

    cx, gy = s * 0.5, s * 0.62          # gy: ground line (wheel centre height)
    body_w = s * 0.66
    body_h = s * 0.14
    bx0, bx1 = cx - body_w / 2, cx + body_w / 2
    by1 = gy - s * 0.02                 # bottom of the body sits just above the wheel centres
    by0 = by1 - body_h

    # cabin (simple wedge roof)
    cab_h = body_h * 1.55
    cabin = [
        (bx0 + body_w * 0.16, by0),
        (bx0 + body_w * 0.30, by0 - cab_h),
        (bx1 - body_w * 0.30, by0 - cab_h),
        (bx1 - body_w * 0.12, by0),
    ]
    d.polygon(cabin, fill=ACCENT)
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=body_h * 0.6, fill=ACCENT)
    # subtle lower-body shade
    d.rounded_rectangle([bx0, by0 + body_h * 0.55, bx1, by1], radius=body_h * 0.45, fill=ACCENT_DK)
    # window
    win = [(bx0 + body_w * 0.335, by0 - cab_h * 0.86), (bx0 + body_w * 0.5, by0 - s * 0.01),
           (bx1 - body_w * 0.335, by0 - s * 0.01), (bx1 - body_w * 0.335, by0 - cab_h * 0.86)]
    d.polygon(win, fill=BG)
    # headlight + taillight
    lamp_r = body_h * 0.22
    d.ellipse([bx1 - lamp_r * 2.4, by0 + body_h * 0.28, bx1 - lamp_r * 0.4, by0 + body_h * 0.28 + lamp_r * 2], fill=WARM)
    d.ellipse([bx0 + lamp_r * 0.4, by0 + body_h * 0.28, bx0 + lamp_r * 2.4, by0 + body_h * 0.28 + lamp_r * 2], fill=WHITE)

    # wheels
    r = body_h * 1.05
    for wx in (bx0 + body_w * 0.24, bx1 - body_w * 0.24):
        d.ellipse([wx - r, gy - r, wx + r, gy + r], fill=PANEL, outline=RIM, width=max(1, int(s * 0.012)))
        d.ellipse([wx - r * 0.38, gy - r * 0.38, wx + r * 0.38, gy + r * 0.38], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    imgs = [draw_car(sz) for sz in SIZES]
    imgs[-1].save(OUT, sizes=[(sz, sz) for sz in SIZES])
    print(f"wrote {OUT} with sizes {SIZES}")
