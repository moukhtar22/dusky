---
title: "Source Tutorial — sysguides KVM Overview"
tags:
  - kvm
  - reference
---

# Source Tutorial — sysguides KVM Overview

> [!info] Upstream primer (pre-modular era)
> This vault imported steps from **sysguides — “Install KVM on Linux”** as the narrative backbone. Keep it as lineage reference, but treat spec differences via Aug 2026 notes as authoritative where they diverge.

- **URL:** <https://sysguides.com/install-kvm-on-linux#0-01-overview-of-key-kvm-components>
- **Imported:** component map (QEMU / libvirt / virt-manager / virt-viewer / dnsmasq / edk2-ovmf / swtpm / virtio-win).
- **Divergences to trust in this vault:**
  - `libvirtd` monolith → modular `virtqemud` + socket activation (`10_virt_modular_daemon.py`)
  - `qemu-full` → `qemu-desktop` (`05_virtio_iso.py`)
  - `iptables-nft` / `bridge-utils` / `ebtables` → `nftables` + `iproute2`/`nmcli` (`20_networking_nmcli.py`)
  - GRUB → `systemd-boot` BLS Type1/Type2 (`15_gpu_probing_kernal_param_mkinit.py`)
  - `<qemu:commandline> ivshmem` → native `<shmem>` (`25_looking_glass.py`)

If a param in this vault disagrees with sysguides, **this vault wins** on Arch 2026 hosts.
