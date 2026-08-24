---
title: "Virtual NIC — Virtio Model (Windows Delta)"
tags:
  - kvm
  - networking
  - virtio
  - windows
aliases:
  - Windows NIC Stub
---

# Virtual NIC — Virtio Model (Windows)

> [!tip] Merged — canonical source
> **Shared `virtio` NIC + NAT/br0 chooser lives in [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]].** Follow that note for **Device model `virtio`**, **Network source `Virtual network default : NAT`** vs **`Bridge device br0`** vs **`Macvtap`**, CLI `--network network=default,model=virtio`, firewall `nftables`/`UFW`, and topology table. This stub keeps **only Windows NetKVM driver** delta.

## Windows delta

| Canonical | Windows note |
|---|---|
| `virtio` model + `network=default` (NAT `192.168.122.x`) or `bridge=br0` (LAN) | **identical** — set in virt-manager → NIC → virtio → Apply |
| Firewall `nftables` `inet libvirt_network`, UFW route allow | **identical** |

**Windows-only — NetKVM driver:**

- **During OOBE (optional, only if LAN needed mid-Setup):** **Load driver → Browse → `E:\NetKVM\w11\amd64` → OK** (same `virtio-win.iso` 2nd CDROM as storage)
- **After desktop (recommended):** `virtio-win-guest-tools.exe` installs `NetKVM`; `Device Manager → Network adapters → Red Hat VirtIO Ethernet Adapter` appears. Without it `virtio` NIC stays “Unknown device.”

> [!info] Linux route — no driver
> `virtio_net` in-kernel — guest `enp1s0`/`ens3` appears immediately; no ISO.

CLI reminder (both OSes): `virsh -c qemu:///system domifaddr win11 --source lease` vs `--source agent` (once QEMU Guest Agent present).

See: canonical [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]], [[Activating Network and Setting it to Autostart]], [[Network Bridging for LAN access]], [[Mount the VirtIO-Win ISO Image]], [[Install a Windows Virtual Machine on KVM]] (§2 NetKVM).
