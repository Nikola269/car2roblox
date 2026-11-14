# Car2Roblox

Convert car models from **GTA San Andreas**, **GTA V** and **BeamNG.drive** into assets for
**Roblox Studio**: textured meshes (a `.glb` plus separate `.obj` pieces), a manifest, and a
Studio script that assembles them into a simple drivable car. The app and the browser version both
show a picture of the converted car before you convert, and both can test-drive it.

```
GTA SA  .dff + .txd ─┐
BeamNG  mod .zip     ─┼─►  car2roblox  ─►  Car.glb · obj/*.obj · textures/*.png
GTA V   .obj/.glb/.dae export ─┘            manifest.json · Car_setup.lua · IMPORT_INSTRUCTIONS.txt
```

Only convert models you made or are licensed to use. GTA and BeamNG assets belong to their
creators, and Roblox's rules apply to anything you upload.

---

## What it reads

| Input | How |
|---|---|
| **GTA San Andreas** `.dff` (+ `.txd`) | Read directly. PC files only (not PS2/Xbox). The `.txd` next to the `.dff`, plus `vehicle.txd` / `generic.txd` if present, are loaded automatically; add more with `--txd`. Wheels are placed from the `wheel_*_dummy` frames, `_dam` / `_vlo` variants are skipped, and the magic paint colours become recolourable paint. |
| **BeamNG.drive** mod `.zip` or unpacked folder | Reads the `.dae` meshes, the JBeam `flexbodies` to pick the meshes of the default (or a chosen `--config`) part set, `materials.json` textures and paint flags, and the `.pc` paint colours. Flexbody `pos` / `rot` / `scale` are applied, so the one wheel mesh a mod ships is placed on all four wheels. Wheel positions, radius and tyre width come from the JBeam `pressureWheels` / `hubWheels` nodes. |
| **GTA V** | The game's own formats are **not** read directly - a `.rpf` is a whole encrypted game archive, and `.yft`/`.ydr`/`.ytd` are RAGE's proprietary resource format, neither of which this tool parses. Dropping one in still works, though: it's recognised and you get a message explaining the next step, instead of a dead-end error. Export the car first with **CodeWalker**, **OpenIV** or **Blender + Sollumz** as **OBJ, GLB or DAE** (textures as `.png`/`.dds` next to it), then convert that file. |
| Any **OBJ / GLB / glTF / DAE** | Generic importer. Up/forward axes are detected (override with `--axes`), units are guessed (override with `--unit`). FBX is not supported: export OBJ/GLB/DAE from Blender. |

## Install

Needs Python 3.9+ with two packages:

```
pip install -r requirements.txt        # numpy, Pillow
```

## Use it

**Window:** `python launcher.py` (or double-click `run_gui.pyw` on Windows). Pick a model and the
window shows it straight away (drag to turn it, wheel to zoom, **Spin**, **Day view**), then press
**Convert**. **Test drive** drives the converted car around on a grid with the arrow keys / WASD.
The **Drive** tab holds *Make the car drivable*, drivetrain, top speed, the *A-Chassis style* suspension
option, *Add tires* and *Always use new tires*. The window title and the corner of the header show the
version.

**Command line:**

```
python -m car2roblox convert infernus.dff  -o out
python -m car2roblox convert some_mod.zip  -o out --config sport --paint "#c0392b"
python -m car2roblox convert car.glb       -o out --axes x,z,y --unit cm
python -m car2roblox inspect some_mod.zip              # list parts without converting
python -m car2roblox convert --help
```

Useful options: `--scale` (1.0 = real size, 1 m ≈ 3.57 studs) or `--length STUDS`,
`--paint/--paint2/--paint3`, `--flip-forward`, `--no-glass`, `--no-interior`,
`--max-tris` (default 20 000, Roblox's per-mesh limit: bigger meshes are split into several
MeshParts without losing detail), `--target-tris N` (crude whole-car decimation),
`--max-texture 1024`, `--drivetrain awd|rwd|fwd`, `--format glb,obj`, `--no-rig` (do not make it
drivable), `--top-speed KMH`, `--a-chassis` (real per-wheel suspension + an RPM/torque-curve
drivetrain, see below), `--no-tires` (do not generate tyres), `--new-tires` (replace the model's
wheels with clean generated tires at the same positions).

