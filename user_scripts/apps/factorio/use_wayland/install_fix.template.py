#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_run_on_wayland.py — Factorio 2.1.x "Failed to create shader" native-Wayland fix.

SELF-CONTAINED: this single file embeds the EGL interposer source (eglfix.c),
the linker version script (export.map) and a GL/EGL smoke test. It builds the
shim, installs it into a persistent location, wires the jc141 launcher config
and verifies everything empirically. Package installs auto-elevate to sudo
(interactive password prompt) only when needed.

Why this exists
---------------
Factorio 2.1.14 crashes ~1s after launch on a native Wayland session (no
XWayland) with:

    Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.

Root cause (empirically verified): on the Wayland/EGL path the game loses EGL
context currency on the main thread between GL init and the first shader
compile. At the first glCreateShader, eglGetCurrentContext()==NULL, every GL
call fails silently, glCreateShader returns 0 and the game aborts. The X11
path works; native Wayland doesn't. The game dlsyms EGL itself, so the only
way to intercept is a libEGL.so.1 interposer (same SONAME) loaded via
LD_PRELOAD. It forwards all EGL calls to the real glvnd library, remembers the
last good eglMakeCurrent binding, and re-binds it on the calling thread right
before the first glCreateShader/glCreateProgram.

Where the shim lives
--------------------
The generated fix is installed to:

    ~/.factorio/wayland_fix/libEGL.so.1        (used for non-sandboxed runs)
    <JC_DIRECTORY>/native-docs/.factorio/wayland_fix/libEGL.so.1
                                               (used inside the bubblewrap
                                                sandbox, which maps /home/<you>
                                                -> native-docs)

local.config references `$HOME/.factorio/wayland_fix/libEGL.so.1`; the shell
expands $HOME when sourcing, so the one line resolves correctly in BOTH launch
modes. This location is persistent (survives re-downloads of the game repack),
unlike the old in-game `eglfix/` dir which this script now removes.

Usage
-----
    python3 fix_run_on_wayland.py [--game-dir /path/to/Factorio_2.1.14]
    python3 fix_run_on_wayland.py --check                # inspect only
    python3 fix_run_on_wayland.py --force                # force shim rebuild
    python3 fix_run_on_wayland.py --verify-game          # also launch the game
    python3 fix_run_on_wayland.py --reset                # remove the fix
    python3 fix_run_on_wayland.py --troubleshoot         # full diagnostic report

Exit codes: 0 = ok, 1 = error, 2 = check found problems.
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# --------------------------------------------------------------------------
# Rich console (optional; plain-text fallback if python-rich is absent)
# --------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    RICH = True
except Exception:  # pragma: no cover
    RICH = False

# --------------------------------------------------------------------------
# Embedded sources (generated at build time; do not edit by hand)
# --------------------------------------------------------------------------
EGLFIX_C = @@EGLFIX_C@@
EXPORT_MAP = @@EXPORT_MAP@@

# README written next to the installed shim so the fix dir is self-documenting.
SHIM_README = """# Factorio 2.1.14 native-Wayland fix (libEGL shim)

Managed by fix_run_on_wayland.py (regenerated on every run).

The fix: an EGL interposer (libEGL.so.1) loaded via LD_PRELOAD that forwards
all EGL calls to the real glvnd libEGL and re-binds the remembered EGL context
on the calling thread right before the game's first glCreateShader -- this
fixes the native-Wayland-only crash:

    Error ShaderOpenGL.cpp:15: Failed to create shader '__core__/graphics/shaders/sprite.vert'.

Root cause: on the Wayland/EGL path Factorio loses EGL context currency on the
main thread between GL init and first shader compile, so glCreateShader returns
0 and the game aborts. X11 works; native Wayland does not.

Files: libEGL.so.1 (built), eglfix.c + export.map (sources), README.md.

Wiring: <game>/local.config gets ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"
(the `env` prefix is REQUIRED -- the jc141 launcher word-splits $ENV into the
command array and bwrap only execs real binaries; $HOME expands at source time
and resolves in both sandboxed and unsandboxed launches).

Reset: run `fix_run_on_wayland.py --reset` to remove this dir and the config line.
Diagnostics: the shim logs to /tmp/eglfix.log.
"""

