#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

_dusky_root = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

from python.frontend.core_types import ConfigItem
from python.engines.fstab import FstabEngine, NEW_ENTRY

ENGINE_TYPE = "fstab"
TARGET_FILE = "/etc/fstab"
APP_TITLE = "Dusky FSTAB Orchestrator"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True

TABS = [
    "Mount Info",
    "Filesystem",
    "BTRFS Ops",
    "System Flags"
]

SCHEMA = {i: [] for i in range(len(TABS))}

# --- TAB 0: MOUNT INFO ---
SCHEMA[0].append(ConfigItem(
    label="Target ID (UUID/Path)",
    key="uuid",
    scope="mount_info",
    type_="string",
    default="",
    options=[],
    hints=[],
    extended_help=(
        "**Target ID (UUID/Path):** Select or enter the partition identifier. "
        "Can be a raw UUID, UUID=, PARTUUID=, LABEL=, PARTLABEL=, or absolute device/swap-file path (e.g. `/dev/sda1`).\n\n"
        "Press **Enter** to open the selection menu or type a custom string manually."
    )
))

SCHEMA[0].append(ConfigItem(
    label="Target Mount Point",
    key="mount_point",
    scope="mount_info",
    type_="string",
    default="/",
    options=["none", "/", "/home", "/boot", "/boot/efi", "/swap", "/mnt/data"],
    hints=["Swap target (not a disable flag)", "Root filesystem", "User directory", "Legacy boot ESP", "UEFI ESP", "Swap space", "Generic mount data"],
    extended_help=(
        "**Target Mount Point:** The absolute directory path where the partition will be mounted.\n\n"
        "Spaces will be automatically translated to safe fstab-compliant octal codes (`\\040`)."
    )
))

SCHEMA[0].insert(1, ConfigItem(
    label="Existing Entry (Mount Point)",
    key="entry",
    scope="mount_info",
    type_="string",
    default="",
    options=["", NEW_ENTRY],
    hints=["First matching entry", "Create another entry"],
    extended_help=(
        "Select the existing mount point to edit for the chosen device, for example `/home`. "
        "Empty selects its first fstab entry; `<new>` prepares a new entry. "
        "Changing Target Mount Point moves only the selected record. "
        "Selecting a device or entry only loads settings; edits save to fstab. "
        "After saving, run `systemctl daemon-reload` to refresh systemd mount units. "
        "Changing filesystem type rebuilds filesystem-specific options."
    ),
))

# --- TAB 1: FILESYSTEM ---
SCHEMA[1].append(ConfigItem(
    label="Filesystem Type",
    key="fs_type",
    scope="filesystem",
    type_="cycle",
    default="btrfs",
    options=["btrfs", "vfat", "exfat", "ntfs", "ext4", "ext3", "ext2", "swap"],
    extended_help=(
        "**Filesystem Type:** The filesystem driver to mount with.\n\n"
        "Targets **Linux kernel 7.3+**:\n"
        "- **ntfs**: Uses the modern native read/write in-kernel module. No alternate NTFS driver is generated.\n"
        "- **exfat**: Native exFAT driver mapping POSIX user IDs.\n"
        "- **vfat**: Standard EFI/FAT32 bootloader driver.\n"
        "- **btrfs**: Copy-on-Write multi-subvolume filesystem."
    )
))

SCHEMA[1].append(ConfigItem(
    label="Drive Architecture",
    key="drive_type",
    scope="filesystem",
    type_="cycle",
    default="hdd",
    options=["ssd", "hdd"],
    extended_help=(
        "**Drive Architecture:** SSD vs. HDD specific optimizations.\n\n"
        "Optimizations applied:\n"
        "- **SSD (Btrfs)**: Enables `ssd,discard=async` non-blocking TRIM queuing.\n"
        "- **Ext filesystems**: New entries use `noatime,lazytime`; schedule periodic TRIM separately. Existing options are preserved.\n"
        "- **HDD (Btrfs)**: Uses `nodiscard` and, with CoW enabled, `autodefrag`.\n\nHardware is detected through sysfs, including mapped devices. Unknown hardware defaults to HDD."
    )
))

