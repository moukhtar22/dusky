#!/usr/bin/env python3
"""
040_disk_mount.py - DUSKY Final Fixed - Python 3.14.6 + Rich 15.0.0
Fixes:
 [2] removeprefix not lstrip - vda -> a bug
 [3] NOCOW: chattr +C alone, clear stale m flag then +C, no btrfs property compression none before +C
 [4] swapoff safe: scan /proc/swaps + swapon --raw, match /mnt/swap/swapfile and /swap/swapfile and basename swapfile under /mnt
 [6] EFI kept hardened fmask=0177,dmask=0077,noexec,nosuid,nodev
 [7] Panel width: Panel.fit + Align.center + safe_box=False fixes full-width +---+ ASCII
 [8] make_console: direct assignment os.environ["TERM"]="linux" not setdefault
 [9] Removed unreachable duplicate return
 [10] Tight centered banners for AUTONOMOUS / INTERACTIVE
 [11] Surgically fixed hidden directory shadowing on @home/.snapshots
 [12] Augmented run() wrapper to expose stderr on CalledProcessError
"""

from __future__ import annotations
import os, sys, re, json, shlex, shutil, signal, subprocess, tempfile, argparse
from pathlib import Path

def _ensure_rich():
    import importlib.util
    try:
        if importlib.util.find_spec("rich") is not None:
            return
    except ModuleNotFoundError:
        pass
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("python-rich missing", file=sys.stderr)
        sys.exit(1)
    print(">> Installing python-rich...", file=sys.stderr)
    subprocess.run(["pacman","-Sy","--needed","--noconfirm","python-rich"], stdout=sys.stderr, stderr=sys.stderr)

_ensure_rich()
from rich.console import Console
from rich.panel import Panel
from rich.align import Align
from rich.text import Text
from rich.prompt import Confirm, Prompt
from rich import box

def make_console():
    term = os.environ.get("TERM","")
    if term in ("dumb","unknown",""):
        os.environ["TERM"] = "linux"
        return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False, safe_box=False)
    return Console(color_system="auto", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)

def refresh_console():
    global console
    console = make_console()

console = make_console()

EFI_GPT_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BTRFS_OPTS = "rw,noatime,compress=zstd:3,discard=async"
STATE_ENV = Path("/tmp/arch_install_state.env")
STATE_JSON = Path("/tmp/dusky_state.json")
DUSKY_EFI_LABEL = "DUSKY_EFI"
DUSKY_ROOT_LABEL = "DUSKY_ROOT"
SWAPFILE_PATH = Path("/mnt/swap/swapfile")
STD_SUBVOLS = ["@", "@home", "@snapshots", "@home_snapshots", "@var_log", "@var_cache", "@var_tmp", "@var_lib_machines", "@var_lib_portables"]
NOCOW_SUBVOLS = ["@var_lib_libvirt", "@var_lib_mysql", "@var_lib_postgres", "@swap"]
VALID_PART_RE = re.compile(r"^[a-zA-Z0-9_./-]+$")

def run(*cmd, check=True, capture=True, input_text=None, timeout=300):
    argv = [os.fspath(c) for c in cmd]
    try:
        if isinstance(input_text, (bytes, bytearray)):
            return subprocess.run(argv, check=check, text=False, capture_output=capture, input=bytes(input_text), timeout=timeout)
        elif isinstance(input_text, str):
            return subprocess.run(argv, check=check, text=True, capture_output=capture, input=input_text, timeout=timeout)
        return subprocess.run(argv, check=check, text=True, capture_output=capture, timeout=timeout)
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[red]Failed {shlex.join([str(x) for x in argv])}[/red]")
            err = getattr(e, 'stderr', None)
            if err:
                if isinstance(err, bytes): err = err.decode('utf-8', 'replace')
                err = err.strip()
                if err: console.print(f"[red]Details: {err}[/red]")
        raise

def detect_boot_mode():
    try:
        state = json.loads(STATE_JSON.read_text())
        bm = str(state.get("boot_mode", "")).upper()
        if bm in ("UEFI", "BIOS"):
            return bm
    except Exception:
        pass
    if Path("/sys/firmware/efi").is_dir():
        return "UEFI"
    try:
        if Path("/sys/firmware/efi/fw_platform_size").read_text().strip():
            return "UEFI"
    except Exception:
        pass
    try:
        for line in Path("/proc/mounts").read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and (fields[0] == "efivarfs" or fields[2] == "efivarfs"):
                return "UEFI"
    except Exception:
        pass
    try:
        r = run("dmesg", check=False, capture=True)
        if re.search(r"efi:.*v[0-9]", r.stdout or "", re.IGNORECASE):
            return "UEFI"
    except Exception:
        pass
    return "BIOS"