# Small GL/EGL smoke test: creates a surfaceless context, then simulates the
# Factorio bug (unbind, then glCreateShader with no current context).
# Without the shim this must FAIL (glCreateShader -> 0, the crash condition);
# with the shim preloaded it must PASS (shim re-binds and the shader is made).
SMOKE_TEST_C = r"""
#define EGL_EGLEXT_PROTOTYPES 1
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <stdio.h>

#ifndef EGL_PLATFORM_SURFACELESS_MESA
#define EGL_PLATFORM_SURFACELESS_MESA 0x31DD
#endif

typedef unsigned int GLenum;
typedef unsigned int GLuint;
typedef const unsigned char *(*PFN_glGetString)(GLenum);
typedef void (*PFN_glClear)(GLenum);
typedef GLuint (*PFN_glCreateShader)(GLenum);
typedef EGLDisplay (*PFN_eglGetPlatformDisplayEXT)(EGLenum, void *, const EGLint *);

int main(void) {
    /* Prefer the surfaceless platform (headless-safe); resolve the EXT
     * entry point via eglGetProcAddress because glvnd does not export it. */
    EGLDisplay dpy = EGL_NO_DISPLAY;
    PFN_eglGetPlatformDisplayEXT pGetPlatformDisplayEXT =
        (PFN_eglGetPlatformDisplayEXT)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (pGetPlatformDisplayEXT) {
        dpy = pGetPlatformDisplayEXT(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    }
    if (dpy == EGL_NO_DISPLAY) dpy = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (dpy == EGL_NO_DISPLAY) { printf("FAIL: no EGL display\n"); return 1; }

    EGLint major = 0, minor = 0;
    if (!eglInitialize(dpy, &major, &minor)) { printf("FAIL: eglInitialize\n"); return 1; }
    if (!eglBindAPI(EGL_OPENGL_API)) { printf("FAIL: eglBindAPI\n"); return 1; }

    EGLint ca[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
                    EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    if (!eglChooseConfig(dpy, ca, &cfg, 1, &n) || n < 1) { printf("FAIL: eglChooseConfig\n"); return 1; }

    EGLContext ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, NULL);
    if (ctx == EGL_NO_CONTEXT) { printf("FAIL: eglCreateContext\n"); return 1; }
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) { printf("FAIL: eglMakeCurrent\n"); return 1; }

    PFN_glGetString glGetString = (PFN_glGetString)eglGetProcAddress("glGetString");
    PFN_glClear glClear = (PFN_glClear)eglGetProcAddress("glClear");
    PFN_glCreateShader glCreateShader = (PFN_glCreateShader)eglGetProcAddress("glCreateShader");

    if (glGetString) printf("GL_VERSION=%s\n", (const char *)glGetString(0x1F02));
    if (glClear) glClear(0x4000);
    printf("pre-unbind glGetError=0x%x\n", (unsigned)eglGetError());

    /* Simulate the Factorio bug: lose context currency, then compile a shader. */
    if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT)) { printf("FAIL: unbind\n"); return 1; }
    printf("eglGetCurrentContext after unbind=%p (0 == lost, exactly like Factorio)\n",
           (void *)eglGetCurrentContext());

    GLuint shader = glCreateShader ? glCreateShader(0x8B31) : 0; /* GL_VERTEX_SHADER */
    printf("glCreateShader after unbind -> %u\n", shader);
    if (shader != 0) {
        printf("PASS: shader created without current context (shim re-bound it)\n");
        return 0;
    }
    printf("FAIL: glCreateShader returned 0 -- this IS the Factorio crash condition\n");
    return 1;
}
"""

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
PACKAGES_FIX = ["gcc", "libglvnd", "sdl3", "fuse-overlayfs", "bubblewrap", "python-rich"]
PACKAGES_TEST_TOOLS = ["wtype", "grim", "imagemagick"]

SHIM_SUBDIR = os.path.join(".factorio", "wayland_fix")
ENV_LINE = 'ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"'
MARKER = "# Factorio native-Wayland shader fix (managed by fix_run_on_wayland.py)"
CREATED_MARKER = "# created by fix_run_on_wayland.py because the repack had no local.config"


def log(msg, level="*"):
    print("[%s] %s" % (level, msg), flush=True)


def rich_table(title, rows):
    """Pretty table if rich is available, else aligned plain text."""
    if RICH:
        t = Table(title=title)
        t.add_column("Item", style="cyan", no_wrap=True)
        t.add_column("Status", style="bold")
        t.add_column("Detail", style="dim")
        for item, status, detail in rows:
            t.add_row(item, status, detail)
        Console().print(t)
    else:
        print("== %s ==" % title)
        for item, status, detail in rows:
            print("  %-22s %-12s %s" % (item, status, detail))


