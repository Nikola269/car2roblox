"""Bundles web/src/*.js. Outputs: dist/core.node.js (Node, for tests) and dist/Car2Roblox.html (the page)."""
import re, subprocess, sys
from pathlib import Path
here = Path(__file__).resolve().parent
subprocess.run([sys.executable, str(here / "gen_rig.py")], check=True)
subprocess.run([sys.executable, str(here / "gen_achassis.py")], check=True)
src = sorted((here / "src").glob("*.js"))
core_files = [p for p in src if int(p.name[:2]) < 60]
core = "\n".join(p.read_text() for p in core_files)
(here / "dist").mkdir(exist_ok=True)
(here / "dist" / "core.node.js").write_text("'use strict';\nmodule.exports = (function () {\n" + core + "\nreturn API;\n})();\n")
print("core.node.js", len(core))
if (here / "template.html").exists():
    ui = "\n".join(p.read_text() for p in src)
    tpl = (here / "template.html").read_text()
    page = tpl.replace("/*__SCRIPT__*/", ui.replace("</script", "<\\/script"))
    # 1) the Artifact: the host wraps it in a document skeleton, so it is published as is
    (here / "dist" / "Car2Roblox.artifact.html").write_text(page)
    # 2) the standalone file: same content inside its own skeleton
    head_part, body_part = page.split('<div class="wrap">', 1)
    skeleton = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>'
                + head_part + '</head><body><div class="wrap">' + body_part + '</body></html>\n')
    (here / "dist" / "Car2Roblox.html").write_text(skeleton)
    print("Car2Roblox.html", len(skeleton), "artifact", len(page))