BOOT_MODE = detect_boot_mode()

def print_banner(title: str):
    txt = Text.from_markup(f"[bold cyan]{title}[/] [dim]{BOOT_MODE}[/]", justify="center")
    panel = Panel.fit(txt, box=box.ROUNDED, border_style="cyan", padding=(0,2))
    console.print(Align.center(panel))

def findmnt_json(target="/mnt"):
    try:
        r = run("findmnt","--json","--list","--submounts","--output","TARGET,SOURCE,FSTYPE,OPTIONS,ID","--target",target, check=False, capture=True)
        if r.returncode==0 and r.stdout.strip():
            return json.loads(r.stdout).get("filesystems",[])
    except:
        pass
    return []

def safe_deactivate_swaps():
    wanted = {"/mnt/swap/swapfile","/swap/swapfile"}
    candidates = set()
    try:
        r = run("swapon","--show=NAME","--raw","--noheadings", check=False, capture=True)
        candidates.update(l.strip() for l in r.stdout.splitlines() if l.strip())
    except:
        pass
    try:
        for line in Path("/proc/swaps").read_text().splitlines()[1:]:
            name = line.split()[0].strip()
            if name:
                candidates.add(name)
    except:
        pass
    for name in candidates:
        if name in wanted or (name.startswith("/mnt/") and name.endswith("swapfile")) or (Path(name).name=="swapfile" and (name.startswith("/mnt/") or name=="/swap/swapfile")):
            run("swapoff",name, check=False, capture=True)
    for p in wanted:
        run("swapoff",p, check=False, capture=True)

def unmount_mount_tree():
    try:
        r = run("swapon","--show=NAME","--raw","--noheadings", check=False, capture=True)
        for line in r.stdout.splitlines():
            n = line.strip()
            if not n:
                continue
            if Path(n).name=="swapfile" and (n in ("/mnt/swap/swapfile","/swap/swapfile") or n.startswith("/mnt/")):
                run("swapoff",n, check=False, capture=True)
        safe_deactivate_swaps()
        run("swapoff", "-a", check=False, capture=True)
    except:
        pass
    mnts = findmnt_json("/mnt")
    targets = []
    for fs in mnts:
        t = fs.get("target","")
        if t=="/mnt" or t.startswith("/mnt/"):
            targets.append(t)
    for mp in sorted(set(targets), key=lambda p:(p.count("/"),len(p)), reverse=True):
        try:
            if run("umount","-R",mp, check=False, capture=True).returncode != 0:
                run("umount", "-R", "-f", "-l", mp, check=False, capture=True)
        except:
            pass
    try:
        r = run("findmnt","-rn","-o","TARGET", check=False, capture=True)
        remaining = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("/mnt")]
        for mp in sorted(remaining, key=lambda p:(p.count("/"),len(p)), reverse=True):
            if run("umount",mp, check=False, capture=True).returncode != 0:
                run("umount", "-f", "-l", mp, check=False, capture=True)
    except:
        pass

    # Purge systemd 261 slave mount namespaces holding /mnt
    try:
        for proc_dir in Path("/proc").glob("[0-9]*"):
            try:
                mi = proc_dir / "mountinfo"
                if mi.is_file():
                    text = mi.read_text(errors="ignore")
                    if "/mnt" in text:
                        for line in text.splitlines():
                            if "/mnt" in line:
                                parts = line.split()
                                if len(parts) >= 5:
                                    target_mp = parts[4]
                                    run("nsenter", f"--mount=/proc/{proc_dir.name}/ns/mnt", "umount", "-R", "-f", "-l", target_mp, check=False, capture=True)
            except Exception:
                pass
    except Exception:
        pass

def is_empty_dir(p: Path):
    try:
        if not p.is_dir():
            return False
        with os.scandir(p) as it:
            return next(it,None) is None
    except:
        return False

def ensure_subvolume(path: Path, nocow=False):
    if path.exists():
        r = run("btrfs","subvolume","show",str(path), check=False, capture=True)
        if r.returncode!=0:
            console.print(f"[red]{path} exists not subvol[/red]")
            sys.exit(1)
        existed=True
    else:
        run("btrfs","subvolume","create",str(path), capture=True)
        existed=False
    if nocow:
        try:
            run("chattr","-m",str(path), check=False, capture=True)
            run("btrfs","property","set",str(path),"compression","", check=False, capture=True)
        except:
            pass
        if not existed:
            run("chattr","+C",str(path), check=False, capture=True)
        elif is_empty_dir(path):
            run("chattr","+C",str(path), check=False, capture=True)

