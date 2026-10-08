#!/usr/bin/env python3
"""gpu-disable-toggle — completely disable dedicated GPU(s) on Arch / kernel 7.2+.

Disables a discrete GPU so no userspace DRM driver ever binds it (hibernates
hybrid laptops, silences a dGPU you never use). Vendor-agnostic: NVIDIA, AMD,
Intel-dGPU, or anything else presenting as VGA/3D/Display in sysfs.

Method (minimal, proven):
  1. udev hide rule → ATTR{remove}="1" on add for exact PCI addresses and IDs,
     so the GPU is logically removed from the PCI bus at boot coldplug and any
     rescan. Undetectable to lspci / fastfetch / apps; can never be woken.
  2. /etc/modprobe.d/99-gpu-disable.conf → options vfio-pci ids=<slot IDs>,
     blacklist <vendor DRM drivers>, softdep <each> pre: vfio-pci (safety net
     if the bus is ever rescanned). Shared audio/USB are softdep'd, NEVER
     blacklisted.
  3. Kernel cmdline → vfio-pci.ids + module_blacklist + iommu=pt +
     intel_iommu=on (Intel) — vfio-pci probe fails with -EINVAL without IOMMU.
  4. mkinitcpio drop-in → early vfio stub so nothing else probes the card.

Enable reverses all of the above from /var/lib/gpu-disable/state.json.

Targets kernel 7.2+ and Python 3.14.7+ only. No legacy fallbacks.

Usage:
  ./gpu_disable_toggle.py --status
  ./gpu_disable_toggle.py --disable [--slot 0000:01:00] [--all] [--dry-run] [--no-rebuild]
  ./gpu_disable_toggle.py --enable [--dry-run] [--no-rebuild]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

MIN_PY = (3, 14, 7)
MIN_KERNEL = (7, 2)


def check_versions() -> None:
    if sys.version_info[:3] < MIN_PY:
        sys.stderr.write(
            f"[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required, "
            f"have {sys.version.split()[0]}.\n"
        )
        raise SystemExit(1)
    m = re.match(r"(\d+)\.(\d+)", Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip())
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_KERNEL:
        sys.stderr.write("[FATAL] Kernel 7.2+ required.\n")
        raise SystemExit(1)


# Enforce Python version before third-party imports
if sys.version_info[:3] < MIN_PY:
    sys.stderr.write(
        f"[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required, "
        f"have {sys.version.split()[0]}.\n"
    )
    raise SystemExit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt
    from rich.table import Table
except ModuleNotFoundError:
    sys.stderr.write("[FATAL] 'python-rich' is missing: pacman -S python-rich\n")
    raise SystemExit(1)

console = Console()

MODPROBE_FILE = Path("/etc/modprobe.d/99-gpu-disable.conf")
UDEV_RULE = Path("/etc/udev/rules.d/99-gpu-hide.rules")
MKINITCPIO_CONF = Path("/etc/mkinitcpio.conf")
MKINITCPIO_DROPIN_DIR = Path("/etc/mkinitcpio.conf.d")
MKINITCPIO_DROPIN = MKINITCPIO_DROPIN_DIR / "99-gpu-disable.conf"
KERNEL_CMDLINE = Path("/etc/kernel/cmdline")
CMDLINE_D = Path("/etc/cmdline.d")
CMDLINE_D_DROPIN = CMDLINE_D / "99-gpu-disable.conf"
STATE_DIR = Path("/var/lib/gpu-disable")
STATE_FILE = STATE_DIR / "state.json"
ASUS_DGPU_DISABLE = Path("/sys/devices/platform/asus-nb-wmi/dgpu_disable")
ASUS_ARMOURY_DGPU_DISABLE = Path("/sys/devices/virtual/firmware-attributes/asus-armoury/attributes/dgpu_disable/current_value")
ASUS_TMPFILES = Path("/etc/tmpfiles.d/99-asus-dgpu-disable.conf")

# Only these cmdline keys are owned. The iommu keys are required: without an
# active IOMMU, vfio-pci probe fails with -EINVAL and the GPU stays half-claimed.
MANAGED_KEYS = ("vfio-pci.ids", "module_blacklist", "iommu", "intel_iommu", "amd_iommu")
VOLATILE_TOKENS = {
    "single", "1", "s", "S", "rescue", "emergency", "init=/bin/sh",
    "systemd.unit=rescue.target", "systemd.unit=emergency.target",
    "systemd.debug-shell",
}
VOLATILE_PREFIXES = ("BOOT_IMAGE=", "initrd=")

GPU_CLASSES = {"0300", "0301", "0302", "0380"}

# DRM drivers to keep off the disabled card, per PCI GPU vendor.
VENDOR_DRM: dict[str, list[str]] = {
    "10de": ["nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nvidia_peermem"],
    "1002": ["amdgpu", "radeon"],
    "8086": ["i915", "xe"],
    "1af4": ["virtio_gpu"],
}

# Shared host infrastructure: reroute ordering only, never blacklist.
NEVER_BLACKLIST = {
    "snd_hda_intel", "snd_hda_codec_hdmi", "xhci_pci", "xhci_hcd",
    "i2c_designware_pci", "typec_ucsi", "ucsi_ccg", "ahci", "nvme",
}

SYS_PCI = Path("/sys/bus/pci/devices")

DRY_RUN = False


# --------------------------------------------------------------------------
# bootstrap / guards
# --------------------------------------------------------------------------

def elevate() -> None:
    if os.geteuid() == 0:
        return
    sudo = shutil.which("sudo")
    if sudo is None:
        sys.stderr.write("[FATAL] Need root (sudo not found). Re-run as root.\n")
        raise SystemExit(1)
    try:
        script = Path(sys.argv[0]).resolve(strict=True)
    except OSError:
        script = Path(sys.argv[0]).resolve()
    sys.stderr.write("[INFO] Elevating via sudo…\n")
    os.execv(sudo, [sudo, "--", sys.executable, str(script), *sys.argv[1:]])


def bail(msg: str) -> NoReturn:
    console.print(Panel(f"[bold red]FATAL[/bold red]\n{msg}", border_style="red"))
    raise SystemExit(1)


def run(argv: list[str], *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Execute command safely, converting missing binary or timeout into a CompletedProcess."""
    try:
        return subprocess.run(
            argv, text=True, capture_output=True, timeout=timeout,
            stdin=subprocess.DEVNULL, check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: command not found\n")
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]  ! {argv[0]} timed out after {timeout}s.[/yellow]")
        return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]}: timed out after {timeout}s\n")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 126, "", str(exc))


