---
title: "Networking — NAT vs Bridging vs Macvtap (Aug 2026)"
tags:
  - kvm
  - networking
  - nftables
  - bridge
  - arch
---

# KVM Networking — NAT vs Bridging vs Macvtap (Aug 2026)

> [!abstract] Choose once, canonical in `20_networking_nmcli.py` (adaptive `br0` vs `virbr0`). This note is manual `virt-manager`/`nmcli` view; it matches the script's probe/rollback logic.

## Identify uplink (kernel truth vs NM alias)

```bash
nmcli device status                      # human-friendly
ip -j route show default | python3 -m json.tool   # kernel truth: {"dev":"enp0s31f6"|"wlp…","metric":…}
# Ethernet: enp3s0/eno1/enx…   Wi-Fi: wlp2s0/wlan0
```

Pipeline: `default_route_iface()` picks lowest `metric` default route; `is_wireless()` checks `/sys/class/net/<iface>/wireless|phy80211`.

## Mandatory: virtio model (every mode)

1. `virt-manager` → VM → lightbulb → **NIC**
2. **Device model:** `virtio` → **Apply**

CLI: `--network network=default,model=virtio` (or `bridge=br0`). Windows needs `NetKVM` from `virtio-win` (Drivers → Network).

## Option 1 — NAT (`Virtual network 'default'`) ✅ preferred for labor

**Use:** VM needs internet + host SSH → `ssh 192.168.122.x`. Secure, stable.

1. NIC → **Network source:** `Virtual network 'default' : NAT` → Apply
2. If missing: [[Activating Network and Setting it to Autostart]] (`net-define` → `net-autostart` → `net-start`, `dnsmasq`+`nftables`)

> NAT collision: pipeline warns if host already owns `192.168.122.0/24` via another dev (would black-hole DHCP).

## Option 2 — Wi-Fi host (macvtap `Bridge` mode)

**Limitation:** 802.11 STA 3-addr frame has no room for guest MAC → AP drops bridged frames. Layer-2 bridge on Wi-Fi **breaks** without 4-addr/WDS/AP support. So:

> [!danger] macvtap trade
> `macvtap` gives VM real LAN IP (e.g. from router DHCP) via `macvtap` bridge mode, but **host ↔ VM cannot talk** (hairpin forbidden). VM ↔ internet/LAN OK. Pipeline surfaces this panel and asks `Force bridging (requires WDS)?` (defaults to NAT).

1. NIC → **Network source:** `Macvtap device`
2. **Device name:** `wlp2s0` (your Wi-Fi)
3. **Source mode:** `Bridge` → Apply

## Option 3 — Ethernet host (`br0` system bridge) — LAN-visible

**Use:** VM appears as separate LAN host (`192.168.1.x` from physical router), host↔guest OK. Requires wired uplink (wireless `br0` fails per above). Script verifies reachability (`wait_for_default_route`) and **rolls back** (`rollback_bridge`) on loss.

### Step 1 — Create bridge (nmcli long-form, no aliases)

Replace `enp3s0` with your dev from above.

```bash
sudo nmcli connection add type bridge ifname br0 con-name br0 bridge.stp no connection.autoconnect yes ipv6.method auto
# carry static IP if uplink is static (script migrates ip4.method=manual addresses/gateway/dns to br0), otherwise auto:
sudo nmcli connection modify br0 ipv4.method auto
sudo nmcli connection add type ethernet ifname enp3s0 con-name br0-port-enp3s0 controller br0 port-type bridge connection.autoconnect yes
# race-safe: disable old uplink autoconnect, then activate with timeout + default-route probe
sudo nmcli connection modify "<uplink-profile>" connection.autoconnect no
sudo nmcli connection down "<uplink-profile>" 2>/dev/null || true
sudo nmcli --wait 20 connection up br0   # internet dips 2–5 s
ip -j route show default | grep -q '"dev":"br0"' && echo "br0 carries default route ✅" || echo "rollback needed"
```

> Rollback (script runs on timeout): `nmcli connection down br0` → `delete br0` + `br0-port-*` → `modify <profile> autoconnect yes` → `nmcli connection up <profile>` / `device connect` → re-check default route.

### Step 2 — UFW bridge forward

```bash
sudo ufw route allow in on br0
sudo ufw route allow out on br0
sudo ufw reload
# nftables-only hosts: libvirt manages `inet libvirt_network` table — nothing extra
```

### Step 3 — Attach VM

1. `virt-manager` → NIC → **Network source:** `Bridge device` → **Device name:** `br0` → Apply

### Optional — Advertise `br0` in libvirt chooser

```bash
cat <<'EOF' > /tmp/host-bridge.xml
<network><name>host-bridge</name><forward mode='bridge'/><bridge name='br0'/></network>
EOF
sudo virsh -c qemu:///system net-define /tmp/host-bridge.xml
sudo virsh -c qemu:///system net-start host-bridge && sudo virsh -c qemu:///system net-autostart host-bridge
rm /tmp/host-bridge.xml
sudo virsh -c qemu:///system net-list --all   # host-bridge now in dropdown
```

## Disaster recovery (Option 3)

```bash
sudo nmcli connection down br0
sudo nmcli connection delete br0
sudo nmcli connection delete br0-port-enp3s0
sudo systemctl restart NetworkManager
# or script's rollback: restores original profile + default route
```

See: [[Activating Network and Setting it to Autostart]], `20_networking_nmcli.py` (`provision_bridge`/`provision_nat`/`host_owns_nat_subnet`).
