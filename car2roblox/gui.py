"""Desktop window: settings tabs, a live 3D preview you can turn and drive, and the converter.

Everything slow (reading the model, drawing the preview, converting) runs in worker threads;
results come back through queues that ``drain`` empties on the Tk thread, so the window never
freezes. The preview is the software renderer in ``preview.py`` - what you see is the converted
car (Roblox axes and size), not the original file.
"""
from __future__ import annotations

import base64
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ROBLOX_MAX_TRIANGLES, __version__
from . import drive
from . import preview as pv
from .export import sanitize
from .model import AxisSpec, ConversionError
from .pipeline import UNITS, Options, build_pieces, convert, load_model

try:  # tkinter is missing on some minimal Linux installs
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
except ImportError:  # pragma: no cover
    tk = None  # type: ignore

SOURCES = {"Detect automatically": "auto", "GTA San Andreas (.dff)": "gta_sa",
           "BeamNG (.zip / folder)": "beamng", "GTA V export / other (.obj .glb .dae)": "generic"}
UNIT_CHOICES = ["Auto"] + list(UNITS)
DRIVETRAINS = ["awd", "rwd", "fwd"]

# colours taken from the preview's dusk theme so the window and the 3D view belong together
PALETTE = {
    "bg": "#0b1022", "panel": "#131a35", "panel2": "#1a2347", "line": "#2d3b73", "field": "#0e1530",
    "text": "#e9edff", "muted": "#9aa7d8", "accent": "#6ea0ff", "on_accent": "#0b1022",
    "warm": "#ffaa78", "ok": "#7ee2a8", "bad": "#ff7a8a",
}

STAGE_W, STAGE_H = 560, 350            # the preview picture; follows the window size
HEADER_H = 108
DRAIN_MS = 40
PREVIEW_DELAY_MS = 400                 # wait this long after the last change before reloading the car
# settings that change what the car looks like: changing one reloads the preview
PREVIEW_KEYS = ("input", "source", "scale", "length", "unit", "axes", "flip", "paint", "paint2",
                "paint3", "glass", "interior", "lights", "lod", "add_tires", "new_tires", "max_tris",
                "target_tris", "vehicle", "config")
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# --------------------------------------------------------------------------- settings -> Options
def _num(text: str, kind, what: str, allow_empty: bool = True):
    t = (text or "").strip()
    if not t:
        if allow_empty:
            return None
        raise ConversionError(f"{what} is required")
    try:
        return kind(t)
    except ValueError:
        raise ConversionError(f"{what} must be a number (got '{text}')") from None


def _colour(text: object, what: str) -> Optional[str]:
    t = str(text or "").strip()
    if not t:
        return None
    if not _HEX.match(t):
        raise ConversionError(f"{what} must look like #RRGGBB (got '{t}')")
    return t if t.startswith("#") else "#" + t


def _axes(text: object) -> Optional[Tuple[str, str, str]]:
    t = str(text or "").strip().replace(";", ",").replace(" ", "")
    if not t:
        return None
    parts = [p.lower() for p in t.split(",")]
    if len(parts) != 3:
        raise ConversionError("Axes needs three values: right,up,forward (for example x,z,-y)")
    AxisSpec(parts[0], parts[1], parts[2]).validate()          # ConversionError with a clear message
    return (parts[0], parts[1], parts[2])


def options_from_values(v: Dict[str, object]) -> Tuple[str, str, Options]:
    """Turn the GUI's raw field values into (input, output folder, Options)."""
    inp = str(v.get("input", "")).strip()
    if not inp:
        raise ConversionError("choose a model file or folder first")
    out = str(v.get("output", "")).strip() or "roblox_out"
    fmts = tuple(f for f, on in (("glb", v.get("glb", True)), ("obj", v.get("obj", True))) if on)
    if not fmts:
        raise ConversionError("tick at least one output format (GLB or OBJ)")
    scale = _num(str(v.get("scale", "1")), float, "Scale") or 1.0
    if scale <= 0:
        raise ConversionError("Scale must be greater than zero")
    top_speed = _num(str(v.get("top_speed", "")), float, "Top speed")
    if top_speed is not None and not (0 < top_speed <= 1000):
        raise ConversionError("Top speed must be between 1 and 1000 km/h (leave it empty for the default)")
    drivetrain = str(v.get("drivetrain", "awd") or "awd").lower()
    if drivetrain not in DRIVETRAINS:
        raise ConversionError("Drivetrain must be awd, rwd or fwd")
    unit = str(v.get("unit", "") or "").strip()
    if unit.lower() in ("", "auto"):
        unit = ""
    elif unit not in UNITS:
        raise ConversionError(f"Units must be one of {', '.join(UNITS)} (or Auto)")
    return inp, out, Options(
        source=SOURCES.get(str(v.get("source", "")), str(v.get("source", "auto")) or "auto"),
        name=str(v.get("name", "")).strip() or None,
        scale=scale,
        length=_num(str(v.get("length", "")), float, "Length"),
        unit=unit or None,
        axes=_axes(v.get("axes")),
        flip_forward=bool(v.get("flip", False)),
        paint=_colour(v.get("paint"), "Primary paint"),
        paint2=_colour(v.get("paint2"), "Secondary paint"),
        paint3=_colour(v.get("paint3"), "Third paint"),
        include_lod=bool(v.get("lod", False)),
        include_glass=bool(v.get("glass", True)),
        include_interior=bool(v.get("interior", True)),
        include_lights=bool(v.get("lights", True)),
        max_tris_per_mesh=_num(str(v.get("max_tris", ROBLOX_MAX_TRIANGLES)), int, "Triangles per mesh",
                               False),
        target_tris=_num(str(v.get("target_tris", "")), int, "Triangle budget"),
        max_texture=_num(str(v.get("max_texture", "1024")), int, "Texture size", False),
        formats=fmts, rig=bool(v.get("rig", True)), add_tires=bool(v.get("add_tires", True)),
        new_tires=bool(v.get("new_tires", False)), top_speed=top_speed, drivetrain=drivetrain,
        advanced_chassis=bool(v.get("advanced_chassis", False)),
        achassis_real=bool(v.get("achassis_real", False)),
        txd=tuple(v.get("txd", ()) or ()),                      # type: ignore[arg-type]
        vehicle=str(v.get("vehicle", "") or "").strip() or None,
        config=str(v.get("config", "") or "").strip() or None,
        strip_links=bool(v.get("strip_links", True)))


