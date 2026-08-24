#!/usr/bin/env python3
"""Regenerate fix_run_on_wayland.py (self-contained Factorio Wayland fix).

Reads the embedded C sources from ./sources/ (this backup layout) or the old
../eglfix dev location. If the template is missing, it is reconstructed from
the current generated file, so the pipeline is self-healing.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "install_fix.template.py")
DEST = os.path.join(HERE, "fix_run_on_wayland.py")


def read_src(name):
    for base in (os.path.join(HERE, "sources"), os.path.join(HERE, "..", "eglfix")):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            with open(p) as f:
                return f.read()
    return None


def extract_from_current():
    """Pull embedded sources out of the existing generated file."""
    for fn in (DEST, os.path.join(HERE, "install_fix.py")):
        if not os.path.isfile(fn):
            continue
        with open(fn) as f:
            body = f.read()
        e = re.search(r"^EGLFIX_C = (.*?)^EXPORT_MAP", body, re.M | re.S)
        m = re.search(r"^EXPORT_MAP = (.*?)^GAME_README", body, re.M | re.S)
        if e and m:
            return eval(e.group(1).strip()), eval(m.group(1).strip())
    raise SystemExit("ERROR: no source for the embedded C/version-script found. "
                     "Put eglfix.c + export.map into %s" % os.path.join(HERE, "sources"))


eglfix_c = read_src("eglfix.c")
export_map = read_src("export.map")
if eglfix_c is None or export_map is None:
    eglfix_c, export_map = extract_from_current()
    print("recovered embedded sources from the existing generated file")

if not os.path.isfile(TEMPLATE):
    # Reconstruct the template from the current generated file.
    with open(DEST) as f:
        body = f.read()
    body = re.sub(r"^EGLFIX_C = '.*?^EXPORT_MAP", "EGLFIX_C = @@EGLFIX_C@@\nEXPORT_MAP",
                  body, count=1, flags=re.M | re.S)
    body = re.sub(r"^EXPORT_MAP = '.*?^GAME_README",
                  "EXPORT_MAP = @@EXPORT_MAP@@\nGAME_README",
                  body, count=1, flags=re.M | re.S)
    with open(TEMPLATE, "w") as f:
        f.write(body)
    print("reconstructed template from generated file")

with open(TEMPLATE) as f:
    template = f.read()

assert "@@EGLFIX_C@@" in template and "@@EXPORT_MAP@@" in template
out = template.replace("@@EGLFIX_C@@", repr(eglfix_c)).replace("@@EXPORT_MAP@@", repr(export_map))

with open(DEST, "w") as f:
    f.write(out)
print("wrote %s (%d bytes, eglfix.c=%d, export.map=%d)"
      % (DEST, len(out), len(eglfix_c), len(export_map)))
