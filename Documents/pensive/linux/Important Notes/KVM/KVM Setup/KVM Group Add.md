---
title: "KVM Group Membership"
tags:
  - kvm
  - arch
  - permissions
  - libvirt
---

# KVM Group Membership

> [!abstract] Goal
> Grant the human operator access to libvirt sockets and `/dev/kvm` without running desktop sessions as `root`. Canonical: `05_virtio_iso.py:configure_groups` + `07_storage_setup.py:resolve_qemu_identity`.

## Required groups (Aug 2026)

```bash
# idempotent check — only acts if missing
sudo usermod -aG libvirt,kvm,input "$(id -un)"
```

| Group | Purpose | Note |
|---|---|---|
| `libvirt` | RW on `/run/libvirt/virtqemud-sock` (`SocketGroup=libvirt` via drop-in) — `qemu:///system` via polkit | **must** have; pipeline verifies existence |
| `kvm` | `/dev/kvm` ACL + `/dev/shm/looking-glass` `kvm` group (Phase 5) | uaccess also grants it, but explicit group stabilizes headless/ssh sessions |
| `input` | evdev passthrough for some tablet/GPU setups | pipeline includes; safe on desktop |

> [!warning] Legacy — `disk`
> Older guides (`Give the User System-Wide Permission_old`) added `disk` — **never do this**. It grants raw write to every block device including your root filesystem. Not needed for any KVM operation.

## Apply

```bash
groups         # before re-login — old creds
groups "$USER" # effective after next login
id -nG
```

> [!important] Credential refresh
> `usermod` does **not** affect the current session. You must terminate **every** session (logout / reboot; `newgrp` is insufficient for Wayland/systemd). Verify with `id -nG` after re-login. `05_virtio_iso.py` prints a reminder panel.

## How QEMU identity interacts

- Arch's `/etc/libvirt/qemu.conf` ships with every `user=`/`group=` key **commented out** → upstream compiled default is `root:root` (`07_storage_setup.py:resolve_qemu_identity` parses only *uncommented* lines).
- When QEMU runs as `root:root`, traversal ACLs on `/`→`/var/lib/libvirt/images` are provisioned **only for the human operator** — the privileged QEMU can already traverse. If you de-privilege QEMU (`user="qemu"`), `07_storage_setup.py` provisions ACLs for both principals.

See also: [[Give the User System-Wide Permission]], [[Set ACL on the Image Directory]].