def load_state():
    state={}
    if STATE_JSON.exists():
        try:
            state.update(json.loads(STATE_JSON.read_text()))
        except:
            pass
    if STATE_ENV.exists():
        try:
            script=f'set +u; source {shlex.quote(str(STATE_ENV))} 2>/dev/null; printf "PROVISIONED_ROOT_PART=%s\\nPROVISIONED_EFI_PART=%s\\nENCRYPT_ROOT=%s\\n" "$PROVISIONED_ROOT_PART" "$PROVISIONED_EFI_PART" "$ENCRYPT_ROOT"'
            r=subprocess.run(["bash","-c",script],text=True,capture_output=True,check=False,timeout=5)
            for line in r.stdout.splitlines():
                if "=" not in line:
                    continue
                k,v=line.split("=",1)
                if not v:
                    continue
                if k=="PROVISIONED_ROOT_PART":
                    state.setdefault("root_part",v)
                elif k=="PROVISIONED_EFI_PART":
                    state.setdefault("efi_part",v)
                elif k=="ENCRYPT_ROOT":
                    state.setdefault("encrypt",v=="1")
        except:
            pass
    return state

def get_partition_path(disk,num):
    disk=disk.rstrip("/")
    num_str=str(num)
    name=Path(disk).name
    if re.search(rf"\d+p{num_str}$",name):
        return disk
    if re.search(rf"[a-zA-Z]{num_str}$",name) and not re.search(r"(?:nvme\d+n\d+|mmcblk\d+|loop\d+|nbd\d+|pmem\d+)$",name):
        return disk
    if re.search(r"(?:nvme\d+n\d+|mmcblk\d+|loop\d+|nbd\d+|pmem\d+)$",name) or (disk and disk[-1].isdigit()):
        return f"{disk}p{num_str}"
    return f"{disk}{num_str}"

