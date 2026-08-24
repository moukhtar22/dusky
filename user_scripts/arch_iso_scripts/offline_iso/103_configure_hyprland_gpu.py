#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
103_configure_hyprland_gpu.py
Arch ISO / Chroot GPU Configurator -> /etc/skel/.config/hypr/gpu.lua
v2026.07-Final | Python 3.14.6 | systemd 261 | Hyprland 0.55.4+

Adapted for execution during Arch installation phase.
Generates the configuration payload into the system skeleton directory.
"""

# ── 1. Rich bootstrap & fallbacks ──
import os, sys, shutil, subprocess, argparse, glob, pwd, re, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Attempt to load rich, attempt to install if missing, but do not crash on failure
try:
    import rich
    HAS_RICH = True
except Exception:
    pm = shutil.which("pacman")
    if pm:
        cmd = [pm, "-Sy", "--needed", "--noconfirm", "python-rich"]
        print(f"[BOOTSTRAP] Attempting to install python-rich: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
            import rich
            HAS_RICH = True
        except Exception as e:
            print(f"[BOOTSTRAP] Failed to install python-rich: {e}. Falling back to plain text.")
            HAS_RICH = False
    else:
        HAS_RICH = False

if HAS_RICH:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    console = Console()
else:
    def strip_rich(text: str) -> str:
        return re.sub(r'\[/?[a-zA-Z0-9_=# ,.:;@&-]*\]', '', text)

    class DummyTable:
        def __init__(self, title=None, **kwargs):
            self.title = title
            self.columns = []
            self.rows = []
        def add_column(self, name):
            self.columns.append(name)
        def add_row(self, *args):
            self.rows.append(args)

    class DummyPanel:
        def __init__(self, text, title=None, *args, **kwargs):
            self.text = text
            self.title = title
        @staticmethod
        def fit(text, title=None, *args, **kwargs):
            return DummyPanel(text, title)

    class DummyConsole:
        def print(self, *args, **kwargs):
            for arg in args:
                if isinstance(arg, DummyTable):
                    if arg.title:
                        print(f"\n=== {strip_rich(arg.title)} ===")
                    print(" | ".join(strip_rich(col) for col in arg.columns))
                    for row in arg.rows:
                        print(" | ".join(strip_rich(str(item)) for item in row))
                elif isinstance(arg, DummyPanel):
                    if arg.title:
                        print(f"\n--- {strip_rich(arg.title)} ---")
                    print(strip_rich(arg.text))
                else:
                    print(strip_rich(str(arg)))
    console = DummyConsole()
    Table = DummyTable
    Panel = DummyPanel

    class DummyPrompt:
        @staticmethod
        def ask(prompt, choices=None, default=None):
            clean_prompt = strip_rich(prompt)
            choice_str = f" ({'/'.join(choices)})" if choices else ""
            default_str = f" [{default}]" if default is not None else ""
            try:
                val = input(f"{clean_prompt}{choice_str}{default_str}: ").strip()
                if not val and default is not None:
                    return default
                return val
            except (KeyboardInterrupt, EOFError):
                print()
                raise KeyboardInterrupt
    Prompt = DummyPrompt

    class DummyProgress:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
        def add_task(self, description, **kwargs):
            print(f"Progress: {strip_rich(description)}")
            return 0
    Progress = DummyProgress
    SpinnerColumn = lambda *args: None
    TextColumn = lambda *args: None

# Attempt to load pyudev, attempt to install if missing, but do not crash on failure
try:
    import pyudev
    HAS_PYUDEV = True
except Exception:
    pm = shutil.which("pacman")
    if pm:
        cmd = [pm, "-Sy", "--needed", "--noconfirm", "python-pyudev"]
        print(f"[BOOTSTRAP] Attempting to install python-pyudev: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True)
            import pyudev
            HAS_PYUDEV = True
        except Exception as e:
            print(f"[BOOTSTRAP] Failed to install python-pyudev: {e}. Proceeding without pyudev.")
            HAS_PYUDEV = False
    else:
        HAS_PYUDEV = False

# ── 2. Privilege & package helpers ──
# Explicit output path for the installation phase skeleton
OUT_DEFAULT = Path("/etc/skel/.config/hypr/gpu.lua")
DRI_DIRS = [Path("/usr/lib/dri"), Path("/usr/lib64/dri")]

def pacman_install(pkgs: List[str]) -> bool:
    if not pkgs: return True
    pm = shutil.which("pacman")
    if not pm:
        console.print("[red]pacman not found[/]"); return False
    
    # ISO/Chroot environments require -Sy to pull updated databases
    cmd = [pm, "-Sy", "--needed", "--noconfirm"] + pkgs
    console.print(f"[yellow]Installing {', '.join(pkgs)}...[/]")
    try: subprocess.run(cmd, check=True); return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]pacman failed: {e}[/]"); return False

def ensure_bin(bin_name: str, pkg: str) -> bool:
    if shutil.which(bin_name): return True
    return pacman_install([pkg])

def ensure_py_module(mod: str, pkg: str) -> bool:
    try: __import__(mod); return True
    except ImportError:
        if pacman_install([pkg]):
            try: __import__(mod); return True
            except ImportError: return False
        return False

# ── 3. Data model ──
VENDOR_MAP = {
    "0x8086":"Intel","0x1002":"AMD","0x10de":"NVIDIA",
    "0x1af4":"RedHat VirtIO","0x15ad":"VMware","0x80ee":"VirtualBox",
    "0x1234":"QEMU Bochs","0x1414":"Hyper-V","0x1b36":"RedHat QXL","0x1013":"Cirrus",
}
@dataclass(slots=True)
class Gpu:
    dev_node: str; pci: str; vendor_id: str; vendor_label: str
    name: str; boot_vga: int; driver: str; by_path: str; is_real: bool

def sh(cmd: List[str]) -> str:
    try: return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except: return ""

def vendor_label(v: str) -> str:
    return VENDOR_MAP.get(v.lower(), f"Vendor {v}")

def pci_name(pci: str) -> str:
    if shutil.which("lspci"):
        out = sh(["lspci", "-s", pci])
        if out:
            m = re.match(r"^[0-9a-fA-F:.]+ [^:]+: (.+)$", out)
            return m.group(1) if m else out
    lp = Path(f"/sys/bus/pci/devices/{pci}/label")
    if lp.exists():
        try: return lp.read_text().strip()
        except: pass
    return "Unknown PCI Device"

def find_vendor_dir(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(10):
        if (cur / "vendor").exists(): return cur
        if cur == cur.parent: break
        cur = cur.parent
    return None

# ── 4. Topology discovery ──
def detect() -> List[Gpu]:
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as prog:
        prog.add_task("Scanning /sys/class/drm/card* + udev", total=None)
        raw = []
        for s in glob.glob("/sys/class/drm/card[0-9]*"):
            p = Path(s)
            if not re.fullmatch(r"card\d+", p.name): continue
            dev = f"/dev/dri/{p.name}"
            if not Path(dev).exists(): continue
            try: sys_dev = Path(os.path.realpath(p / "device"))
            except: continue
            vdir = find_vendor_dir(sys_dev)
            if not vdir:
                drv = "unknown"
                try: drv = Path(os.path.realpath(sys_dev / "driver")).name
                except: pass
                raw.append(Gpu(dev, f"platform:{p.name}", "0x0000", "Platform", f"Platform {p.name}", 0, drv, "unavailable", drv != "simpledrm"))
                continue
            try: vid = vdir.joinpath("vendor").read_text().strip().lower()
            except: continue
            pci = vdir.name
            boot = 0
            for bp in [vdir / "boot_vga", sys_dev / "boot_vga"]:
                if bp.exists():
                    try: boot = int(bp.read_text().strip())
                    except: boot = 0
                    break
            drv = "unknown"
            for d in [vdir / "driver", sys_dev / "driver"]:
                if d.exists():
                    try: drv = Path(os.path.realpath(d)).name
                    except: pass
                    break
            by = "unavailable"
            for l in glob.glob(f"/dev/dri/by-path/pci-{pci}*card"):
                if Path(l).exists(): by = l; break
            raw.append(Gpu(dev, pci, vid, vendor_label(vid), pci_name(pci), boot, drv, by, drv != "simpledrm"))

    if not raw:
        return []

    if HAS_PYUDEV:
        try:
            ctx = pyudev.Context()
            for d in ctx.list_devices(subsystem='drm', DEVTYPE='drm_minor'):
                if not d.sys_name.startswith("card"): continue
                node = d.device_node
                idp = d.get("ID_PATH")
                if idp and node:
                    cand = f"/dev/dri/by-path/{idp}-card"
                    for c in raw:
                        if c.dev_node == node and Path(cand).exists(): c.by_path = cand
        except Exception: pass

    real = [c for c in raw if c.is_real]
    return sorted(real if real else raw, key=lambda c: c.pci)

def default_gpu(cards: List[Gpu]):
    boots = [c for c in cards if c.boot_vga == 1]
    if not boots: return cards[0], "No boot_vga, lowest PCI"
    if len(boots) == 1: return boots[0], "boot_vga"
    return sorted(boots, key=lambda c: c.pci)[0], "Multiple boot_vga, lowest PCI"

def select_gpu(cards: List[Gpu], auto: bool):
    def_card, reason = default_gpu(cards)
    tbl = Table(title=f"GPU Topology - default {def_card.dev_node} ({reason})", show_header=True, header_style="bold magenta")
    for h in ["#","Node","Vendor","Name","PCI","Driver","Flags"]: tbl.add_column(h)
    for i, c in enumerate(cards, 1):
        flags = []
        if c.boot_vga: flags.append("[yellow]boot_vga[/]")
        if c.dev_node == def_card.dev_node: flags.append("[green]default[/]")
        if not c.is_real: flags.append("[dim]simpledrm/virt[/]")
        tbl.add_row(str(i), c.dev_node, c.vendor_label, c.name[:50], c.pci, c.driver, " ".join(flags))
    console.print(tbl)
    
    # Standardize on auto-execution for automated installation scripts
    if len(cards) == 1 or auto or not os.isatty(0):
        return def_card, "auto" if auto else "single"
    idx = str(cards.index(def_card) + 1)
    ans = Prompt.ask("Primary GPU", choices=[str(i) for i in range(1, len(cards) + 1)], default=idx)
    try: return cards[int(ans) - 1], "manual"
    except: return def_card, "manual"

# ── 5. VA-API probe ──
def probe_drivers():
    found = {}
    for k in ["nvidia", "nouveau", "iHD", "i965", "radeonsi"]:
        name = f"{k}_drv_video.so"
        found[k] = any((d / name).exists() for d in DRI_DIRS)
    return found

# ── 6. Lua generation ──
def gen_lua(primary: Gpu, ordered: List[Gpu], mode: str) -> str:
    pr = probe_drivers()
    L = []
    L.append("-- -----------------------------------------------------------------")
    L.append(f"-- Hyprland GPU | Mode: {mode.upper()} | Primary: {primary.dev_node}")
    L.append(f"-- GPU: {primary.vendor_label} | {primary.name} | {primary.pci} | drv:{primary.driver}")
    L.append("-- Gen: Python 3.14.6 + Rich + pyudev | systemd 261 | Hyprland 0.55.4+ Lua")
    L.append("-- DRM nodes shift per reboot - resolved via stable by-path")
    L.append("-- Exported automatically during installation phase.")
    L.append("-- -----------------------------------------------------------------")
    L.append("local function resolve_card(pci_address, fallback)")
    L.append(" local cmd = \"for d in /dev/dri/by-path/pci-\"..pci_address..\"*card; do [ -e \\\"$d\\\" ] && readlink -f \\\"$d\\\" 2>/dev/null && break; done\"")
    L.append(" local h = io.popen(cmd)")
    L.append(" if h then")
    L.append(" local path = h:read(\"*l\")")
    L.append(" h:close()")
    L.append(" if path and path ~= \"\" then return path end")
    L.append(" end")
    L.append(" return fallback")
    L.append("end")
    L.append("")
    parts = []
    for c in ordered:
        if c.pci.startswith("platform:"): parts.append(f'"{c.dev_node}"')
        else: parts.append(f'resolve_card("{c.pci}", "{c.dev_node}")')
    L.append(f'hl.env("AQ_DRM_DEVICES", { ".. \":\".. ".join(parts) })')
    L.append("")
    vid = primary.vendor_id.lower(); drv = primary.driver.lower(); vlabel = primary.vendor_label.lower()
    match vid:
        case "0x8086" | _ if "intel" in vlabel:
            L.append("-- Intel")
            if pr["iHD"]: L.append('hl.env("LIBVA_DRIVER_NAME", "iHD")')
            elif pr["i965"]: L.append('hl.env("LIBVA_DRIVER_NAME", "i965")')
        case "0x1002" | _ if "amd" in vlabel or "radeon" in primary.name.lower() or "amd" in primary.name.lower():
            L.append("-- AMD")
            if pr["radeonsi"]: L.append('hl.env("LIBVA_DRIVER_NAME", "radeonsi")')
        case "0x10de":
            if drv == "nvidia" or Path("/usr/lib/gbm/nvidia-drm_gbm.so").exists() or Path("/usr/lib64/gbm/nvidia-drm_gbm.so").exists():
                tgt = "nvidia"
            elif drv == "nouveau":
                tgt = "nouveau"
            else:
                tgt = drv

            if tgt == "nvidia":
                L.append("-- NVIDIA Proprietary")
                L.append('hl.env("GBM_BACKEND", "nvidia-drm")')
                L.append('hl.env("__GLX_VENDOR_LIBRARY_NAME", "nvidia")')
                if pr["nvidia"]: L.append('hl.env("LIBVA_DRIVER_NAME", "nvidia")')
            elif tgt == "nouveau":
                L.append("-- NVIDIA Nouveau")
                L.append('hl.env("MESA_LOADER_DRIVER_OVERRIDE", "nouveau")')
                if pr["nouveau"]: L.append('hl.env("LIBVA_DRIVER_NAME", "nouveau")')
            else:
                L.append(f"-- NVIDIA unknown driver {drv}")
        case _:
            if pr["radeonsi"] and "amd" in primary.name.lower():
                L.append('hl.env("LIBVA_DRIVER_NAME", "radeonsi")')
            L.append(f"-- Generic/VM ({primary.vendor_label}) - by-path only")
    L.append("")
    return "\n".join(L)

def atomic_write(p: Path, data: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        try:
            if p.read_text(encoding="utf-8") == data:
                console.print(f"[green][OK] Up to date: {p}[/]"); return
        except: pass
    
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".gpu.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        Path(tmp).replace(p)
        # Note: Do not run chown in /etc/skel/. 
        # Must be preserved as root:root so useradd -m properly processes it.
        console.print(f"[green][OK] Atomically written {p}[/]")
    finally:
        try: Path(tmp).unlink(missing_ok=True)
        except: pass

# ── 7. Robustness guards ──
REQUIRE_GPU_RE = re.compile(r'''require\s*\(\s*["']gpu["']\s*\)|require\s+["']gpu["']|pcall\s*\(\s*require\s*,\s*["']gpu["']''')

def ensure_privilege(out: Path) -> None:
    """Fail gracefully (clean message, no traceback) when the target is not writable."""
    if os.geteuid() == 0: return
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        ok = os.access(out.parent, os.W_OK)
    except PermissionError:
        ok = False
    if not ok:
        console.print(Panel.fit(
            f"[bold red]Permission denied[/] - cannot write to [cyan]{out}[/]\n"
            f"Run as root or use sudo, e.g.: [green]sudo {sys.argv[0]} {' '.join(sys.argv[1:])}[/]",
            style="bold red"))
        raise SystemExit(1)

def hyprland_version() -> Optional[str]:
    """Return 'major.minor' if a Hyprland binary is available, else None."""
    for binp, arg in ((shutil.which("hyprctl"), "version"), (shutil.which("hyprland"), "--version")):
        if not binp: continue
        try:
            out = subprocess.run([binp, arg], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"Hyprland\s+v?(\d+)\.(\d+)", out or "", re.IGNORECASE)
            if m: return f"{m.group(1)}.{m.group(2)}"
        except Exception: pass
    return None

def check_hyprland_version() -> None:
    """Guard: Lua configs (hl.env) only exist in Hyprland >= 0.55."""
    ver = hyprland_version()
    if ver is None:
        console.print("[dim][INFO] Hyprland not detectable (normal during ISO/chroot phase). gpu.lua requires Hyprland >= 0.55 (Lua configs).[/]")
        return
    major, minor = (int(x) for x in ver.split("."))
    if (major, minor) < (0, 55):
        console.print(Panel.fit(
            f"[bold red]Unsupported Hyprland {ver}[/] - Lua configs (hl.env) were introduced in 0.55.\n"
            "The generated gpu.lua will NOT be loaded. Upgrade to Hyprland >= 0.55,\n"
            "or set the equivalent env vars manually in hyprland.conf (env = KEY,value).",
            style="bold red"))
    else:
        console.print(f"[green][OK] Hyprland {ver} detected - Lua configs (>= 0.55) supported.[/]")

def nvidia_target(primary: Gpu) -> str:
    """Mirror gen_lua's driver resolution for the NVIDIA GBM branch."""
    if primary.vendor_id.lower() != "0x10de":
        return primary.driver.lower()
    drv = primary.driver.lower()
    if drv == "nvidia" or Path("/usr/lib/gbm/nvidia-drm_gbm.so").exists() or Path("/usr/lib64/gbm/nvidia-drm_gbm.so").exists():
        return "nvidia"
    elif drv == "nouveau":
        return "nouveau"
    return drv