# --------------------------------------------------------------------------- preview data (no Tk)
def build_preview(path: str, opts: Options, max_tris: int = 14000, log=lambda s: None):
    """Load and build the car exactly like ``convert`` does, but write nothing.

    Returns ``(scene, info)``: a ``preview.Scene`` (with wheels, for the test drive) and a dict with
    name, source, size (studs), piece / triangle / wheel counts, the wheel slots and the warnings.
    """
    warnings: List[str] = []
    model = load_model(path, opts, log)
    warnings.extend(model.notes)
    pieces, wheels, size = build_pieces(model, opts, log, warnings)
    scene = pv.scene_from_build(pieces, wheels, model.textures, size, max_tris=max_tris)
    info = {"name": sanitize(opts.name or model.name, "Car"), "source": model.source,
            "size": tuple(float(x) for x in size), "pieces": len(pieces),
            "triangles": int(sum(p.mesh.tri_count for p in pieces)), "wheels": len(wheels),
            "wheel_slots": [w.slot for w in wheels], "warnings": warnings}
    return scene, info


def summary_line(info: Dict[str, Any]) -> str:
    w, h, ln = info["size"]
    wheels = info.get("wheels", 0)
    return (f"{info['name']}  -  {info['source']}  -  {w:.1f} x {h:.1f} x {ln:.1f} studs "
            f"({ln * 0.28:.1f} m long)  -  {info['pieces']} pieces, {info['triangles']:,} triangles, "
            f"{wheels} wheel{'' if wheels == 1 else 's'}")


_WHEELISH = re.compile(r"tyre|tire|wheel|rim", re.I)


def note_lines(warnings: List[str]) -> List[Tuple[str, str]]:
    """[(tag, text)] for the notes panel. Tags: 'safety' (removed content), 'wheel', 'warn'."""
    out = []
    for w in warnings:
        tag = "safety" if w.startswith("Safety") else ("wheel" if _WHEELISH.search(w) else "warn")
        out.append((tag, w))
    return out


