---
title: "Boot Recovery — Black Screen After VFIO"
tags:
  - kvm
  - vfio
  - recovery
  - arch
  - systemd-boot
---

# Boot Recovery — Black Screen After VFIO

> [!abstract] Symptom
> You isolated your **only** GPU → `vfio-pci` claims it early (`initramfs`), `nvidia`/`amdgpu` never load → DM has no renderer → black.

## Prereqs

- Live USB (Arch)
- Know root dev (`/dev/nvme0n1p2`, `/dev/sda2`, …) + ESP (`/dev/nvme0n1p1`)

## 1. Boot Live USB

Live → Terminal

## 2. Mount root

### ext4/xfs (non-btrfs)

```bash
sudo mount /dev/nvme0n1p2 /mnt    # adjust
```

### Btrfs (`@` subvol)

```bash
sudo mount /dev/nvme0n1p2 /mnt
ls /mnt   # look for @, @home, @log, @cache
sudo umount /mnt
sudo mount -o subvol=@ /dev/nvme0n1p2 /mnt
```

## 3. Identify VFIO state

```bash
cat /mnt/etc/modprobe.d/arsonix-vfio.conf        # options vfio-pci ids=…
grep -E '^MODULES=' /mnt/etc/mkinitcpio.conf /mnt/etc/mkinitcpio.conf.d/*.conf 2>/dev/null
cat /mnt/proc/cmdline 2>/dev/null || cat /mnt/etc/kernel/cmdline 2>/dev/null   # Type2 UKI source
# GRUB hosts: grep -R vfio /mnt/etc/default/grub /mnt/boot/loader/entries/ 2>/dev/null
ls /mnt/boot/loader/entries/*.conf
```

## 4. Remove isolation

### A — Full revert (single-GPU host — recommended)

```bash
sudo rm /mnt/etc/modprobe.d/arsonix-vfio.conf /mnt/etc/modprobe.d/vfio.conf 2>/dev/null || true
# keep FS modules (btrfs), drop vfio from MODULES
sudo sed -i -E 's/^(MODULES=\([^)]*) *vfio_pci[^)]*/\1/' /mnt/etc/mkinitcpio.conf
sudo sed -i -E 's/^(MODULES=\([^)]*) *vfio[^)]*/\1/'     /mnt/etc/mkinitcpio.conf
sudo sed -i -E 's/^(MODULES=\([^)]*) *vfio_iommu_type1[^)]*/\1/' /mnt/etc/mkinitcpio.conf
# scrub kernel cmdline of managed keys
for f in /mnt/boot/loader/entries/*.conf /mnt/etc/kernel/cmdline /mnt/etc/cmdline.d/*.conf; do
  [ -f "$f" ] || continue
  sudo sed -i -E 's/ *intel_iommu=on//g; s/ *amd_iommu=[^ ]*//g; s/ *iommu=[^ ]*//g; s/ *vfio-pci\.ids=[^ ]*//g; s/ *module_blacklist=[^ ]*//g' "$f"
done
```

### B — Keep passthrough of one card (dual-GPU host)

```bash
lspci -nn | grep -i vga
sudo nvim /mnt/etc/modprobe.d/arsonix-vfio.conf   # keep only passthrough IDs, delete primary GPU's pair
```

## 5. Chroot + rebuild

### ESP + binds

```bash
sudo mount /dev/nvme0n1p1 /mnt/boot/efi  2>/dev/null || sudo mount /dev/nvme0n1p1 /mnt/boot 2>/dev/null || true
# btrfs extra mounts (adjust names)
sudo mount -o subvol=@home  /dev/nvme0n1p2 /mnt/home  2>/dev/null || true
sudo mount -o subvol=@cache /dev/nvme0n1p2 /mnt/var/cache 2>/dev/null || true
sudo mount -o subvol=@log   /dev/nvme0n1p2 /mnt/var/log   2>/dev/null || true
sudo mount --bind /dev /mnt/dev
sudo mount --bind /proc /mnt/proc
sudo mount --bind /sys /mnt/sys
sudo mount --bind /run /mnt/run
```

### Rebuild

```bash
# Arch (preferred):
sudo arch-chroot /mnt mkinitcpio -P
# manual:
sudo chroot /mnt bash -c "mkinitcpio -P"
# Fedora/RHEL dracut: chroot /mnt dracut --force
# Ubuntu: chroot /mnt update-initramfs -u -k all
```

### Bootloader

```bash
# GRUB (only if this host uses GRUB):
sudo arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg    # Arch
# sudo chroot /mnt grub2-mkconfig -o /boot/grub2/grub.cfg     # Fedora
# systemd-boot (Type1/Type2): no extra mkconfig — kernel cmdline + mkinitcpio/kernel-install already rebuilt UKI/entry
sudo arch-chroot /mnt bootctl is-installed && sudo arch-chroot /mnt bootctl update 2>/dev/null || true
```

## 6. Reboot

```bash
sudo umount -R /mnt
reboot
```

## Troubleshoot

| Error | Fix |
|---|---|
| `failed to detect root filesystem` during `mkinitcpio` | `/dev` not bound → use `arch-chroot` (handles devtmpfs) or `mount -t devtmpfs devtmpfs /mnt/dev` |
| `nvidia module not found` warnings | kernel/headers mismatch or `nvidia-dkms` not built for that kernel → boot known-good kernel |
| Still black | `cat /mnt/etc/modprobe.d/*nouveau*` → nouveau blacklisted → remove; `pacman -Q nvidia` in chroot; temporarily add `nomodeset` to loader entry |

## Prevention

1. **Looking Glass** — view VM via shm instead of pass-through display
2. **SSH** — headless host access
3. **Cheap second GPU** (GT 710) for host
4. **iGPU** for host (Hybrid, not Discrete MUX)

### Minimal cheatsheet (Arch + btrfs `@`)

```bash
sudo mount -o subvol=@ /dev/nvme0n1p2 /mnt
sudo rm /mnt/etc/modprobe.d/arsonix-vfio.conf 2>/dev/null; sudo sed -i -E 's/^(MODULES=\([^)]*) *vfio[^)]*/\1/' /mnt/etc/mkinitcpio.conf
sudo mount /dev/nvme0n1p1 /mnt/boot/efi
sudo arch-chroot /mnt mkinitcpio -P
sudo umount -R /mnt; reboot
```
