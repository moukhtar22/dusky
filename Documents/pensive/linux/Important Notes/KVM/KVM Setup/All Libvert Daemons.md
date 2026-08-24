---
title: "All Libvirt Daemons — Architecture Reference"
tags:
  - kvm
  - libvirt
  - systemd
  - reference
aliases:
  - Monolithic vs Modular
---

# All Libvirt Daemons — Architecture Reference

> [!info] Upstream status (Aug 2026)
> Arch ships **both** monolithic (`libvirtd`) and modular (`virt${drv}d`) builds, but the RPC client now prefers modular and the monolith is deprecated. Within 1–2 years it will be deleted. New hosts should run modular only (`10_virt_modular_daemon.py`).

## Operating modes

| Mode | Identity | Connection URI | Use |
|---|---|---|---|
| **System** | `root` | `qemu:///system` | Full host access (passthrough, bridges). Requires `libvirt` group + socket perms. |
| **Session** | `$USER` | `qemu:///session` | Unprivileged, no passthrough, `$XDG_RUNTIME_DIR/libvirt`. |

For passthrough you want **system**.

## Monolithic (deprecated)

- Daemon: `libvirtd` • Config: `/etc/libvirt/libvirtd.conf`
- Sockets (system): `/run/libvirt/libvirt-sock` (rw), `-sock-ro` (ro), `-admin-sock` + TCP `16509`/TLS `16514`
- Session: `$XDG_RUNTIME_DIR/libvirt/libvirt-sock`
- Systemd: `libvirtd.service`, `libvirtd.socket`, `-ro/-admin/-tcp/-tls.socket`

> [!warning] Socket activation overrides conf
> When systemd socket activation is used, `libvirtd.conf` settings `listen_tcp/tls`, `tcp/tls_port`, `listen_addr`, `unix_sock_group/ro/rw/admin_perms`, `unix_sock_dir` are **not honoured** — they are `ListenStream`/`SocketGroup`/`SocketMode` in the unit files instead. Modular has the same rule (see below).

## Modular (current)

| Daemon | Driver | Sockets |
|---|---|---|
| `virtqemud` | QEMU/KVM (system + session) | `virtqemud.sock` / `-sock-ro` / `-admin-sock` |
| `virtxend` | Xen | system |
| `virtlxcd` | LXC | system |
| `virtbhyved` | bhyve | FreeBSD |
| `virtvboxd` | VirtualBox | system |
| `virtinterfaced` | host NIC | system |
| `virtnetworkd` | virtual nets | system |
| `virtnodedevd` | host devs | system |
| `virtnwfilterd` | firewall | system |
| `virtsecretd` | secrets | system+session |
| `virtstoraged` | storage | system+session |
| `virtproxyd` | remote/compat proxy | system |

Config per driver: `/etc/libvirt/virt${drv}d.conf`. Systemd: `virt${drv}d.service` + `virt${drv}d.socket`/`-ro`/`-admin` (TCP/TLS via `virtproxyd-tcp/tls.socket`).

**Modular conf also inert under sockets:**

> `unix_sock_group`, `unix_sock_ro/rw/admin_perms`, `unix_sock_dir` → `SocketGroup`/`SocketMode`/`ListenStream` in `virt${drv}d*.socket` drop-ins.

## Migrating (systemd)

```bash
# stop monolith
systemctl stop libvirtd.service libvirtd{,-ro,-admin,-tcp,-tls}.socket
systemctl disable libvirtd.service libvirtd{,-ro,-admin,-tcp,-tls}.socket  # or mask

# enable modular fleet (enable sockets, not services)
for drv in qemu interface network nodedev nwfilter secret storage; do
  systemctl unmask virt${drv}d.service virt${drv}d{,-ro,-admin}.socket
  systemctl enable virt${drv}d{,-ro,-admin}.socket
done
# start sockets — services auto-wake
for drv in qemu network nodedev nwfilter secret storage; do
  systemctl start virt${drv}d{,-ro,-admin}.socket
done
```

## Which mode is active?

```bash
systemctl is-active virtqemud.socket; systemctl is-enabled virtqemud.socket
systemctl is-active libvirtd.socket;   systemctl is-enabled libvirtd.socket
systemctl is-active virtqemud.service # inactive (dead) = correct (socket-activated, 0 RSS)
```

New distributions already use modular even though upgrades preserve prior mode.

See: [[libvert Modular daemon enable]] (hands-on), [[KVM Services]] (legacy).
