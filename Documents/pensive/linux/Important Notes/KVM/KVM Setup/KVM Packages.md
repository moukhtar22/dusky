---
title: "KVM Packages (Aug 2026)"
tags:
  - kvm
  - arch
  - packages
  - libvirt
  - qemu
aliases:
  - Hypervisor Packages
---

# KVM Packages — Arch Rolling (Aug 2026)

> [!info] Canonical source
> `05_virtio_iso.py:REPO_PACKAGES` is the source of truth. All names below verified against `pacman -Si` on Aug 2026 Arch.

## Repo set (pacman)

```bash
sudo pacman -S --needed \
  qemu-desktop libvirt virt-install virt-manager virt-viewer \
  dnsmasq edk2-ovmf swtpm nftables libosinfo \
  pciutils dmidecode python-rich
```

| Package | Role (Aug 2026) |
|---|---|
| `qemu-desktop` | Superset for passthrough incl. `spice/gtk/virtio-gpu/ovmf`. Replaces `qemu-full` (all foreign archs, ~2× deps). See script comment `qemu-full not needed`. |
| `libvirt` | 12.6+ modular daemons `virtqemud`/`virtnetworkd`/`virtstoraged` etc. |
| `virt-install` | `virt-install`/`virt-xml`/`virt-clone` (`--osinfo` not `--os-variant`) |
| `virt-manager` / `virt-viewer` | GTK frontend / SPICE viewer |
| `dnsmasq` | libvirt NAT/DHCP backend |
| `edk2-ovmf` | `/usr/share/qemu/firmware/*.json` descriptors + `OVMF_CODE.secboot.4m.fd` / `OVMF_VARS.4m.fd` (libvirt picks blob via JSON, no hard-coded `fd` path) |
| `swtpm` | TPM 2.0 emulation (mandatory for `win11` osinfo) |
| `nftables` | `firewall_backend=nftables` in `/etc/libvirt/network.conf` (no iptables shim since libvirt 10.3) |
| `libosinfo` | `osinfo-db` for `--osinfo` |
| `pciutils` / `dmidecode` | `lspci`/`dmidecode` topology probes (Phase 3) |
| `python-rich` | pipeline TUI |

> [!tip] Why `qemu-desktop` not `qemu-full`
> `qemu-full` builds every foreign-arch usermode (`qemu-arm`, `qemu-riscv`…) — irrelevant for x86 KVM passthrough and pulls extra deps. `qemu-desktop` is the Arch-recommended superset that already includes `virtio-gpu`, `spice`, `gtk`, `vhost-user` needed for passthrough.

Optional host tools:

```bash
sudo pacman -S --needed iproute2 openbsd-netcat  # debugging
# tuned is optional — conflicts with TLP (see Optimize the Host with TuneD)
# guestfs-tools is optional — only if you offline-edit qcow2
```

### AUR

```bash
paru -S --needed virtio-win   # → /usr/share/virtio-win/virtio-win.iso or pool symlink
# looking-glass / libvirt custom builds via AUR use same pattern, see 05_virtio_iso.py:install_aur (paru as operator, not root)
```

Check ISO location idempotently (never hardcode):

```bash
pacman -Qlq virtio-win | grep '\.iso$'
ls -l /var/lib/libvirt/images/virtio-win.iso   # symlink → AUR file, or standalone download
```

> [!warning] Legacy — packages to *remove* from old guides
> - `qemu-full` → `qemu-desktop` (above)
> - `vde2`, `ebtables-git`, `bridge-utils` — legacy bridge/ebtables; replaced by `nftables` + `iproute2`/`nmcli` (`bridge-utils` dropped in Arch 2025)
> - `iptables-nft` / `iptables` — libvirt now speaks `nftables` natively; no shim needed
> - `qemu-img` — now part of `qemu-desktop`/`qemu-base` (`qemu-img create -o cluster_size=64k,lazy_refcounts=on` still valid)
> - `libvirtd` monolith — package still ships it but `10_virt_modular_daemon.py` masks it; do not `enable libvirtd.service`
> - `--os-variant` — deprecated; use `--osinfo` (see virt-install notes)

## Verify

```bash
pacman -Q qemu-desktop libvirt virt-manager edk2-ovmf swtpm nftables
virt-host-validate  # QEMU/KVM should PASS; IOMMU WARN expected before Phase 3
virsh --version     # ≥12.6
qemu-system-x86_64 --version  # ≥11.1
```

## Next

- [[KVM Group Add]] + [[Give the User System-Wide Permission]] → groups + `LIBVIRT_DEFAULT_URI`
- `07_storage_setup.py` — ACLs, `qemu.conf` `root:root` note

See also: `05_virtio_iso.py`, [[KVM Services]] (legacy monolith doc retained as warning).