**Windows installer (`Car2RobloxSetup.exe`):** no source project can ship a pre-built Windows binary
(it has to be built on Windows), so pick one of these:

* **GitHub Actions (no Windows machine needed):** push this project to a GitHub repo, open the
  *Actions* tab, run **Build Windows exe** once (or push a tag like `v1.3.1`). It builds on GitHub's
  own Windows runner and attaches `Car2RobloxSetup.exe` (plus the plain `Car2Roblox.exe` /
  `car2roblox-cli.exe`) to the run's *Artifacts*, or to a GitHub Release if you used a tag.
* **On your own Windows PC:** double-click `build_exe.bat` (needs Python). It always makes
  `dist\Car2Roblox.exe` and `dist\car2roblox-cli.exe`. If you also install
  [Inno Setup](https://jrsoftware.org/isdl.php) once (free, a few MB), the same script detects it and
  additionally builds `dist\Car2RobloxSetup.exe` - a normal install wizard (Welcome → choose folder →
  optional desktop icon → Install → Finish) with its own Start Menu entry and uninstaller. Without
  Inno Setup you still get the two plain `.exe` files, just not the installer wizard.
* The wizard's look (icon, pages, Start Menu group) comes from `installer/Car2Roblox.iss`. Its
  Welcome/Finished banner and the small logo on every other page are branded images - the same car
  artwork and colours as the app icon - generated by `installer/make_wizard_images.py`; the app/
  installer icon itself comes from `assets/make_icon.py` into `assets/icon.ico`. Edit either script
  and rebuild to restyle it.

## Output

```
out/<CarName>/
  <CarName>.glb              one file with every piece + textures  (recommended)
  obj/*.obj *.mtl *.png      the same pieces separately            (fallback)
  textures/*.png
  <CarName>_setup.lua        builds the drivable car in Studio
  preview.png                a picture of the converted car (handy for the Roblox upload page)
  manifest.json              every piece: position, size, material, texture, wheel slot
  IMPORT_INSTRUCTIONS.txt
```

The car is exported in Roblox orientation and studs: **front = −Z**, Y up, ground at Y = 0,
centred on X/Z.

## In Roblox Studio

1. **File > Import**, choose `<CarName>.glb`. Set **Scale Unit = Studs** and tick
   **Import Only As Model**.
2. Select the imported Model in the Explorer.
3. Open `<CarName>_setup.lua`, paste it into the **Command Bar** (View tab), press Enter.
4. Press Play, walk up to the car and press **E** at a seat (*Drive* for the driver's). W/S throttle,
   A/D steer.

Each wheel gets an invisible cylinder collider that spins on a hinge, and the wheels, seat and body
are set not to collide with each other, so the wheels do not fight the chassis. The car body itself
(including the doors) does not collide either, so you can walk straight through it to reach any
seat - the trade-off is that nothing can stand on the roof or hood any more, since there is no solid
part there. Besides the driver's seat, a front passenger seat is always added (mirrored across the
car), and rear seats too when there's clearly enough car behind the front seats for them - a rough
guess at the interior, not a real layout, so it can be off on unusual shapes. Passenger seats have no
controls; press E at one to sit, same as the driver's.

If the car drives backwards or steers the wrong way, set `DriveSign` / `SteerSign` to `1`
(they are attributes on the finished Model). If the model imported facing backwards, re-run
the converter with `--flip-forward`.

## A-Chassis style suspension & drivetrain

By default each wheel is bolted straight to the chassis on a fixed hinge - simple and robust, but
with no suspension travel. Tick **A-Chassis style** in the GUI's Drive tab (or pass `--a-chassis` on
the command line) for a rig closer to the community "A-Chassis" style of car:

* **Suspension:** every wheel gets its own invisible `Upright_<slot>` part, held under the chassis by
  a `PrismaticConstraint` (vertical travel, `SuspensionTravel` on the Model) and a `SpringConstraint`
  (the spring itself). Front wheels still steer through a knuckle, now carried by the upright instead
  of being bolted straight to the chassis, so steering keeps working as the suspension moves.