def thumb_png(path: Path, width: int = 230) -> Optional[bytes]:
    """The converted preview.png shrunk to a thumbnail (PNG bytes), or None if it cannot be read."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        img.thumbnail((width, width * 2))
        return pv.png_bytes(img)
    except Exception:  # noqa: BLE001 - a missing thumbnail is not worth an error
        return None


def _open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def _photo(png: bytes):
    return tk.PhotoImage(data=base64.b64encode(png))


# --------------------------------------------------------------------------- the window
class ConverterApp:
    def __init__(self, root: "tk.Tk") -> None:
        self.root = root
        self.title_text = "Car2Roblox"
        self.version_text = f"v{__version__}"
        self.window_title = f"Car2Roblox {__version__} - car mod converter for Roblox Studio"
        root.title(self.window_title)
        root.minsize(1020, 700)
        try:
            root.geometry("1200x780")
        except Exception:  # noqa: BLE001
            pass
        root.configure(bg=PALETTE["bg"])

        self.q: "queue.Queue[str]" = queue.Queue()          # log lines + done/fail markers
        self.pq: "queue.Queue[tuple]" = queue.Queue()       # preview events (frames, scenes, results)
        self.worker: Optional[threading.Thread] = None
        self.last_out: Optional[Path] = None
        self.last_result: Any = None
        self.txd_files: list = []
        self._imgs: Dict[str, Any] = {}                     # keep PhotoImages alive

        # preview state
        self.scene: Optional[pv.Scene] = None
        self.scene_info: Optional[Dict[str, Any]] = None
        self.scene_opts: Optional[Options] = None
        self.yaw, self.pitch, self.zoom = 2.55, 0.26, 1.0
        self.pv_theme = "dusk"
        self.spinning = False
        self.driving = False
        self.car = drive.reset()
        self.tuning = drive.Tuning()
        self.keys: set = set()
        self.frame_png: Optional[bytes] = None
        self.frames_shown = 0
        self.stage_w, self.stage_h = STAGE_W, STAGE_H
        self._pv_job: Any = None
        self._pv_gen = 0
        self._load_thread: Optional[threading.Thread] = None
        self._load_dirty = False
        self._render_thread: Optional[threading.Thread] = None
        self._render_pending: Optional[Dict[str, Any]] = None
        self._drag: Optional[Tuple[int, int]] = None
        self._resize_job: Any = None
        self._drive_job: Any = None
        self._spin_job: Any = None
        self._last_t = 0.0
        self.closing = False

        s, b = tk.StringVar, tk.BooleanVar
        self.v = {
            "input": s(), "output": s(value=str(Path.home() / "Car2Roblox_output")),
            "source": s(value="Detect automatically"), "name": s(), "vehicle": s(), "config": s(),
            "scale": s(value="1.0"), "length": s(), "unit": s(value="Auto"), "axes": s(),
            "paint": s(value="#2F6DB5"), "paint2": s(), "paint3": s(),
            "max_tris": s(value=str(ROBLOX_MAX_TRIANGLES)), "target_tris": s(),
            "max_texture": s(value="1024"), "drivetrain": s(value="awd"), "top_speed": s(),
            "glb": b(value=True), "obj": b(value=True), "rig": b(value=True), "add_tires": b(value=True), "new_tires": b(value=False),
            "glass": b(value=True), "interior": b(value=True), "lights": b(value=True),
            "lod": b(value=False), "flip": b(value=False), "advanced_chassis": b(value=False),
            "achassis_real": b(value=False), "strip_links": b(value=True),
        }

        self._build_style()
        self.bg_label = tk.Label(root, bd=0, bg=PALETTE["bg"])
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self.bg_label.lower()
        self._build_header()
        self._build_tabs()
        self._build_stage()
        self._build_result()
        self._build_bar()
        root.columnconfigure(0, weight=0, minsize=470)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=3)
        root.rowconfigure(2, weight=2)

        for key in PREVIEW_KEYS:
            self.v[key].trace_add("write", self._settings_changed)
        self.v["rig"].trace_add("write", self.update_drive_state)
        self.update_drive_state()
        root.bind("<KeyPress>", self._key_down)
        root.bind("<KeyRelease>", self._key_up)
        root.bind("<Configure>", self._window_resized)
        try:
            root.protocol("WM_DELETE_WINDOW", self.close)
        except Exception:  # noqa: BLE001
            pass

        self.write("Pick a GTA SA .dff, a BeamNG .zip/folder, or an OBJ/GLB/DAE export (e.g. GTA V "
                   "cars exported with CodeWalker/Blender). The preview shows the converted car; press "
                   "Convert to write the files.")
        self._rebuild_art(1200, 780)
        self.request_render("high")
        self.root.after(DRAIN_MS, self.drain)

    # ---- building the window
    def _build_style(self) -> None:
        P = PALETTE
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:  # noqa: BLE001 - keep the platform theme if clam is unavailable
            pass
        font = ("Segoe UI", 10)
        st.configure(".", background=P["panel"], foreground=P["text"], fieldbackground=P["field"],
                     bordercolor=P["line"], lightcolor=P["panel"], darkcolor=P["panel"],
                     troughcolor=P["field"], focuscolor=P["accent"], font=font)
        st.configure("Card.TFrame", background=P["panel"], bordercolor=P["line"], borderwidth=1,
                     relief="solid")
        st.configure("Inner.TFrame", background=P["panel"])
        st.configure("TLabel", background=P["panel"], foreground=P["text"])
        st.configure("Muted.TLabel", background=P["panel"], foreground=P["muted"], font=("Segoe UI", 9))
        st.configure("Head.TLabel", background=P["panel"], foreground=P["warm"], font=("Segoe UI", 11, "bold"))
        st.configure("Summary.TLabel", background=P["panel"], foreground=P["text"], font=("Segoe UI", 10, "bold"))
        st.configure("TNotebook", background=P["panel"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=P["panel2"], foreground=P["muted"], padding=(14, 7),
                     borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", P["panel"]), ("active", P["line"])],
               foreground=[("selected", P["text"])])
        st.configure("TEntry", fieldbackground=P["field"], foreground=P["text"], insertcolor=P["text"],
                     bordercolor=P["line"], padding=4)
        st.map("TEntry", fieldbackground=[("disabled", P["panel2"])], foreground=[("disabled", P["muted"])])
        st.configure("TCombobox", fieldbackground=P["field"], foreground=P["text"], background=P["panel2"],
                     arrowcolor=P["text"], bordercolor=P["line"], padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", P["field"]), ("disabled", P["panel2"])],
               foreground=[("disabled", P["muted"])], arrowcolor=[("disabled", P["muted"])])
        st.configure("TCheckbutton", background=P["panel"], foreground=P["text"], indicatorcolor=P["field"])
        st.map("TCheckbutton", background=[("active", P["panel"])], foreground=[("disabled", P["muted"])],
               indicatorcolor=[("selected", P["accent"]), ("pressed", P["accent"])])
        st.configure("TButton", background=P["panel2"], foreground=P["text"], bordercolor=P["line"],
                     padding=(10, 5), focuscolor=P["panel2"])
        st.map("TButton", background=[("active", P["line"]), ("disabled", P["panel"])],
               foreground=[("disabled", P["muted"])])
        st.configure("Accent.TButton", background=P["accent"], foreground=P["on_accent"],
                     font=("Segoe UI", 11, "bold"), padding=(22, 8), bordercolor=P["accent"])
        st.map("Accent.TButton", background=[("active", "#8db5ff"), ("disabled", P["line"])],
               foreground=[("disabled", P["muted"])])
        st.configure("TScrollbar", background=P["panel2"], troughcolor=P["field"], bordercolor=P["line"],
                     arrowcolor=P["muted"])
        st.configure("Horizontal.TProgressbar", background=P["accent"], troughcolor=P["field"],
                     bordercolor=P["line"])
        self.root.option_add("*TCombobox*Listbox.background", P["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", P["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", P["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", P["on_accent"])

    def _build_header(self) -> None:
        P = PALETTE
        self.header = tk.Canvas(self.root, height=HEADER_H, highlightthickness=0, bd=0, bg=P["bg"])
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.header_img = self.header.create_image(0, 0, anchor="nw")
        self.header.create_text(28, 38, anchor="w", text=self.title_text, fill="#ffffff",
                                font=("Segoe UI", 28, "bold"))
        self.header.create_text(30, 76, anchor="w", fill="#d6defc", font=("Segoe UI", 11),
                                text="GTA San Andreas, GTA V and BeamNG cars  >  drivable Roblox cars")
        self.header_chip = self.header.create_rectangle(190, 24, 190 + 64, 24 + 24, outline=P["warm"],
                                                        fill=P["bg"])
        self.header.create_text(190 + 32, 36, text=self.version_text, fill=P["warm"],
                                font=("Segoe UI", 10, "bold"))

    def _card(self, **grid: Any) -> Any:
        f = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        f.grid(**grid)
        return f

    def _row(self, parent: Any, r: int, label: str, key: str, width: int = 0, hint: str = "") -> Any:
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=4)
        e = ttk.Entry(parent, textvariable=self.v[key], width=width or 24)
        e.grid(row=r, column=1, sticky="ew", pady=4)
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=r, column=2, sticky="w", padx=8)
        return e

    def _build_tabs(self) -> None:
        card = self._card(row=1, column=0, rowspan=2, sticky="nsew", padx=(14, 7), pady=(12, 6))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(card)
        self.tabs.grid(row=0, column=0, sticky="nsew")

        def tab(title: str) -> Any:
            f = ttk.Frame(self.tabs, style="Inner.TFrame", padding=12)
            f.columnconfigure(1, weight=1)
            self.tabs.add(f, text=title)
            return f

        # -- Input
        t = tab("Input")
        ttk.Label(t, text="Model").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(t, textvariable=self.v["input"]).grid(row=0, column=1, sticky="ew", pady=4)
        bf = ttk.Frame(t, style="Inner.TFrame")
        bf.grid(row=0, column=2, sticky="e", padx=(6, 0))
        ttk.Button(bf, text="File...", command=self.pick_file).pack(side="left", padx=2)
        ttk.Button(bf, text="Folder...", command=self.pick_folder).pack(side="left", padx=2)
        ttk.Label(t, text="Input type").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(t, textvariable=self.v["source"], values=list(SOURCES), state="readonly"
                     ).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(t, text="Extra textures\n(GTA SA .txd)").grid(row=2, column=0, sticky="nw", pady=4)
        self.txd_list = tk.Listbox(t, height=3, bg=PALETTE["field"], fg=PALETTE["text"], relief="flat",
                                   highlightthickness=1, highlightbackground=PALETTE["line"],
                                   selectbackground=PALETTE["accent"], selectforeground=PALETTE["on_accent"])
        self.txd_list.grid(row=2, column=1, sticky="ew", pady=4)
        tf = ttk.Frame(t, style="Inner.TFrame")
        tf.grid(row=2, column=2, sticky="ne", padx=(6, 0))
        ttk.Button(tf, text="Add...", command=self.add_txd).pack(anchor="e", pady=1)
        ttk.Button(tf, text="Remove", command=self.remove_txd).pack(anchor="e", pady=1)
        self._row(t, 3, "BeamNG vehicle", "vehicle", hint="if the zip holds several")
        self._row(t, 4, "BeamNG config", "config", hint=".pc name, optional")
        ttk.Label(t, text="Output folder").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(t, textvariable=self.v["output"]).grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Button(t, text="Browse...", command=self.pick_output).grid(row=5, column=2, sticky="e",
                                                                         padx=(6, 0))
        self._row(t, 6, "Car name", "name", hint="default: file name")

        # -- Look & size
        t = tab("Look & size")
        self._row(t, 0, "Scale", "scale", 8, "1.0 = real size")
        self._row(t, 1, "or length (studs)", "length", 8, "overrides scale")
        ttk.Label(t, text="Units").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(t, textvariable=self.v["unit"], values=UNIT_CHOICES, state="readonly", width=8
                     ).grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(t, text="OBJ / GLB / DAE only", style="Muted.TLabel").grid(row=2, column=2, sticky="w", padx=8)
        self._row(t, 3, "Axes", "axes", 12, "right,up,forward  e.g. x,z,-y")
        ttk.Checkbutton(t, text="Flip front / back (turn the car around)", variable=self.v["flip"]
                        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(t, text="Paint", style="Head.TLabel").grid(row=5, column=0, columnspan=3, sticky="w",
                                                              pady=(10, 2))
        for i, (lab, key) in enumerate((("Primary", "paint"), ("Secondary", "paint2"), ("Third", "paint3"))):
            ttk.Label(t, text=lab).grid(row=6 + i, column=0, sticky="w", pady=4)
            ttk.Entry(t, textvariable=self.v[key], width=10).grid(row=6 + i, column=1, sticky="w", pady=4)
            ttk.Button(t, text="Pick...", command=lambda k=key: self.pick_color(k)
                       ).grid(row=6 + i, column=2, sticky="w", padx=8)

        # -- Car
        t = tab("Car")
        ttk.Label(t, text="Parts", style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        for i, (lab, key) in enumerate((("Windows", "glass"), ("Interior", "interior"), ("Lights", "lights"),
                                        ("Include LOD meshes", "lod"))):
            ttk.Checkbutton(t, text=lab, variable=self.v[key]).grid(row=1 + i // 2, column=i % 2,
                                                                    sticky="w", pady=3, padx=(0, 18))
        ttk.Label(t, text="Limits", style="Head.TLabel").grid(row=3, column=0, columnspan=3, sticky="w",
                                                               pady=(14, 0))
        self._row(t, 4, "Triangles / mesh", "max_tris", 8, f"Roblox: {ROBLOX_MAX_TRIANGLES:,}")
        self._row(t, 5, "Total budget", "target_tris", 8, "optional, shrinks the car")
        self._row(t, 6, "Max texture px", "max_texture", 8, "1024 is plenty")
        ttk.Label(t, text="Files", style="Head.TLabel").grid(row=7, column=0, columnspan=3, sticky="w",
                                                              pady=(14, 0))
        ttk.Checkbutton(t, text="GLB (recommended)", variable=self.v["glb"]).grid(row=8, column=0, sticky="w", pady=3)
        ttk.Checkbutton(t, text="OBJ pieces", variable=self.v["obj"]).grid(row=8, column=1, sticky="w", pady=3)
        ttk.Label(t, text="Names", style="Head.TLabel").grid(row=9, column=0, columnspan=3, sticky="w",
                                                              pady=(14, 0))
        ttk.Checkbutton(t, text="Strip links (URLs) out of part/material/texture names",
                        variable=self.v["strip_links"]).grid(row=10, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Deleting a part/material/texture whose name carries an adult-content marker, "
                       "and refusing a risky file (.exe, a disguised shortcut, ...) found in a mod "
                       "archive, always run - that part of the safety filter cannot be turned off."
                  ).grid(row=11, column=0, columnspan=3, sticky="w")

        # -- Drive
        t = tab("Drive")
        ttk.Checkbutton(t, text="Make the car drivable in Roblox Studio", variable=self.v["rig"]
                        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 2))
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Writes a setup script that adds a driver seat, wheel motors, steering and "
                       "a driving script. Paste it into Studio's Command Bar after importing."
                  ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(t, text="Drivetrain").grid(row=2, column=0, sticky="w", pady=4)
        self.cb_drivetrain = ttk.Combobox(t, textvariable=self.v["drivetrain"], values=DRIVETRAINS,
                                          state="readonly", width=8)
        self.cb_drivetrain.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(t, text="Top speed (km/h)").grid(row=3, column=0, sticky="w", pady=4)
        self.en_speed = ttk.Entry(t, textvariable=self.v["top_speed"], width=8)
        self.en_speed.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(t, text="empty = about 91", style="Muted.TLabel").grid(row=3, column=2, sticky="w", padx=8)
        ttk.Label(t, text="Suspension & drivetrain", style="Head.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(14, 2))
        self.ck_a_chassis = ttk.Checkbutton(t, text="A-Chassis style: real suspension + RPM/torque drivetrain",
                                            variable=self.v["advanced_chassis"])
        self.ck_a_chassis.grid(row=5, column=0, columnspan=3, sticky="w")
        self._drive_controls = [(self.cb_drivetrain, "readonly"), (self.en_speed, "normal"),
                                 (self.ck_a_chassis, "normal")]
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Rigs each wheel with its own spring-loaded suspension upright (and a "
                       "steering knuckle on the front wheels) instead of a fixed hinge, and drives "
                       "it with a simulated engine - RPM, a torque curve and gears - in the spirit "
                       "of the community 'A-Chassis' rigs. Leave this off for the simpler, lighter "
                       "fixed-hinge rig."
                  ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(2, 10))
        self.ck_a_chassis_real = ttk.Checkbutton(
            t, text="Real A-Chassis (by lisphm, MPL-2.0) - the actual community chassis kit",
            variable=self.v["achassis_real"])
        self.ck_a_chassis_real.grid(row=7, column=0, columnspan=3, sticky="w")
        self._drive_controls.append((self.ck_a_chassis_real, "normal"))
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Bundles the real, widely-used github.com/lisphm/A-Chassis scripts (used by "
                       "hundreds of Roblox experiences) verbatim, with its own on-screen dashboard "
                       "and controls, instead of either rig above. Needs exactly one front axle and "
                       "one rear axle (a left and right wheel on each) - takes over from the option "
                       "above when both are ticked."
                  ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 10))

        def _achassis_real_toggled(*_a):
            if self.v["achassis_real"].get():
                self.v["advanced_chassis"].set(False)
        self.v["achassis_real"].trace_add("write", _achassis_real_toggled)

        ttk.Label(t, text="Tires", style="Head.TLabel").grid(row=9, column=0, columnspan=3, sticky="w",
                                                              pady=(4, 2))
        ttk.Checkbutton(t, text="Add tires if the model has none", variable=self.v["add_tires"]
                        ).grid(row=10, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(t, text="Always use new tires (keep the wheel positions, replace the look)",
                        variable=self.v["new_tires"]).grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Many mods (BeamNG especially) arrive without wheels, with the wheels "
                       "left in the middle of the car, or with only some of them. The converter "
                       "moves, copies or builds the missing wheels. If a mod's own wheels still "
                       "look wrong, tick 'Always use new tires'. Turn 'Add tires' off if the body "
                       "already has its wheels built in."
                  ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(t, style="Muted.TLabel", justify="left", wraplength=380,
                  text="Use 'Test drive' under the preview to try steering and wheel placement "
                       "before importing. It is a simple preview, not Roblox physics."
                  ).grid(row=13, column=0, columnspan=3, sticky="w")

    def _build_stage(self) -> None:
        P = PALETTE
        card = self._card(row=1, column=1, sticky="nsew", padx=(7, 14), pady=(12, 6))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        self.stage = tk.Canvas(card, width=STAGE_W, height=STAGE_H, highlightthickness=0, bd=0,
                               bg=P["bg"], takefocus=1)
        self.stage.grid(row=0, column=0, sticky="nsew")
        self.stage_img = self.stage.create_image(0, 0, anchor="nw")
        self.stage_hint = self.stage.create_text(
            STAGE_W // 2, 40, text="Choose a car to see it here", fill="#ffffff", font=("Segoe UI", 13, "bold"))
        self.hud = self.stage.create_text(14, 12, anchor="nw", text="", fill="#ffffff",
                                          font=("Segoe UI", 13, "bold"))
        self.stage.bind("<ButtonPress-1>", self._drag_start)
        self.stage.bind("<B1-Motion>", self._drag_move)
        self.stage.bind("<ButtonRelease-1>", self._drag_end)
        self.stage.bind("<MouseWheel>", self._wheel)
        self.stage.bind("<Button-4>", self._wheel)
        self.stage.bind("<Button-5>", self._wheel)
        self.stage.bind("<Configure>", self._stage_resized)

        bar = ttk.Frame(card, style="Inner.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.spin_btn = ttk.Button(bar, text="Spin", command=self.toggle_spin)
        self.spin_btn.pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Reset view", command=self.reset_view).pack(side="left", padx=4)
        self.theme_btn = ttk.Button(bar, text="Day view", command=self.toggle_theme)
        self.theme_btn.pack(side="left", padx=4)
        self.drive_btn = ttk.Button(bar, text="Test drive", command=self.toggle_drive, state="disabled")
        self.drive_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Save picture...", command=self.save_picture).pack(side="left", padx=4)
        self.pv_status = ttk.Label(bar, text="Drag to turn, scroll to zoom", style="Muted.TLabel")
        self.pv_status.pack(side="right")

    def _build_result(self) -> None:
        P = PALETTE
        card = self._card(row=2, column=1, sticky="nsew", padx=(7, 14), pady=(6, 6))
        card.columnconfigure(1, weight=1)
        card.rowconfigure(1, weight=1)
        self.summary = ttk.Label(card, text="No car loaded yet", style="Summary.TLabel")
        self.summary.grid(row=0, column=0, columnspan=2, sticky="w")
        self.thumb = ttk.Label(card, text="", style="Muted.TLabel")
        self.thumb.grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        nb = ttk.Notebook(card)
        nb.grid(row=1, column=1, sticky="nsew", pady=(6, 0))

        def text_tab(title: str) -> Any:
            f = ttk.Frame(nb, style="Inner.TFrame")
            f.columnconfigure(0, weight=1)
            f.rowconfigure(0, weight=1)
            nb.add(f, text=title)
            t = tk.Text(f, height=6, wrap="word", state="disabled", bg=P["field"], fg=P["text"], relief="flat",
                        highlightthickness=0, insertbackground=P["text"], padx=8, pady=6,
                        font=("Consolas", 9))
            t.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(f, command=t.yview)
            sb.grid(row=0, column=1, sticky="ns")
            t.configure(yscrollcommand=sb.set)
            return t

        self.notes = text_tab("Notes")
        self.log = text_tab("Log")
        self.notes.tag_configure("safety", foreground=P["bad"])
        self.notes.tag_configure("wheel", foreground=P["warm"])
        self.notes.tag_configure("warn", foreground=P["muted"])
        self.notes.tag_configure("ok", foreground=P["ok"])

    def _build_bar(self) -> None:
        bar = ttk.Frame(self.root, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 14))
        bar.columnconfigure(3, weight=1)
        self.go = ttk.Button(bar, text="Convert", command=self.start, style="Accent.TButton")
        self.go.grid(row=0, column=0)
        self.openbtn = ttk.Button(bar, text="Open output folder", command=self.open_out, state="disabled")
        self.openbtn.grid(row=0, column=1, padx=10)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self.progress.grid(row=0, column=2, padx=6)
        self.status = ttk.Label(bar, text="Ready")
        self.status.grid(row=0, column=3, sticky="w", padx=8)
        self.version_label = ttk.Label(bar, text=f"Car2Roblox {__version__}", style="Muted.TLabel")
        self.version_label.grid(row=0, column=4, sticky="e")

    # ---- artwork
    def _rebuild_art(self, width: int, height: int) -> None:
        """Banner and window background for the current window size."""
        width, height = max(200, int(width)), max(200, int(height))
        try:
            self._imgs["banner"] = _photo(pv.png_bytes(pv.banner(width, HEADER_H)))
            self.header.itemconfigure(self.header_img, image=self._imgs["banner"])
            self._imgs["backdrop"] = _photo(pv.png_bytes(pv.window_backdrop(width, height)))
            self.bg_label.configure(image=self._imgs["backdrop"])
        except Exception:  # noqa: BLE001 - artwork is decoration; the window works without it
            pass

    def _window_resized(self, event: Any) -> None:
        if getattr(event, "widget", self.root) is not self.root:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        w, h = int(event.width), int(event.height)
        self._resize_job = self.root.after(250, lambda: self._rebuild_art(w, h))

    # ---- pickers
    def pick_file(self) -> None:
        p = filedialog.askopenfilename(title="Choose a car model", filetypes=[
            ("Car models", "*.dff *.zip *.obj *.glb *.gltf *.dae"), ("All files", "*.*")])
        if p:
            self.v["input"].set(p)

    def pick_folder(self) -> None:
        p = filedialog.askdirectory(title="Choose a car folder")
        if p:
            self.v["input"].set(p)

    def pick_output(self) -> None:
        p = filedialog.askdirectory(title="Choose the output folder")
        if p:
            self.v["output"].set(p)

    def add_txd(self) -> None:
        for p in filedialog.askopenfilenames(title="Choose .txd files", filetypes=[("TXD", "*.txd")]):
            if p not in self.txd_files:
                self.txd_files.append(p)
                self.txd_list.insert("end", p)
        self.schedule_preview()

    def remove_txd(self) -> None:
        for i in reversed(self.txd_list.curselection()):
            self.txd_list.delete(i)
            del self.txd_files[i]
        self.schedule_preview()

    def pick_color(self, key: str = "paint") -> None:
        c = colorchooser.askcolor(title="Paint colour")
        if c and c[1]:
            self.v[key].set(c[1].upper())

    def open_out(self) -> None:
        if self.last_out:
            _open_folder(self.last_out)

    def save_picture(self) -> None:
        if not self.frame_png:
            return
        p = filedialog.asksaveasfilename(title="Save the preview picture", defaultextension=".png",
                                         filetypes=[("PNG picture", "*.png")])
        if p:
            Path(p).write_bytes(self.frame_png)
            self.pv_status.configure(text=f"Saved {Path(p).name}")

    # ---- drive options
    def update_drive_state(self, *_: Any) -> None:
        """Drivetrain and top speed only matter when the car is made drivable."""
        on = bool(self.v["rig"].get())
        for widget, enabled_state in self._drive_controls:
            widget.configure(state=enabled_state if on else "disabled")

    # ---- log / notes
    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def show_notes(self, lines: List[Tuple[str, str]]) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        for tag, text in lines:
            self.notes.insert("end", text + "\n", tag)
        self.notes.configure(state="disabled")

    # ---- event pump
    def drain(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                if msg.startswith("\0done:"):
                    self.finish(msg[6:])
                elif msg.startswith("\0fail:"):
                    self.finish(None, msg[6:])
                else:
                    self.write(msg)
        except queue.Empty:
            pass
        try:
            while True:
                self._handle_event(self.pq.get_nowait())
        except queue.Empty:
            pass
        self._pump_render()
        if self._load_dirty and not (self._load_thread and self._load_thread.is_alive()):
            self._load_dirty = False
            self._start_preview()
        if not self.closing:
            self.root.after(DRAIN_MS, self.drain)

    def _handle_event(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "frame":
            self._show_frame(ev[1])
        elif kind == "scene":
            _, gen, scene, info, opts = ev
            if gen != self._pv_gen:
                return                                  # an older load that was overtaken
            self.scene, self.scene_info, self.scene_opts = scene, info, opts
            self.summary.configure(text=summary_line(info))
            self.show_notes(note_lines(info["warnings"]) or [("ok", "Nothing to report.")])
            self.tuning = drive.tuning_for(scene.wheel_hubs.tolist(), scene.wheel_front.tolist(),
                                           scene.size[2], opts.top_speed)
            self.drive_btn.configure(state="normal")
            self.stage.itemconfigure(self.stage_hint, text="")
            self.pv_status.configure(text="Drag to turn, scroll to zoom")
            self.request_render("high")
        elif kind == "scene_fail":
            _, gen, msg = ev
            if gen != self._pv_gen:
                return
            self.scene, self.scene_info = None, None
            self.drive_btn.configure(state="disabled")
            self.summary.configure(text="The preview could not be built")
            self.show_notes([("safety", msg)])
            self.stage.itemconfigure(self.stage_hint, text="This file could not be previewed")
            self.pv_status.configure(text="Preview failed - see Notes")
            self.request_render("high")
        elif kind == "result":
            self._show_result(ev[1])

    def _show_frame(self, png: bytes) -> None:
        self.frame_png = png
        self.frames_shown += 1
        self._imgs["frame"] = _photo(png)
        self.stage.itemconfigure(self.stage_img, image=self._imgs["frame"])

    def _show_result(self, res: Any) -> None:
        self.last_result = res
        w, h, ln = res.size_studs
        self.summary.configure(
            text=f"Converted: {res.pieces} pieces, {res.triangles:,} triangles, {res.wheels} wheels  -  "
                 f"{w:.1f} x {h:.1f} x {ln:.1f} studs")
        self.show_notes(note_lines(list(res.warnings)) or [("ok", "Converted without warnings.")])
        if res.preview:
            png = thumb_png(Path(res.preview))
            if png:
                self._imgs["thumb"] = _photo(png)
                self.thumb.configure(image=self._imgs["thumb"])

    # ---- loading the car for the preview
    def _settings_changed(self, *_: Any) -> None:
        self.schedule_preview()

    def schedule_preview(self, delay: int = PREVIEW_DELAY_MS) -> None:
        """Reload the preview shortly after the last change (typing a number fires many)."""
        if self.closing:
            return
        if self._pv_job is not None:
            self.root.after_cancel(self._pv_job)
        self._pv_job = self.root.after(delay, self._start_preview)

    def _start_preview(self) -> None:
        self._pv_job = None
        if self._load_thread and self._load_thread.is_alive():
            self._load_dirty = True                     # run again once the current load is done
            return
        inp = str(self.v["input"].get()).strip()
        self._pv_gen += 1
        gen = self._pv_gen
        if not inp:
            self.scene = self.scene_info = self.scene_opts = None
            self.drive_btn.configure(state="disabled")
            self.summary.configure(text="No car loaded yet")
            self.stage.itemconfigure(self.stage_hint, text="Choose a car to see it here")
            self.stop_drive()
            self.request_render("high")
            return
        vals = self.values()
        vals["glb"] = vals["obj"] = True                # unticking output formats must not hide the preview
        try:
            _, _, opts = options_from_values(vals)
        except ConversionError as exc:
            self.pv_status.configure(text=f"Preview waiting: {exc}")
            return
        self.pv_status.configure(text="Loading the car...")

        def work() -> None:
            try:
                scene, info = build_preview(inp, opts)
                self.pq.put(("scene", gen, scene, info, opts))
            except ConversionError as exc:
                self.pq.put(("scene_fail", gen, str(exc)))
            except Exception:  # noqa: BLE001 - show anything unexpected in the notes
                self.pq.put(("scene_fail", gen, "Unexpected error:\n" + traceback.format_exc()))

        self._load_thread = threading.Thread(target=work, daemon=True)
        self._load_thread.start()

    # ---- drawing the preview (worker thread, newest request wins)
    def request_render(self, quality: str = "high") -> None:
        """Ask for a new picture. ``low`` is quick (no anti-aliasing) for dragging and driving."""
        pose = self.car if self.driving else None
        camera = drive.chase_camera(self.car, self.scene.size) if (self.driving and self.scene) else None
        self._render_pending = {
            "scene": self.scene, "width": self.stage_w, "height": self.stage_h, "yaw": self.yaw,
            "pitch": self.pitch, "zoom": self.zoom, "theme": self.pv_theme,
            "ssaa": 2 if quality == "high" else 1, "arrow": True, "pose": pose, "camera": camera}
        self._pump_render()

    def _pump_render(self) -> None:
        if self._render_thread and self._render_thread.is_alive():
            return
        p = self._render_pending
        if p is None:
            return
        self._render_pending = None
        self._render_thread = threading.Thread(target=self._render_job, args=(p,), daemon=True)
        self._render_thread.start()

    def _render_job(self, p: Dict[str, Any]) -> None:
        try:
            img = pv.render(p["scene"], p["width"], p["height"], yaw=p["yaw"], pitch=p["pitch"],
                            zoom=p["zoom"], theme=p["theme"], ssaa=p["ssaa"], arrow=p["arrow"],
                            pose=p["pose"], camera=p["camera"])
            self.pq.put(("frame", pv.png_bytes(img)))
        except Exception:  # noqa: BLE001 - a failed frame just keeps the previous picture
            pass

    # ---- mouse: turn and zoom
    def _drag_start(self, event: Any) -> None:
        self._drag = (int(event.x), int(event.y))
        self.spinning = False
        self.spin_btn.configure(text="Spin")
        if self.stage is not None:
            self.stage.focus_set()

    def _drag_move(self, event: Any) -> None:
        if self._drag is None or self.driving:
            return
        dx, dy = int(event.x) - self._drag[0], int(event.y) - self._drag[1]
        self._drag = (int(event.x), int(event.y))
        self.yaw -= dx * 0.01
        self.pitch = max(0.02, min(1.25, self.pitch + dy * 0.008))
        self.request_render("low")

    def _drag_end(self, _event: Any = None) -> None:
        if self._drag is not None and not self.driving:
            self.request_render("high")
        self._drag = None

    def _wheel(self, event: Any) -> None:
        if self.driving:
            return
        delta = getattr(event, "delta", 0)
        num = getattr(event, "num", 0)
        step = 1 if (delta > 0 or num == 4) else (-1 if (delta < 0 or num == 5) else 0)
        if not step:
            return
        self.zoom = max(0.4, min(4.0, self.zoom * (1.1 ** step)))
        self.request_render("high")

    def _stage_resized(self, event: Any) -> None:
        w, h = int(event.width), int(event.height)
        if w < 120 or h < 90 or (w, h) == (self.stage_w, self.stage_h):
            return
        self.stage_w, self.stage_h = min(w, 960), min(h, 640)
        self.stage.coords(self.stage_hint, self.stage_w // 2, 40)
        self.request_render("high")

    # ---- view buttons
    def toggle_spin(self) -> None:
        if self.driving:
            return
        self.spinning = not self.spinning
        self.spin_btn.configure(text="Stop" if self.spinning else "Spin")
        if self.spinning:
            self._spin_tick()
        else:
            self.request_render("high")

    def _spin_tick(self) -> None:
        if not self.spinning or self.driving or self.closing:
            return
        self.yaw += 0.035
        self.request_render("low")
        self._spin_job = self.root.after(70, self._spin_tick)

    def reset_view(self) -> None:
        self.yaw, self.pitch, self.zoom = 2.55, 0.26, 1.0
        self.spinning = False
        self.spin_btn.configure(text="Spin")
        self.request_render("high")

    def toggle_theme(self) -> None:
        self.pv_theme = "day" if self.pv_theme == "dusk" else "dusk"
        self.theme_btn.configure(text="Dusk view" if self.pv_theme == "day" else "Day view")
        self.request_render("high")

    # ---- test drive
    def toggle_drive(self) -> None:
        if self.driving:
            self.stop_drive()
        else:
            self.start_drive()

    def start_drive(self) -> None:
        if self.scene is None:
            return
        self.spinning = False
        self.spin_btn.configure(text="Spin")
        self.driving = True
        self.car = drive.reset()
        self.keys = set()
        self.drive_btn.configure(text="Stop driving (Esc)")
        self.pv_status.configure(text=drive.keys_text())
        self.stage.focus_set()
        self._last_t = time.monotonic()
        self._drive_tick()

    def stop_drive(self) -> None:
        if not self.driving:
            return
        self.driving = False
        self.keys = set()
        if self._drive_job is not None:
            self.root.after_cancel(self._drive_job)
            self._drive_job = None
        self.stage.itemconfigure(self.hud, text="")
        self.drive_btn.configure(text="Test drive")
        self.pv_status.configure(text="Drag to turn, scroll to zoom")
        self.request_render("high")

    def _drive_tick(self) -> None:
        if not self.driving or self.closing:
            return
        now = time.monotonic()
        dt, self._last_t = now - self._last_t, now
        self.car = drive.step(self.car, drive.inputs_from_keys(self.keys), dt, self.tuning)
        self.stage.itemconfigure(self.hud, text=f"{self.car.kmh:3.0f} km/h")
        self.request_render("low")
        self._drive_job = self.root.after(33, self._drive_tick)

    def _key_down(self, event: Any) -> None:
        if not self.driving:
            return
        k = str(getattr(event, "keysym", ""))
        if k == "Escape":
            self.stop_drive()
        elif k.lower() == "r":
            self.car = drive.reset()
        else:
            self.keys.add(k)

    def _key_up(self, event: Any) -> None:
        self.keys.discard(str(getattr(event, "keysym", "")))

    # ---- converting
    def values(self) -> Dict[str, object]:
        d: Dict[str, object] = {k: var.get() for k, var in self.v.items()}
        d["txd"] = tuple(self.txd_files)
        return d

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            inp, out, opts = options_from_values(self.values())
        except ConversionError as exc:
            messagebox.showerror("Check the settings", str(exc))
            return
        self.go.configure(state="disabled")
        self.openbtn.configure(state="disabled")
        self.status.configure(text="Converting...")
        self.progress.start(12)
        self.write("-" * 60)

        def work() -> None:
            try:
                res = convert(inp, out, opts, log=self.q.put)
                self.pq.put(("result", res))
                self.q.put("\0done:" + str(res.out_dir))
            except ConversionError as exc:
                self.q.put("\0fail:" + str(exc))
            except Exception:  # noqa: BLE001 - show anything unexpected in the log
                self.q.put("\0fail:Unexpected error:\n" + traceback.format_exc())

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def finish(self, out_dir: Optional[str], error: Optional[str] = None) -> None:
        self.go.configure(state="normal")
        self.progress.stop()
        if error:
            self.status.configure(text="Failed")
            self.write("ERROR: " + error)
            messagebox.showerror("Conversion failed", error.splitlines()[0])
        else:
            self.last_out = Path(out_dir or ".")
            self.status.configure(text="Done")
            self.openbtn.configure(state="normal")
            self.write(f"Finished. Open IMPORT_INSTRUCTIONS.txt in {out_dir}")

    def close(self) -> None:
        """Stop the timers and close the window (worker threads are daemons)."""
        self.closing = True
        self.driving = self.spinning = False
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def run_gui() -> int:
    if tk is None:
        print("The graphical interface needs tkinter, which is not installed.\n"
              "Use the command line instead:  python -m car2roblox convert --help", file=sys.stderr)
        return 1
    if sys.platform.startswith("win"):
        try:  # crisp text on high-DPI Windows displays
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - purely cosmetic
            pass
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()
    return 0
