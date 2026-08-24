---
title: "Permissions — Historical (Pre-Modular)"
tags:
  - kvm
  - libvirt
  - legacy
  - archive
aliases:
  - Old libvirt Permission Model
---

# Permissions — Historical (Pre-Modular) — Archived

> [!warning] Legacy — retained as archive, not guidance
> This file is the *old* model before `10_virt_modular_daemon.py` and systemd socket activation. Kept so diffs against current note stay visible. **Do not follow these steps on Aug 2026 Arch.** Use [[Give the User System-Wide Permission]] instead.

## What it taught (and why changed)

```bash
# HISTORICAL — do NOT run
sudo usermod -aG libvirt,kvm,input,disk "$(id -un)"
echo "export LIBVIRT_DEFAULT_URI='qemu:///system'" >> ~/.zshrc
source ~/.zshrc
virsh uri          # → qemu:///system (if .zshrc loaded)
sudo virsh uri     # root session check
```

| Historical claim | Aug 2026 reality | Reason |
|---|---|---|
| `disk` group needed for raw images | **never**; use POSIX ACLs (`07_storage_setup.py`) | raw `disk` = `dd` over any block dev incl. root |
| `input` always needed | optional; uaccess covers seated user | bypasses Wayland input isolation |
| `kvm` must be manually added | usually auto via `systemd-logind` uaccess; `virtqemud` holds `/dev/kvm` for system VMs | explicit add only for headless/SSH workflows |
| `export … >> ~/.zshrc` | breaks GUI launchers on Wayland | use `~/.config/environment.d/libvirt.conf` (`systemd` generator, covers terminals + GUI) |
| `libvirtd` monolith | masked; modular `virtqemud.socket` | 0 RSS idle, socket `SocketGroup=libvirt` is real perm (conf keys inert) |

## Why `~/.zshrc` failed

`virt-manager` launched from an app launcher inherits `systemd --user` env, not an interactive shell rc. So `LIBVIRT_DEFAULT_URI` set only in `.zshrc` applied in terminals, not GUI — landing you back in `qemu:///session` with no passthrough.

## Current fix (one-liner)

```bash
mkdir -p ~/.config/environment.d && echo "LIBVIRT_DEFAULT_URI='qemu:///system'" > ~/.config/environment.d/libvirt.conf
sudo usermod -aG libvirt "$(id -un)"  # + kvm,input only if your lab needs them
# log out / reboot, then:  virsh -c qemu:///system version
```

See current note: [[Give the User System-Wide Permission]].
