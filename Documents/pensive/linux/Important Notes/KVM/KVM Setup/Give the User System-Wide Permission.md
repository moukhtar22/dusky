---
title: "Libvirt Connection & Permissions (qemu:///system)"
tags:
  - kvm
  - libvirt
  - permissions
  - polkit
  - arch
---

# Libvirt Connection & Permissions — Modern Modular

> [!abstract] Goal
> Seamlessly use `qemu:///system` (passthrough, bridges) as a regular user — no `sudo virt-manager`. Uses polkit + `libvirt` group + systemd way, not `.bashrc`. Supersedes the old `Give the User System-Wide Permission_old` (`disk/kvm/input` blanket).

## 1. Two URIs — pick system

| URI | Runs as | Passthrough | Net |
|---|---|---|---|
| `qemu:///session` (default for plain user) | `$USER` via user session | **no** | slirp/user only |
| `qemu:///system` ✅ | `root` via `virtqemud` | **yes** | NAT + `br0` |

We want **system**.

## 2. Polkit + socket perms (Aug 2026)

- Daemon `virtqemud` runs as `root` listening on `/run/libvirt/virtqemud-sock` (0660 `root:libvirt` via drop-in — `10_virt_modular_daemon.py`).
- Regular user gains RW by being in `libvirt` group (Arch polkit rules map that group to allow).

```bash
sudo usermod -aG libvirt "$(id -un)"
groups; sleep 1; # then fully log out & back in (Wayland/systemd — newgrp insufficient)
groups  # must show libvirt after re-login
```

> [!warning] Do **not** add `disk`/`input`/`kvm` as blanket
> - `disk` → raw write over *every* block device (root FS).
> - `input` → global keylog bypassing Wayland isolation.
> - `kvm` → auto-granted to seated user via `uaccess`; `virtqemud` already holds `/dev/kvm`. Only add `kvm`/`input` if the specific lab needs it (pipeline adds `kvm,input` for completeness; `disk` never).

> [!important] Re-login required
> Group creds are per-session. Log out of **all** sessions (or reboot). `id -nG` confirms.

## 3. Default URI — systemd-way (Wayland-safe)

Do **not** use `.bashrc`/`.zshrc` — GUI launchers (wofi/rofi/Steam) don't read them and `virt-manager` falls back to `qemu:///session`.

```bash
mkdir -p ~/.config/environment.d
printf "LIBVIRT_DEFAULT_URI='qemu:///system'\n" > ~/.config/environment.d/libvirt.conf
# Takes effect next login (systemd — user env generator). Or export for current shell:
export LIBVIRT_DEFAULT_URI='qemu:///system'
```

> [!tip] Dusk/UWSM already sets this
> If you run the Dusk UWSM env files, `LIBVIRT_DEFAULT_URI` is already exported — skip manual step.

## 4. Verify

```bash
# as regular user, no sudo
virsh uri                          # → qemu:///system
virsh -c qemu:///system -q version # socket RW OK
virt-manager --connect qemu:///system  # explicit fallback test
virsh -c qemu:///system list --all
```

If you get `Permission denied` / `qemu:///session`:

1. `groups | grep libvirt` → missing → re-login
2. `systemctl status virtqemud.socket` → must be `active (listening)`
3. `ls -l /run/libvirt/virtqemud-sock` → `srwxrwx---  root libvirt`
4. `loginctl user-status $USER | grep -i libvirt` (polkit session)

See: [[KVM Group Add]], [[libvert Modular daemon enable]], [[Set ACL on the Image Directory]] (ACLs after group perms).