def atomic_write(path: Path, content: str) -> bool:
    """Write atomically and durably, inheriting existing mode/ownership. Returns True if changed."""
    if path.is_symlink():
        path = Path(os.path.realpath(path))
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (UnicodeDecodeError, OSError):
            pass
        st = path.stat()
        mode, uid, gid = stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid
    else:
        mode, uid, gid = 0o644, 0, 0
    if DRY_RUN:
        console.print(f"[magenta]  [dry-run] would write {path}[/magenta]")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(content)
            h.flush()
            os.fsync(h.fileno())
        # vfat rejects chown/chmod outside mount options; ignore failure gracefully
        try:
            os.chmod(tmp, mode)
            os.chown(tmp, uid, gid)
        except OSError:
            pass
        os.replace(tmp, path)
        # Flush directory metadata on parent directory (critical for durability on vfat ESP)
        try:
            dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
        return True
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def state_load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def state_save(**kv: object) -> None:
    if DRY_RUN:
        return
    data = state_load()
    data.update(kv)
    data["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    atomic_write(STATE_FILE, json.dumps(data, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# PCI discovery (sysfs is the source of truth)
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PciDevice:
    addr: str
    vendor: str
    device: str
    klass: str
    driver: str | None
    boot_vga: bool
    label: str

    @property
    def ids(self) -> str:
        return f"{self.vendor}:{self.device}"

    @property
    def slot(self) -> str:
        return self.addr.rsplit(".", 1)[0]

    @property
    def klass4(self) -> str:
        return self.klass[:4]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def lspci_labels() -> dict[str, str]:
    if shutil.which("lspci") is None:
        return {}
    res = run(["lspci", "-Dmm"], timeout=60)
    labels: dict[str, str] = {}
    for line in res.stdout.splitlines():
        try:
            f = shlex.split(line)
        except ValueError:
            continue
        if len(f) >= 4:
            labels[f[0]] = f"{f[2]} {f[3]}"
    return labels


def enumerate_pci() -> list[PciDevice]:
    labels = lspci_labels()
    devs: list[PciDevice] = []
    if not SYS_PCI.is_dir():
        bail("/sys/bus/pci/devices missing — not a PCI Linux host?")
    for node in sorted(SYS_PCI.iterdir()):
        vendor = _read(node / "vendor").removeprefix("0x")
        device = _read(node / "device").removeprefix("0x")
        klass = _read(node / "class").removeprefix("0x")
        if not vendor or not device or not klass:
            continue
        link = node / "driver"
        driver = link.resolve().name if link.is_symlink() else None
        devs.append(PciDevice(
            addr=node.name, vendor=vendor, device=device, klass=klass,
            driver=driver, boot_vga=_read(node / "boot_vga") == "1",
            label=labels.get(node.name, "unknown device"),
        ))
    return devs


@dataclass
class Claim:
    """Whole-slot claim: every PCI function in the slot(s) moves together."""
    functions: list[PciDevice] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return sorted({d.ids for d in self.functions})

    @property
    def addrs(self) -> list[str]:
        return [d.addr for d in self.functions]

    @property
    def slots(self) -> list[str]:
        return sorted({d.slot for d in self.functions})


def gpu_slots(devices: list[PciDevice]) -> list[str]:
    return sorted({d.slot for d in devices if d.klass[:4] in GPU_CLASSES})


def normalize_slot(slot_arg: str, slots: list[str]) -> str | None:
    if "." in slot_arg:
        slot_arg = slot_arg.rsplit(".", 1)[0]
    if slot_arg in slots:
        return slot_arg
    hits = [s for s in slots if s.endswith(slot_arg)]
    return hits[0] if len(hits) == 1 else None


def claim_from_state(state: dict) -> Claim | None:
    """Rebuild a claim for a slot already hidden from the PCI bus.

    After bus removal sysfs no longer enumerates the functions, so the live
    lookup fails. State (written by the run that hid it) is the source of truth.
    """
    funcs_meta = state.get("functions", [])
    if funcs_meta:
        funcs = [
            PciDevice(addr=f["addr"], vendor=f["ids"].split(":")[0],
                      device=f["ids"].split(":")[1], klass=f.get("klass4", "0300") + "00",
                      driver=None, boot_vga=False, label="hidden (removed from PCI bus)")
            for f in funcs_meta if "addr" in f and "ids" in f and ":" in f["ids"]
        ]
        if funcs:
            return Claim(functions=funcs)
    addrs = sorted(state.get("addrs", []))
    ids = state.get("ids", [])
    if not addrs and not ids:
        return None
    funcs = []
    for i, addr in enumerate(addrs):
        id_str = ids[i] if i < len(ids) else (ids[0] if ids else "unknown:unknown")
        ven, did = id_str.split(":", 1) if ":" in id_str else ("unknown", "unknown")
        funcs.append(PciDevice(
            addr=addr, vendor=ven, device=did,
            klass="030000", driver=None, boot_vga=False,
            label="hidden (removed from PCI bus)"
        ))
    return Claim(functions=funcs) if funcs else None


def drm_blacklist_for(devices: list[PciDevice], claim: Claim) -> tuple[set[str], set[str]]:
    """Return (softdeps, blacklist). Shared host infra is softdep'd, never blacklisted."""
    softdeps: set[str] = set()
    for dev in claim.functions:
        drv = dev.driver.replace("-", "_") if dev.driver else None
        if drv and drv not in {"vfio_pci", "pcieport"}:
            softdeps.add(drv)
        if dev.klass4 in GPU_CLASSES:
            softdeps.update(VENDOR_DRM.get(dev.vendor, []))
        if dev.klass4 == "0403":
            softdeps.add("snd_hda_intel")
        if dev.klass4 in ("0c03", "0c80"):
            softdeps.add("xhci_pci")

    survivors = {d.vendor for d in devices
                 if d.klass4 in GPU_CLASSES and d.addr not in claim.addrs}
    blacklist: set[str] = set()
    for dev in claim.functions:
        if dev.klass4 not in GPU_CLASSES:
            continue
        if dev.vendor in survivors:
            console.print(
                f"[yellow]  ! Another {dev.vendor} GPU stays on the host; its driver "
                "will NOT be blacklisted (softdep ordering only).[/yellow]"
            )
            continue
        blacklist.update(VENDOR_DRM.get(dev.vendor, []))
        drv = dev.driver.replace("-", "_") if dev.driver else None
        if drv and drv not in NEVER_BLACKLIST and drv != "vfio_pci":
            # Live driver unknown to the map: still block it.
            blacklist.add(drv)
    blacklist -= NEVER_BLACKLIST
    softdeps -= {"vfio_pci"}
    return softdeps, blacklist


def render_gpus(devices: list[PciDevice], slots: list[str]) -> None:
    table = Table(title="GPU slots (sysfs)", header_style="bold magenta")
    table.add_column("#", justify="center", style="cyan")
    table.add_column("Slot", style="dim")
    table.add_column("Functions", style="green")
    table.add_column("Driver", style="yellow")
    table.add_column("Flags")
    for i, slot in enumerate(slots, 1):
        funcs = [d for d in devices if d.slot == slot]
        fn = "\n".join(f"{d.addr} [{d.ids}] {d.klass4} {d.label}" for d in funcs)
        drv = "\n".join(d.driver or "-" for d in funcs)
        flags = []
        if any(d.boot_vga for d in funcs):
            flags.append("[bold red]boot_vga[/bold red]")
        if any(d.driver == "vfio-pci" for d in funcs):
            flags.append("[green]vfio-pci[/green]")
        table.add_row(str(i), slot, fn, drv, " ".join(flags))
    console.print(table)


def select_claim(devices: list[PciDevice], slots: list[str], args: argparse.Namespace) -> Claim:
    if args.slot:
        slot = normalize_slot(args.slot, slots)
        if slot is None:
            # May already be hidden from the bus — caller falls back to state.
            return Claim()
        funcs = [d for d in devices if d.slot == slot]
        return Claim(functions=funcs)

    dedicated = [s for s in slots
                 if not any(d.boot_vga for d in devices if d.slot == s)]
    if args.all:
        if not dedicated:
            bail("No non-boot_vga GPU slot found — nothing safe to disable. "
                 "Use --slot with --allow-boot-vga if you really mean the boot GPU.")
        funcs = [d for d in devices if d.slot in dedicated]
        return Claim(functions=funcs)

    headless = args.auto or not sys.stdin.isatty()
    if len(dedicated) == 1 and headless:
        console.print(f"[dim]Single dedicated GPU slot: {dedicated[0]}[/dim]")
        return Claim(functions=[d for d in devices if d.slot == dedicated[0]])
    if len(slots) == 1 and headless:
        return Claim(functions=[d for d in devices if d.slot == slots[0]])
    if headless:
        render_gpus(devices, slots)
        bail(f"Ambiguous: {len(slots)} GPU slot(s) and {len(dedicated)} dedicated -- "
             "no interactive TTY to ask. Pass --slot <addr> or --all.")

    render_gpus(devices, slots)
    idx = IntPrompt.ask("Slot to disable", choices=[str(i + 1) for i in range(len(slots))])
    slot = slots[idx - 1]
    return Claim(functions=[d for d in devices if d.slot == slot])


def check_id_collisions(devices: list[PciDevice], claim: Claim) -> None:
    """vfio-pci.ids and module_blacklist are ID-based, not address-based.

    If a device outside the claim shares vendor:device with one inside it, the
    selection cannot be expressed without collateral damage -- the surviving
    device would get bound by vfio-pci and its driver blacklisted anyway.
    Refuse rather than half-disable a surviving GPU.
    """
    claimed = set(claim.ids)
    strays = sorted(f"{d.addr} [{d.ids}] {d.klass4} {d.label}"
                    for d in devices
                    if d.ids in claimed and d.addr not in claim.addrs)
    if strays:
        detail = "\n  ".join(strays)
        bail(f"ID collision: the following devices outside the selected claim share "
             f"IDs with claimed hardware:\n  {detail}\n\n"
             "vfio-pci.ids and module_blacklist are ID-based, so they would be disabled too.\n"
             "Use --all to take all matching GPU slots, or pick a different slot.")


def guard_claim(claim: Claim, devices: list[PciDevice], allow_boot_vga: bool,
                *, from_state: bool = False) -> None:
    if from_state:
        # Claim was synthesised from state.json because the card is already hidden
        # from the PCI bus. Live-topology checks are meaningless here.
        return
    if any(d.boot_vga for d in claim.functions):
        if not allow_boot_vga:
            slots = ", ".join(claim.slots)
            bail(f"Slot {slots} is the firmware boot VGA device. Refusing to blind "
                 "the host console. Re-run with --allow-boot-vga if you truly have "
                 "serial/SSH access and mean it.")
        console.print("[bold yellow]  ! Disabling the boot_vga device. Host console will go dark.[/bold yellow]")
    remaining = [s for s in gpu_slots(devices) if s not in claim.slots]
    if not remaining and not allow_boot_vga:
        bail("This would disable the last display GPU in the system. Re-run with "
             "--allow-boot-vga (and preferably SSH access) if you mean it.")
    if not remaining:
        console.print("[bold yellow]  ! This disables the last display GPU in the system.[/bold yellow]")
        if sys.stdin.isatty():
            if not Confirm.ask("Disable the last GPU anyway?", default=False):
                raise SystemExit(0)


def warn_modprobe_conflicts() -> None:
    """modprobe concatenates every matching 'options' line -- duplicates are
    ambiguous. Also flag any hybrid-GPU managers which fight this tool."""
    pat = re.compile(r"^\s*options\s+vfio[-_]pci\b.*\bids=", re.MULTILINE)
    for d in (Path("/etc/modprobe.d"), Path("/run/modprobe.d"), Path("/usr/lib/modprobe.d")):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.conf")):
            if f.name == MODPROBE_FILE.name:
                continue
            if pat.search(_read(f)):
                console.print(f"[bold yellow]  ! {f} also sets 'options vfio-pci ids=' -- "
                              "modprobe merges both lines and the winner is undefined. "
                              "Remove or merge it.[/bold yellow]")
    for unit in ("optimus-manager.service", "supergfxd.service"):
        if run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0:
            console.print(f"[bold yellow]  ! {unit} is enabled; it rebinds GPU drivers at "
                          "runtime and will fight this configuration.[/bold yellow]")


# --------------------------------------------------------------------------
# kernel cmdline merge
# --------------------------------------------------------------------------

def desired_params(blacklist: set[str], ids: list[str], vendor: str, amd_force: bool) -> dict[str, str]:
    params: dict[str, str] = {"iommu": "pt"}
    match vendor:
        case "intel":
            params["intel_iommu"] = "on"
        case "amd":
            # AMD-Vi is on by default with a sane IVRS; only force on request.
            if amd_force:
                params["amd_iommu"] = "force_enable"
        case _:
            console.print("[yellow]  ! Unknown CPU vendor; emitting iommu=pt only.[/yellow]")
    if ids:
        params["vfio-pci.ids"] = ",".join(sorted(ids))
    if blacklist:
        params["module_blacklist"] = ",".join(sorted(blacklist))
    return params


def cpu_vendor() -> str:
    text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    if "GenuineIntel" in text:
        return "intel"
    if "AuthenticAMD" in text:
        return "amd"
    return "unknown"


def merge_cmdline(current: str, desired: dict[str, str]) -> str:
    """Strip volatile + managed tokens from `current`, then append `desired`."""
    try:
        tokens = shlex.split(current, posix=False)
    except ValueError:
        tokens = current.split()
    kept: list[str] = []
    tail: list[str] = []
    seen_sep = False
    for tok in tokens:
        if tok == "--":
            seen_sep = True
            continue
        if seen_sep:
            tail.append(tok)
            continue
        if tok in VOLATILE_TOKENS or tok.startswith(VOLATILE_PREFIXES):
            continue
        if tok.split("=", 1)[0].replace("vfio_pci.", "vfio-pci.") in MANAGED_KEYS:
            continue
        kept.append(tok)
    for key in MANAGED_KEYS:
        if key in desired:
            kept.append(f"{key}={desired[key]}")
    if tail:
        kept += ["--", *tail]
    return " ".join(kept)


# --------------------------------------------------------------------------
# bootloader
# --------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BootEntry:
    kind: str  # "type1" | "type2"
    path: Path | None
    options: str
    ident: str


def _esp_roots() -> list[Path]:
    roots: list[Path] = []
    for flag in ("-x", "-p"):
        res = run(["bootctl", flag])
        if res.returncode == 0 and res.stdout.strip():
            cand = Path(res.stdout.splitlines()[0].strip())
            if cand.is_dir() and cand not in roots:
                roots.append(cand)
    for cand in (Path("/boot"), Path("/efi")):
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    return roots


def scan_entries_on_disk() -> BootEntry | None:
    """Fallback for ESPs bootctl rejects (e.g. non-ESP partition type):
    scan /boot and /efi directly, honouring loader.conf default."""
    for root in _esp_roots():
        entries = root / "loader" / "entries"
        if not entries.is_dir():
            continue
        confs = sorted(entries.glob("*.conf"))
        if not confs:
            continue
        lc = root / "loader" / "loader.conf"
        if lc.is_file():
            m = re.search(r"^default\s+(\S+)", lc.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                want = m.group(1) if m.group(1).endswith(".conf") else m.group(1) + ".conf"
                hit = [c for c in confs if c.name == want]
                if hit:
                    return BootEntry("type1", hit[0], "", hit[0].name)
        for name in ("arch-linux.conf", "arch.conf"):
            hit = entries / name
            if hit.exists():
                return BootEntry("type1", hit, "", hit.name)
        return BootEntry("type1", confs[0], "", confs[0].name)
    return None


def _patchable_type(typ: str) -> bool:
    """Accept both bootctl JSON schemas: 'Type #1' (older) and 'type1' (systemd 261+)."""
    return typ in ("type1", "type2") or typ.startswith("Type #")


def _resolve_entry_path(entry: dict, ident: str) -> Path | None:
    # New schema: absolute "path". Old schema: ESP-relative "source".
    for key in ("path", "source"):
        ps = entry.get(key)
        if isinstance(ps, str) and ps and ps != "esp":
            cand = Path(re.sub(r"/{2,}", "/", ps))
            if cand.suffix == ".conf" and cand.exists():
                return cand
    if ident.endswith(".conf"):
        for root in _esp_roots():
            hit = root / "loader" / "entries" / ident
            if hit.exists():
                return hit
    return None


def find_boot_entry(*, quiet: bool = False) -> BootEntry | None:
    res = run(["bootctl", "list", "--json=short"])
    raw: list[dict] = []
    if res.returncode == 0 and res.stdout.strip().startswith(("[", "{")):
        try:
            parsed = json.loads(res.stdout)
            raw = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            raw = []
    if raw:
        def score(e: dict) -> int:
            # The DEFAULT entry boots next — it outranks merely-selected.
            dfl = bool(e.get("isDefault", e.get("is_default", False)))
            sel = bool(e.get("isSelected", e.get("is_selected", False)))
            return (2 if dfl else 0) + (1 if sel else 0)
        cands = [e for e in raw if isinstance(e, dict) and _patchable_type(str(e.get("type", "")))]
        if cands:
            best = max(cands, key=score)
            typ = str(best.get("type", ""))
            kind = "type2" if typ == "type2" or typ.startswith("Type #2") else "type1"
            ident = str(best.get("id", "?"))
            path = _resolve_entry_path(best, ident)
            why = "default" if score(best) >= 2 else "selected"
            if not quiet:
                console.print(f"[dim]Boot entry: {ident} ({kind}, {why})[/dim]")
            if kind == "type1" and path is None:
                if not quiet:
                    console.print("[yellow]  ! bootctl gave no usable .conf path; "
                                  "falling back to an on-disk scan.[/yellow]")
            else:
                return BootEntry(kind, path,
                                 str(best.get("options", best.get("cmdline", "")) or ""),
                                 ident)
    # Fallback: on-disk scan (covers ESPs bootctl rejects by partition type).
    found = scan_entries_on_disk()
    if found is not None:
        if not quiet:
            console.print(f"[yellow]  ! bootctl JSON unavailable; using {found.path}[/yellow]")
        return found
    return None


def resolve_boot_entry() -> BootEntry:
    entry = find_boot_entry()
    if entry is None:
        bail("No systemd-boot Type #1/#2 entry found to patch.")
    return entry


def read_target_options(entry: BootEntry) -> str:
    """Return the raw options text of whichever file patch_bootloader would edit."""
    if entry.kind == "type1" and entry.path is not None and entry.path.exists():
        m = re.search(r"^options[ \t]+(.*)$",
                      entry.path.read_text(encoding="utf-8"), re.MULTILINE)
        return m.group(1) if m else ""
    if KERNEL_CMDLINE.is_file():
        return KERNEL_CMDLINE.read_text(encoding="utf-8")
    if CMDLINE_D.is_dir() and sorted(CMDLINE_D.glob("*.conf")):
        return " ".join(p.read_text(encoding="utf-8").strip()
                        for p in sorted(CMDLINE_D.glob("*.conf")))
    return entry.options


def parse_managed_keys(options_text: str) -> dict[str, str]:
    """Parse and normalize all MANAGED_KEYS present in options_text."""
    snap: dict[str, str] = {}
    try:
        tokens = shlex.split(options_text, posix=False)
    except ValueError:
        tokens = options_text.split()
    for tok in tokens:
        if tok == "--" or "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        key = k.replace("vfio_pci.", "vfio-pci.")
        if key in MANAGED_KEYS:
            snap[key] = v
    return snap


def managed_snapshot(options_text: str) -> dict[str, str]:
    """Extract pre-existing managed keys so --enable can restore (not just sweep).

    Keys this tool authors itself (vfio-pci.ids / module_blacklist) are never
    captured while our own config is active -- otherwise a repeated --disable
    would snapshot our own output and --enable would 'restore' the disable.
    """
    ours = MODPROBE_FILE.exists() or UDEV_RULE.exists() or MKINITCPIO_DROPIN.exists()
    snap = parse_managed_keys(options_text)
    if ours:
        snap.pop("vfio-pci.ids", None)
        snap.pop("module_blacklist", None)
    return snap


def patch_bootloader(entry: BootEntry, blacklist: set[str], ids: list[str], vendor: str,
                     amd_force: bool, *, enable: bool,
                     restore: dict[str, str] | None = None) -> None:
    if enable:
        desired = dict(restore) if restore else {}
    else:
        desired = desired_params(blacklist, ids, vendor, amd_force)
    if entry.kind == "type1":
        if entry.path is None or entry.path.suffix != ".conf":
            bail(f"Type #1 entry '{entry.ident}' has no writable .conf.")
        content = entry.path.read_text(encoding="utf-8")
        m = re.search(r"^(options[ \t]+)(.*)$", content, re.MULTILINE)
        if m:
            merged = merge_cmdline(m.group(2), desired)
            new = content[: m.start()] + m.group(1) + merged + content[m.end():]
        else:
            merged = merge_cmdline("", desired)
            new = content.rstrip("\n") + f"\noptions {merged}\n"
        if atomic_write(entry.path, new):
            console.print(f"[green]  ~[/green] {entry.path.name}: options {merged}")
        else:
            console.print(f"[bold green]  ok[/bold green] {entry.path.name} already convergent.")
    else:
        if KERNEL_CMDLINE.is_file():
            target = KERNEL_CMDLINE
            merged = merge_cmdline(KERNEL_CMDLINE.read_text(encoding="utf-8"), desired)
            if atomic_write(target, merged + "\n"):
                console.print(f"[green]  ~[/green] {target}: {merged}")
            else:
                console.print(f"[bold green]  ok[/bold green] {target} already convergent.")
            return

        foreign: list[Path] = []
        if CMDLINE_D.is_dir():
            foreign = [p for p in sorted(CMDLINE_D.glob("*.conf")) if p != CMDLINE_D_DROPIN]

        if foreign or CMDLINE_D_DROPIN.exists():
            target = CMDLINE_D_DROPIN
            merged = " ".join(f"{k}={desired[k]}" for k in MANAGED_KEYS if k in desired)
            if not merged:
                # Enable path with nothing to restore: drop our file entirely.
                if not target.exists():
                    console.print(f"[dim]No {target.name} present.[/dim]")
                    return
                if DRY_RUN:
                    console.print(f"[magenta]  [dry-run] would remove {target}[/magenta]")
                else:
                    target.unlink()
                    console.print(f"[green]  ~[/green] removed {target}")
                return
            if atomic_write(target, merged + "\n"):
                console.print(f"[green]  ~[/green] {target}: {merged}")
            else:
                console.print(f"[bold green]  ok[/bold green] {target} already convergent.")
            return

        target = KERNEL_CMDLINE
        seed = entry.options or _read(Path("/proc/cmdline"))
        merged = merge_cmdline(seed, desired)
        if atomic_write(target, merged + "\n"):
            console.print(f"[green]  ~[/green] {target}: {merged}")
        else:
            console.print(f"[bold green]  ok[/bold green] {target} already convergent.")


# --------------------------------------------------------------------------
# modprobe + initramfs
# --------------------------------------------------------------------------

def write_modprobe(claim: Claim, softdeps: set[str], blacklist: set[str]) -> None:
    lines = [
        "# Managed by gpu-disable-toggle. Do not hand-edit — re-run the script.",
        "#",
        "# Disabled slot functions:",
    ]
    lines += [f"#   {d.addr}  {d.ids}  {d.klass4}  {d.label}" for d in claim.functions]
    lines += ["", f"options vfio-pci ids={','.join(claim.ids)}", ""]
    for mod in sorted(softdeps):
        lines.append(f"softdep {mod} pre: vfio-pci")
    if blacklist:
        lines.append("")
        for mod in sorted(blacklist):
            lines.append(f"blacklist {mod}")
    payload = "\n".join(lines) + "\n"
    if atomic_write(MODPROBE_FILE, payload):
        console.print(f"[green]  ~[/green] {MODPROBE_FILE}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MODPROBE_FILE} already convergent.")
    console.print(f"[dim]    ids={','.join(claim.ids)}[/dim]")
    console.print(f"[dim]    blacklist: {', '.join(sorted(blacklist)) or 'none'}[/dim]")


def remove_modprobe() -> None:
    if not MODPROBE_FILE.exists():
        console.print(f"[dim]No {MODPROBE_FILE.name} present.[/dim]")
        return
    if DRY_RUN:
        console.print(f"[magenta]  [dry-run] would remove {MODPROBE_FILE}[/magenta]")
        return
    MODPROBE_FILE.unlink()
    console.print(f"[green]  ~[/green] removed {MODPROBE_FILE}")


def write_udev_hide(claim: Claim) -> None:
    """Logically remove the slot's functions from the PCI bus at add-time.

    This is what makes the GPU undetectable to lspci/fastfetch/apps: fully
    unbound + blacklisted hardware can still be enumerated (and woken) via
    PCI config space. The rule fires on every add event — boot coldplug and
    any later rescan — so the device never survives long enough to be used.

    Matched on KERNEL (the PCI address) AND vendor:device, so an identical
    second card in another slot is never removed, and a card swap stops
    matching instead of removing the wrong device.
    """
    lines = [
        "# Managed by gpu-disable-toggle. Logically removes the disabled GPU",
        "# from the PCI bus at add-time (boot coldplug + any rescan), so it is",
        "# invisible to lspci / fastfetch / apps and can never be woken.",
    ]
    for dev in sorted(claim.functions, key=lambda d: d.addr):
        lines.append(
            f'ACTION=="add", SUBSYSTEM=="pci", KERNEL=="{dev.addr}", '
            f'ATTR{{vendor}}=="0x{dev.vendor}", ATTR{{device}}=="0x{dev.device}", '
            'ATTR{remove}="1"'
        )
    payload = "\n".join(lines) + "\n"
    changed = atomic_write(UDEV_RULE, payload)
    if DRY_RUN:
        return
    if changed:
        console.print(f"[green]  ~[/green] {UDEV_RULE}")
        reload_udev()
    else:
        console.print(f"[bold green]  ok[/bold green] {UDEV_RULE.name} already convergent.")


def remove_udev_hide() -> None:
    if not UDEV_RULE.exists():
        console.print(f"[dim]No {UDEV_RULE.name} present.[/dim]")
        return
    if DRY_RUN:
        console.print(f"[magenta]  [dry-run] would remove {UDEV_RULE}[/magenta]")
        return
    UDEV_RULE.unlink()
    console.print(f"[green]  ~[/green] removed {UDEV_RULE}")
    reload_udev()


def reload_udev() -> None:
    res = run(["udevadm", "control", "--reload-rules"])
    if res.returncode != 0:
        console.print("[yellow]  ! udevadm reload failed; rule applies after reboot.[/yellow]")
    else:
        console.print("[dim]    udev rules reloaded.[/dim]")


def asus_wmi_available() -> bool:
    return ASUS_ARMOURY_DGPU_DISABLE.exists() or ASUS_DGPU_DISABLE.exists()


def set_asus_dgpu_disable(disabled: bool) -> None:
    val = "1" if disabled else "0"
    if DRY_RUN:
        console.print(f"[magenta]  [dry-run] would set ASUS WMI dgpu_disable to {val}[/magenta]")
        return
    for p in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE):
        if p.exists():
            try:
                p.write_text(val, encoding="utf-8")
                console.print(f"[green]  ~[/green] ASUS WMI dgpu_disable set to {val} ({p.name})")
            except OSError as exc:
                console.print(f"[yellow]  ! Failed to write {val} to {p}: {exc}[/yellow]")
    if disabled:
        payload = (
            "# Managed by gpu-disable-toggle. Ensure ASUS firmware dGPU power cut persists across boots.\n"
            f"w- {ASUS_ARMOURY_DGPU_DISABLE} - - - - 1\n"
            f"w- {ASUS_DGPU_DISABLE} - - - - 1\n"
        )
        if atomic_write(ASUS_TMPFILES, payload):
            console.print(f"[green]  ~[/green] {ASUS_TMPFILES}")
    else:
        if ASUS_TMPFILES.exists():
            ASUS_TMPFILES.unlink()
            console.print(f"[green]  ~[/green] removed {ASUS_TMPFILES}")


def find_upstream_bridge(slot: str) -> Path | None:
    try:
        domain, bus_dev = slot.split(":", 1)
        bus_str, _ = bus_dev.split(":", 1)
        bus_num = int(bus_str, 16)
    except (ValueError, IndexError):
        return None
    if not SYS_PCI.is_dir():
        return None
    for b in SYS_PCI.iterdir():
        sec_f = b / "secondary_bus_number"
        sub_f = b / "subordinate_bus_number"
        if sec_f.is_file() and sub_f.is_file():
            try:
                sec = int(sec_f.read_text().strip())
                sub = int(sub_f.read_text().strip())
                if sec <= bus_num <= sub:
                    return b
            except (ValueError, OSError):
                pass
    return None


def pci_bridge_power_state(bridge: Path) -> tuple[str, str]:
    import time
    time.sleep(0.15)
    pst = _read(bridge / "power_state") or "unknown"
    rst = _read(bridge / "power" / "runtime_status") or "unknown"
    return pst, rst


def effective_hooks() -> list[str]:
    """Evaluate effective HOOKS mkinitcpio will see, excluding our own drop-in."""
    files = [MKINITCPIO_CONF]
    if MKINITCPIO_DROPIN_DIR.is_dir():
        files += sorted(MKINITCPIO_DROPIN_DIR.glob("*.conf"))
    files = [p for p in files if p.is_file() and p != MKINITCPIO_DROPIN]
    source = "\n".join(f"source {shlex.quote(str(p))}" for p in files)
    script = source + '\nprintf "%s\\n" "${HOOKS[@]}"\n'
    proc = run(["bash", "--noprofile", "--norc", "-c", script])
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def configure_initramfs(*, enable: bool) -> None:
    """Own exactly one file: MKINITCPIO_DROPIN. Never rewrite user config files."""
    if shutil.which("mkinitcpio") is None:
        console.print("[yellow]  ! mkinitcpio absent; skipping initramfs config.[/yellow]")
        return
    if enable:
        if not MKINITCPIO_DROPIN.exists():
            console.print(f"[dim]No {MKINITCPIO_DROPIN.name} present.[/dim]")
            return
        if DRY_RUN:
            console.print(f"[magenta]  [dry-run] would remove {MKINITCPIO_DROPIN}[/magenta]")
            return
        MKINITCPIO_DROPIN.unlink()
        console.print(f"[green]  ~[/green] removed {MKINITCPIO_DROPIN.name}")
        return

    MKINITCPIO_DROPIN_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
    payload = (
        "# Managed by gpu-disable-toggle. Early vfio stub claims the disabled GPU\n"
        "# before any DRM driver probes it. Drop-ins are sourced after the main\n"
        "# config, so '+=' is the only safe operator here.\n"
        "MODULES+=(vfio_pci vfio vfio_iommu_type1)\n"
    )
    hooks = effective_hooks()
    if hooks and "modconf" not in hooks:
        payload += "HOOKS+=(modconf)\n"
        console.print("[yellow]  ! 'modconf' absent from HOOKS; appending it via our "
                      "drop-in (user config left untouched).[/yellow]")

    if atomic_write(MKINITCPIO_DROPIN, payload):
        console.print(f"[green]  ~[/green] {MKINITCPIO_DROPIN}")
    else:
        console.print(f"[bold green]  ok[/bold green] {MKINITCPIO_DROPIN.name} already convergent.")


def rebuild_initramfs(*, no_rebuild: bool, uki: bool = False) -> None:
    if no_rebuild or DRY_RUN:
        console.print("[dim]Skipping initramfs/UKI rebuild (--no-rebuild/dry-run).[/dim]")
        return
    console.print("\n[bold blue]==>[/bold blue] [bold]Rebuilding initramfs / UKI[/bold]")

    presets = sorted(Path("/etc/mkinitcpio.d").glob("*.preset"))
    uki_preset = any(re.search(r"^[^#\n]*_uki=", _read(p), re.MULTILINE) for p in presets)

    if uki and not uki_preset and shutil.which("kernel-install"):
        argv = ["kernel-install", "add-all"]
    elif shutil.which("mkinitcpio") and presets:
        if uki and not uki_preset:
            console.print("[bold yellow]  ! Boot entry is a UKI but no mkinitcpio preset "
                          "defines *_uki= and kernel-install is absent. The embedded "
                          ".cmdline may NOT be refreshed -- verify before rebooting.[/bold yellow]")
        argv = ["mkinitcpio", "-P"]
    elif shutil.which("dracut"):
        argv = ["dracut", "--regenerate-all", "--force"]
    elif shutil.which("kernel-install"):
        argv = ["kernel-install", "add-all"]
    else:
        bail("No initramfs generator found (mkinitcpio / dracut / kernel-install).")

    console.print(f"  [cyan]{' '.join(argv)}[/cyan]")
    proc = subprocess.run(argv, check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        bail(f"{argv[0]} failed (rc={proc.returncode}). Host is NOT safe to reboot yet.")
    console.print("[bold green]  ok[/bold green] Images regenerated.")


def verify_staged_entry(entry: BootEntry, expected: dict[str, str]) -> None:
    """Verify that the staged boot options match the expected values."""
    refreshed = resolve_boot_entry()
    options = (
        refreshed.options
        if refreshed.kind == "type2"
        else read_target_options(refreshed)
    )
    actual = parse_managed_keys(options)
    all_keys = set(expected.keys()) | set(actual.keys())
    mismatches = []
    for k in sorted(all_keys):
        exp_v = expected.get(k)
        act_v = actual.get(k)
        if exp_v != act_v:
            mismatches.append(f"{k}: expected '{exp_v or '(absent)'}', got '{act_v or '(absent)'}'")
    if mismatches:
        for m in mismatches:
            console.print(f"[bold yellow]  ! Staged option mismatch: {m}[/bold yellow]")
    else:
        console.print("[bold green]  ok[/bold green] Staged boot entry verified.")


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def do_status() -> None:
    devices = enumerate_pci()
    slots = gpu_slots(devices)
    render_gpus(devices, slots)
    modprobe = MODPROBE_FILE.exists()
    hide = UDEV_RULE.exists()
    dropin = MKINITCPIO_DROPIN.exists()
    running_cmdline = _read(Path("/proc/cmdline"))
    running_ids = re.search(r"vfio[-_]pci\.ids=([0-9a-fA-F:,]+)", running_cmdline)
    state = state_load()
    action = state.get("action", "unknown" if state else "none")
    staged_ids = state.get("ids", [])

    staged_cmdline_ids = None
    entry = find_boot_entry(quiet=True)
    if entry:
        opts = read_target_options(entry)
        m = re.search(r"vfio[-_]pci\.ids=([0-9a-fA-F:,]+)", opts)
        if m:
            staged_cmdline_ids = m.group(1)
    elif os.geteuid() != 0:
        staged_cmdline_ids = "(run with sudo to check staged boot entries)" 

    table = Table(title="Disable state", header_style="bold magenta")
    table.add_column("Component")
    table.add_column("State")
    table.add_row(str(MODPROBE_FILE), "[green]present[/green]" if modprobe else "[dim]absent[/dim]")
    table.add_row(str(UDEV_RULE), "[green]present (bus-hide)[/green]" if hide else "[dim]absent[/dim]")
    table.add_row(str(MKINITCPIO_DROPIN), "[green]present[/green]" if dropin else "[dim]absent[/dim]")
    table.add_row("running cmdline vfio-pci.ids",
                  f"[green]{running_ids.group(1)}[/green]" if running_ids else "[dim]absent[/dim]")
    table.add_row("staged cmdline vfio-pci.ids",
                  f"[green]{staged_cmdline_ids}[/green]" if staged_cmdline_ids else "[dim]absent[/dim]")
    table.add_row("state.json action",
                  f"[cyan]{action}[/cyan]" if state else "[dim]none[/dim]")
    table.add_row("state.json claimed IDs",
                  f"[green]{', '.join(staged_ids)}[/green]" if staged_ids else "[dim]none[/dim]")

    # Show PCIe Root Port power status for disabled slot
    target_slots = state.get("slots", [])
    for s in target_slots:
        bridge = find_upstream_bridge(s)
        if bridge:
            pst, rst = pci_bridge_power_state(bridge)
            style = "green" if pst == "D3cold" else "yellow"
            table.add_row(f"PCIe Root Port ({bridge.name})", f"[{style}]{pst} ({rst})[/{style}]")

    # Show ASUS hardware power gate if supported
    if asus_wmi_available():
        val = None
        for p in (ASUS_ARMOURY_DGPU_DISABLE, ASUS_DGPU_DISABLE):
            if p.exists():
                val = _read(p)
                break
        status_str = "[green]disabled (power cut)[/green]" if val == "1" else "[yellow]enabled (powered)[/yellow]"
        table.add_row("ASUS WMI dgpu_disable", status_str)

    console.print(table)
    if modprobe:
        console.print(Panel(MODPROBE_FILE.read_text(encoding="utf-8").strip(),
                            title=str(MODPROBE_FILE), border_style="dim"))
    if action == "disabled" and not running_ids:
        console.print("[bold yellow]  ! Disable is STAGED for next boot, but not active in current session (reboot required).[/bold yellow]")
    elif action == "enabled" and running_ids:
        console.print("[bold yellow]  ! Enable is STAGED for next boot, but disable parameters are still active in current session (reboot required).[/bold yellow]")


def do_disable(args: argparse.Namespace) -> None:
    devices = enumerate_pci()
    slots = gpu_slots(devices)
    prior = state_load()
    from_state = False

    claim = select_claim(devices, slots, args) if slots else Claim()

    if args.slot and not claim.functions:
        known = prior.get("slots", [])
        if prior.get("action") == "disabled" and (
            args.slot in known or any(s.endswith(args.slot) for s in known)
        ):
            restored = claim_from_state(prior)
            if restored is not None:
                console.print("[yellow]  ! Slot already hidden from the PCI bus; "
                              "rebuilding config from state.[/yellow]")
                claim, from_state = restored, True

    if not claim.functions:
        bail(f"--slot {args.slot or '(auto)'} matches no visible GPU slot and no disabled state.\n"
             f"Visible GPU slots: {', '.join(slots) or 'none (all hidden?)'}")

    guard_claim(claim, devices, args.allow_boot_vga, from_state=from_state)
    if not from_state:
        check_id_collisions(devices, claim)
    warn_modprobe_conflicts()

    softdeps, blacklist = drm_blacklist_for(devices, claim)
    vendor = cpu_vendor()
    entry = resolve_boot_entry()

    # Snapshot user's pre-existing managed keys once. If already disabled, keep
    # the original baseline so re-running --disable never poisons preserved_cmdline!
    if prior.get("action") == "disabled" and isinstance(prior.get("preserved_cmdline"), dict):
        preserved = {k: v for k, v in prior["preserved_cmdline"].items()
                     if isinstance(k, str) and isinstance(v, str) and k in MANAGED_KEYS}
        console.print("[dim]Reusing pre-existing cmdline snapshot taken before first disable.[/dim]")
    else:
        preserved = managed_snapshot(read_target_options(entry))

    console.print(Panel(
        f"[bold red]Disabling {len(claim.functions)} function(s)[/bold red]\n"
        f"Slots: {', '.join(claim.slots)}\nAddrs: {', '.join(claim.addrs)}\n"
        f"IDs: {', '.join(claim.ids)}\n"
        f"Blacklist: {', '.join(sorted(blacklist)) or 'none'}\n"
        f"Boot target: {entry.ident} ({entry.kind})",
        expand=False, border_style="red"))
    if not args.yes and sys.stdin.isatty() and not DRY_RUN:
        if not Confirm.ask("Apply?", default=False):
            raise SystemExit(0)

    write_modprobe(claim, softdeps, blacklist)
    write_udev_hide(claim)
    if asus_wmi_available():
        set_asus_dgpu_disable(True)
    patch_bootloader(entry, blacklist, claim.ids, vendor, args.amd_force_enable, enable=False)
    configure_initramfs(enable=False)
    state_save(ids=claim.ids, addrs=claim.addrs, slots=claim.slots,
               functions=[{"addr": d.addr, "ids": d.ids, "klass4": d.klass4}
                          for d in claim.functions],
               blacklist=sorted(blacklist), cpu_vendor=vendor,
               preserved_cmdline=preserved, boot_kind=entry.kind, action="disabled")
    rebuild_initramfs(no_rebuild=args.no_rebuild, uki=entry.kind == "type2")
    if not args.no_rebuild and not DRY_RUN:
        verify_staged_entry(
            entry,
            desired_params(blacklist, claim.ids, vendor, args.amd_force_enable),
        )
    console.print("\n[bold green]=== DISABLE STAGED — REBOOT TO APPLY ===[/bold green]")
    console.print("[dim]Verify after reboot: the IDs below must print NOTHING:[/dim]")
    console.print(f"[dim]  lspci -Dnn | grep -E '{'|'.join(claim.ids)}'[/dim]")


def do_enable(args: argparse.Namespace) -> None:
    state = state_load()
    ids = state.get("ids", [])
    restore = {k: v for k, v in state.get("preserved_cmdline", {}).items()
               if isinstance(k, str) and isinstance(v, str) and k in MANAGED_KEYS}
    console.print(Panel("[bold yellow]Re-enabling dedicated GPU(s)[/bold yellow]\n"
                        f"Releasing IDs: {', '.join(ids) or 'unknown (sweeping managed keys)'}\n"
                        f"Restoring cmdline: {restore or 'none (clean sweep)'}",
                        expand=False))
    entry = resolve_boot_entry()
    remove_modprobe()
    remove_udev_hide()
    if asus_wmi_available() or ASUS_TMPFILES.exists():
        set_asus_dgpu_disable(False)
    patch_bootloader(entry, set(), [], cpu_vendor(), args.amd_force_enable,
                     enable=True, restore=restore)
    # Stale cmdline.d drop-in sweep if active target is type1 or KERNEL_CMDLINE
    if (entry.kind == "type1" or KERNEL_CMDLINE.is_file()) and CMDLINE_D_DROPIN.exists():
        if DRY_RUN:
            console.print(f"[magenta]  [dry-run] would remove stale {CMDLINE_D_DROPIN}[/magenta]")
        else:
            CMDLINE_D_DROPIN.unlink()
            console.print(f"[green]  ~[/green] removed stale {CMDLINE_D_DROPIN}")

    configure_initramfs(enable=True)
    # Clean up all disabled-scoped keys so no stale state mis-targets a future run
    state_save(action="enabled", ids=[], addrs=[], slots=[], functions=[],
               blacklist=[], preserved_cmdline={})
    rebuild_initramfs(no_rebuild=args.no_rebuild, uki=entry.kind == "type2")
    if not args.no_rebuild and not DRY_RUN:
        verify_staged_entry(entry, restore)
    console.print("\n[bold green]=== RE-ENABLE STAGED — REBOOT TO APPLY ===[/bold green]")
    console.print("[dim]The GPU re-enumerates on the PCI bus after reboot; "
                  "its native driver loads again.[/dim]")


def main() -> None:
    global DRY_RUN
    ap = argparse.ArgumentParser(description="Completely disable / re-enable dedicated GPU(s).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--disable", action="store_true", help="Disable the dedicated GPU slot(s).")
    g.add_argument("--enable", action="store_true", help="Remove the disable config (re-enable).")
    g.add_argument("--status", action="store_true", help="Show GPUs + disable state.")

    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--slot", help="PCI slot, e.g. 0000:01:00 (bare 01:00 also works).")
    sel.add_argument("--all", action="store_true", help="With --disable: all non-boot_vga GPU slots.")

    ap.add_argument("--auto", action="store_true", help="Non-interactive (pick single/dedicated).")
    ap.add_argument("--allow-boot-vga", action="store_true",
                    help="Permit disabling the boot_vga / last GPU (console goes dark).")
    ap.add_argument("--amd-force-enable", action="store_true",
                    help="On AMD CPUs with a broken IVRS, emit amd_iommu=force_enable.")
    ap.add_argument("--yes", "-y", action="store_true", help="Skip confirmation.")
    ap.add_argument("--dry-run", action="store_true", help="Print changes, write nothing.")
    ap.add_argument("--no-rebuild", action="store_true", help="Skip initramfs regeneration.")
    args = ap.parse_args()

    # CLI semantic validation
    if args.status and any((args.slot, args.all, args.auto, args.allow_boot_vga, args.amd_force_enable, args.yes, args.dry_run, args.no_rebuild)):
        ap.error("--status takes no other options.")
    if args.enable and any((args.slot, args.all, args.auto, args.allow_boot_vga)):
        ap.error("--slot/--all/--auto/--allow-boot-vga only apply to --disable.")

    DRY_RUN = args.dry_run
    check_versions()

    if args.disable or args.enable:
        elevate()

    console.print(Panel.fit(
        "[bold cyan]GPU Disable Toggle[/bold cyan]  "
        f"kernel {_read(Path('/proc/sys/kernel/osrelease'))}  "
        f"python {'.'.join(map(str, sys.version_info[:3]))}",
        border_style="cyan"))
    if DRY_RUN:
        console.print("[magenta]DRY RUN: no file will be modified.[/magenta]")

    match args:
        case argparse.Namespace(status=True):
            do_status()
        case argparse.Namespace(disable=True):
            do_disable(args)
        case argparse.Namespace(enable=True):
            do_enable(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]! Interrupted.[/bold red]")
        raise SystemExit(130) from None
