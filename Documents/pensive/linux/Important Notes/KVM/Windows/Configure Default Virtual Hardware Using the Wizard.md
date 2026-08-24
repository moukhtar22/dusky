---
title: "Wizard — Create Windows VM (Redirect)"
tags:
  - kvm
  - windows
  - virt-manager
  - arch
aliases:
  - Windows Wizard Stub
---

# Wizard — Create Windows VM (virt-manager) — Windows

> [!tip] Merged — canonical source
> **Full wizard (ISO + osinfo + CPU/RAM + pool/volume + Customize-before-install) lives in [[KVM Setup/VM Creation/01 Wizard — Create VM]] (shared Windows + Linux routes).** This stub keeps **only Windows-specific choices** inline so filename `[[wikilink]]` stays valid.

## Windows choices (when following canonical)

| Step | Windows selection |
|---|---|
| **ISO + osinfo** | Browse → Windows 11 ISO → uncheck *Automatically detect* if mis-detects → type `win11` → **Microsoft Windows 11** (`http://microsoft.com/win/11`). For LTSC/custom use `win10` if TPM patched out. (`--osinfo win11`, not `--os-variant` deprecated) |
| **CPU & RAM** | **8192 MiB** recommended (min 4096), **4–8 vCPUs** (later pin via `35_cpu_pinning_generator.py`). See canonical table for Linux sizing. |
| **Storage** | **Select or create custom storage → Manage → Pool (`dir: Filesystem Directory`)** → target `/var/lib/libvirt/images` (ACLs via `07_storage_setup.py`) → **Volume `win11.qcow2` `qcow2` 64 GiB** (Win10 40 GiB). **Uncheck** *Allocate entire volume now* (thin `cluster_size=64k,lazy_refcounts=on`). |
| **Finalize** | Name `win11` → **✅ Customize configuration before install** → Finish → lands in hardware detail (then [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI|02 Chipset]], etc.) |
| **Extra disk** | After Finish → **Add Hardware → Storage → CDROM → `virtio-win.iso`** (see [[Mount the VirtIO-Win ISO Image]]) — Windows needs `viostor`/`NetKVM`; Linux skips. |

> [!info] Linux route (same wizard, different OS box)
> Linux pick is `archlinux` / `linux2024` etc., 4 GiB / 20–40 GiB, **no** 2nd `virtio-win.iso`. See canonical `> [!info] Linux route`.

Screenshots retained in `Windows/`: `![[Pasted image 20250726180159.png]]` (ISO/osinfo picker), `![[Pasted image 20250727150813.png]]` (pool/volume).

See: canonical [[KVM Setup/VM Creation/01 Wizard — Create VM]], [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[+ MOC Windows Installation Through Virt Manager]] (ordered path).