def run(cmd, timeout=120, env=None, input_data=None):
    """Run a command; return CompletedProcess. Timeouts abort loudly.

    NOTE: do not pass input=None explicitly -- CPython then sets the child's
    stdin to /dev/null, which breaks sudo -S reading a piped password.
    """
    kwargs = dict(capture_output=True, text=True, timeout=timeout)
    if env is not None:
        kwargs["env"] = env
    if input_data is not None:
        kwargs["input"] = input_data
    try:
        return subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        raise SystemExit("COMMAND TIMED OUT after %ss: %s\n%s%s" % (timeout, " ".join(cmd), out, err))


def sudo_cmd():
    """Return a sudo command list. Uses -n if passwordless sudo works,
    otherwise plain sudo (interactive). Supports SUDO_STDIN=1 for -S."""
    if not shutil.which("sudo"):
        raise SystemExit("ERROR: sudo not found -- install it with: pacman -S sudo "
                         "(or run this script as root / with a root-capable user)")
    probe = subprocess.run(["sudo", "-n", "true"], capture_output=True)
    if probe.returncode == 0:
        return ["sudo", "-n"]
    if os.environ.get("SUDO_STDIN") == "1":
        return ["sudo", "-S"]
    return ["sudo"]


def run_root(cmd, input_data=None, timeout=120):
    """Run a command with root privileges (auto-elevate)."""
    return run(sudo_cmd() + cmd, input_data=input_data, timeout=timeout)


def find_game_dir(guess):
    """Locate the Factorio jc141 repack directory."""
    candidates = []
    if guess:
        candidates.append(os.path.abspath(os.path.expanduser(guess)))
    env_dir = os.environ.get("FACTORIO_DIR")
    if env_dir:
        candidates.append(os.path.abspath(os.path.expanduser(env_dir)))
    home = os.path.expanduser("~")
    candidates += [
        "/mnt/zram1/Factorio_2.1.14",
        os.path.join(home, "Factorio_2.1.14"),
        os.path.join(home, "Downloads", "Factorio-jc141"),
        os.path.join(home, "Games", "Factorio_2.1.14"),
        os.path.join(home, "Games", "jc141", "Factorio_2.1.14"),
        os.path.join(os.getcwd(), "Factorio_2.1.14"),
        os.getcwd(),
    ]
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        # local.config is NOT required -- fresh jc141 repacks may not ship one
        # (the launcher auto-generates it, and we create+wiring it ourselves).
        if (os.path.isfile(os.path.join(c, "start.n.sh"))
                or os.path.isfile(os.path.join(c, "space.age.sh"))):
            return c
    return None


def distro_supports_pacman():
    return shutil.which("pacman") is not None


def package_installed(pkg):
    r = run(["pacman", "-Q", pkg])
    return r.returncode == 0


def install_packages(pkgs, skip):
    if skip:
        log("--skip-packages: skipping package installation")
        return
    missing = [p for p in pkgs if not package_installed(p)]
    if not missing:
        log("all required packages already installed: %s" % ", ".join(pkgs))
        return
    log("installing packages: %s (may prompt for sudo password)" % ", ".join(missing))
    # Fresh-install pacman must sync the repo DB and download packages; that
    # can take minutes, so give it a generous timeout.
    r = run_root(["pacman", "-S", "--needed", "--noconfirm"] + missing, timeout=600)
    if r.returncode != 0:
        raise SystemExit("ERROR: package install failed:\n%s%s" % (r.stdout, r.stderr))
    for p in missing:
        if not package_installed(p):
            raise SystemExit("ERROR: package '%s' still not installed after pacman -S" % p)
    log("packages installed successfully")


def check_prereqs():
    if not shutil.which("gcc"):
        raise SystemExit("ERROR: gcc not found. Run without --skip-packages, or: sudo pacman -S gcc")
    if not os.path.isfile("/usr/include/EGL/egl.h"):
        raise SystemExit("ERROR: EGL headers missing. Run without --skip-packages, or: sudo pacman -S libglvnd")
    if not os.path.isfile("/usr/lib/libEGL.so.1"):
        raise SystemExit("ERROR: runtime libEGL.so.1 missing. Run without --skip-packages, or: sudo pacman -S libglvnd")


def write_if_changed(path, content):
    """Write content only if it differs; returns True if written."""
    try:
        with open(path) as f:
            if f.read() == content:
                return False
    except OSError:
        pass
    with open(path, "w") as f:
        f.write(content)
    return True


