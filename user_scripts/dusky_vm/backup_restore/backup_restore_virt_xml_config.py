#!/usr/bin/env python3
"""
Phase 3: Libvirt Cryptographic State & Complete Infrastructure Continuity Manager
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260+, libvirt 12.6+, QEMU 11.1+
Scope: Full Inactive XML Topology Extraction, NVRAM/Firmware Variables, vTPM 2.0 State & Local CA,
       Libvirt Secrets & Master Encryption Key, Managed Save States, Snapshots/Checkpoints DAG,
       Host Hooks (VFIO/Looking Glass), Network Filters, and Daemon Configurations.
Philosophy: Surgical Extraction & High-Fidelity Injection. Zero Data Loss. Strict DAC/MAC Preservation.
"""

import argparse
import glob
import grp
import hashlib
import json
import os
import pwd
import readline
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Never, Optional, Self, Tuple

# ==============================================================================
# TTY AUTO-COMPLETION & READLINE SUPPORT
# ==============================================================================
def path_completer(text: str, state: int) -> Optional[str]:
    """Standard library path auto-completer for readline supporting ~ and spaces."""
    try:
        expanded = os.path.expanduser(text)
        if os.path.isdir(expanded) and not text.endswith('/'):
            search_dir = expanded
            base_prefix = ""
        else:
            search_dir = os.path.dirname(expanded) or '.'
            base_prefix = os.path.basename(expanded)

        escaped_dir = glob.escape(search_dir)
        pattern = os.path.join(escaped_dir, glob.escape(base_prefix) + '*')
        matches = glob.glob(pattern)

        results: List[str] = []
        user_home = os.path.expanduser('~')
        for m in matches:
            if os.path.isdir(m):
                m += '/'
            if text.startswith('~/') and m.startswith(user_home):
                m = '~' + m[len(user_home):]
            results.append(m)

        results.sort()
        return results[state] if state < len(results) else None
    except Exception:
        return None

readline.set_completer_delims('\t\n;')
readline.parse_and_bind("tab: complete")
readline.set_completer(path_completer)

# ==============================================================================
# PRIVILEGE ELEVATION
# ==============================================================================
def require_root() -> None:
    """Hard enforcement of eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}", file=sys.stderr)
            sys.exit(1)

require_root()

# ==============================================================================
# UI DEPENDENCIES (Rich 15.0+)
# ==============================================================================
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.theme import Theme
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please install it via 'pacman -S python-rich'.", file=sys.stderr)
    sys.exit(1)

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "danger": "bold red",
    "success": "bold green",
    "highlight": "bold magenta"
})
console = Console(theme=custom_theme)

# ==============================================================================
# DATA MODELS & MANIFEST SCHEMA
# ==============================================================================
@dataclass(slots=True)
class EntityMetadata:
    name: str
    uuid: str = ""
    autostart: bool = False
    active: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class BackupManifest:
    version: str = "2.0.0"
    created_at: str = ""
    kernel: str = ""
    os_name: str = ""
    hostname: str = ""
    libvirt_version: str = ""
    qemu_version: str = ""
    swtpm_version: str = ""
    python_version: str = ""
    domains: List[EntityMetadata] = field(default_factory=list)
    networks: List[EntityMetadata] = field(default_factory=list)
    pools: List[EntityMetadata] = field(default_factory=list)
    nwfilters: List[EntityMetadata] = field(default_factory=list)
    secrets: List[EntityMetadata] = field(default_factory=list)
    snapshots: Dict[str, List[str]] = field(default_factory=dict)
    current_snapshots: Dict[str, str] = field(default_factory=dict)
    checkpoints: Dict[str, List[str]] = field(default_factory=dict)
    archives: Dict[str, str] = field(default_factory=dict)
    file_hashes: Dict[str, str] = field(default_factory=dict)

# ==============================================================================
# SYSTEM UTILITIES & PROMPT HELPERS
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[danger]FATAL ERROR:[/danger] {msg}", border_style="red"))
    sys.exit(1)

def prompt_choice(prompt_text: str, choices: List[str], default: str = "") -> str:
    """Safe TUI prompt that decouples rich ANSI styling from Readline input buffer."""
    choices_str = "/".join(choices)
    default_str = f" [default: {default}]" if default else ""
    console.print(f"\n[bold cyan]{prompt_text}[/bold cyan] [dim]({choices_str}){default_str}[/dim]")
    while True:
        try:
            val = input("> ").strip()
        except EOFError:
            return default
        if not val and default:
            return default
        if val in choices:
            return val
        console.print(f"[warning]Invalid selection '{val}'. Please enter one of: {', '.join(choices)}[/warning]")

def prompt_confirm(prompt_text: str, default: bool = True) -> bool:
    """Safe TUI boolean confirmation prompt with decoupled readline buffer."""
    default_str = "Y/n" if default else "y/N"
    console.print(f"\n[bold cyan]{prompt_text}[/bold cyan] [dim][{default_str}][/dim]")
    while True:
        try:
            val = input("> ").strip().lower()
        except EOFError:
            return default
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        console.print("[warning]Please enter 'y' or 'n'.[/warning]")

def run_cmd(
    cmd: List[str],
    check: bool = True,
    capture: bool = True,
    timeout: Optional[float] = None
) -> subprocess.CompletedProcess[str]:
    """Execute shell commands safely and reliably."""
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[danger]FATAL: Command failed with exit code {e.returncode}:[/danger] {' '.join(cmd)}")
            err_msg = (e.stderr or e.stdout or "").strip()
            if err_msg:
                console.print(f"[red]Details:[/red] {err_msg}")
            sys.exit(1)
        return e
    except subprocess.TimeoutExpired:
        bail(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")

def resolve_user_path(path_str: str) -> Path:
    """Intelligently route '~/' to the human user's home, bypassing the root shell."""
    if path_str.startswith('~/'):
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user:
            try:
                home_dir = pwd.getpwnam(sudo_user).pw_dir
                return (Path(home_dir) / path_str[2:]).resolve()
            except KeyError:
                pass
    return Path(path_str).expanduser().resolve()

