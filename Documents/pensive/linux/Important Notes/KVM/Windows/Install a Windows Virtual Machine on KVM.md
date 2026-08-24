---
title: "Windows Install — Load VirtIO Drivers & Guest Tools"
tags:
  - kvm
  - windows
  - virtio
  - arch
---

# Windows Install — Load VirtIO Drivers & Guest Tools

> [!info] Context
> You booted the VM after `Customize configuration before install` + `VirtIO` disk/NIC ([[Configure the Storage]] / [[Configure Virtual Network Interface]]) + second CDROM `virtio-win.iso` ([[Mount the VirtIO-Win ISO Image]]). Windows *will* show empty drive list — it has no virtio driver yet.

## 1. Storage driver (mandatory)

On **Where do you want to install Windows?** (empty list):

1. **Load driver → Browse** → **CD Drive (E:)** (virtio-win)
2. Expand **`viostor` → `w10` or `w11` → `amd64`** → **OK**
3. Select **Red Hat VirtIO SCSI controller** → **Next**

Drive appears → select → **Next** (installer copies; auto-reboot).

## 2. Network driver (optional, not recommended during Setup)

Needed only if you need LAN *during* OOBE. Same flow → **`NetKVM` → `w10|w11` → `amd64`** → **OK**.

Otherwise skip; Windows Update or later `virtio-win-guest-tools` provides it.

## 3. Guest Tools (first desktop boot)

After OOBE login:

1. **File Explorer → CD Drive (E:)** (virtio-win)
2. Run **`virtio-win-guest-tools.exe`** (not the per-arch MSI unless debugging).

Installs:
- `qxl`/`virtio-gpu` video (fallback when `virtio` video in Q35)
- `spice-agent` (clipboard) + `QEMU Guest Agent` + `viostor`/`NetKVM`/`vioinput`/`viofs`

> [!tip] Which file?
> `virtio-win-guest-tools.exe` is the meta-installer. Per-arch MSIs are partial. If cursor vanishes after `vioinput`, uninstall via **Device Manager → Mice → Uninstall** then re-run `virtio-win-guest-tools` (post-reboot).

## 4. Auto-resize display

With Guest Tools installed:

1. `virt-manager` viewer → **View → Scale Display** → ✅ **Auto resize VM with window**
2. Resize window → guest resolution snaps.

## 5. Cleanup — remove installer ISO

Shut off VM:

1. `virt-manager` main → VM **Shutoff**
2. Lightbulb → **SATA CDROM 1** (Windows ISO) → **Disconnect / remove**
3. **SATA CDROM 2** (`virtio-win.iso`) → **keep** attached (handy for driver reinstall) or Remove

![[Pasted image 20250726223648.png]]

> [!success] Ready — proceed to [[Optimize Windows Performance]] + shared folder / TPM passthrough notes. For scripted labs, `30_kvm_vm_deploy.py` attaches both ISOs via `virt-install --disk path=…,device=cdrom,bus=sata,readonly=on` and boots `uefi,cdrom,hd,menu=on`.

See: [[Configure Windows Virtual Hardware]], [[Enable Trusted Platform Module (TPM)]].
