"""Safety filter applied to every imported model.

* Links (``http://...``, ``www....``, bare ``name.com`` style domains) are removed from part,
  material, texture and model names.
* Parts, materials and textures whose *names* carry adult-content markers (nsfw, nude, porn,
  18+ ...) are deleted from the model, together with the triangles that use them.
* Files in a mod archive that can carry links or run code (``.url``, ``.lnk``, ``.exe``, ``.bat``,
  scripts, double extensions such as ``skin.png.exe`` ...) are reported and ignored. They are never
  opened, executed or copied to the output.

This is name/file based. It cannot recognise explicit *pictures* inside a texture, and it is
not a substitute for looking at a mod before you upload it.
"""
from __future__ import annotations

import posixpath
import re
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np

from .classify import tokens
from .model import ConversionError, Model

_TLDS = ("com|net|org|info|biz|ru|xyz|io|gg|ly|tk|ml|ga|cf|gq|su|cn|pw|icu|vip")
_URL = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:https?|ftp|file|javascript)\s*:\s*[^\s\"'<>]*)"
    r"|(?:(?<![A-Za-z0-9])www\.[^\s\"'<>]+)"
    rf"|(?:(?<![A-Za-z0-9-])[A-Za-z0-9][A-Za-z0-9-]*\.(?:{_TLDS})(?![A-Za-z0-9])(?:/[^\s\"'<>]*)?)",
    re.I)

_ADULT_TOKENS = frozenset("""
nude nudes naked nsfw porn porno pornhub pornography xxx hentai erotic erotica lewd topless
boob boobs tits nipple nipples vagina pussy penis genital genitals playboy onlyfans brazzers
sex sexy sexual adult fetish bdsm dildo vibrator lingerie stripper milf
""".split())
_ADULT_RAW = re.compile(r"(?<![a-z0-9])(?:r-?18|18\s?\+|18plus|x-?rated)(?![a-z0-9])", re.I)

_RISKY_EXT = {
    ".url": "internet shortcut", ".lnk": "Windows shortcut", ".webloc": "web shortcut",
    ".desktop": "desktop shortcut", ".website": "web shortcut",
    ".exe": "program", ".dll": "program library", ".bat": "script", ".cmd": "script",
    ".com": "program", ".scr": "program", ".msi": "installer", ".ps1": "script",
    ".vbs": "script", ".vbe": "script", ".js": "script", ".jse": "script", ".wsf": "script",
    ".hta": "script", ".jar": "program", ".apk": "program", ".sh": "script", ".py": "script",
    ".reg": "registry file", ".cpl": "program", ".pif": "program", ".dmg": "program",
    ".html": "web page", ".htm": "web page",
}
_IMAGE_LIKE = {".png", ".jpg", ".jpeg", ".dds", ".tga", ".bmp", ".txt", ".pdf", ".dae", ".jbeam",
               ".json", ".dff", ".txd", ".obj", ".mtl", ".glb", ".gltf", ".zip", ".doc", ".docx"}


def strip_links(text: str) -> Tuple[str, List[str]]:
    """Return ``(text without links, [removed links])``."""
    found: List[str] = []

    def drop(m: "re.Match[str]") -> str:
        found.append(m.group(0))
        return ""
    out = _URL.sub(drop, text)
    if found:
        out = re.sub(r"\s{2,}", " ", out).strip(" _-.")
    return out, found


def is_adult_name(name: str) -> bool:
    if _ADULT_RAW.search(name):
        return True
    return any(t in _ADULT_TOKENS for t in tokens(name))


def risky_file(path: str) -> Optional[str]:
    """Why a file in a mod archive is ignored, or None when it is harmless."""
    p = path.replace("\\", "/").lower()
    base = posixpath.basename(p)
    parts = base.split(".")
    ext = "." + parts[-1] if len(parts) > 1 else ""
    if ext in _RISKY_EXT:
        # "setup.com.png"-style names are handled below; a plain risky extension is enough here
        if len(parts) >= 3 and ("." + parts[-2]) in _IMAGE_LIKE:
            return f"double extension ({_RISKY_EXT[ext]} disguised as {'.' + parts[-2]})"
        return _RISKY_EXT[ext]
    if len(parts) >= 3 and ("." + parts[-2]) in _RISKY_EXT and ext in _IMAGE_LIKE:
        return "double extension (looks like a program or shortcut)"
    return None


def scan_files(paths: Iterable[str]) -> List[Tuple[str, str]]:
    out = []
    for p in paths:
        why = risky_file(p)
        if why:
            out.append((p, why))
    return sorted(out)


def _clean(name: str, what: str, msgs: List[str]) -> str:
    cleaned, links = strip_links(name)
    if links:
        msgs.append(f"removed a link from a {what} name (now '{cleaned or what}')")
        return cleaned or what
    return name


def scrub_model(model: Model, log: Callable[[str], None] = lambda s: None) -> List[str]:
    """Apply the filter in place. Returns the messages (they are also added to ``model.notes``)."""
    msgs: List[str] = []
    model.name = _clean(model.name, "model", msgs) or "Car"

    bad_tex = set()
    for key, tex in model.textures.items():
        tex.name = _clean(tex.name, "texture", msgs)
        if is_adult_name(key) or is_adult_name(tex.name):
            bad_tex.add(key)
    bad_mats = set()
    for i, m in enumerate(model.materials):
        m.name = _clean(m.name, "material", msgs)
        if is_adult_name(m.name) or (m.texture in bad_tex):
            bad_mats.add(i)
    for key in bad_tex:
        msgs.append("deleted a texture (adult-content name)")
        del model.textures[key]
    for i in bad_mats:
        if model.materials[i].texture in bad_tex:
            model.materials[i].texture = None

    kept = []
    for part in model.parts:
        part.name = _clean(part.name, "part", msgs)
        if is_adult_name(part.name):
            msgs.append("deleted a part (adult-content name)")
            continue
        if bad_mats:
            drop = np.isin(part.mesh.material_ids, list(bad_mats))
            if drop.any():
                msgs.append(f"deleted {int(drop.sum())} triangles of '{part.name}' that use an "
                            "adult-content material")
                part.mesh = part.mesh.select(~drop)
                if part.mesh.tri_count == 0:
                    continue
        kept.append(part)
    model.parts = kept
    if not model.parts:
        raise ConversionError("every part was removed by the safety filter (links / adult-content "
                              "names); nothing is left to convert")
    for m in msgs:
        log(f"  safety: {m}")
    model.notes.extend("Safety: " + m for m in msgs)
    return msgs