* **Drivetrain:** instead of mapping the throttle straight onto wheel speed, the driving `Script` runs
  a small simulated engine - idle/redline RPM, a torque curve, a 5-speed gearbox that shifts on its
  own - and reads each drive wheel's real spin to work out what RPM it implies. The wheel motors are
  driven the way real Roblox vehicle rigs do it: an unreachable target speed, with the actual torque
  (and so the actual speed) coming from `MotorMaxTorque`, computed every frame from the torque curve.
  `EngineRPM` and `Gear` attributes appear on the Model so you can watch it shift.

It is still a from-scratch rig built for this converter (not the third-party "A-Chassis" asset
itself), tuned with the same TUNING table at the top of the setup script. Turn it off again for the
simpler, lighter fixed-hinge rig if a particular car doesn't need it.

## Tires

Many mods arrive without usable wheels. The converter deals with that in this order:

* **BeamNG:** the one wheel mesh a mod ships is placed on every wheel from the JBeam `flexbodies`
  (`pos`, `rot`, `scale`); if the chosen configuration has no wheels installed, a wheel part is added.
* **Fused or unnamed wheels:** one mesh holding all four wheels is split into four; round parts
  that sit on the ground in left/right pairs are recognised as wheels even without "wheel" in the name.
  Arches and liners are never treated as wheels.
* **Hardware mistaken for the wheel:** a detailed mod often names or groups its brake calipers, discs,
  hubs and suspension arms right alongside the wheel (e.g. a BeamNG part called `wheel_hub_f`, or a
  brake caliper sharing the tyre's own JBeam group so it travels with the suspension) - none of that
  hardware is swept in as if it were the tyre or rim any more, so it stays body-coloured bodywork
  instead of getting jammed onto the hub as crumpled, misplaced debris.
* **A wheel in the middle of the car:** some exports leave the wheels at their origin (all stacked at the
  centre line, or a single "template" wheel). Wheels on the centre line are moved onto the four wheel
  positions (JBeam hubs / GTA dummies / wheels that are already in place, else the usual spots) and
  copied where needed. A single spare-wheel-sized disc is left alone.
* **Only some wheels, or a BeamNG mod with wheels on only one axle:** if a car has, say, two wheels,
  the missing ones are copied from the existing ones, mirrored to the other side or axle, and a
  warning says so. This also covers a common real-world case: a BeamNG mod whose rear axle's hub
  nodes can't be resolved (a `wheels` table pointing at node ids a trimmed part doesn't define), so
  only the front axle has usable JBeam hints even though the rear wheel meshes are right there -
  hints for one axle no longer stand in for the whole car; what's known from the meshes and from
  JBeam is combined per wheel, and only a genuinely missing corner gets filled in.
* **Glitches:** duplicate meshes on top of each other (they flicker) are dropped, the hub and radius come
  from the tyre itself, and the car is stood on its wheels so they touch the ground.
* **No wheels at all:** with *Add tires* on (default) a tyre and rim are generated at the JBeam hubs
  (BeamNG), at the GTA SA wheel dummies, or at the usual spots for a car of that size, and a warning
  says so. If the body already has wheels built into it, turn *Add tires* off.
* **Always use new tires** (Drive tab, browser checkbox, `--new-tires`): keeps the wheel positions but
  swaps every wheel for a generated tyre + rim. Use it when a mod's own wheels look wrong.

## Safety filter

Every model passes through `safety.py` (and its JavaScript twin in the browser version) before
anything is written:

* **Links are removed.** `http://`, `www.`, `ftp:`, `javascript:` and bare `site.com` / `site.ru` style
  text are stripped from part, material, texture and model names. Remote texture addresses
  (`scheme://...`) are never followed.
* **18+ content is deleted.** A part, material or texture whose *name* carries an adult-content
  marker (nsfw, nude, porn, hentai, `18+`, r18, x-rated, ...) is removed, together with the
  triangles that use it. If nothing is left the conversion stops with an error.
* **Risky files are ignored.** Shortcuts (`.url`, `.lnk`), programs, installers, scripts, web pages
  and double extensions such as `skin.png.exe` found in a mod folder or zip are never opened, run
  or copied.
* Everything removed is listed in the warnings (`manifest.json`, the GUI log, the browser's
  "Safety filter" panel). Deleted adult names are not echoed back.

The filter works on **names and file types only**. It cannot tell what a texture picture shows, so it is
not a guarantee that a model is clean. Check models yourself before uploading anything to Roblox.