# --------------------------------------------------------------------------
# Shim locations
# --------------------------------------------------------------------------
def jc141_sandbox_home():
    """Sandbox HOME used by the jc141 launcher: JC_DIRECTORY/native-docs
    (default $HOME/Games/jc141/native-docs). Returns None if unparseable."""
    home = os.path.expanduser("~")
    jc = os.path.join(home, "Games", "jc141")
    rc = os.path.join(home, ".jc141rc")
    if os.path.isfile(rc):
        try:
            with open(rc) as f:
                for ln in f:
                    m = re.match(r"^\s*JC_DIRECTORY\s*=\s*(.*)$", ln)
                    if m and not ln.strip().startswith("#"):
                        val = os.path.expandvars(m.group(1).strip().strip('"').strip("'"))
                        if val:
                            jc = val
                        break
        except OSError:
            pass
    return os.path.join(jc, "native-docs")


def shim_locations():
    """Return (primary_dir, sandbox_dir, shim_path). The shim_path is the
    $HOME-relative form used in local.config (resolves in both launch modes)."""
    home = os.path.expanduser("~")
    primary_dir = os.path.join(home, SHIM_SUBDIR)
    sandbox = jc141_sandbox_home()
    sandbox_dir = os.path.join(sandbox, SHIM_SUBDIR) if sandbox else None
    return primary_dir, sandbox_dir, os.path.join(primary_dir, "libEGL.so.1")


def build_shim(primary_dir, force=False):
    """Write embedded sources and compile libEGL.so.1 in primary_dir.
    Returns (shim_path, rebuilt_bool)."""
    os.makedirs(primary_dir, exist_ok=True)
    src_path = os.path.join(primary_dir, "eglfix.c")
    map_path = os.path.join(primary_dir, "export.map")
    shim_path = os.path.join(primary_dir, "libEGL.so.1")

    src_written = write_if_changed(src_path, EGLFIX_C)
    write_if_changed(map_path, EXPORT_MAP)
    write_if_changed(os.path.join(primary_dir, "README.md"), SHIM_README)

    need_build = force or not os.path.isfile(shim_path) or src_written
    if not need_build:
        try:
            if os.path.getmtime(src_path) > os.path.getmtime(shim_path):
                need_build = True
        except OSError:
            need_build = True
    if not need_build:
        log("shim already built and up to date: %s" % shim_path)
        return shim_path, False

    log("compiling EGL interposer...")
    tmp = tempfile.mkdtemp(prefix="eglfix_build_")
    try:
        out_tmp = os.path.join(tmp, "libEGL.so.1")
        cmd = ["gcc", "-shared", "-fPIC", "-O2", "-Wall",
               "-o", out_tmp, src_path, "-ldl", "-pthread",
               "-Wl,-soname,libEGL.so.1", "-Wl,--version-script=%s" % map_path]
        r = run(cmd)
        if r.returncode != 0:
            raise SystemExit("ERROR: gcc failed:\n%s%s" % (r.stdout, r.stderr))
        shutil.copyfile(out_tmp, shim_path)
        os.chmod(shim_path, 0o755)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if shutil.which("readelf"):
        r = run(["readelf", "-d", shim_path])
        if "Library soname: [libEGL.so.1]" not in r.stdout:
            raise SystemExit("ERROR: built shim has wrong SONAME (expected libEGL.so.1)")
    log("built and verified: %s" % shim_path)
    return shim_path, True


def install_shims(primary_dir, sandbox_dir, force=False):
    """Build the shim and mirror it into the sandbox location. Returns shim_path."""
    shim_path, _ = build_shim(primary_dir, force=force)
    if sandbox_dir and os.path.abspath(sandbox_dir) != os.path.abspath(primary_dir):
        os.makedirs(sandbox_dir, exist_ok=True)
        dst = os.path.join(sandbox_dir, "libEGL.so.1")
        if not os.path.isfile(dst) or open(dst, "rb").read() != open(shim_path, "rb").read():
            shutil.copyfile(shim_path, dst)
            os.chmod(dst, 0o755)
            log("mirrored shim into sandbox home: %s" % dst)
        else:
            log("sandbox copy up to date: %s" % dst)
    return shim_path


def remove_legacy_game_dir_shim(game_dir):
    """Remove the old <game>/eglfix/ dir (pre-persistent-location layout)."""
    legacy = os.path.join(game_dir, "eglfix")
    if os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)
        log("removed legacy in-game eglfix/ dir: %s" % legacy)


