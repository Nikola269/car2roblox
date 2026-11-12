"""GUI logic tests. tkinter is replaced by tiny fakes so these run headless (and on machines
without tkinter); they exercise option parsing, validation messages and the worker-thread /
queue plumbing that the real window relies on."""
import types
from pathlib import Path

import pytest

import fixtures as fx
from car2roblox import gui
from car2roblox.model import ConversionError


class _Var:
    def __init__(self, value=None):
        self.value = "" if value is None else value
        self.watchers = []

    def get(self):
        return self.value

    def set(self, v):
        self.value = v
        for fn in list(self.watchers):
            fn()

    def trace_add(self, mode, fn):
        self.watchers.append(lambda: fn("name", "", mode))


class _BoolVar(_Var):
    def __init__(self, value=False):
        super().__init__(bool(value))


class _Widget:
    """Stands in for any Tk widget: remembers what was configured and ignores layout calls."""

    def __init__(self, *a, **k):
        self.cfg = dict(k)
        self.items = []
        self.text = ""
        self.bound = {}

    def __getattr__(self, name):                       # grid, pack, after, bind, create_text, ...
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None

    def grid(self, *a, **k): return self
    def pack(self, *a, **k): return self
    def place(self, *a, **k): return self
    def curselection(self): return ()
    def create_image(self, *a, **k): return 1
    def create_text(self, *a, **k): return 2
    def create_rectangle(self, *a, **k): return 3
    def bind(self, seq, fn): self.bound[seq] = fn

    def configure(self, *a, **k):
        self.cfg.update(k)

    config = configure

    def insert(self, where, text, *tags):
        self.items.append(text)
        self.text += text

    def delete(self, *a):
        if a[:2] == ("1.0", "end"):
            self.text = ""


class _Style(_Widget):
    pass


class _Box:
    def __init__(self):
        self.errors = []

    def showerror(self, title, msg):
        self.errors.append((title, msg))


@pytest.fixture()
def fake_tk(monkeypatch):
    names = ("Frame", "Label", "Entry", "Button", "Combobox", "LabelFrame", "Checkbutton", "Scrollbar",
             "Notebook", "Progressbar")
    monkeypatch.setattr(gui, "tk", types.SimpleNamespace(
        StringVar=_Var, BooleanVar=_BoolVar, Listbox=_Widget, Text=_Widget, Tk=_Widget, Canvas=_Widget,
        Label=_Widget, PhotoImage=_Widget))
    monkeypatch.setattr(gui, "ttk", types.SimpleNamespace(Style=_Style, **{n: _Widget for n in names}),
                        raising=False)
    box = _Box()
    monkeypatch.setattr(gui, "messagebox", box, raising=False)
    monkeypatch.setattr(gui, "filedialog", types.SimpleNamespace(), raising=False)
    monkeypatch.setattr(gui, "colorchooser", types.SimpleNamespace(), raising=False)
    return box


def _run(app):
    app.start()
    assert app.worker is not None
    app.worker.join(timeout=60)
    app.drain()


def test_options_from_values_defaults_and_maps_source():
    inp, out, o = gui.options_from_values({"input": "x.dff", "source": "BeamNG (.zip / folder)",
                                           "length": "18", "paint": " #112233 ", "flip": True})
    assert (inp, out) == ("x.dff", "roblox_out")
    assert o.source == "beamng" and o.length == 18.0 and o.paint == "#112233" and o.flip_forward
    assert o.formats == ("glb", "obj") and o.max_tris_per_mesh == 20000 and o.rig


@pytest.mark.parametrize("values,needle", [
    ({}, "choose a model"),
    ({"input": "a", "glb": False, "obj": False}, "output format"),
    ({"input": "a", "scale": "abc"}, "Scale must be a number"),
    ({"input": "a", "scale": "-2"}, "greater than zero"),
    ({"input": "a", "max_tris": ""}, "Triangles per mesh is required"),
    ({"input": "a", "target_tris": "lots"}, "Triangle budget"),
])
def test_options_validation_messages(values, needle):
    with pytest.raises(ConversionError, match=needle):
        gui.options_from_values(values)