## Browser version

`web/dist/Car2Roblox.html` is the same converter as one self-contained page: open it in a browser, drop the
files in, and download a `.zip`. Nothing is uploaded; everything runs in the page. It adds:

* a 3D preview with orbit / zoom, shaded / wireframe / pieces views, wheels and a front arrow,
  a **Save picture** button, and the same `preview.png` inside the zip,
* the same options as the app: *Make drivable*, drivetrain, top speed, *A-Chassis style* suspension,
  *Add tires*, *Always use new tires*, and
* **Drive**: a quick test drive of the converted car (W/S gas and brake, A/D steer, Space handbrake,
  R reset; on-screen buttons on phones). It is a simple vehicle model for checking size, wheel
  placement and steering, **not** Roblox physics. The real driving rig is the Studio script.

Rebuild it with `python web/build.py` (needs only Python; Node is used for the tests in
`web/test/`).

## Honest limits

* **Not tested on real game files.** The build environment had no GTA or BeamNG assets. The
  DFF/TXD layouts were checked against the open-source DragonFF importer and the whole pipeline
  is covered by tests on synthetic files built to those layouts. Please report any real file
  that fails.
* **The Studio script has not been run in Studio.** It was run against Lua 5.4 with a small mock
  of the Roblox API (it builds, finds and places parts, wires hinges and steering, and the driver
  signs are checked), but real physics, tuning and the importer's naming were not. If the importer
  renames pieces, the script falls back to matching them by size. Treat the rig as a starter: no
  suspension, simple motors.
* **Name-based safety filter.** See above: pictures are not scanned.
* **No `.exe` or installer is included.** Both can only be built on Windows: run `build_exe.bat`
  (needs Python; also builds `Car2RobloxSetup.exe` if Inno Setup is installed), or use the GitHub
  Action in `.github/workflows/build-windows-exe.yml`, which builds all three on GitHub's own
  Windows runner with nothing to install locally.
* BeamNG: binary `.cdae` caches are not read; damage/deformation is ignored; paint materials become
  flat colours. The wheel placement and generated tyres were tested on synthetic mods built to the
  documented JBeam layout, not on real ones.
* The **Test drive** in the app and the browser is a simple vehicle model for checking size, wheels and
  steering. It is not Roblox physics.
* Generated tyres are plain (a rubber tyre and a dished rim), not copies of the car's real wheels.
* GTA SA: console-native DFF/TXD are not supported. The front/back direction of generic files is
  guessed; use `--flip-forward` when wrong.
* `--target-tris` is a vertex-clustering decimator. It gets heavy cars under a budget but is not
  hero-quality.
* **Passenger seats are a rough guess**, not a real interior: a front passenger seat is mirrored off
  the driver's, and rear seats are added only by a simple "is there enough car behind the front
  seats" check. A 2-seat car can still get rear seats it shouldn't, and an unusual layout (cab-forward
  trucks, mid-engine cars) can place a seat somewhere odd. Move the `VehicleSeat` parts in Studio if so.
* **`.rpf` is not actually parsed.** It's an encrypted RAGE game archive; dropping one in just gets
  you a clear message pointing at CodeWalker/OpenIV instead of a dead-end error. Reading `.rpf`/`.yft`
  directly would mean reimplementing Rockstar's resource format, which this project does not do.

## Development

```
pip install -r requirements-dev.txt
python -m pytest -q          # unit + format + wheels + pipeline + preview + GUI (stubbed) + Lua rig tests
```

Browser tests: `python web/test/make_fixtures.py <dir>` then `node web/test/run_core.js <dir>` (checks the JS
port against the Python output).

Layout: `rwparse.py` (DFF/TXD), `imp_gtasa.py`, `imp_beamng.py` + `jbeam.py` + `collada.py`,
`imp_generic.py` (OBJ/glTF/DAE), `pipeline.py` (axes, scale, pieces, files), `wheels.py` (wheel repair
and generated tyres), `preview.py` (the software-rendered preview picture), `drive.py` (test-drive
model), `export.py`
(GLB/OBJ/Lua tables), `safety.py` (link / 18+ / risky-file filter), `rigscript.py` (Studio script), `cli.py`, `gui.py`.
