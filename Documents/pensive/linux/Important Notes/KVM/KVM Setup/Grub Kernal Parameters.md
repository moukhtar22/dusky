---
title: "Kernel Cmdline — systemd-boot (Aug 2026)"
tags:
  - kvm
  - kernel
  - vfio
  - systemd-boot
  - bootctl
aliases:
  - IOMMU Boot Parameters
---

# Kernel Cmdline — systemd-boot (Aug 2026)

> [!info] This host uses **systemd-boot** BLS, not GRUB. `10_virt_modular_daemon.py` through `15_gpu_probing_kernal_param_mkinit.py` own this plane via `bootctl` JSON. Patch the correct Type1/Type2 source — never both.

## TL;DR

```bash
# Type1 (snippet) — edit the active entry
sudo nvim /boot/loader/entries/arch.conf
# find `options` line, MERGE (keep existing root=, rw, quiet…)
options root=UUID=… rw quiet intel_iommu=on iommu=pt

# Type2 (UKI) — canonical source
sudo nvim /etc/kernel/cmdline          # or /etc/cmdline.d/99-arsonix-vfio.conf if you already use drop-ins
# put the SAME desired tokens; run mkinitcpio/kernel-install to rebake the UKI
sudo mkinitcpio -P   # or: sudo kernel-install add-all
sudo reboot
cat /proc/cmdline | tr ' ' '\n' | grep -E 'iommu|vfio|module_blacklist'
```

| Vendor | Emit | Why |
|---|---|---|
| Intel | `intel_iommu=on iommu=pt` | Intel VT-d on + host bypass (perf). Canonical. |
| AMD | `iommu=pt` only | `amd_iommu=on` is **invalid** (`parse_amd_iommu_options` accepts `off/force_enable/...` — `15_*.py:desired_params`); AMD-Vi already on when IVRS sane. Only `amd_iommu=force_enable` if `--amd-force-enable` (broken IVRS). |
| Either | `vfio-pci.ids=` — **do not add here** | Committed via `/etc/modprobe.d/arsonix-vfio.conf` baked into initramfs (race-free). Boot-line mirror only if `--cmdline-ids`. |
| Either | `module_blacklist=nvidia,nouveau,…` | Emitted when discrete driver not shared with another GPU (twin-card check). Never blacklist shared `amdgpu`/`i915`. |

## How the pipeline decides

- `cpu_vendor()` reads `/proc/cpuinfo` (`GenuineIntel` vs `AuthenticAMD`).
- `resolve_boot_entry()` prefers `bootctl list --json=short` (`isSelected`/`isDefault` score), falls back to `bootctl` text parse.
- `merge_cmdline()` keeps foreign tokens verbatim/in-order, owns `MANAGED_KEYS=(intel_iommu,amd_iommu,iommu,module_blacklist,vfio-pci.ids)`, drops volatile tokens (`single`, `rescue`, `BOOT_IMAGE=`…), honors `--` init separator, and de-pollutes cross-vendor stale keys.
- Type2 with existing `/etc/kernel/cmdline` vs `/etc/cmdline.d/*.conf` chooses a **single source** and de-pollutes the other (prevents concatenated duplicate `vfio-pci.ids=`).

Verify entry type:

```bash
bootctl status
bootctl list --json=short | python3 -m json.tool | grep -E 'type|id|isSelected|isDefault'
ls -l /boot/loader/entries/ /etc/kernel/ /etc/cmdline.d/
```

> [!warning] Legacy — GRUB
> Old notes used `sudo nvim /etc/default/grub` → `GRUB_CMDLINE_LINUX_DEFAULT="… intel_iommu=on iommu=pt …"` → `sudo grub-mkconfig -o /boot/grub/grub.cfg`.
> - GRUB's `update-grub` chain is not present on systemd-boot hosts (this machine boots via `systemd-boot` + BLS Type1/Type2 UKI).
> - `amd_iommu=on` inside that GRUB line is **ignored** by the kernel — same bug as above.
> - `vfio-pci.ids=` on the GRUB line alone loses the race on early-modeset; `modprobe.d` + `modconf` before `kms` is the reliable claim.
> Keep GRUB instructions only for hosts actually running GRUB; otherwise follow this note.

See: [[KVM Prepare dGPU passthrough]], [[Host PC  Preparation for GPU isolation]], `15_gpu_probing_kernal_param_mkinit.py`.
