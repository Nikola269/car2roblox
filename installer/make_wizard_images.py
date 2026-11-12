"""Generates the Inno Setup wizard's branded images:

  WizardImage.bmp       164x314  - tall banner shown on the Welcome and Finished pages
  WizardSmallImage.bmp   55x 58  - small logo shown in the corner of every other page

Both reuse the same car artwork and palette as assets/make_icon.py (the app/taskbar icon), so the
installer looks like it belongs to the app instead of using Inno Setup's generic default graphics.
Re-run this after changing the design; only the committed .bmp files matter, not this script's
intermediate state. Needs Pillow (already a project dependency) and the Poppins font (Google Fonts,
commonly preinstalled; falls back to Pillow's default font if it isn't found).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "assets"))
from make_icon import ACCENT, BG, PANEL, RIM, WHITE, draw_car  # noqa: E402  (palette + car artwork)

OUT_DIR = Path(__file__).resolve().parent
BIG_SIZE = (164, 314)
SMALL_SIZE = (55, 58)
S = 4  # supersample factor for smooth text/edges, downscaled at the end

_FONT_DIR = Path("/usr/share/fonts/truetype/google-fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = _FONT_DIR / name
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)


def _centered_text(d: ImageDraw.ImageDraw, cx: float, y: float, text: str, font, fill, spacing: float = 0.0):
    if spacing:
        # manual letter-spacing: draw glyph by glyph so uppercase tracking doesn't look cramped
        widths = [d.textlength(c, font=font) for c in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = cx - total / 2
        for c, w in zip(text, widths):
            d.text((x, y), c, font=font, fill=fill)
            x += w + spacing
    else:
        w = d.textlength(text, font=font)
        d.text((cx - w / 2, y), text, font=font, fill=fill)


def make_big() -> Image.Image:
    w, h = BIG_SIZE[0] * S, BIG_SIZE[1] * S
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)

    # soft vertical gradient so the banner doesn't look like a flat colour swatch
    top, bottom = BG[:3], PANEL[:3]
    for row in range(h):
        t = row / h
        d.line([(0, row), (w, row)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))

    car = draw_car(int(w * 0.68), tile=False)
    img.paste(car, (int(w * 0.16), int(h * 0.12)), car)

    title_font = _font("Poppins-Bold.ttf", int(h * 0.060))
    tag_font = _font("Poppins-Medium.ttf", int(h * 0.027))
    _centered_text(d, w / 2, h * 0.645, "Car2Roblox", title_font, ACCENT[:3])
    for i, line in enumerate(("GTA SA · GTA V · BeamNG", "for Roblox Studio")):
        _centered_text(d, w / 2, h * 0.722 + i * h * 0.042, line, tag_font, RIM[:3])

    # thin accent rule, a small finishing touch
    ry = int(h * 0.825)
    d.line([(w * 0.28, ry), (w * 0.72, ry)], fill=ACCENT[:3], width=max(1, S // 2))

    return img.resize(BIG_SIZE, Image.LANCZOS)


def make_small() -> Image.Image:
    w, h = SMALL_SIZE[0] * S, SMALL_SIZE[1] * S
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    pad = int(min(w, h) * 0.12)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(min(w, h) * 0.16), fill=PANEL[:3])
    size = min(w, h) - pad * 2
    car = draw_car(size)
    img.paste(car, ((w - size) // 2, (h - size) // 2), car)
    return img.resize(SMALL_SIZE, Image.LANCZOS)


if __name__ == "__main__":
    big = make_big()
    big.save(OUT_DIR / "WizardImage.bmp", "BMP")
    print(f"wrote {OUT_DIR / 'WizardImage.bmp'} {big.size}")

    small = make_small()
    small.save(OUT_DIR / "WizardSmallImage.bmp", "BMP")
    print(f"wrote {OUT_DIR / 'WizardSmallImage.bmp'} {small.size}")
