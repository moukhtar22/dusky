---
title: "Modular Libvirt Daemons — Socket Activation (Aug 2026)"
tags:
  - kvm
  - libvirt
  - systemd
  - arch
aliases:
  - Enabling Modular Libvirt Daemons
---

# Modular Libvirt Daemons — Socket Activation (Aug 2026)

> [!info] Canonical
> `10_virt_modular_daemon.py` — phases: eradicate monolith → enforce socket activation → drop-ins for real perms → daemon conf (fallback) → `libvirt-guests` → arm + verification. Follow the script for idempotent runs; this note teaches *why*.

## Why modular

| Era | Model | Cost |
|---|---|---|
| <libvirt 6 | `libvirtd` monolith handles every driver | one giant process, always resident |
| ≥12.6 (Arch 2026) | per-driver `virt${drv}d` + `virtproxyd` + `virtlogd`/`virtlockd`, **systemd socket-activated** | **0 MB idle** per driver (inactive/dead until first `virsh`/`virt-manager` connect) |

Only sockets (`*.socket`) listen; services (`*.service`) wake on demand and auto-idle.

> [!abstract] Drivers
> - **QEMU/compute:** `virtqemud` — CPU/RAM, the one you care about for VFIO
> - **Network:** `virtnetworkd` — NAT/bridge, `dnsmasq` + `nftables`
> - **Node:** `virtnodedevd` — PCIe/USB hostdev
> - **Storage:** `virtstoraged` — pools/qcow2
> - **Infra:** `virtinterfaced`, `virtnwfilterd`, `virtsecretd`, `virtproxy` (compat), `virtlogd`, `virtlockd`, `virtlxcd`/`virtvboxd`/`virtchd` (alt hypervisors, idle)

## Step 1 — Eradicate the monolith

```bash
sudo systemctl stop libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl disable libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
# Never mask if hard-RequiredBy libvirt-guests.service — script checks Requires/Requisite/BindsTo
sudo systemctl mask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl daemon-reload
```
> Expected noise `The unit files have no installation config` / `masked, ignoring` = monolith dead.

## Step 2 — Enable sockets only (never `.service`)

> [!warning] Critical systemd rule
> Do **not** `enable --now virtqemud.service`. That makes it resident 24/7. Enable **sockets** only.

```bash
# persist
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do
  sudo systemctl enable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket
done
for drv in log lock; do sudo systemctl enable virt${drv}d.socket virt${drv}d-admin.socket; done

# now
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do
  sudo systemctl start virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket
done
for drv in log lock; do sudo systemctl start virt${drv}d.socket virt${drv}d-admin.socket; done
```

Disable always-on services + TCP/TLS listeners (script does this):

```bash
# should be disabled — socket-activated only
systemctl is-enabled virtqemud.service   # → disabled
systemctl is-enabled virtqemud-tcp.socket # → masked/disabled
```

## Step 3 — Real socket perms (the part most guides get wrong)

`libvirtd.conf` / `virtqemud.conf` contain verbatim:

> *“This setting is not required or honoured if using systemd socket activation.”* — `unix_sock_group` / `unix_sock_rw_perms` are **inert**.

Real perms come from **systemd drop-ins**:

```bash
sudo mkdir -p /etc/systemd/system/virtqemud.socket.d
sudo tee /etc/systemd/system/virtqemud.socket.d/10-arsonix.conf <<'EOF'
# Managed by Arsonix (Phase 2)
[Socket]
SocketUser=root
SocketGroup=libvirt
SocketMode=0660
EOF
sudo tee /etc/systemd/system/virtqemud-ro.socket.d/10-arsonix.conf <<'EOF'
[Socket]
SocketUser=root
SocketGroup=libvirt
SocketMode=0666
EOF
sudo systemctl daemon-reload
sudo systemctl restart virtqemud.socket virtqemud-ro.socket
ls -l /run/libvirt/virtqemud-sock*  # srwxrwx---  root libvirt  0660  ← fact on disk
```

The pipeline also writes inert `virtqemud.conf` values (`unix_sock_group="libvirt"` etc.) for manual `--listen` fallback, plus `firewall_backend="nftables"` in `network.conf` and `libvirt-guests` (`URIS='qemu:///system' ON_BOOT=start … SYNC_TIME=1`).

## Step 4 — Graceful shutdown

```bash
sudo systemctl enable --now libvirt-guests.service
# /etc/conf.d/libvirt-guests → URIS='qemu:///system' ON_BOOT=start ON_SHUTDOWN=shutdown SHUTDOWN_TIMEOUT=120
```

## Step 5 — Verify

```bash
systemctl list-sockets | grep virt        # LISTEN
systemctl status virtqemud.service        # inactive (dead) = 0 MB idle — correct
virsh -c qemu:///system version           # wakes to active, self-terminates after idle timeout
sudo virsh -c qemu:///system -q uri       # root OK
sudo -u $USER virsh -c qemu:///system -q uri  # operator OK only after re-login (libvirt group)
```

## Undo (to monolith)

```bash
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox log lock; do
  sudo systemctl stop virt${drv}d.service virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket 2>/dev/null || true
  sudo systemctl disable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket 2>/dev/null || true
done
sudo systemctl unmask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
```

See: [[KVM Services]] (legacy retained), [[All Libvert Daemons]], `10_virt_modular_daemon.py`.