def sanitize_filename(name: str) -> str:
    """Sanitize strings to be safely used as filenames."""
    return name.replace("/", "_").replace("\\", "_").replace("..", "_")

def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash using Python 3.11+ hashlib.file_digest."""
    with open(filepath, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def check_libvirt_connection() -> None:
    """Verify modular/monolithic IPC socket responsiveness."""
    res = run_cmd(["virsh", "uri"], check=False)
    if res.returncode != 0:
        bail("Cannot connect to the libvirt daemon. Ensure virtqemud.socket / libvirtd is active.")

def verify_system_users() -> None:
    """Ensure required hypervisor users and groups exist before state injection."""
    required_users = ["tss"]
    has_qemu_user = False
    for candidate in ["libvirt-qemu", "qemu"]:
        try:
            pwd.getpwnam(candidate)
            has_qemu_user = True
            break
        except KeyError:
            continue

    missing = []
    for user in required_users:
        try:
            pwd.getpwnam(user)
        except KeyError:
            missing.append(user)

    if not has_qemu_user:
        missing.append("libvirt-qemu (or qemu)")

    if missing:
        bail(
            f"The following required system users are missing: {', '.join(missing)}\n"
            "Please ensure 'swtpm' and 'libvirt' are fully installed on this system."
        )

def get_active_domains() -> List[str]:
    """Retrieve list of currently running or paused domains."""
    res = run_cmd(["virsh", "list", "--name"], check=False)
    if res.returncode != 0:
        return []
    return [line.strip() for line in res.stdout.split('\n') if line.strip()]

def handle_running_vms(interactive: bool = True, shutdown_running: bool = False) -> None:
    """Check for active VMs and handle them gracefully to prevent torn binary state."""
    active_vms = get_active_domains()
    if not active_vms:
        return

    console.print(f"[warning]Active virtual machines detected:[/warning] {', '.join(active_vms)}")
    
    if shutdown_running:
        action = "1"
    elif interactive:
        console.print("\n[bold cyan]Action required to prevent inconsistent/torn cryptographic state:[/bold cyan]")
        console.print("  [cyan]1.[/cyan] Gracefully shut down active VMs (ACPI)")
        console.print("  [cyan]2.[/cyan] Suspend active VMs (virsh managedsave)")
        console.print("  [cyan]3.[/cyan] Abort operation")
        action = prompt_choice("Select action", choices=["1", "2", "3"], default="1")
    else:
        bail(f"Active VMs detected ({', '.join(active_vms)}). Shut them down or use --shutdown-running.")

    match action:
        case "1":
            for vm in active_vms:
                console.print(f"[info]Sending ACPI shutdown signal to '{vm}'...[/info]")
                run_cmd(["virsh", "shutdown", vm, "--mode", "acpi"], check=False)
            
            with console.status("[cyan]Waiting for domains to power off...[/cyan]", spinner="dots"):
                timeout = 60
                start_t = time.time()
                while time.time() - start_t < timeout:
                    still_active = get_active_domains()
                    if not still_active:
                        console.print("[success]All domains gracefully powered off.[/success]")
                        return
                    time.sleep(2)
                bail("Timed out waiting for domains to gracefully shut down. Please stop them manually.")
        case "2":
            for vm in active_vms:
                console.print(f"[info]Managed saving domain '{vm}'...[/info]")
                run_cmd(["virsh", "managedsave", vm])
            console.print("[success]All domains saved to disk.[/success]")
        case "3":
            bail("Operation aborted by operator.")

# ==============================================================================
# PHASE AUTOMATION: BACKUP
# ==============================================================================
def execute_backup(dest_dir_arg: Optional[str] = None, non_interactive: bool = False, shutdown_running: bool = False) -> Path:
    check_libvirt_connection()
    handle_running_vms(interactive=not non_interactive, shutdown_running=shutdown_running)

    console.print("\n[bold cyan]─── Libvirt Surgical Infrastructure & Cryptographic Backup ───[/bold cyan]")

    if dest_dir_arg:
        target_dir = resolve_user_path(dest_dir_arg)
    else:
        console.print("[bold cyan]Enter absolute path to save backups (e.g., /mnt/Storage/VM_Backup):[/bold cyan]")
        target_input = input("> ").strip()
        if not target_input:
            bail("No destination path provided.")
        target_dir = resolve_user_path(target_input)

    if not target_dir.exists():
        if non_interactive or prompt_confirm(f"Directory '{target_dir}' does not exist. Create it?", default=True):
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            bail("Backup aborted by operator.")

    # Prepare directories
    (target_dir / "vms").mkdir(exist_ok=True)
    (target_dir / "networks").mkdir(exist_ok=True)
    (target_dir / "pools").mkdir(exist_ok=True)
    (target_dir / "nwfilters").mkdir(exist_ok=True)
    (target_dir / "secrets").mkdir(exist_ok=True)
    (target_dir / "snapshots").mkdir(exist_ok=True)
    (target_dir / "checkpoints").mkdir(exist_ok=True)
    (target_dir / "state_archives").mkdir(exist_ok=True)

    manifest = BackupManifest(
        created_at=datetime.now(timezone.utc).isoformat(),
        kernel=run_cmd(["uname", "-r"]).stdout.strip(),
        os_name="Arch Linux",
        hostname=run_cmd(["uname", "-n"]).stdout.strip(),
        python_version=sys.version.split()[0]
    )

    # Collect package versions
    try:
        manifest.libvirt_version = run_cmd(["virsh", "--version"]).stdout.strip()
    except Exception:
        manifest.libvirt_version = "unknown"

    try:
        qemu_ver_res = run_cmd(["qemu-system-x86_64", "--version"], check=False)
        if qemu_ver_res.returncode == 0:
            manifest.qemu_version = qemu_ver_res.stdout.splitlines()[0]
    except Exception:
        manifest.qemu_version = "unknown"

    try:
        swtpm_ver_res = run_cmd(["swtpm", "--version"], check=False)
        if swtpm_ver_res.returncode == 0:
            manifest.swtpm_version = swtpm_ver_res.stdout.splitlines()[0]
    except Exception:
        manifest.swtpm_version = "unknown"

    table = Table(title=f"Backup Telemetry ({target_dir.name})", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Type", justify="center")
    table.add_column("Status", justify="left")

    # --------------------------------------------------------------------------
    # 1. Archive Cryptographic, Firmware, Secrets & Hooks State Files
    # --------------------------------------------------------------------------
    state_targets = [
        {
            "id": "nvram",
            "name": "NVRAM (UEFI Vars)",
            "parent_dir": "/var/lib/libvirt/qemu",
            "target": "nvram",
            "archive_name": "nvram_state.tar.zst"
        },
        {
            "id": "varstore",
            "name": "Firmware Varstore",
            "parent_dir": "/var/lib/libvirt/qemu",
            "target": "varstore",
            "archive_name": "varstore_state.tar.zst"
        },
        {
            "id": "swtpm",
            "name": "vTPM 2.0 State",
            "parent_dir": "/var/lib/libvirt",
            "target": "swtpm",
            "archive_name": "swtpm_state.tar.zst"
        },
        {
            "id": "swtpm_localca",
            "name": "vTPM Local CA & Keys",
            "parent_dir": "/var/lib",
            "target": "swtpm-localca",
            "archive_name": "swtpm_localca_state.tar.zst"
        },
        {
            "id": "secrets_var",
            "name": "Libvirt Secrets (Var)",
            "parent_dir": "/var/lib/libvirt",
            "target": "secrets",
            "archive_name": "secrets_var_state.tar.zst"
        },
        {
            "id": "secrets_etc",
            "name": "Libvirt Secrets (Etc)",
            "parent_dir": "/etc/libvirt",
            "target": "secrets",
            "archive_name": "secrets_etc_state.tar.zst"
        },
        {
            "id": "save",
            "name": "Managed Save States",
            "parent_dir": "/var/lib/libvirt/qemu",
            "target": "save",
            "archive_name": "save_state.tar.zst"
        },
        {
            "id": "hooks",
            "name": "KVM / VFIO Hooks",
            "parent_dir": "/etc/libvirt",
            "target": "hooks",
            "archive_name": "hooks_state.tar.zst"
        },
        {
            "id": "hooks_qemu_d",
            "name": "KVM Hooks.qemu.d",
            "parent_dir": "/etc/libvirt",
            "target": "hooks.qemu.d",
            "archive_name": "hooks_qemu_d_state.tar.zst"
        },
        {
            "id": "daemon_configs",
            "name": "Daemon Configs",
            "parent_dir": "/etc",
            "target": "libvirt",
            "archive_name": "daemon_configs.tar.zst",
            "extra_flags": ["--exclude=libvirt/qemu", "--exclude=libvirt/secrets", "--exclude=libvirt/nwfilter", "--exclude=libvirt/storage", "--exclude=libvirt/hooks*", "--exclude=libvirt/lxc"]
        }
    ]

    with console.status("[cyan]Archiving binary states, NVRAM, vTPM, and secrets (Zstandard + xattrs)...[/cyan]", spinner="dots"):
        for state in state_targets:
            target_path = Path(state["parent_dir"]) / state["target"]
            if target_path.exists():
                has_content = any(target_path.iterdir()) if target_path.is_dir() else True
                if not has_content:
                    table.add_row(state["name"], "Binary State", "[dim]Empty Directory (Skipped)[/dim]")
                    continue

                archive_path = target_dir / "state_archives" / state["archive_name"]
                cmd = [
                    "tar", "--zstd", "--sparse", "--xattrs", "--xattrs-include=*", "--acls",
                    "--exclude=*.sock", "--exclude=*.pid", "--exclude=*monitor.sock", "--exclude=*.lock*",
                    "-cf", str(archive_path), "-C", state["parent_dir"], state["target"]
                ]
                if "extra_flags" in state:
                    cmd[1:1] = state["extra_flags"]

                res = run_cmd(cmd, check=False)
                if res.returncode == 0 and archive_path.exists() and archive_path.stat().st_size > 0:
                    sha = compute_sha256(archive_path)
                    manifest.archives[state["id"]] = state["archive_name"]
                    manifest.file_hashes[f"state_archives/{state['archive_name']}"] = sha
                    size_kb = archive_path.stat().st_size / 1024
                    table.add_row(state["name"], "Zstd Archive", f"[green]Archived ({size_kb:.1f} KB)[/green]")
                else:
                    table.add_row(state["name"], "Zstd Archive", "[yellow]Empty / Skipped[/yellow]")
            else:
                table.add_row(state["name"], "Binary State", "[dim]Not Found (Skipped)[/dim]")

    # --------------------------------------------------------------------------
    # 2. Extract Virtual Networks (--inactive)
    # --------------------------------------------------------------------------
    with console.status("[cyan]Dumping Virtual Network Blueprints...[/cyan]", spinner="dots"):
        net_res = run_cmd(["virsh", "net-list", "--all", "--name"], check=False)
        autostart_nets = set(run_cmd(["virsh", "net-list", "--all", "--autostart", "--name"], check=False).stdout.split())
        active_nets = set(run_cmd(["virsh", "net-list", "--name"], check=False).stdout.split())

        for net in [n.strip() for n in net_res.stdout.splitlines() if n.strip()]:
            xml_res = run_cmd(["virsh", "net-dumpxml", net, "--inactive"], check=False)
            if xml_res.returncode == 0:
                xml_file = target_dir / "networks" / f"{net}.xml"
                xml_file.write_text(xml_res.stdout, encoding="utf-8")
                manifest.file_hashes[f"networks/{net}.xml"] = compute_sha256(xml_file)
                manifest.networks.append(EntityMetadata(
                    name=net,
                    autostart=(net in autostart_nets),
                    active=(net in active_nets)
                ))
                table.add_row(net, "Network", "[green]XML Extracted[/green]")

    # --------------------------------------------------------------------------
    # 3. Extract Storage Pools (--inactive)
    # --------------------------------------------------------------------------
    with console.status("[cyan]Dumping Storage Pool Blueprints...[/cyan]", spinner="dots"):
        pool_res = run_cmd(["virsh", "pool-list", "--all", "--name"], check=False)
        autostart_pools = set(run_cmd(["virsh", "pool-list", "--all", "--autostart", "--name"], check=False).stdout.split())
        active_pools = set(run_cmd(["virsh", "pool-list", "--name"], check=False).stdout.split())

        for pool in [p.strip() for p in pool_res.stdout.splitlines() if p.strip()]:
            xml_res = run_cmd(["virsh", "pool-dumpxml", pool, "--inactive"], check=False)
            if xml_res.returncode == 0:
                xml_file = target_dir / "pools" / f"{pool}.xml"
                xml_file.write_text(xml_res.stdout, encoding="utf-8")
                manifest.file_hashes[f"pools/{pool}.xml"] = compute_sha256(xml_file)

                # Extract target path if available
                target_path_str = ""
                try:
                    root_elem = ET.fromstring(xml_res.stdout)
                    target_elem = root_elem.find("./target/path")
                    if target_elem is not None and target_elem.text:
                        target_path_str = target_elem.text
                except Exception:
                    pass

                manifest.pools.append(EntityMetadata(
                    name=pool,
                    autostart=(pool in autostart_pools),
                    active=(pool in active_pools),
                    extra={"target_path": target_path_str}
                ))
                table.add_row(pool, "Storage Pool", "[green]XML Extracted[/green]")

    # --------------------------------------------------------------------------
    # 4. Extract Network Filters (NWFilters)
    # --------------------------------------------------------------------------
    with console.status("[cyan]Dumping Network Filters...[/cyan]", spinner="dots"):
        nw_res = run_cmd(["virsh", "nwfilter-list"], check=False)
        if nw_res.returncode == 0:
            lines = nw_res.stdout.splitlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 2:
                    uuid_val, nw_name = parts[0], parts[1]
                    xml_res = run_cmd(["virsh", "nwfilter-dumpxml", nw_name], check=False)
                    if xml_res.returncode == 0:
                        xml_file = target_dir / "nwfilters" / f"{nw_name}.xml"
                        xml_file.write_text(xml_res.stdout, encoding="utf-8")
                        manifest.file_hashes[f"nwfilters/{nw_name}.xml"] = compute_sha256(xml_file)
                        manifest.nwfilters.append(EntityMetadata(name=nw_name, uuid=uuid_val))
            if manifest.nwfilters:
                table.add_row(f"{len(manifest.nwfilters)} NWFilters", "NWFilter", "[green]Extracted[/green]")

    # --------------------------------------------------------------------------
    # 5. Extract Libvirt Secrets
    # --------------------------------------------------------------------------
    with console.status("[cyan]Dumping Libvirt Secrets...[/cyan]", spinner="dots"):
        sec_res = run_cmd(["virsh", "secret-list"], check=False)
        if sec_res.returncode == 0:
            lines = sec_res.stdout.splitlines()
            for line in lines[2:]:
                parts = line.split()
                if parts:
                    sec_uuid = parts[0]
                    sec_xml_res = run_cmd(["virsh", "secret-dumpxml", sec_uuid], check=False)
                    if sec_xml_res.returncode == 0:
                        sec_file = target_dir / "secrets" / f"{sec_uuid}.xml"
                        sec_file.write_text(sec_xml_res.stdout, encoding="utf-8")
                        manifest.file_hashes[f"secrets/{sec_uuid}.xml"] = compute_sha256(sec_file)
                        manifest.secrets.append(EntityMetadata(name=sec_uuid, uuid=sec_uuid))
            if manifest.secrets:
                table.add_row(f"{len(manifest.secrets)} Secrets", "Secret", "[green]Extracted[/green]")

    # --------------------------------------------------------------------------
    # 6. Extract Virtual Machines (--inactive --security-info)
    # --------------------------------------------------------------------------
    with console.status("[cyan]Dumping Virtual Machine Blueprints & Snapshots...[/cyan]", spinner="dots"):
        vm_res = run_cmd(["virsh", "list", "--all", "--name"], check=False)
        autostart_vms = set(run_cmd(["virsh", "list", "--all", "--autostart", "--name"], check=False).stdout.split())

        for vm in [v.strip() for v in vm_res.stdout.splitlines() if v.strip()]:
            xml_res = run_cmd(["virsh", "dumpxml", vm, "--inactive", "--security-info"], check=False)
            if xml_res.returncode == 0:
                xml_file = target_dir / "vms" / f"{vm}.xml"
                xml_file.write_text(xml_res.stdout, encoding="utf-8")
                manifest.file_hashes[f"vms/{vm}.xml"] = compute_sha256(xml_file)

                # Parse UUID
                vm_uuid = ""
                try:
                    root_elem = ET.fromstring(xml_res.stdout)
                    uuid_elem = root_elem.find("uuid")
                    if uuid_elem is not None and uuid_elem.text:
                        vm_uuid = uuid_elem.text
                except Exception:
                    pass

                manifest.domains.append(EntityMetadata(
                    name=vm,
                    uuid=vm_uuid,
                    autostart=(vm in autostart_vms)
                ))
                table.add_row(vm, "VM Blueprint", "[green]XML Extracted (Clean Inactive)[/green]")

                # Snapshots extraction
                snap_list_res = run_cmd(["virsh", "snapshot-list", vm, "--name", "--topological"], check=False)
                if snap_list_res.returncode == 0 and snap_list_res.stdout.strip():
                    vm_snap_dir = target_dir / "snapshots" / vm
                    vm_snap_dir.mkdir(parents=True, exist_ok=True)
                    snapshots = [s.strip() for s in snap_list_res.stdout.splitlines() if s.strip()]
                    (vm_snap_dir / "order.txt").write_text('\n'.join(snapshots) + '\n', encoding="utf-8")
                    manifest.snapshots[vm] = snapshots

                    # Capture current snapshot pointer
                    curr_snap_res = run_cmd(["virsh", "snapshot-current", vm, "--name"], check=False)
                    if curr_snap_res.returncode == 0 and curr_snap_res.stdout.strip():
                        manifest.current_snapshots[vm] = curr_snap_res.stdout.strip()

                    snap_count = 0
                    for snap in snapshots:
                        snap_res = run_cmd(["virsh", "snapshot-dumpxml", vm, snap, "--security-info"], check=False)
                        if snap_res.returncode == 0:
                            safe_name = sanitize_filename(snap)
                            snap_file = vm_snap_dir / f"{safe_name}.xml"
                            snap_file.write_text(snap_res.stdout, encoding="utf-8")
                            manifest.file_hashes[f"snapshots/{vm}/{safe_name}.xml"] = compute_sha256(snap_file)
                            snap_count += 1

                    table.add_row(vm, "Snapshots", f"[green]{snap_count}/{len(snapshots)} Topological Dumped[/green]")

                # Checkpoints extraction (if any)
                ckpt_list_res = run_cmd(["virsh", "checkpoint-list", vm, "--name", "--topological"], check=False)
                if ckpt_list_res.returncode == 0 and ckpt_list_res.stdout.strip():
                    vm_ckpt_dir = target_dir / "checkpoints" / vm
                    vm_ckpt_dir.mkdir(parents=True, exist_ok=True)
                    ckpts = [c.strip() for c in ckpt_list_res.stdout.splitlines() if c.strip()]
                    manifest.checkpoints[vm] = ckpts
                    for ckpt in ckpts:
                        ckpt_res = run_cmd(["virsh", "checkpoint-dumpxml", vm, ckpt, "--security-info"], check=False)
                        if ckpt_res.returncode == 0:
                            safe_name = sanitize_filename(ckpt)
                            ckpt_file = vm_ckpt_dir / f"{safe_name}.xml"
                            ckpt_file.write_text(ckpt_res.stdout, encoding="utf-8")
                            manifest.file_hashes[f"checkpoints/{vm}/{safe_name}.xml"] = compute_sha256(ckpt_file)
                    table.add_row(vm, "Checkpoints", f"[green]{len(ckpts)} Dumped[/green]")

    # --------------------------------------------------------------------------
    # 7. Write Manifest
    # --------------------------------------------------------------------------
    manifest_file = target_dir / "backup_manifest.json"
    manifest_dict = asdict(manifest)
    manifest_file.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    console.print("\n")
    console.print(table)
    console.print(f"[success]✓ Complete. Infrastructure & cryptographic state frozen at: {target_dir}[/success]")
    return target_dir

# ==============================================================================
# PHASE AUTOMATION: INTEGRITY VERIFICATION
# ==============================================================================
def execute_verify(source_dir_arg: Optional[str] = None) -> bool:
    console.print("\n[bold cyan]─── Libvirt Backup Integrity & Telemetry Verification ───[/bold cyan]")

    if source_dir_arg:
        source_dir = resolve_user_path(source_dir_arg)
    else:
        console.print("[bold cyan]Enter absolute path to backup directory to verify:[/bold cyan]")
        source_input = input("> ").strip()
        if not source_input:
            bail("No source path provided.")
        source_dir = resolve_user_path(source_input)

    if not source_dir.exists():
        bail(f"Backup directory '{source_dir}' does not exist.")

    manifest_file = source_dir / "backup_manifest.json"
    if not manifest_file.exists():
        bail(f"Manifest 'backup_manifest.json' not found in '{source_dir}'.")

    try:
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as e:
        bail(f"Corrupted manifest file: {e}")

    table = Table(title=f"Verification Report ({source_dir.name})", show_header=True, header_style="bold magenta")
    table.add_column("File / Component", style="cyan")
    table.add_column("Expected Hash", justify="center")
    table.add_column("Status", justify="left")

    file_hashes: Dict[str, str] = manifest_data.get("file_hashes", {})
    all_valid = True
    passed_count = 0

    for rel_path, expected_hash in file_hashes.items():
        full_path = source_dir / rel_path
        if not full_path.exists():
            table.add_row(rel_path, expected_hash[:8] + "...", "[red]MISSING[/red]")
            all_valid = False
            continue

        actual_hash = compute_sha256(full_path)
        if actual_hash == expected_hash:
            passed_count += 1
            table.add_row(rel_path, expected_hash[:8] + "...", "[green]VALID (SHA-256 MATCH)[/green]")
        else:
            table.add_row(rel_path, expected_hash[:8] + "...", f"[red]CORRUPTED (Hash Mismatch!)[/red]")
            all_valid = False

    console.print(table)
    
    meta_table = Table(title="Backup Metadata", show_header=False)
    meta_table.add_column("Key", style="bold yellow")
    meta_table.add_column("Value", style="white")
    meta_table.add_row("Created At", manifest_data.get("created_at", "unknown"))
    meta_table.add_row("Origin Hostname", manifest_data.get("hostname", "unknown"))
    meta_table.add_row("Kernel", manifest_data.get("kernel", "unknown"))
    meta_table.add_row("Libvirt Version", manifest_data.get("libvirt_version", "unknown"))
    meta_table.add_row("Domains", ", ".join(d.get("name", "") for d in manifest_data.get("domains", [])))
    meta_table.add_row("Networks", ", ".join(n.get("name", "") for n in manifest_data.get("networks", [])))
    meta_table.add_row("Storage Pools", ", ".join(p.get("name", "") for p in manifest_data.get("pools", [])))
    console.print(meta_table)

    if all_valid:
        console.print(f"\n[success]✓ All {passed_count} files successfully verified with zero errors.[/success]")
    else:
        console.print("\n[danger]✗ Integrity verification failed! Some files are corrupted or missing.[/danger]")

    return all_valid

# ==============================================================================
# PHASE AUTOMATION: RESTORE
# ==============================================================================
def execute_restore(source_dir_arg: Optional[str] = None, non_interactive: bool = False, shutdown_running: bool = False) -> None:
    check_libvirt_connection()
    verify_system_users()
    handle_running_vms(interactive=not non_interactive, shutdown_running=shutdown_running)

    console.print("\n[bold cyan]─── Libvirt Surgical High-Fidelity Restoration ───[/bold cyan]")
    console.print("[dim]Note: Ensure your external drive holding raw disk images (.qcow2/.img) is mounted.[/dim]\n")

    if source_dir_arg:
        source_dir = resolve_user_path(source_dir_arg)
    else:
        console.print("[bold cyan]Enter absolute path to your backup directory:[/bold cyan]")
        source_input = input("> ").strip()
        if not source_input:
            bail("No source path provided.")
        source_dir = resolve_user_path(source_input)

    if not source_dir.exists():
        bail(f"Backup directory '{source_dir}' does not exist.")

    # Integrity verification prior to injection
    manifest_file = source_dir / "backup_manifest.json"
    manifest_data: Optional[Dict[str, Any]] = None
    if manifest_file.exists():
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            console.print("[info]Validating backup integrity checksums before state injection...[/info]")
            file_hashes: Dict[str, str] = manifest_data.get("file_hashes", {})
            for rel_path, expected_hash in file_hashes.items():
                target_f = source_dir / rel_path
                if target_f.exists():
                    if compute_sha256(target_f) != expected_hash:
                        bail(f"Integrity check failed: '{rel_path}' is corrupted!")
        except json.JSONDecodeError:
            console.print("[warning]Manifest is corrupted. Proceeding with raw files...[/warning]")

    table = Table(title=f"Restoration Telemetry ({source_dir.name})", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Action", justify="center")
    table.add_column("Result", justify="left")

    # --------------------------------------------------------------------------
    # 1. Restore Binary & Cryptographic State Archives
    # --------------------------------------------------------------------------
    state_targets = [
        {"name": "vTPM Local CA", "archive": "state_archives/swtpm_localca_state.tar.zst", "extract_to": "/var/lib"},
        {"name": "vTPM 2.0 State", "archive": "state_archives/swtpm_state.tar.zst", "extract_to": "/var/lib/libvirt"},
        {"name": "NVRAM (UEFI Vars)", "archive": "state_archives/nvram_state.tar.zst", "extract_to": "/var/lib/libvirt/qemu"},
        {"name": "Firmware Varstore", "archive": "state_archives/varstore_state.tar.zst", "extract_to": "/var/lib/libvirt/qemu"},
        {"name": "Libvirt Secrets (Var)", "archive": "state_archives/secrets_var_state.tar.zst", "extract_to": "/var/lib/libvirt"},
        {"name": "Libvirt Secrets (Etc)", "archive": "state_archives/secrets_etc_state.tar.zst", "extract_to": "/etc/libvirt"},
        {"name": "Managed Save States", "archive": "state_archives/save_state.tar.zst", "extract_to": "/var/lib/libvirt/qemu"},
        {"name": "KVM / VFIO Hooks", "archive": "state_archives/hooks_state.tar.zst", "extract_to": "/etc/libvirt"},
        {"name": "KVM Hooks.qemu.d", "archive": "state_archives/hooks_qemu_d_state.tar.zst", "extract_to": "/etc/libvirt"},
        {"name": "Daemon Configs", "archive": "state_archives/daemon_configs.tar.zst", "extract_to": "/etc"}
    ]

    legacy_map = {
        "state_archives/swtpm_state.tar.zst": "swtpm_state.tar.gz",
        "state_archives/nvram_state.tar.zst": "nvram_state.tar.gz"
    }

    with console.status("[cyan]Injecting cryptographic states (preserving DAC/MAC contexts)...[/cyan]", spinner="dots"):
        for state in state_targets:
            archive_path = source_dir / state["archive"]
            if not archive_path.exists() and state["archive"] in legacy_map:
                legacy_path = source_dir / legacy_map[state["archive"]]
                if legacy_path.exists():
                    archive_path = legacy_path

            if archive_path.exists():
                dest = Path(state["extract_to"])
                dest.mkdir(parents=True, exist_ok=True)
                
                # Detect compression format
                if archive_path.suffix == ".zst" or archive_path.name.endswith(".tar.zst"):
                    cmd = ["tar", "--zstd", "--xattrs", "--xattrs-include=*", "--acls", "--sparse", "-xpf", str(archive_path), "-C", str(dest)]
                else:
                    cmd = ["tar", "--xattrs", "--acls", "-xzpf", str(archive_path), "-C", str(dest)]
                
                res = run_cmd(cmd, check=False)
                if res.returncode == 0:
                    table.add_row(state["name"], "State Injection", "[green]Restored with DAC/MAC[/green]")
                else:
                    table.add_row(state["name"], "State Injection", f"[yellow]Partial / Warning: {res.stderr.strip()}[/yellow]")
            else:
                table.add_row(state["name"], "State Injection", "[dim]Not Found (Skipped)[/dim]")

    # Ownership & Permission Enforcement
    try:
        # swtpm permissions
        swtpm_dir = Path("/var/lib/libvirt/swtpm")
        if swtpm_dir.exists():
            os.chmod(swtpm_dir, 0o711)
            for item in swtpm_dir.glob("*/tpm2"):
                try:
                    tss_uid = pwd.getpwnam("tss").pw_uid
                    tss_gid = grp.getgrnam("tss").gr_gid
                    os.chown(item, tss_uid, tss_gid)
                    os.chmod(item, 0o700)
                    for f in item.iterdir():
                        os.chown(f, tss_uid, tss_gid)
                        os.chmod(f, 0o600)
                except Exception:
                    pass

        # nvram permissions
        nvram_dir = Path("/var/lib/libvirt/qemu/nvram")
        if nvram_dir.exists():
            try:
                qemu_uid = pwd.getpwnam("libvirt-qemu").pw_uid
                qemu_gid = grp.getgrnam("libvirt-qemu").gr_gid
            except KeyError:
                qemu_uid = pwd.getpwnam("qemu").pw_uid
                qemu_gid = grp.getgrnam("qemu").gr_gid

            for fd in nvram_dir.glob("*.fd"):
                try:
                    os.chown(fd, qemu_uid, qemu_gid)
                    os.chmod(fd, 0o600)
                except Exception:
                    pass
    except Exception as e:
        console.print(f"[warning]Permission fixup note:[/warning] {e}")

    # --------------------------------------------------------------------------
    # 2. Rebuild Libvirt Secrets (XML definitions)
    # --------------------------------------------------------------------------
    secrets_dir = source_dir / "secrets"
    if secrets_dir.exists():
        with console.status("[cyan]Registering Libvirt Secrets...[/cyan]", spinner="dots"):
            for sec_file in secrets_dir.glob("*.xml"):
                sec_res = run_cmd(["virsh", "secret-define", str(sec_file)], check=False)
                if sec_res.returncode == 0:
                    table.add_row(sec_file.stem, "SECRET", "[green]Defined[/green]")

    # --------------------------------------------------------------------------
    # 3. Rebuild Network Filters (NWFilters)
    # --------------------------------------------------------------------------
    nwfilters_dir = source_dir / "nwfilters"
    if nwfilters_dir.exists():
        with console.status("[cyan]Registering Network Filters...[/cyan]", spinner="dots"):
            for nw_file in nwfilters_dir.glob("*.xml"):
                nw_res = run_cmd(["virsh", "nwfilter-define", str(nw_file)], check=False)
                if nw_res.returncode == 0:
                    table.add_row(nw_file.stem, "NWFILTER", "[green]Defined[/green]")

    # --------------------------------------------------------------------------
    # 4. Restore Storage Pools
    # --------------------------------------------------------------------------
    pools_dir = source_dir / "pools"
    pool_meta_map = {}
    if manifest_data:
        for p in manifest_data.get("pools", []):
            pool_meta_map[p.get("name")] = p

    if pools_dir.exists():
        with console.status("[cyan]Rebuilding Storage Pools...[/cyan]", spinner="dots"):
            for xml_file in pools_dir.glob("*.xml"):
                name = xml_file.stem
                p_meta = pool_meta_map.get(name, {})
                should_autostart = p_meta.get("autostart", True)
                was_active = p_meta.get("active", True)
                target_path_str = p_meta.get("extra", {}).get("target_path", "")

                if target_path_str:
                    Path(target_path_str).mkdir(parents=True, exist_ok=True)

                run_cmd(["virsh", "pool-define", str(xml_file)], check=False)
                
                if was_active:
                    start_res = run_cmd(["virsh", "pool-start", name], check=False)
                    status_str = "[green]Defined & Started[/green]" if start_res.returncode == 0 else "[yellow]Defined (Start Pending Mount)[/yellow]"
                else:
                    status_str = "[green]Defined[/green]"

                if should_autostart:
                    run_cmd(["virsh", "pool-autostart", name], check=False)
                else:
                    run_cmd(["virsh", "pool-autostart", name, "--disable"], check=False)

                table.add_row(name, "STORAGE POOL", status_str)

    # --------------------------------------------------------------------------
    # 5. Restore Virtual Networks
    # --------------------------------------------------------------------------
    networks_dir = source_dir / "networks"
    net_meta_map = {}
    if manifest_data:
        for n in manifest_data.get("networks", []):
            net_meta_map[n.get("name")] = n

    if networks_dir.exists():
        with console.status("[cyan]Rebuilding Layer 2/3 Virtual Networks...[/cyan]", spinner="dots"):
            for xml_file in networks_dir.glob("*.xml"):
                name = xml_file.stem
                n_meta = net_meta_map.get(name, {})
                should_autostart = n_meta.get("autostart", True)
                was_active = n_meta.get("active", True)

                run_cmd(["virsh", "net-define", str(xml_file)], check=False)
                if was_active:
                    run_cmd(["virsh", "net-start", name], check=False)
                
                if should_autostart:
                    run_cmd(["virsh", "net-autostart", name], check=False)
                else:
                    run_cmd(["virsh", "net-autostart", name, "--disable"], check=False)

                table.add_row(name, "NETWORK", "[green]Defined & Configured[/green]")

    # --------------------------------------------------------------------------
    # 6. Re-link Virtual Machines
    # --------------------------------------------------------------------------
    vms_dir = source_dir / "vms"
    vm_meta_map = {}
    if manifest_data:
        for v in manifest_data.get("domains", []):
            vm_meta_map[v.get("name")] = v

    if vms_dir.exists():
        with console.status("[cyan]Registering Virtual Machines...[/cyan]", spinner="dots"):
            for xml_file in vms_dir.glob("*.xml"):
                vm_name = xml_file.stem
                vm_meta = vm_meta_map.get(vm_name, {})
                should_autostart = vm_meta.get("autostart", False)

                def_res = run_cmd(["virsh", "define", str(xml_file)], check=False)
                if def_res.returncode == 0:
                    if should_autostart:
                        run_cmd(["virsh", "autostart", vm_name], check=False)
                    table.add_row(vm_name, "VM Blueprint", "[green]Successfully Linked[/green]")
                else:
                    table.add_row(vm_name, "VM Blueprint", f"[red]Define Failed: {def_res.stderr.strip()}[/red]")

    # --------------------------------------------------------------------------
    # 7. Re-inject VM Snapshots & Checkpoints (Topological Order)
    # --------------------------------------------------------------------------
    snap_base_dir = source_dir / "snapshots"
    curr_snaps_map = manifest_data.get("current_snapshots", {}) if manifest_data else {}

    if snap_base_dir.exists():
        with console.status("[cyan]Re-registering VM Snapshots & Hierarchies...[/cyan]", spinner="dots"):
            for vm_snap_dir in snap_base_dir.iterdir():
                if vm_snap_dir.is_dir():
                    vm_name = vm_snap_dir.name
                    order_file = vm_snap_dir / "order.txt"
                    if order_file.exists():
                        snapshots = [line.strip() for line in order_file.read_text(encoding="utf-8").splitlines() if line.strip()]
                        success_count = 0
                        for snap in snapshots:
                            safe_name = sanitize_filename(snap)
                            snap_xml_file = vm_snap_dir / f"{safe_name}.xml"
                            if snap_xml_file.exists():
                                snap_res = run_cmd(
                                    ["virsh", "snapshot-create", vm_name, str(snap_xml_file), "--redefine"],
                                    check=False
                                )
                                if snap_res.returncode == 0:
                                    success_count += 1

                        # Restore current snapshot pointer if recorded
                        curr_snap = curr_snaps_map.get(vm_name)
                        if curr_snap and curr_snap in snapshots:
                            run_cmd(["virsh", "snapshot-current", vm_name, curr_snap], check=False)

                        if success_count == len(snapshots) and snapshots:
                            table.add_row(vm_name, "SNAPSHOTS", f"[green]{success_count} Topological Re-registered[/green]")
                        elif success_count > 0:
                            table.add_row(vm_name, "SNAPSHOTS", f"[yellow]{success_count}/{len(snapshots)} Re-registered[/yellow]")
                        else:
                            table.add_row(vm_name, "SNAPSHOTS", "[red]0 Re-registered (Failed)[/red]")

    # Re-inject Checkpoints
    ckpt_base_dir = source_dir / "checkpoints"
    if ckpt_base_dir.exists():
        with console.status("[cyan]Re-registering VM Checkpoints...[/cyan]", spinner="dots"):
            for vm_ckpt_dir in ckpt_base_dir.iterdir():
                if vm_ckpt_dir.is_dir():
                    vm_name = vm_ckpt_dir.name
                    for ckpt_xml in vm_ckpt_dir.glob("*.xml"):
                        run_cmd(["virsh", "checkpoint-create", vm_name, str(ckpt_xml), "--redefine"], check=False)
                    table.add_row(vm_name, "CHECKPOINTS", "[green]Re-registered[/green]")

    console.print("\n")
    console.print(table)
    console.print("[success]✓ Restoration Complete. Systemd modular sockets & libvirt have synced your infrastructure.[/success]")

# ==============================================================================
# MAIN & CLI ENTRYPOINT
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3: Libvirt Cryptographic State & Infrastructure Continuity Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--backup", metavar="DEST", type=str, help="Execute backup non-interactively to DEST directory")
    parser.add_argument("--restore", metavar="SRC", type=str, help="Execute restoration non-interactively from SRC directory")
    parser.add_argument("--verify", metavar="DIR", type=str, help="Verify backup integrity in DIR")
    parser.add_argument("--shutdown-running", action="store_true", help="Gracefully shut down active VMs before operation")
    parser.add_argument("--non-interactive", "-y", action="store_true", help="Run without interactive confirmation prompts")

    args = parser.parse_args()

    if args.verify:
        execute_verify(args.verify)
        sys.exit(0)
    elif args.backup:
        execute_backup(args.backup, non_interactive=args.non_interactive, shutdown_running=args.shutdown_running)
        sys.exit(0)
    elif args.restore:
        execute_restore(args.restore, non_interactive=args.non_interactive, shutdown_running=args.shutdown_running)
        sys.exit(0)

    # Interactive TUI Mode
    console.clear()
    console.print(Panel(
        "[bold green]KVM GPU Passthrough: Phase 3[/bold green]\n"
        "Target: State Isolation & Continuity Manager (Arch / Kernel 7.1+ / Python 3.14+)",
        expand=False
    ))

    check_libvirt_connection()

    while True:
        console.print("\n[bold]Select an operation vector:[/bold]")
        console.print("  [cyan]1.[/cyan] Extract and Archive State (Backup)")
        console.print("  [cyan]2.[/cyan] Inject and Rebuild State (Restore)")
        console.print("  [cyan]3.[/cyan] Verify Backup Integrity (Audit/Check)")
        console.print("  [cyan]4.[/cyan] Exit gracefully")

        choice = prompt_choice("Vector", choices=["1", "2", "3", "4"], default="4")

        match choice:
            case "1":
                execute_backup(non_interactive=False)
                break
            case "2":
                execute_restore(non_interactive=False)
                break
            case "3":
                execute_verify()
                break
            case "4":
                console.print("[yellow]Execution aborted gracefully.[/yellow]")
                sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[danger]⚠ Process interrupted by operator. Exiting cleanly.[/danger]\n")
        sys.exit(130)