def nvidia_modeset_confirmed(cards: Optional[List[Gpu]] = None) -> bool:
    """True if nvidia_drm.modeset=1 is active (sysfs, sudo fallback, cmdline, driver 560+ default, or DRM card node)."""
    # 1. Direct sysfs check
    p = Path("/sys/module/nvidia_drm/parameters/modeset")
    if p.exists():
        try:
            val = p.read_text().strip().lower()
            if val in ("y", "1"): return True
        except PermissionError:
            # 2. Non-interactive sudo cat fallback if parameter is mode 0400 (root-only)
            try:
                res = subprocess.run(["sudo", "-n", "cat", str(p)], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip().lower() in ("y", "1"):
                    return True
            except Exception: pass

    # 3. Kernel cmdline check
    try:
        cl = Path("/proc/cmdline").read_text()
        if "nvidia-drm.modeset=1" in cl or "nvidia_drm.modeset=1" in cl: return True
    except Exception: pass

    # 4. Check if NVIDIA driver version is >= 560 (modeset=1 enabled by default in driver)
    try:
        ver_p = Path("/sys/module/nvidia/version")
        if ver_p.exists():
            ver_str = ver_p.read_text().strip()
            m = re.match(r"^(\d+)", ver_str)
            if m and int(m.group(1)) >= 560:
                return True
    except Exception: pass

    # 5. Check DRM node presence for NVIDIA card (nvidia-drm only creates DRM card nodes when modeset=1)
    if cards:
        for c in cards:
            if c.vendor_id.lower() == "0x10de" and c.driver.lower() == "nvidia" and c.dev_node.startswith("/dev/dri/card"):
                return True

    return False

def wire_gpu_require(out: Path) -> None:
    """Verify (and idempotently auto-wire) pcall(require, "gpu") in the sibling hyprland.lua."""
    hl = out.parent / "hyprland.lua"
    if not hl.exists():
        console.print("[yellow][WARN] No hyprland.lua beside gpu.lua - ensure your installer creates it with: pcall(require, \"gpu\")[/]")
        return
    try:
        text = hl.read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"[yellow][WARN] Cannot read {hl}: {e}. Ensure it contains: pcall(require, \"gpu\")[/]")
        return
    # Ignore commented-out lines so '-- pcall(require, "gpu")' doesn't count as wired
    for line in text.splitlines():
        if line.lstrip().startswith("--"): continue
        if REQUIRE_GPU_RE.search(line):
            console.print(f"[green][OK] {hl} already loads gpu.lua[/]")
            return
    # Only auto-wire Lua-style configs (Hyprland >= 0.55); never touch hyprlang key-value configs
    looks_lua = ("require(" in text) or ("hl.env" in text) or text.lstrip().startswith("--")
    if not looks_lua:
        console.print("[yellow][WARN] hyprland.lua does not look like a Lua config (Hyprland < 0.55?). Skipping auto-wire - add manually: pcall(require, \"gpu\")[/]")
        return
    try:
        with open(hl, "a", encoding="utf-8") as f:
            f.write("\n-- Auto-added by 103_configure_hyprland_gpu.py\npcall(require, \"gpu\")\n")
        console.print(f"[green][OK] Auto-wired: added 'pcall(require, \"gpu\")' to {hl}[/]")
    except Exception as e:
        console.print(f"[yellow][WARN] Could not auto-wire {hl}: {e}. Add manually: pcall(require, \"gpu\")[/]")

