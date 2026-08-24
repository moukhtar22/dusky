---
title: "KVM Default Network (NAT) — Provision & Autostart"
tags:
  - kvm
  - libvirt
  - networking
  - nftables
  - dnsmasq
  - arch
---

# KVM Default Network — Provision & Autostart

> [!info] NAT mental model
> Like a home router: VMs **outbound** full internet via host NAT; **inbound** from LAN not visible. For LAN-visible hosting use [[Network Bridging for LAN access]] (Option 3 `br0`). Canonical: `20_networking_nmcli.py`.

## 1. Diagnose

```bash
virsh -c qemu:///system net-list --all
virsh -c qemu:///system net-info default 2>/dev/null || echo "no default"
systemctl is-active virtnetworkd.socket   # must be active — Phase 2 gates this
```

- Empty / `inactive` / `Persistent no` → provision §2.
- `nft` present + `network.conf` `firewall_backend="nftables"` → libvirt owns `table inet libvirt_network`; no iptables shim.

## 2. Pristine provision (idempotent, durable /tmp via mkstemp)

`20_*.py:define_network` never writes world-writable `/tmp/libvirt-default.xml` — uses `mkstemp` + `os.fsync` + `os.replace`. This note mirrors it:

```bash
# 1. Purge broken state (ignore errors)
sudo virsh -c qemu:///system net-destroy default 2>/dev/null || true
sudo virsh -c qemu:///system net-undefine default 2>/dev/null || true

# 2. Inject (uuid/mac auto-generated if omitted)
cat <<'EOF' | sudo tee /tmp/libvirt-default.xml >/dev/null
<network>
  <name>default</name>
  <forward mode='nat'>
    <nat><port start='1024' end='65535'/></nat>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp><range start='192.168.122.2' end='192.168.122.254'/></dhcp>
  </ip>
</network>
EOF
sudo virsh -c qemu:///system net-define /tmp/libvirt-default.xml
rm /tmp/libvirt-default.xml

# 3. Autostart + start
sudo virsh -c qemu:///system net-autostart default
sudo virsh -c qemu:///system net-start default
```

> [!note] Preserve vs purge
> `20_*.py:provision_nat(purge=False)` *preserves* an existing `default` with custom DHCP — only `purge=True` destroys it. The block above is the `purge` variant (clean-room).

## 3. Firewall (Aug 2026 = `nftables`)

Libvirt manages its own `nft` table; most hosts need **nothing**. Only inject if you run a filtering frontend that defaults `FORWARD` to `DROP`:

```bash
# UFW active?
sudo ufw status | head -1   # Status: active → needs rules
sudo ufw route allow in on virbr0
sudo ufw route allow out on virbr0
sudo ufw reload
# firewalld: firewall-cmd --reload (zone auto-detected)
```

Check:

```bash
sudo nft list table inet libvirt_network 2>/dev/null | head -n 40
sudo virsh -c qemu:///system net-dumpxml default | grep -A4 '<ip'
# Host IP 192.168.122.1   DHCP .2-.254
```

## 4. Verify

```bash
sudo virsh -c qemu:///system net-list --all
# Name      State   Autostart  Persistent
# default   active  yes        yes
ip -4 addr show virbr0
virsh -c qemu:///system net-dhcp-leases default
```

> [!tip] NAT subnet collision
> `20_*.py:host_owns_nat_subnet` warns if `192.168.122.0/24` already routed via another device — guest DHCP will black-hole. Move NAT CIDR or disable conflicting docker/lxd route.

See: [[Network Bridging for LAN access]], `20_networking_nmcli.py`.