# --- TAB 2: BTRFS OPS ---
SCHEMA[2].append(ConfigItem(
    label="BTRFS Subvolume",
    key="subvol",
    scope="btrfs_ops",
    type_="string",
    default="",
    options=["", "@", "@home", "@snapshots", "@var_log", "@var_cache", "@var_tmp", "@swap"],
    hints=["Use filesystem default subvolume", "Root subvolume", "Home subvolume", "Snapshots subvolume", "Log subvolume", "Cache subvolume", "Temporary subvolume", "Swap subvolume"],
    extended_help=(
        "**BTRFS Subvolume:** The specific Btrfs subvolume path to mount.\n\n"
        "Only applies to `btrfs`. Empty uses the filesystem default. Selecting a path replaces any `subvolid=` option."
    )
))

SCHEMA[2].append(ConfigItem(
    label="Copy-on-Write (CoW)",
    key="cow_enabled",
    scope="btrfs_ops",
    type_="bool",
    default=True,
    extended_help=(
        "**Copy-on-Write (CoW):** Toggles Btrfs CoW behavior.\n\n"
        "If disabled, applies `nodatacow` and removes compression for newly created files. "
        "Most Btrfs mount options apply filesystem-wide: the first mounted subvolume determines CoW and compression. "
        "For per-directory CoW control, use the NOCOW file attribute instead."
    )
))

# --- TAB 3: SYSTEM FLAGS ---
SCHEMA[3].append(ConfigItem(
    label="Mount at Boot (auto)",
    key="auto_mount",
    scope="system_flags",
    type_="bool",
    default=True,
    extended_help=(
        "**Mount at Boot (auto):** Controls system behavior during boot.\n\n"
        "- **ON**: Automatic mounting; new noncritical entries use `nofail`. Critical system mounts do not.\n"
        "- **OFF**: Adds `noauto`, including for swap. Existing `x-systemd.automount` options are preserved and can override this flag."
    )
))

SCHEMA[3].append(ConfigItem(
    label="Show in File Manager (GVfs)",
    key="gvfs_show",
    scope="system_flags",
    type_="bool",
    default=True,
    extended_help=(
        "**Show in File Manager (GVfs):** Toggles visibility of this mount in your file manager (like Thunar/Nautilus).\n\n"
        "- **ON**: Adds `x-gvfs-show`. Mount permissions and existing `user` options are preserved.\n"
        "- **OFF**: Adds `x-gvfs-hide`. Does not apply to swap."
    )
))


def DEFERRED_LOAD() -> list[int]:
    """Refresh device identifiers and existing mount-point choices without writing fstab."""
    hints: dict[str, str] = {}
    entries: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--paths", "--output",
             "NAME,FSTYPE,UUID,LABEL,PARTUUID,PARTLABEL,MOUNTPOINTS"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=10, check=True,
        )
        devices = json.loads(result.stdout)["blockdevices"]
        while devices:
            device = devices.pop()
            devices.extend(device.get("children") or [])
            name = device.get("name")
            details = [str(value) for value in (name, device.get("fstype"), device.get("label")) if value]
            details.extend(mp for mp in device.get("mountpoints", []) if mp)
            hint = ", ".join(details)
            for tag, key in (("UUID", "uuid"), ("PARTUUID", "partuuid"),
                             ("LABEL", "label"), ("PARTLABEL", "partlabel")):
                if value := device.get(key):
                    hints[f"{tag}={value}"] = hint
            if name:
                hints[name] = hint
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        print(f"[tui_fstab] Device discovery failed: {exc}", file=sys.stderr)

    try:
        data = Path(TARGET_FILE).read_bytes()
        for record in FstabEngine._records(data):
            source = FstabEngine._unescape_token(record.fields[0])
            mount_point = FstabEngine._unescape_token(record.fields[1])
            hints.setdefault(source, f"fstab: {mount_point} ({record.fields[2]})")
            entries[mount_point] = "Existing fstab mount point"
    except OSError as exc:
        print(f"[tui_fstab] Cannot read existing entries: {exc}", file=sys.stderr)

    uuid_item = next(item for item in SCHEMA[0] if item.key == "uuid")
    uuid_item.options = sorted(hints)
    uuid_item.hints = [hints[value] for value in uuid_item.options]
    entry_item = next(item for item in SCHEMA[0] if item.key == "entry")
    entry_item.options = ["", NEW_ENTRY, *sorted(entries)]
    entry_item.hints = ["First matching entry", "Create another entry for the selected device",
                        *[entries[value] for value in sorted(entries)]]
    return [0]

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _dusky_root / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
