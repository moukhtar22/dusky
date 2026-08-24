---
title: "Wizard — Create VM (virt-manager, Unified)"
tags:
  - kvm
  - virt-manager
  - arch
  - qemu
  - libvirt
aliases:
  - VM Wizard Unified
---

# Wizard — Create VM (virt-manager, Unified)

> [!abstract] Goal
> Scaffold the VM correctly **before** any tuning. Canonical automation: `30_kvm_vm_deploy.py:interview` + `build_command` (`--osinfo`, `q35`, `host-passthrough`, `virtio`). This note is the **shared** wizard; OS-specific deltas are marked inside.

## Prerequisites

- Host up: [[KVM Packages]] → [[KVM Loading Kernel Module]] → [[KVM Group Add]] + [[Give the User System-Wide Permission]] ( `LIBVIRT_DEFAULT_URI=qemu:///system` )
- Storage pool declared: [[Set ACL on the Image Directory]] / [[Symbolic link to zram for image file]] → `07_storage_setup.py` (pool dir + ACL `rwx`/`default:rwx`)
- Sockets armed: [[libvert Modular daemon enable]] → `virtqemud.socket` active, `virt-manager --connect qemu:///system`
- Enable XML editing: `virt-manager` → **Edit → Preferences → General → ✅ Enable XML editing** (required for Hyper-V/pinning/`<shmem>` later) — see [[Configure Windows Virtual Hardware]]

## Step 1 — Source

1. `virt-manager` → **Create a new virtual machine** (computer icon)
2. **Local install media (ISO image or CDROM)** → **Forward**

## Step 2 — ISO + osinfo

1. **Browse → Browse Local** → select OS ISO
2. Uncheck **Automatically detect from the installation media** if it mis-detects (custom/LTSC, Arch daily)

> [!info] Windows route
> - OS box → type `win11` → pick **Microsoft Windows 11** (`win11` → `http://microsoft.com/win/11`). Drives libosinfo defaults: Q35, OVMF, TPM, Hyper-V enlightenments. For Windows 10 use `win10`. Do **not** hand-type; select the entry.
> - This choice forces [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]] (`smm=on`) + [[Enable Trusted Platform Module (TPM)]] (`tpm-crb` 2.0) automatically in `virt-manager` 4.1+; script `30_*:build_command --osinfo win11` does same.

> [!info] Linux route
> - OS box → type `archlinux` → **Arch Linux** (`http://archlinux.org/archlinux/rolling`) or `linux2024` / `fedora40` / `ubuntu24.04` as appropriate. Linux osinfo **does not** add TPM/Hyper-V; no `smm` requirement, no Secure Boot need (optional). Virtio drivers are **in-kernel** — no 2nd ISO needed.
> - For generic kernel testing, `linux2024` / `fedora-unknown` are safe fallbacks.

> [!warning] Legacy — `--os-variant` vs `--osinfo`
> Old docs used `--os-variant`. Since `libosinfo` 1.11 / `virt-install` 4.x, CLI is **`--osinfo`** (`--osinfo list`). `30_*` uses `--osinfo archlinux / win11 / linux2024`. `--os-variant` still aliases but deprecated.

## Step 3 — CPU & RAM

| OS | Memory | vCPUs | Note |
|---|---|---|---|
| **Windows 11** | `8192` MiB recommended (min `4096`) | `4–8` typical (later pin via `35_cpu_pinning_generator.py`) | installer blocks <4 GiB |
| **Windows 10** | `4096` MiB | `2–4` |  |
| **Arch / Linux** | `4096` MiB typical (`2048` min for light) | `4–6` typical | GNOME/KDE → 4 GiB+ |

→ **Forward**

## Step 4 — Storage (pool + volume)

1. **Select or create custom storage → Manage**
2. **Pool: `+`** → name e.g. `windows-pool` or `linux-pool` → type `dir: Filesystem Directory` → Target Path: your pool (`/var/lib/libvirt/images` or `/mnt/media/…`; provisioned via `07_storage_setup.py` with ACLs)
3. Select pool → **+ (Volumes)** → name `win11.qcow2` / `archlinux.qcow2` → **Format `qcow2`** → Capacity `64` GiB (Win11 floor), `40` GiB (Win10), `20–40` GiB (Arch thin)

> [!tip] QCOW2 thin provision
> **Uncheck** *Allocate entire volume now* — sparse, grows on demand. File creation uses `qemu-img create -o cluster_size=64k,lazy_refcounts=on` (`30_*:provision_disk`), `660` and pool ACLs make it writable without `sudo`. `raw` is faster (no metadata) but no snapshots.

4. **Choose Volume → Forward**

> [!info] Ephemeral (`/mnt/zram1`) warning
> If your pool is RAM-backed (`zram` / `tmpfs` per [[Symbolic link to zram for image file]]), contents **vanish on reboot**. Use only for throwaway labs; otherwise keep persistent `/var/lib/libvirt/images`. Pipeline `07_*` warns if you mix volatile/persistent.

## Step 5 — Finalize

1. **Name:** e.g. `win11` / `archlinux` (`^[A-Za-z0-9._+-]{1,50}$` — libvirt name)
2. **✅ Customize configuration before install** ← **must tick** (so you land in hardware detail view for [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI|02 Chipset]] → [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard|03 Storage]] → [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)|04 Network]] → [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology|05 CPU]] **before first boot**)
→ **Finish** → lands in **Show virtual hardware details** (lightbulb).

> [!tip] Windows extra disk (VirtIO)
> Windows needs a 2nd CDROM for `virtio-win.iso` **before first boot** (else no `viostor`/`NetKVM`). After Finish → **Add Hardware → Storage → CDROM → `virtio-win.iso`** (see [[Mount the VirtIO-Win ISO Image]]). Linux **skip** — drivers in-kernel.

## Screenshots (retained files)

- `![[Pasted image 20250726180159.png]]` — wizard ISO + osinfo picker
- `![[Pasted image 20250726223648.png]]` — post-install `virtio-win-guest-tools` flow
- `![[Pasted image 20250727150813.png]]` — pool/volume chooser

If images fail to render in reading view, they remain in `Windows/` as archival screenshots.

## Next

- [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]] (Q35 → OVMF)
- [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]] (bus/cache/queues)
- [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]] (virtio + NAT/br0)
- [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]] (host-passthrough)
- Then OS install: [[Install a Windows Virtual Machine on KVM]] (Windows `viostor`/`NetKVM`) or boot Arch ISO directly.

## CLI mirror (same result, scripted)

```bash
# Windows
virt-install --connect qemu:///system --name win11 --memory 8192 --vcpus 4 \
  --osinfo win11 --machine q35 --boot uefi \
  --disk path=/var/lib/libvirt/images/win11.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --disk path=/var/lib/libvirt/images/win11.iso,device=cdrom,bus=sata,readonly=on \
  --disk path=/var/lib/libvirt/images/virtio-win.iso,device=cdrom,bus=sata,readonly=on

# Linux (Arch)
virt-install --connect qemu:///system --name archlinux --memory 4096 --vcpus 4 \
  --osinfo archlinux --machine q35 --boot uefi \
  --disk path=/var/lib/libvirt/images/archlinux.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --disk path=/var/lib/libvirt/images/archlinux.iso,device=cdrom,bus=sata,readonly=on
```

See: `30_kvm_vm_deploy.py`, [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]].
