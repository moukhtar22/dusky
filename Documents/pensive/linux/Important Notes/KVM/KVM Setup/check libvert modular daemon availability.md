---
title: "Check Modular Daemon Availability"
tags:
  - kvm
  - libvirt
  - arch
  - pacman
---

# Check Modular Daemon Availability

> [!info] Goal
> Confirm the installed `libvirt` build actually ships `virt${drv}d` units and locate which package owns them — without guessing.

## 1. Sync the files database

```bash
sudo pacman -Fy           # refresh file database (not -Sy)
pacman -F virtqemud.service
pacman -F virtnetworkd.socket
# → extra/libvirt 12.6.x provides each
```

`pacman -F <path>` reverse-maps a file to its repo package (requires synced `sync/files`). Alternative if `F` DB stale:

```bash
pacman -Qo /usr/lib/systemd/system/virtqemud.service 2>/dev/null || pacman -Qlq libvirt | grep virtqemud
```

## 2. List what is *actually* installed on this host

```bash
systemctl list-unit-files 'virt*.service' 'virt*.socket' --no-legend --no-pager
# pipeline helper: discover_fleet() partitions into .socket rw/ro/admin + -tcp/-tls (neutralised)
virsh --version && systemctl is-active virtqemud.socket
```

## 3. What to expect (Aug 2026 Arch)

```
virtqemud.service / .socket + -ro/-admin
virtnetworkd.service / .socket …
virtstoraged, virtnodedevd, virtinterfaced, virtnwfilterd, virtsecretd, virtproxyd
virtlogd.socket / virtlockd.socket
```

If `virtqemud.socket` is `masked` or `not-found`, you are on a legacy `libvirtd`-only build — `pacman -Syu` (libvirt 12.6+ ships modular). The pipeline bails with *“No virt*d.socket units found”* in that case (`10_virt_modular_daemon.py:discover_fleet`).

See: [[libvert Modular daemon enable]], [[All Libvert Daemons]].
