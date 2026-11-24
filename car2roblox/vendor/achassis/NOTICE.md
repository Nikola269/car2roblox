# A-Chassis (vendored)

The `.luau` files in this folder are the real, unmodified driving scripts from
**[lisphm/A-Chassis](https://github.com/lisphm/A-Chassis)**, a free, open-source
Roblox vehicle chassis kit used by "hundreds of experiences" on the platform.

- Upstream version: **v1.7.2** (the `README.luau` module's `return` value)
- Upstream commit: `67f6b6ddc3fcb058a4a7396bf72ada7f9d3f7c82` (2026-10-31)
- License: **Mozilla Public License 2.0** (see `LICENSE` in this folder) — this
  permits combining these files with car2roblox's differently-licensed code,
  as long as the files below keep this notice and stay source-available.

car2roblox does not modify these files. It generates a Roblox object tree
around a converted car (DriveSeat, a `Wheels` folder with `FL`/`FR`/`RL`/`RR`
parts, a `Body` folder, and an auto-filled `A-Chassis Tune` module) that
matches what `Initialize.luau` expects, then embeds these exact files
verbatim as the `Script`/`ModuleScript` instances A-Chassis itself requires.

`MiscWeld.luau` and `CustomEngine.luau` are kept here for completeness but are
**not** wired into the generated car: both are optional, user-opt-in modules
that upstream only activates if you `require()` them yourself (`MiscWeld`
additionally waits forever for a `Misc` folder that car2roblox doesn't
create, so instantiating it unused would hang the car on load).

If you'd rather use the version straight from the Releases page, see
<https://github.com/lisphm/A-Chassis/releases>.