def determine_root_partition(auto_mode):
    state=load_state()
    encrypt_hint=state.get("encrypt")
    has_mapper=Path("/dev/mapper/cryptroot").exists()
    use_crypt=False
    if isinstance(encrypt_hint,bool):
        use_crypt=encrypt_hint
    elif has_mapper:
        use_crypt=True

    if use_crypt:
        mapped=Path("/dev/mapper/cryptroot")
        if not mapped.exists():
            prov=state.get("root_part")
            if prov and Path(prov).exists() and run("cryptsetup","isLuks",prov,check=False,capture=True).returncode == 0:
                console.print(f"[yellow]Opening LUKS mapper cryptroot on {prov}...[/yellow]")
                cred_pass = None
                try:
                    cred_file = Path("./.arch_credentials")
                    if cred_file.exists():
                        script = f'set +u; source {shlex.quote(str(cred_file))} 2>/dev/null; echo "$ROOT_PASS"'
                        r_pass = subprocess.run(["bash","-c",script], text=True, capture_output=True, check=False, timeout=5)
                        if r_pass.stdout.strip():
                            cred_pass = bytearray(r_pass.stdout.strip().encode())
                except Exception:
                    pass
                if cred_pass:
                    run("cryptsetup","open","--allow-discards","--key-file","-",prov,"cryptroot", input_text=cred_pass, check=False, capture=True)
                    for i in range(len(cred_pass)): cred_pass[i] = 0
            if not mapped.exists():
                console.print("[red]LUKS expected no mapper[/red]")
                sys.exit(1)
        backing=""
        try:
            for dm in Path("/sys/class/block").iterdir():
                if not dm.name.startswith("dm-"):
                    continue
                try:
                    if (dm/"dm"/"name").read_text().strip()=="cryptroot":
                        slaves=list((dm/"slaves").iterdir())
                        if slaves:
                            backing=f"/dev/{slaves[0].name}"
                            break
                except:
                    continue
        except:
            pass
        if not backing:
            r=run("cryptsetup","status","cryptroot",check=False,capture=True)
            for line in r.stdout.splitlines():
                if line.strip().lower().startswith("device:"):
                    backing=line.split(":",1)[1].strip()
                    break
        if not backing:
            console.print("[red]No backing[/red]")
            sys.exit(1)
        root_part=Path(backing).resolve()
        mapped_root=mapped
    else:
        if auto_mode:
            prov=state.get("root_part")
            if prov and Path(prov).exists():
                if run("cryptsetup","isLuks",prov,check=False,capture=True).returncode == 0:
                    mapped=Path("/dev/mapper/cryptroot")
                    if not mapped.exists():
                        cred_pass = None
                        try:
                            cred_file = Path("./.arch_credentials")
                            if cred_file.exists():
                                script = f'set +u; source {shlex.quote(str(cred_file))} 2>/dev/null; echo "$ROOT_PASS"'
                                r_pass = subprocess.run(["bash","-c",script], text=True, capture_output=True, check=False, timeout=5)
                                if r_pass.stdout.strip():
                                    cred_pass = bytearray(r_pass.stdout.strip().encode())
                        except Exception:
                            pass
                        if cred_pass:
                            run("cryptsetup","open","--allow-discards","--key-file","-",prov,"cryptroot", input_text=cred_pass, check=False, capture=True)
                            for i in range(len(cred_pass)): cred_pass[i] = 0
                    if mapped.exists():
                        root_part=Path(prov).resolve()
                        mapped_root=mapped
                    else:
                        root_part=Path(prov).resolve()
                        mapped_root=root_part
                else:
                    root_part=Path(prov).resolve()
                    mapped_root=root_part
            else:
                r=run("lsblk","-pnro","NAME,FSTYPE,LABEL",check=False,capture=True)
                btrfs_parts=[]
                duskies=[]
                for line in r.stdout.splitlines():
                    cols=line.split()
                    if len(cols)<2:
                        continue
                    name=cols[0]
                    fstype=cols[1]
                    label=cols[2] if len(cols)>2 else ""
                    if fstype=="btrfs":
                        if label==DUSKY_ROOT_LABEL:
                            duskies.append(name)
                        btrfs_parts.append(name)
                if len(duskies)==1:
                    root_part=Path(duskies[0]).resolve()
                    mapped_root=root_part
                elif len(btrfs_parts)==1:
                    root_part=Path(btrfs_parts[0]).resolve()
                    mapped_root=root_part
                else:
                    console.print("[red]Cannot auto-detect btrfs root[/red]")
                    sys.exit(1)
        else:
            r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,LABEL,PARTLABEL",check=False,capture=True)
            console.print(r.stdout)
            while True:
                raw=Prompt.ask("Enter DUSKY BTRFS root (e.g. vda2)",console=console)
                if not VALID_PART_RE.match(raw):
                    console.print("[red]Invalid[/red]")
                    continue
                name=raw.removeprefix("/dev/")
                p=Path("/dev")/name
                try:
                    rp=p.resolve()
                    if not rp.exists():
                        console.print(f"[red]{rp} no exist[/red]")
                        continue
                    root_part=rp
                    mapped_root=rp
                    break
                except Exception as e:
                    console.print(f"[red]{e}[/red]")
    if not root_part.exists():
        console.print(f"[red]{root_part} invalid[/red]")
        sys.exit(1)
    try:
        r=run("lsblk","-ndlo","PKNAME",str(root_part),check=False,capture=True)
        pk=r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
        if pk:
            root_disk=Path(f"/dev/{pk}").resolve()
        else:
            raise ValueError
    except:
        m=re.match(r"^(.*?)(?:p?\d+)$",root_part.name)
        if m:
            root_disk=Path(f"/dev/{m.group(1)}").resolve()
        else:
            console.print(f"[red]Failed parent disk[/red]")
            sys.exit(1)
    return mapped_root, root_part, root_disk

def validate_root_state(mapped_root):
    if not mapped_root.exists():
        console.print(f"[red]{mapped_root} not found[/red]")
        sys.exit(1)
    r=run("lsblk","-ndlo","FSTYPE",str(mapped_root),check=False,capture=True)
    if r.stdout.strip()!="btrfs":
        console.print(f"[red]{mapped_root} not btrfs[/red]")
        sys.exit(1)

def validate_efi_partition(part):
    r=run("lsblk","-ndlo","FSTYPE,PARTTYPE",str(part),check=False,capture=True)
    out=r.stdout.lower()
    if EFI_GPT_TYPE not in out and "vfat" not in out and "fat32" not in out:
        console.print(f"[red]{part} not ESP[/red]")
        sys.exit(1)

def is_mounted(dev):
    try:
        r=run("findmnt","-n","-o","TARGET","--source",dev,check=False,capture=True)
        return r.stdout.strip() or None
    except:
        return None

def flatten_lsblk(data):
    nodes = []
    def _walk(item_list):
        for item in item_list:
            nodes.append(item)
            if item.get("children"):
                _walk(item.get("children"))
    if isinstance(data, dict):
        _walk(data.get("blockdevices", []))
    elif isinstance(data, list):
        _walk(data)
    return nodes