def wire_config(cfg_path):
    """Idempotently set the ENV= fix line in local.config.

    Creates a minimal local.config if the repack didn't ship one (the jc141
    launcher would auto-generate its own all-commented copy otherwise)."""
    if not os.path.isfile(cfg_path):
        header = ("# this file is used by jc141 start scripts to specify game-specific settings\n"
                  + CREATED_MARKER + "\n")
        with open(cfg_path, "w") as f:
            f.write(header + ENV_LINE + "\n")
        log("created %s (repack had none) and set the ENV= fix line" % cfg_path)
        return True
    with open(cfg_path) as f:
        lines = f.readlines()

    # Drop stale commented-out ENV lines and old-format fix markers left by
    # earlier versions (e.g. '#ENV=REMOVED_BY_TEST', 'see eglfix/README.md')
    # so the config stays tidy.
    lines = [ln for ln in lines
             if not re.match(r"^\s*#\s*ENV=", ln)
             and "Factorio native-Wayland shader fix" not in ln]

    idx = None
    for i, ln in enumerate(lines):
        if re.match(r"^\s*ENV=", ln):
            idx = i
            break

    changed = False
    if idx is not None:
        if lines[idx].strip() == ENV_LINE:
            log("launcher config already wired correctly")
            return False
        lines[idx] = ENV_LINE + "\n"
        changed = True
        log("updated existing ENV= line in %s" % cfg_path)
    else:
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.strip() and not ln.startswith("#"):
                insert_at = i
                break
            insert_at = i + 1
        lines.insert(insert_at, ENV_LINE + "\n")
        lines.insert(insert_at, MARKER + "\n")
        changed = True
        log("added ENV= line to %s" % cfg_path)

    if changed:
        # Preserve the pristine config: only write the backup once.
        if not os.path.isfile(cfg_path + ".bak"):
            shutil.copyfile(cfg_path, cfg_path + ".bak")
        with open(cfg_path, "w") as f:
            f.writelines(lines)
    return changed


def uninstall_fix(game_dir):
    """Remove everything the installer generated (--reset)."""
    primary_dir, sandbox_dir, _ = shim_locations()
    removed = []
    for d in (primary_dir, sandbox_dir):
        if d and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    legacy = os.path.join(game_dir, "eglfix") if game_dir else None
    if legacy and os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)
        removed.append(legacy)

    cfg_path = os.path.join(game_dir, "local.config") if game_dir else None
    if cfg_path and os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
        if CREATED_MARKER in content:
            os.remove(cfg_path)
            removed.append(cfg_path)
            bak = cfg_path + ".bak"
            if os.path.isfile(bak):
                os.remove(bak)
                removed.append(bak)
        else:
            with open(cfg_path) as f:
                lines = f.readlines()
            kept = [ln for ln in lines
                    if not re.match(r"^\s*ENV=", ln)
                    and "Factorio native-Wayland shader fix" not in ln]
            if kept != lines:
                with open(cfg_path, "w") as f:
                    f.writelines(kept)
                removed.append("%s (ENV line removed)" % cfg_path)
            bak = cfg_path + ".bak"
            if os.path.isfile(bak) and "LD_PRELOAD" in open(bak).read():
                os.remove(bak)
                removed.append(bak)
    for r_ in removed:
        log("removed: %s" % r_)
    if not removed:
        log("nothing to remove -- fix was not installed")
    return removed


# --------------------------------------------------------------------------
# Launcher helpers
# --------------------------------------------------------------------------
def ensure_executables(game_dir):
    """Fresh jc141 extractions frequently lose exec bits (launchers and the
    DwarFS binary come out rw-r--r--), which breaks ./start.n.sh with
    'Permission denied'. Restore them so the game can actually be launched."""
    fixed = []
    for rel in ("start.n.sh", "space.age.sh", "actions.sh",
                os.path.join("files", "dwarfs-binary")):
        p = os.path.join(game_dir, rel)
        if os.path.isfile(p) and not os.access(p, os.X_OK):
            try:
                os.chmod(p, 0o755)
                fixed.append(rel)
            except OSError as e:
                log("could not chmod %s: %s" % (p, e), "!")
    if fixed:
        log("restored exec bit on: %s" % ", ".join(fixed))
    return fixed


def check_launcher_compat(game_dir):
    """The jc141 ENV feature needs start.n.sh to use $ENV word-split."""
    for launcher in ("start.n.sh", "space.age.sh"):
        p = os.path.join(game_dir, launcher)
        if os.path.isfile(p):
            with open(p) as f:
                content = f.read()
            if "ENV" not in content:
                log("WARNING: %s does not reference $ENV -- the fix may not auto-apply; "
                    "manually run: LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1 ./runtime/run.sh ./factorio"
                    % launcher, "!")