def test_window_builds_and_converts(fake_tk, tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    app = gui.ConverterApp(_Widget())
    app.v["input"].set(str(tmp_path / "t.dff"))
    app.v["output"].set(str(tmp_path / "out"))
    app.v["name"].set("GuiCar")
    _run(app)
    assert fake_tk.errors == []
    assert app.status.cfg["text"] == "Done"
    assert app.last_out == tmp_path / "out" / "GuiCar"
    assert (app.last_out / "GuiCar.glb").exists() and (app.last_out / "GuiCar_setup.lua").exists()
    assert "Done:" in app.log.text and app.openbtn.cfg["state"] == "normal"
    assert app.go.cfg["state"] == "normal"          # button re-enabled for the next run


def test_validation_error_pops_a_dialog_and_starts_nothing(fake_tk):
    app = gui.ConverterApp(_Widget())
    app.start()
    assert app.worker is None
    assert fake_tk.errors and "choose a model" in fake_tk.errors[0][1]


def test_conversion_failure_is_reported_not_raised(fake_tk, tmp_path):
    bad = tmp_path / "broken.dff"
    bad.write_bytes(b"this is not a renderware file at all")
    app = gui.ConverterApp(_Widget())
    app.v["input"].set(str(bad))
    app.v["output"].set(str(tmp_path / "out"))
    _run(app)
    assert app.status.cfg["text"] == "Failed"
    assert "ERROR:" in app.log.text
    assert fake_tk.errors and fake_tk.errors[0][0] == "Conversion failed"
    assert app.go.cfg["state"] == "normal"


def test_run_gui_without_tkinter_explains_itself(monkeypatch, capsys):
    monkeypatch.setattr(gui, "tk", None)
    assert gui.run_gui() == 1
    assert "tkinter" in capsys.readouterr().err


# --------------------------------------------------------------------------- drivable / tires / version
def test_drive_and_tire_options_reach_the_converter():
    _, _, o = gui.options_from_values({"input": "a", "rig": False, "add_tires": False,
                                       "top_speed": "140", "drivetrain": "RWD"})
    assert o.rig is False and o.add_tires is False and o.top_speed == 140.0 and o.drivetrain == "rwd"
    _, _, d = gui.options_from_values({"input": "a"})
    assert d.rig and d.add_tires and d.top_speed is None and d.drivetrain == "awd"


@pytest.mark.parametrize("values,needle", [
    ({"input": "a", "top_speed": "fast"}, "Top speed must be a number"),
    ({"input": "a", "top_speed": "-5"}, "Top speed must be between"),
    ({"input": "a", "top_speed": "5000"}, "Top speed must be between"),
    ({"input": "a", "drivetrain": "4x4"}, "Drivetrain must be"),
])
def test_drive_option_validation(values, needle):
    with pytest.raises(ConversionError, match=needle):
        gui.options_from_values(values)


def test_window_shows_the_current_version(fake_tk):
    from car2roblox import __version__
    app = gui.ConverterApp(_Widget())
    assert __version__ in app.window_title and app.version_text == f"v{__version__}"
    assert app.version_label.cfg["text"] == f"Car2Roblox {__version__}"


def test_drive_controls_follow_the_make_drivable_box(fake_tk):
    app = gui.ConverterApp(_Widget())
    app.v["rig"].set(False)
    assert app.en_speed.cfg["state"] == "disabled" and app.cb_drivetrain.cfg["state"] == "disabled"
    app.v["rig"].set(True)
    assert app.en_speed.cfg["state"] == "normal" and app.cb_drivetrain.cfg["state"] == "readonly"


def test_preview_loads_and_test_drive_runs(fake_tk, tmp_path):
    (tmp_path / "t.dff").write_bytes(fx.sa_test_car())
    (tmp_path / "t.txd").write_bytes(fx.sa_test_txd())
    app = gui.ConverterApp(_Widget())
    app.v["input"].set(str(tmp_path / "t.dff"))
    app._start_preview()
    app._load_thread.join(timeout=60)
    app.drain()
    assert app.scene is not None and app.scene_info["wheels"] == 4
    assert "4 wheels" in app.summary.cfg["text"]
    assert app.drive_btn.cfg["state"] == "normal"
    # the test drive moves the car and the HUD shows a speed
    app.start_drive()
    assert app.driving
    app.keys = {"w"}
    for _ in range(20):
        app._last_t -= 0.05
        app._drive_tick()
    assert app.car.kmh > 5
    app.stop_drive()
    assert not app.driving and app.drive_btn.cfg["text"] == "Test drive"


def test_unreadable_model_gives_a_message_in_the_notes_not_a_crash(fake_tk, tmp_path):
    bad = tmp_path / "broken.dff"
    bad.write_bytes(b"nope")
    app = gui.ConverterApp(_Widget())
    app.v["input"].set(str(bad))
    app._start_preview()
    app._load_thread.join(timeout=60)
    app.drain()
    assert app.scene is None and app.drive_btn.cfg["state"] == "disabled"
    assert app.summary.cfg["text"] == "The preview could not be built"
    assert app.notes.text.strip()


def test_notes_are_tagged_by_kind():
    tags = dict((t, w) for t, w in gui.note_lines(
        ["Safety: deleted a part", "The model had no tyres, so 4 tyre + rim pair(s) were generated", "Other"]))
    assert set(tags) == {"safety", "wheel", "warn"}