def auto_detect_efi_partition(root_disk,root_part):
    try:
        r=run("lsblk","--json","--paths","--tree","-o","NAME,PATH,TYPE,PARTTYPE,FSTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True)
        data=json.loads(r.stdout)
        nodes=flatten_lsblk(data)
        guid=[]
        dusky=[]
        labelm=[]
        vfat=[]
        non_root=[]
        for ch in nodes:
            ptype=(ch.get("parttype") or "").lower()
            fstype=(ch.get("fstype") or "").lower()
            partlabel=ch.get("partlabel") or ""
            label=ch.get("label") or ""
            name=ch.get("path") or ch.get("name")
            if not name:
                continue
            try:
                pp=Path(name).resolve()
            except:
                pp=Path(name)
            if pp==root_part.resolve():
                continue
            if ch.get("type")!="part":
                continue
            non_root.append(pp)
            if ptype==EFI_GPT_TYPE:
                guid.append(pp)
                if label==DUSKY_EFI_LABEL or partlabel==DUSKY_EFI_LABEL:
                    dusky.append(pp)
            if "efi" in partlabel.lower():
                labelm.append(pp)
            if fstype in ("vfat","fat32"):
                vfat.append(pp)
        if len(dusky)==1:
            return dusky[0]
        if len(guid)==1:
            return guid[0]
        if len(labelm)==1:
            return labelm[0]
        if len(vfat)==1:
            return vfat[0]
        if len(non_root)==1:
            return non_root[0]
    except:
        pass
    return None

def prompt_for_efi_partition(root_disk):
    r=run("lsblk","-l","-o","NAME,SIZE,TYPE,FSTYPE,PARTTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True)
    console.print(r.stdout)
    while True:
        raw=Prompt.ask("Enter EFI partition (e.g. vda1)",console=console)
        if not VALID_PART_RE.match(raw):
            console.print("[red]Invalid[/red]")
            continue
        name=raw.removeprefix("/dev/")
        p=Path("/dev")/name
        try:
            rp=p.resolve()
            if rp.exists():
                return rp
            console.print(f"[red]{rp} no exist[/red]")
        except Exception as e:
            console.print(f"[red]{e}[/red]")

def determine_efi_partition(auto_mode,root_disk,root_part):
    if BOOT_MODE!="UEFI":
        return None
    state=load_state()
    prov=state.get("efi_part")
    if prov and Path(prov).exists():
        console.print(f"[cyan]Auto EFI {prov}[/cyan]")
        return Path(prov).resolve()
    det=auto_detect_efi_partition(root_disk,root_part)
    if det:
        console.print(f"[cyan]Auto EFI {det}[/cyan]")
        return det
    if auto_mode or not sys.stdin.isatty():
        try:
            parts = flatten_lsblk(json.loads(run("lsblk","--json","--paths","--tree","-o","NAME,PATH,TYPE,PARTTYPE,FSTYPE,PARTLABEL,LABEL",str(root_disk),check=False,capture=True).stdout))
            for p in parts:
                if p.get("type") == "part" and (p.get("parttype","").lower() == EFI_GPT_TYPE or p.get("fstype","").lower() in ("vfat","fat32")):
                    p_path = Path(p.get("path") or p.get("name")).resolve()
                    if p_path != root_part.resolve():
                        console.print(f"[cyan]Auto-fallback EFI {p_path}[/cyan]")
                        return p_path
        except Exception:
            pass
        console.print("[yellow]No EFI partition detected in auto mode[/yellow]")
        return None
    return prompt_for_efi_partition(root_disk)

def construct_subvolume_matrix(mapped_root):
    console.print("[yellow]>> Constructing DUSKY Subvolume Matrix...[/yellow]")
    tmpdir=Path(tempfile.mkdtemp(prefix="dusky-btrfs-",dir="/tmp"))
    try:
        run("mount","-t","btrfs","-o","subvolid=5",str(mapped_root),str(tmpdir),capture=True)
        for sub in STD_SUBVOLS:
            ensure_subvolume(tmpdir/sub,nocow=False)
        for sub in NOCOW_SUBVOLS:
            ensure_subvolume(tmpdir/sub,nocow=True)
        console.print("[green]>> Matrix OK[/green]")
    finally:
        run("umount",str(tmpdir),check=False,capture=True)
        try:
            tmpdir.rmdir()
        except:
            pass

def assemble_fhs(mapped_root,efi_part):
    console.print("[yellow]>> Assembling FHS to /mnt...[/yellow]")
    Path("/mnt").mkdir(parents=True,exist_ok=True)
    run("mount","-o",f"{BTRFS_OPTS},subvol=@",str(mapped_root),"/mnt",capture=True)
    
    # Removed "home/.snapshots" from this loop to prevent creating a masked directory inside the @ subvolume root
    for mp in ["home",".snapshots","var/log","var/cache","var/tmp","var/lib/machines","var/lib/portables","var/lib/libvirt","var/lib/mysql","var/lib/postgres","swap","boot","proc","sys","dev","run","etc","tmp","root","mnt","opt","srv"]:
        Path(f"/mnt/{mp}").mkdir(parents=True,exist_ok=True)
        
    mounts=[
        (f"{BTRFS_OPTS},subvol=@home","/mnt/home"),
        (f"{BTRFS_OPTS},subvol=@snapshots","/mnt/.snapshots"),
        (f"{BTRFS_OPTS},subvol=@var_log","/mnt/var/log"),
        (f"{BTRFS_OPTS},subvol=@var_cache","/mnt/var/cache"),
        (f"{BTRFS_OPTS},subvol=@var_tmp","/mnt/var/tmp"),
        (f"{BTRFS_OPTS},subvol=@var_lib_machines","/mnt/var/lib/machines"),
        (f"{BTRFS_OPTS},subvol=@var_lib_portables","/mnt/var/lib/portables"),
        (f"{BTRFS_OPTS},subvol=@var_lib_libvirt","/mnt/var/lib/libvirt"),
        (f"{BTRFS_OPTS},subvol=@var_lib_mysql","/mnt/var/lib/mysql"),
        (f"{BTRFS_OPTS},subvol=@var_lib_postgres","/mnt/var/lib/postgres"),
        (f"{BTRFS_OPTS},subvol=@swap","/mnt/swap"),
    ]
    for opts,tgt in mounts:
        run("mount","-o",opts,str(mapped_root),tgt,capture=True)
        
    # Safely created over the active @home subvolume mount
    Path("/mnt/home/.snapshots").mkdir(parents=True,exist_ok=True)
    run("mount","-o",f"{BTRFS_OPTS},subvol=@home_snapshots",str(mapped_root),"/mnt/home/.snapshots",capture=True)
    
    if BOOT_MODE=="UEFI" and efi_part:
        console.print(f"[yellow]>> Mounting EFI {efi_part} to /mnt/boot (hardened)...[/yellow]")
        run("mount","-t","vfat","-o","fmask=0177,dmask=0077,noexec,nosuid,nodev",str(efi_part),"/mnt/boot",capture=True)
        sync_secondary_efi_bootloaders("/mnt/boot", str(efi_part))

    if STATE_JSON.exists():
        try:
            chroot_etc = Path("/mnt/etc")
            chroot_etc.mkdir(parents=True, exist_ok=True)
            (chroot_etc / "dusky_state.json").write_text(STATE_JSON.read_text(errors="ignore"))
        except Exception:
            pass

def sync_secondary_efi_bootloaders(primary_esp_mnt: str = "/mnt/boot", primary_esp_dev: Optional[str] = None):
    """
    Best-effort copy of vendor EFI dirs from other ESPs (dual-boot / extra USB).
    Must NEVER abort the install: missing secondary media, busy devices, or
    copy errors are warnings only. Previously a missing `import shutil` turned
    any secondary vfat into a hard Fatal.
    """
    if BOOT_MODE != "UEFI":
        return
    try:
        console.print("[yellow]>> Scanning for secondary EFI bootloaders to synchronize...[/yellow]")
        r = run("lsblk", "--json", "--paths", "--tree", "-o", "PATH,TYPE,FSTYPE,PARTTYPE,LABEL,MOUNTPOINTS", check=False, capture=True)
        if r.returncode != 0 or not r.stdout:
            return
        try:
            data = json.loads(r.stdout)
        except Exception:
            return

        esp_guid = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        primary_esp_path = ""
        if primary_esp_dev:
            try:
                primary_esp_path = str(Path(primary_esp_dev).resolve())
            except Exception:
                pass

        if not primary_esp_path:
            try:
                r_mnt = run("findmnt", "-n", "-e", "-o", "SOURCE", primary_esp_mnt, check=False, capture=True)
                src = (r_mnt.stdout or "").strip()
                if src and src.startswith("/"):
                    primary_esp_path = str(Path(src).resolve())
            except Exception:
                pass

        target_efi_dir = Path(primary_esp_mnt) / "EFI"
        target_efi_dir.mkdir(parents=True, exist_ok=True)

        for child in flatten_lsblk(data):
            try:
                path = child.get("path") or child.get("name")
                if not path:
                    continue
                dev_type = (child.get("type") or "").lower()
                # Only real partitions. Whole disks / loops / roms are not dual-boot ESPs
                if dev_type and dev_type != "part":
                    continue

                try:
                    p_res = str(Path(path).resolve())
                except Exception:
                    p_res = path

                if primary_esp_path and p_res == primary_esp_path:
                    continue

                # Never touch live ISO / airootfs backing devices or existing /mnt mounts
                mps = child.get("mountpoints") or child.get("mountpoint") or []
                if isinstance(mps, str):
                    mps = [mps]
                if any(isinstance(m, str) and (m.startswith("/run/archiso") or m.startswith("/mnt") or m in ("/", "/boot", "/efi")) for m in mps if m):
                    continue

                ptype = (child.get("parttype") or "").lower()
                fstype = (child.get("fstype") or "").lower()
                is_esp = ptype == esp_guid
                is_vfat = fstype in ("vfat", "fat32")
                if not (is_esp or is_vfat):
                    continue

                tmp_dir = None
                try:
                    tmp_dir = tempfile.mkdtemp(prefix="dusky_sec_esp_")
                    m_res = run("mount", "-t", "vfat", "-o", "ro,noexec,nosuid,nodev", p_res, tmp_dir, check=False, capture=True)
                    if m_res.returncode != 0:
                        continue
                    sec_efi = Path(tmp_dir) / "EFI"
                    if not sec_efi.is_dir():
                        continue
                    for vendor_dir in sec_efi.iterdir():
                        if not vendor_dir.is_dir():
                            continue
                        v_name = vendor_dir.name
                        if not v_name or v_name.startswith("."):
                            continue
                        dst_vendor = target_efi_dir / v_name
                        console.print(f"[cyan]Syncing secondary EFI vendor directory '{v_name}' from {p_res} -> {dst_vendor}[/cyan]")
                        try:
                            shutil.copytree(vendor_dir, dst_vendor, dirs_exist_ok=True, copy_function=shutil.copy2)
                        except Exception as e:
                            console.print(f"[yellow]Warning syncing {v_name}: {e}[/yellow]")
                except Exception as e:
                    console.print(f"[yellow]Warning: secondary ESP {path}: {e}[/yellow]")
                finally:
                    if tmp_dir:
                        run("umount", tmp_dir, check=False, capture=True)
                        try:
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                        except Exception:
                            pass
            except Exception as e:
                console.print(f"[yellow]Warning: secondary EFI candidate skipped: {e}[/yellow]")
                continue
    except Exception as e:
        console.print(f"[yellow]Warning: secondary EFI sync skipped: {e}[/yellow]")
        return

def get_free_bytes(path: Path | str) -> int:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except Exception:
        return 8 * 1024**3

def initialize_swapfile():
    console.print("[yellow]>> Ensuring swapfile...[/yellow]")
    try:
        r=run("swapon","--show=NAME","--raw","--noheadings",check=False,capture=True)
        for line in r.stdout.splitlines():
            n=line.strip()
            if not n:
                continue
            if Path(n).name=="swapfile" and (n in ("/mnt/swap/swapfile","/swap/swapfile") or n.startswith("/mnt/")):
                run("swapoff",n,check=False,capture=True)
        try:
            swaps=Path("/proc/swaps").read_text()
            for line in swaps.splitlines()[1:]:
                name=line.split()[0]
                if Path(name).name=="swapfile":
                    run("swapoff",name,check=False,capture=True)
        except:
            pass
    except:
        pass

    if SWAPFILE_PATH.exists() and not SWAPFILE_PATH.is_file():
        console.print(f"[red]{SWAPFILE_PATH} not regular file[/red]")
        sys.exit(1)

    free_bytes = get_free_bytes("/mnt/swap")
    desired_size = 4 * 1024**3
    if free_bytes < 3 * 1024**3:
        desired_size = max(256 * 1024**2, int(free_bytes * 0.35))

    size_str = f"{max(256, desired_size // (1024**2))}M"

    if SWAPFILE_PATH.is_file():
        try:
            cur_sz = SWAPFILE_PATH.stat().st_size
            if cur_sz >= 256 * 1024**2 and abs(cur_sz - desired_size) < 512 * 1024**2:
                if run("swapon",str(SWAPFILE_PATH),check=False,capture=True).returncode==0:
                    console.print(f"[green]>> Swap ({size_str}) re-activated[/green]")
                    return
        except:
            pass
        try:
            SWAPFILE_PATH.unlink(missing_ok=True)
            run("sync", check=False, capture=True)
            run("udevadm", "settle", "--timeout=5", check=False, capture=True)
        except Exception as e:
            console.print(f"[yellow]Warning removing old swapfile: {e}[/yellow]")

    mk_res = run("btrfs","filesystem","mkswapfile","--size",size_str,"--uuid","clear",str(SWAPFILE_PATH),check=False,capture=True)
    if mk_res.returncode == 0 and SWAPFILE_PATH.is_file():
        sw_res = run("swapon",str(SWAPFILE_PATH),check=False,capture=True)
        if sw_res.returncode == 0:
            console.print(f"[green]>> Swapfile ({size_str}) created and activated.[/green]")
            return

    try:
        SWAPFILE_PATH.unlink(missing_ok=True)
        run("truncate", "-s", size_str, str(SWAPFILE_PATH), check=False)
        run("chattr", "+C", str(SWAPFILE_PATH), check=False)
        run("chmod", "600", str(SWAPFILE_PATH), check=False)
        run("mkswap", str(SWAPFILE_PATH), check=False)
        sw_res2 = run("swapon",str(SWAPFILE_PATH),check=False,capture=True)
        if sw_res2.returncode == 0:
            console.print(f"[green]>> Swapfile ({size_str}) created via fallback and activated.[/green]")
            return
    except Exception as e:
        console.print(f"[yellow]Warning setting up swapfile: {e}[/yellow]")

    console.print("[yellow]Warning: Swapfile activation skipped[/yellow]")

def teardown_state():
    try:
        safe_deactivate_swaps()
    except:
        pass
    unmount_mount_tree()

def run_common(auto_mode):
    teardown_state()
    mapped_root,root_part,root_disk=determine_root_partition(auto_mode)
    validate_root_state(mapped_root)
    efi_part=None
    if BOOT_MODE=="UEFI":
        efi_part=determine_efi_partition(auto_mode,root_disk,root_part)
        if efi_part:
            efi_part=efi_part.resolve()
            validate_efi_partition(efi_part)
            tmp_obj=None
            try:
                mnt=is_mounted(str(efi_part))
                tp=mnt
                if not mnt:
                    tmp_obj=tempfile.TemporaryDirectory(prefix="dusky_efi_check_")
                    tp=tmp_obj.name
                    run("mount","--mkdir","-t","vfat","-o","ro,noexec,nosuid,nodev",str(efi_part),tp,check=False,capture=True)
                if tp and Path(tp,"EFI","Microsoft").is_dir():
                    console.print(Align.center(Panel.fit(f"[cyan]Dual-boot Windows on {efi_part}, preserving[/cyan]", box=box.ROUNDED, border_style="cyan")))
                if tmp_obj:
                    run("umount",tp,check=False,capture=True)
            except:
                try:
                    if tmp_obj:
                        run("umount",tp,check=False,capture=True)
                except:
                    pass
            finally:
                try:
                    if tmp_obj:
                        tmp_obj.cleanup()
                except:
                    pass
    construct_subvolume_matrix(mapped_root)
    assemble_fhs(mapped_root,efi_part)
    initialize_swapfile()
    console.print(Align.center(Panel.fit("[bold green]>> DUSKY Setup Complete[/bold green]", box=box.ROUNDED, border_style="green")))
    try:
        r=run("lsblk","-l","-f",str(root_disk),check=False,capture=True)
        console.print(Align.center(Panel.fit(r.stdout, title=f"lsblk {root_disk}", box=box.ROUNDED, border_style="dim")))
    except:
        pass
    try:
        r=run("findmnt","-R","/mnt",check=False,capture=True)
        console.print(Align.center(Panel.fit(r.stdout, title="findmnt /mnt", box=box.ROUNDED, border_style="dim")))
    except:
        pass

def run_auto_mode():
    print_banner("AUTONOMOUS DUSKY BTRFS MOUNT")
    run_common(True)

def run_interactive_mode():
    print_banner("INTERACTIVE DUSKY BTRFS MOUNT")
    run_common(False)

def main():
    parser=argparse.ArgumentParser(description="DUSKY 040 - BTRFS Mount")
    parser.add_argument("--auto",action="store_true")
    args=parser.parse_args()
    if hasattr(os,"geteuid") and os.geteuid()!=0:
        console.print("[red]Need root[/red]")
        sys.exit(1)
    if not args.auto and not sys.stdin.isatty():
        console.print("[red]Need TTY or --auto[/red]")
        sys.exit(1)
    def _h(sig,frame):
        console.print(f"\n[yellow]Signal {signal.Signals(sig).name}[/yellow]")
        teardown_state()
        sys.exit(128+sig)
    signal.signal(signal.SIGINT,_h)
    signal.signal(signal.SIGTERM,_h)
    try:
        if args.auto:
            run_auto_mode()
        else:
            if Confirm.ask("Run AUTONOMOUS?",console=console,default=True):
                run_auto_mode()
            else:
                run_interactive_mode()
    except KeyboardInterrupt:
        console.print("[red]Interrupted[/red]")
        teardown_state()
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Fatal {e}[/red]")
        import traceback
        traceback.print_exc()
        teardown_state()
        sys.exit(1)

if __name__=="__main__":
    main()