def main():
    ap = argparse.ArgumentParser(description="Generate /etc/skel/.config/gpu.lua")
    ap.add_argument("--auto", action="store_true", help="Non-interactive execution suitable for install scripts")
    ap.add_argument("--output", type=Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    ensure_bin("lspci", "pciutils")
    # pyudev is optional and not strictly required since glob fallback is available

    out = args.output
    console.print(Panel.fit(f"Arch ISO Hyprland Skeleton Generator\nPython {'.'.join(map(str, sys.version_info[:3]))} | Targeted Output: {out}", style="bold cyan"))

    ensure_privilege(out)        # clean error (no traceback) for non-root runs
    check_hyprland_version()     # warn if Hyprland < 0.55 (no Lua config support)

    cards = detect()
    if not cards:
        console.print("[yellow][WARN] No DRM nodes detected. Writing empty GPU config fallback to avoid boot crash.[/]")
        atomic_write(out, "-- No DRM nodes detected during installation.\n")
        return
    primary, mode = select_gpu(cards, args.auto)
    ordered = [primary] + [c for c in cards if c.dev_node != primary.dev_node]
    
    lua = gen_lua(primary, ordered, mode)
    atomic_write(out, lua)

    # NVIDIA: GBM_BACKEND=nvidia-drm requires nvidia_drm.modeset=1 (black-screen risk when primary;
    # PRIME offload on hybrid laptops also needs it). Warn whenever an NVIDIA proprietary GPU is present.
    nv_primary = primary.vendor_id.lower() == "0x10de" and nvidia_target(primary) == "nvidia"
    nv_any = any(c.vendor_id.lower() == "0x10de" and nvidia_target(c) == "nvidia" for c in cards)
    if nv_any and not nvidia_modeset_confirmed(cards):
        if nv_primary:
            console.print(Panel.fit(
                "[bold red]WARNING: nvidia_drm.modeset=1 not confirmed[/]\n"
                "GBM_BACKEND=nvidia-drm requires kernel modesetting on the NVIDIA driver,\n"
                "otherwise Hyprland may fail to start (black screen). Add to kernel params:\n"
                "  [green]nvidia_drm.modeset=1[/]   (verify: cat /sys/module/nvidia_drm/parameters/modeset)",
                style="bold red"))
        else:
            console.print("[yellow][NOTE] NVIDIA GPU present (offload). If you plan to use it (DRI_PRIME/offload apps),\n"
                          "        add [green]nvidia_drm.modeset=1[/] to kernel params - PRIME offload requires it.[/]")

    console.print("\n[bold]Preview:[/]")
    for l in lua.splitlines():
        if any(k in l for k in ("AQ_DRM", "LIBVA", "GBM_", "__GLX", "MESA_")):
            console.print(f" {l}")

    wire_gpu_require(out)   # verify / idempotently auto-wire pcall(require, "gpu") in hyprland.lua
    console.print(f"File successfully staged at: [cyan]{out}[/]")

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt:
        console.print("\n[red]Aborted[/]"); raise SystemExit(130)
