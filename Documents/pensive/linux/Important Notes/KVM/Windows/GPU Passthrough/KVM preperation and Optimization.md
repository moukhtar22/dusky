---
title: "KVM Preparation & Optimization (Roadmap)"
tags:
  - kvm
  - arch
  - roadmap
---

# KVM Preparation & Optimization — Roadmap

> [!info] Purpose
> Thin index that points to deep notes. Each checkbox = a gate the pipeline enforces (`05_`, `07_`, `10_`, `15_`, `20_`).

## 1. Prerequisites

- [ ] [[Verify VT-x and Kernel Modules and IOMMU]] — BIOS + `lscpu` + `zgrep CONFIG_IOMMUFD` + IOMMU groups (before any libvirt)

## 2. Kernel modules (rare)

- [ ] [[KVM Loading Kernel Module]] — `lsmod | grep kvm` → `modprobe kvm_intel/amd` → `/etc/modules-load.d/kvm.conf`

### Optional: RAM-backed pool

> [!tip] Ephemeral only
> - [ ] [[Symbolic link to zram for image file]] / [[Set ACL on the Image Directory]] → `07_storage_setup.py` (persistent vs `zram`/`tmpfs`, ACL `rwx` + `default:rwx`)

## 3. Daemon model — choose one (modular recommended)

> [!question] Which?
> - **Modular** (idle 0 MB, socket-activated, Arch default) — [[libvert Modular daemon enable]] → `10_virt_modular_daemon.py`
> - **Monolith** (`libvirtd`) — [[KVM Services]] — **legacy, not recommended; masked in pipeline**

- [ ] **Option A (Recommended):** [[libvert Modular daemon enable]]
- [ ] **Option B (Classic, archived):** [[KVM Services]]

## 4. Host tuning

> [!warning] TLP conflict
> [[Optimize the Host with TuneD]] — skip if `TLP` is active.

## 5. Networking

- [ ] [[Activating Network and Setting it to Autostart]] → `default` NAT (`virbr0`, `nftables`)
- [ ] [[Network Bridging for LAN access]] → *Option 3* only if you need LAN-visible web hosting

## 6. Permissions & ACLs

- [ ] [[Give the User System-Wide Permission]] — `libvirt` group + `~/.config/environment.d/libvirt.conf`
- [ ] [[Set ACL on the Image Directory]] — `u:operator:rwx` + `d:u:operator:rwx` (and `libvirt-qemu` if de-privileged QEMU)

Optional:

> [!tip] Custom pool only
> - [ ] [[Set ACL on the Image Directory]] — needed only if `TARGET != /var/lib/libvirt/images`

Flow → next: [[Host PC  Preparation for GPU isolation]] (VFIO) → [[+ MOC Windows Installation Through Virt Manager]].
