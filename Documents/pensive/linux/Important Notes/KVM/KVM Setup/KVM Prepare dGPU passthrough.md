---
title: "dGPU Passthrough — Quick Cheat Sheet"
tags:
  - kvm
  - vfio
  - passthrough
  - arch
aliases:
  - VFIO Quick Setup
---

# dGPU Passthrough — Quick Cheat Sheet

> [!warning] This is a *condensed* cheat sheet. The canonical, audited guide is [[Host PC  Preparation for GPU isolation]] (Phase 1–7) + `15_gpu_probing_kernal_param_mkinit.py`. Use this only for recall after you understand the full flow.

## 1. Identify IDs (sysfs is truth, not scraped text)

```bash
lspci -nn | grep -E "NVIDIA|VGA|Audio"
# 01:00.0 [10de:25a0] VGA — RTX 3050 Ti Mobile
# 01:00.1 [10de:2291] Audio
```

## 2. Systemd-boot cmdline (not GRUB)

```bash
sudo nvim /boot/loader/entries/arch.conf  # Type1; or /etc/kernel/cmdline for UKI Type2
# append to `options` line (keep existing root= rw quiet …):
intel_iommu=on iommu=pt
# AMD: omit amd_iommu=on unless board IVRS is broken → add amd_iommu=force_enable via --amd-force-enable
```

> [!info] Why `modprobe.d` is the real enforcer
> `15_gpu_probing_kernal_param_mkinit.py:desired_params` documents `amd_iommu=on` is **not a valid token** (`parse_amd_iommu_options` accepts `off/force_enable/...`); AMD-Vi is on by default when IVRS sane. `vfio-pci.ids=` on cmdline is also racy — the committed claim lives in `/etc/modprobe.d/arsonix-vfio.conf` baked into initramfs (`modconf` before `kms`). Boot cmdline `vfio-pci.ids` is optional mirror (`--cmdline-ids`).

## 3. Initramfs

```bash
sudo nvim /etc/mkinitcpio.conf
MODULES=(… vfio_pci vfio vfio_iommu_type1)  # keep i915/amdgpu ahead for LUKS console
# HOOKS must have modconf before kms:
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck)
# or drop-in: /etc/mkinitcpio.conf.d/99-arsonix-vfio.conf → MODULES+=(vfio_pci vfio vfio_iommu_type1)
```

## 4. Modprobe — the committed claim

```bash
sudo nvim /etc/modprobe.d/arsonix-vfio.conf
```
```ini
# Managed by Arsonix — single source of truth
options vfio-pci ids=10de:25a0,10de:2291
softdep nvidia pre: vfio-pci
softdep nouveau pre: vfio-pci
# blacklist only discrete vendor driver; never snd_hda_intel/xhci_pci (Phase 3 NEVER_BLACKLIST)
blacklist nouveau
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset
blacklist nvidia_uvm
```

## 5. Rebuild + verify

```bash
sudo mkinitcpio -P
sudo reboot
lspci -nnk -d 10de:25a0   # Kernel driver in use: vfio-pci
sudo dmesg | grep -i vfio
# optional: sudo lsinitcpio /boot/initramfs-linux.img | grep vfio
```

> [!tip] Cross-refs
> - Full IOMMU/ACS/ID-collision audits: [[Host PC  Preparation for GPU isolation]]`:§1.2`
> - Slot = all functions (VGA+audio+xHCI+UCSI): `15_*:enumerate_pci`
> - Revert: delete `arsonix-vfio.conf`, remove `vfio-pci.ids`/`module_blacklist`, `mkinitcpio -P` — see Host Preparation Appendix.

See also: [[Grub Kernal Parameters]] (legacy GRUB warning), [[Verify VT-x and Kernel Modules and IOMMU]].