def run_smoke_test(shim_path):
    """Compile the smoke test and run it twice: baseline (expect FAIL) and with
    the shim (expect PASS). Returns True if the shim run passed."""
    tmp = tempfile.mkdtemp(prefix="eglfix_smoke_")
    try:
        test_c = os.path.join(tmp, "eglfix_test.c")
        test_bin = os.path.join(tmp, "eglfix_test")
        with open(test_c, "w") as f:
            f.write(SMOKE_TEST_C)
        r = run(["gcc", "-O2", "-o", test_bin, test_c, "-lEGL", "-ldl"])
        if r.returncode != 0:
            raise SystemExit("ERROR: smoke test compile failed:\n%s%s" % (r.stdout, r.stderr))

        base = run([test_bin])
        base_failed = base.returncode != 0 or "PASS" not in base.stdout
        log("baseline (no shim): %s"
            % ("reproduced crash condition (expected)" if base_failed else "UNEXPECTEDLY passed"))

        os.environ.pop("LD_PRELOAD", None)
        run(["rm", "-f", "/tmp/eglfix.log"])
        env = dict(os.environ)
        env["LD_PRELOAD"] = shim_path
        shim_run = run([test_bin], env=env)
        print("    " + shim_run.stdout.replace("\n", "\n    ").rstrip())
        if shim_run.returncode == 0 and "PASS" in shim_run.stdout:
            log("SMOKE TEST PASSED: shim re-binds the context (fix verified)")
            return True
        log("SMOKE TEST FAILED with the shim loaded. Check /tmp/eglfix.log:", "!")
        if os.path.isfile("/tmp/eglfix.log"):
            with open("/tmp/eglfix.log") as f:
                for ln in f.read().splitlines():
                    print("    " + ln)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def kill_game_procs(proc):
    """Kill the whole process tree we launched for verification. The launcher
    is spawned as a session leader, so killing its process group covers the
    start.n.sh script and the bwrap wrapper. NOTE: the jc141 launcher invokes
    bwrap with --new-session, so the sandboxed game lives in its OWN session
    outside our process group -- the pkill -x factorio fallback below is what
    actually kills the game. Do not "simplify" it away."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        subprocess.run(["pkill", "-9", "-x", "factorio"], capture_output=True)
    except Exception:
        pass
    time.sleep(2)


def verify_game_launch(game_dir, max_wait=75):
    """Launch the game via start.n.sh (shim applies automatically through the
    wired local.config) and check the log for either 'Factorio initialised'
    (PASS) or 'Failed to create shader' (FAIL). Requires a live Wayland/X11
    session; returns True/False, or None if the check can't run here."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        log("no display session (WAYLAND_DISPLAY/DISPLAY unset) -- "
            "skipping game launch check", "!")
        return None
    if subprocess.run(["pgrep", "-x", "factorio"],
                      capture_output=True).returncode == 0:
        log("a Factorio process is already running -- skipping game launch check", "!")
        return None
    launcher = os.path.join(game_dir, "start.n.sh")
    if not os.path.isfile(launcher):
        log("start.n.sh not found -- skipping game launch check", "!")
        return None
    if not os.access(launcher, os.X_OK):
        log("start.n.sh is not executable (chmod +x needed) -- skipping game launch check", "!")
        return None

    log("launching game via %s (up to %ss)..." % (launcher, max_wait))
    log_path = os.path.join(tempfile.gettempdir(), "eglfix_verify.log")
    try:
        os.remove(log_path)
    except OSError:
        pass
    with open(log_path, "w") as out:
        proc = subprocess.Popen([launcher], stdout=out, stderr=subprocess.STDOUT,
                                cwd=game_dir, start_new_session=True)
    ok = None
    try:
        start = time.time()
        while time.time() - start < max_wait:
            if proc.poll() is not None:
                log("launcher exited early (rc=%s)" % proc.returncode, "!")
                break
            time.sleep(2)
            try:
                with open(log_path) as f:
                    content = f.read()
            except OSError:
                continue
            if "Failed to create shader" in content:
                ok = False
                break
            if "Factorio initialised" in content:
                ok = True
                break
    finally:
        kill_game_procs(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    if ok is True:
        log("GAME LAUNCH CHECK PASSED: reached 'Factorio initialised' on native Wayland")
        return True
    if ok is False:
        log("GAME LAUNCH CHECK FAILED: 'Failed to create shader' appeared in the log", "!")
        return False
    log("GAME LAUNCH CHECK INCONCLUSIVE: no 'Factorio initialised' within %ss and no "
        "shader error either -- treat as unverified, not broken. Log: %s"
        % (max_wait, log_path), "!")
    return None


# --------------------------------------------------------------------------
# Inspection / troubleshooting
# --------------------------------------------------------------------------
def gather_state(game_dir):
    """Collect a list of (item, status, detail) rows for --check/--troubleshoot."""
    primary_dir, sandbox_dir, shim_path = shim_locations()
    rows = []

    def add(item, ok, detail=""):
        rows.append((item, "OK" if ok else "PROBLEM", detail))
        return ok

    add("Arch/pacman", distro_supports_pacman(), "pacman found" if distro_supports_pacman() else "pacman missing")
    for pkg in PACKAGES_FIX:
        add("package %s" % pkg, package_installed(pkg))
    add("gcc", shutil.which("gcc") is not None, shutil.which("gcc") or "missing")
    add("EGL headers", os.path.isfile("/usr/include/EGL/egl.h"), "/usr/include/EGL/egl.h")
    add("real libEGL", os.path.isfile("/usr/lib/libEGL.so.1"), "/usr/lib/libEGL.so.1")

    if game_dir:
        add("game dir", True, game_dir)
        add("start.n.sh exec", os.access(os.path.join(game_dir, "start.n.sh"), os.X_OK))
        cfg = os.path.join(game_dir, "local.config")
        wired = os.path.isfile(cfg) and ENV_LINE in open(cfg).read()
        add("launcher wired", wired, "ENV line in local.config" if wired else "ENV line missing")

    shim_ok = os.path.isfile(shim_path)
    add("shim (primary)", shim_ok, shim_path if shim_ok else "~/.factorio/wayland_fix/libEGL.so.1 missing")
    if sandbox_dir:
        s_ok = os.path.isfile(os.path.join(sandbox_dir, "libEGL.so.1"))
        add("shim (sandbox)", s_ok, os.path.join(sandbox_dir, "libEGL.so.1"))

    game_proc = subprocess.run(["pgrep", "-x", "factorio"], capture_output=True).returncode == 0
    rows.append(("game running", "RUNNING" if game_proc else "idle",
                 "a Factorio process is running" if game_proc else "not running (normal)"))
    return rows, primary_dir, sandbox_dir, shim_path


def inspect_state(game_dir):
    """--check: report state, return problem count."""
    rows, _, _, _ = gather_state(game_dir)
    rich_table("Factorio Wayland-fix inspection", rows)
    problems = sum(1 for _, status, _ in rows if status == "PROBLEM")
    print("\n  problems found: %d" % problems)
    return problems


def run_troubleshoot(game_dir):
    """--troubleshoot: full read-only diagnostic report."""
    print()
    rows, primary_dir, sandbox_dir, shim_path = gather_state(game_dir)
    rich_table("System & fix state", rows)

    print("\n-- Smoke test --")
    if shutil.which("gcc") and os.path.isfile(shim_path):
        try:
            run_smoke_test(shim_path)
        except SystemExit as e:
            print("  " + str(e))
    else:
        print("  skipped (gcc or shim missing)")

    print("\n-- Shim diagnostics (/tmp/eglfix.log) --")
    if os.path.isfile("/tmp/eglfix.log"):
        with open("/tmp/eglfix.log") as f:
            for ln in f.read().splitlines()[-12:]:
                print("  " + ln)
    else:
        print("  (no log yet -- the shim writes here when the game runs)")

    print("\n-- Game logs --")
    for logpath in (os.path.join(jc141_sandbox_home(), ".factorio", "factorio-current.log"),
                    os.path.join(os.path.expanduser("~"), ".factorio", "factorio-current.log")):
        if os.path.isfile(logpath):
            print("  %s:" % logpath)
            with open(logpath) as f:
                lines = f.read().splitlines()
            interesting = [ln for ln in lines if re.search(r"Error|error|Shader|initialised|Video driver|Failed", ln)]
            for ln in interesting[-8:]:
                print("    " + ln)
        else:
            print("  %s: (absent)" % logpath)

    print("\n-- Suggested fixes --")
    hints = []
    if game_dir and not os.access(os.path.join(game_dir, "start.n.sh"), os.X_OK):
        hints.append("launcher exec bits missing -> run the installer (no flags)")
    if not os.path.isfile(shim_path):
        hints.append("shim missing -> run the installer (no flags)")
    if game_dir:
        cfg = os.path.join(game_dir, "local.config")
        if not (os.path.isfile(cfg) and ENV_LINE in open(cfg).read()):
            hints.append("launcher not wired -> run the installer (no flags)")
    if not hints:
        hints.append("everything looks good -- if the game still crashes, run `--reset` then re-install")
    for h in hints:
        print("  * " + h)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Install/repair the Factorio native-Wayland shader fix.")
    ap.add_argument("--game-dir", help="Path to the Factorio jc141 repack dir "
                                       "(auto-detected if omitted)")
    ap.add_argument("--check", action="store_true",
                    help="Inspect state and exit (no changes)")
    ap.add_argument("--force", action="store_true",
                    help="Force rebuild of the shim even if up to date")
    ap.add_argument("--skip-packages", action="store_true",
                    help="Do not install packages with pacman")
    ap.add_argument("--with-testing-tools", action="store_true",
                    help="Also install wtype/grim/imagemagick (test tools)")
    ap.add_argument("--verify-game", action="store_true",
                    help="After installing, launch the game and confirm it "
                         "reaches 'Factorio initialised' (needs a display session)")
    ap.add_argument("--reset", action="store_true",
                    help="Remove everything the fix generated (shims, config line)")
    ap.add_argument("--troubleshoot", action="store_true",
                    help="Run a full diagnostic report (re-runs the smoke test; "
                         "writes only to /tmp)")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Answer yes to confirmation prompts")
    args = ap.parse_args()

    log("Factorio native-Wayland fix installer")
    if not distro_supports_pacman():
        raise SystemExit("ERROR: pacman not found. This installer targets Arch Linux "
                         "(the jc141 repack + Mesa/EGL setup this fix was developed on).")

    # Diagnostic/reset modes work even without a game dir (e.g. after the game
    # was deleted) -- the shim lives in $HOME, not in the game dir. Only the
    # install path requires the game.
    game_dir = find_game_dir(args.game_dir)
    if not game_dir and not (args.troubleshoot or args.reset or args.check):
        raise SystemExit("ERROR: could not find the Factorio game dir. "
                         "Pass --game-dir /path/to/Factorio_2.1.14")
    if game_dir:
        log("game dir: %s" % game_dir)
    else:
        log("game dir: not found (running %s in game-independent mode)"
            % ("--reset" if args.reset else "diagnostics"))

    if args.troubleshoot:
        run_troubleshoot(game_dir)
        sys.exit(0)

    if args.reset:
        if not args.yes:
            try:
                ans = input("This removes the shim + config wiring. Continue? [y/N] ")
            except EOFError:
                ans = "n"  # piped stdin (e.g. SUDO_STDIN=1) -> treat as no
            if ans.strip().lower() not in ("y", "yes"):
                log("aborted")
                sys.exit(1)
        uninstall_fix(game_dir)
        log("reset complete. Re-run without flags to reinstall.")
        sys.exit(0)

    if args.check:
        problems = inspect_state(game_dir)
        if problems:
            log("run the installer (no flags) to fix the %d problem(s) above" % problems, "!")
            sys.exit(2)
        log("everything looks good")
        sys.exit(0)

    ensure_executables(game_dir)

    pkgs = list(PACKAGES_FIX)
    if args.with_testing_tools:
        pkgs += PACKAGES_TEST_TOOLS
    install_packages(pkgs, args.skip_packages)
    check_prereqs()

    primary_dir, sandbox_dir, shim_path = shim_locations()
    shim = install_shims(primary_dir, sandbox_dir, force=args.force)
    remove_legacy_game_dir_shim(game_dir)

    cfg_path = os.path.join(game_dir, "local.config")
    wire_config(cfg_path)
    check_launcher_compat(game_dir)

    passed = run_smoke_test(shim)
    if not passed:
        raise SystemExit("ERROR: smoke test failed -- fix not verified. See output above.")

    if args.verify_game:
        print()
        game_ok = verify_game_launch(game_dir)
        if game_ok is False:
            raise SystemExit("ERROR: the game still failed to initialise -- fix NOT verified. "
                             "See the log above.")

    print()
    log("DONE. Factorio should now run on native Wayland: cd %s && ./start.n.sh" % game_dir)
    log("Shim installed: %s" % shim)
    log("Config: %s (ENV= line set; backup at local.config.bak if it existed)" % cfg_path)
    log("Diagnostic log written by the shim at /tmp/eglfix.log")
    if RICH:
        Console().print(Panel(
            "Game: [bold cyan]%s[/]\nShim: [bold green]%s[/]\nLaunch: [bold]cd %s && ./start.n.sh[/]"
            % (game_dir, shim, game_dir),
            title="Fix applied", border_style="green"))


if __name__ == "__main__":
    main()
