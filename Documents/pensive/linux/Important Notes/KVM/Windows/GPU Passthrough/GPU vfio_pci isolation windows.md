---
title: "GPU vfio-pci Isolation — Compact Recipe"
tags:
  - kvm
  - vfio
  - arch
---

# GPU `vfio-pci` Isolation — Compact Recipe

> [!info] This is the short form. Canonical deep dive: [[Host PC  Preparation for GPU isolation]] (Phases 1–7) + `15_gpu_probing_kernal_param_mkinit.py`.

## Identify

```bash
lspci -nn | grep -E "NVIDIA"
# 01:00.0 [10de:25a0] VGA
# 01:00.1 [10de:2291] Audio
```

## systemd-boot

```bash
sudo nvim /boot/loader/entries/arch.conf   # Type1; Type2 → /etc/kernel/cmdline
# keep existing zswap… add (same line as root=):
intel_iommu=on iommu=pt vfio-pci.ids=10de:25a0,10de:2291 module_blacklist=nvidia,nvidia_modeset,nvidia_uvm,nvidia_drm,nouveau
# AMD: omit vfio-pci.ids here; rely on modprobe.d (below)
```

> [!warning] Legacy GRUB kept as collapsed context in [[Grub Kernal Parameters]]; host actually boots systemd-boot BLS. `vfio-pci.ids=` on cmdline alone is racy — `modprobe.d` baked into initramfs is authoritative.

## mkinitcpio

```bash
sudo nvim /etc/mkinitcpio.conf
MODULES=(btrfs vfio_pci vfio vfio_iommu_type1)   # keep your FS modules (btrfs…) first
HOOKS=(systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems)  # modconf<kms critical
```

## Modprobe

```bash
sudo nvim /etc/modprobe.d/arsonix-vfio.conf
options vfio-pci ids=10de:25a0,10de:2291
softdep nvidia pre: vfio-pci
blacklist nouveau
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset
```

```bash
sudo mkinitcpio -P
```

## Verify

```bash
lspci -nnk -d 10de:25a0   # → vfio-pci
lspci -k | grep -E "vfio-pci|NVIDIA"
sudo dmesg | grep -i vfio
sudo modprobe vfio-pci   # if nothing bound yet (rare)
```

---

## Below: post-isolation host setup (historical block, modernized)

```bash
sudo pacman -S --needed qemu-desktop libvirt virt-install virt-manager virt-viewer dnsmasq edk2-ovmf swtpm nftables libosinfo
# replace iptables-nft shim → nftables (prompt on upgrade: yes)
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket   # minimal; pipeline enables full fleet
sudo nvim /etc/libvirt/virtqemud.conf   # fallback only: unix_sock_group="libvirt" / unix_sock_rw_perms="0770" (inert under socket)
sudo usermod -aG libvirt,kvm,input "$(id -un)"  # disk group never
sudo virsh -c qemu:///system net-start default && sudo virsh -c qemu:///system net-autostart default
```

> [!tip] Use `qemu:///system` always for passthrough. `qemu:///session` cannot do `hostdev` PCIe.
> Post-isolation next: [[Windows Configurations for Passthrough]] → [[Looking Glass]].
