---
title: "Virtual NIC — Virtio Model (Unified)"
tags:
  - kvm
  - networking
  - virtio
  - arch
  - nftables
aliases:
  - Network Unified
---

# Virtual NIC — Virtio Model (Unified)

> [!abstract] Rule
> Always **`virtio`** — paravirtual, no emulation overhead, near-native throughput. `e1000e` is legacy. **Same for Windows and Linux** — only Windows needs an extra driver from `virtio-win.iso`.

## In virt-manager (shared)

1. VM Details (lightbulb) → **NIC** (Network Interface)
2. **Device model:** `virtio`
3. **Network source:** your topology
   - `Virtual network 'default' : NAT` (standard, `192.168.122.x`, host `192.168.122.1` → `ssh` host↔guest) — see [[Activating Network and Setting it to Autostart]]
   - `Bridge device br0` (LAN-visible `192.168.1.x`, needs wired uplink + [[Network Bridging for LAN access]] Option 3) — Ethernet only; Wi-Fi bridge fails (AP drops bridged MAC)
   - `Macvtap` Bridge on Wi-Fi (LAN IP but **host↔VM broken** — hairpin forbidden) — see bridging note Option 2
4. **Apply**

CLI equivalent (`30_kvm_vm_deploy.py:build_command`, both OSes):

```bash
--network network=default,model=virtio   # NAT (preferred for labs)
--network bridge=br0,model=virtio        # bridge (LAN-visible, Ethernet only)
--network type=direct,source=wlp2s0,source.mode=bridge,model=virtio  # macvtap Wi-Fi
```

## OS deltas

> [!info] Windows route — `NetKVM` driver
> Windows needs `NetKVM` from `virtio-win.iso` (same ISO as disk driver). Two moments:
> - **During OOBE (optional):** if you need LAN *during* Setup, **Load driver → Browse → `E:\NetKVM\w11\amd64` → OK**. Otherwise skip.
> - **After desktop:** `virtio-win-guest-tools.exe` (or Windows Update) installs `NetKVM`; NIC then shows **Red Hat VirtIO Ethernet Adapter** (not “Unknown device”). Verify `Device Manager → Network adapters`.
> If NIC stays “Unknown device”, re-mount `virtio-win.iso` per [[Mount the VirtIO-Win ISO Image]] and load `NetKVM`.

> [!info] Linux route — no driver, kernel virtio-net
> Arch/Fedora/Ubuntu have `virtio_net`/`virtio_pci` in-kernel. No ISO, no load. Guest sees `enp1s0`/`ens3` immediately. Check `ip -4 addr` → `192.168.122.x` (NAT) or `192.168.1.x` (br0). For Boxes `qemu:///session` VMs the default is **user-mode `10.0.2.15` (slirp, outbound-only)** — see [[SSHing into vm]] for `hostfwd` vs import to `qemu:///system`.

## Topology chooser

| Goal | Choose | Addr | Host→guest SSH? | Note |
|---|---|---|---|---|
| Disposable lab, internet only | `Virtual network default : NAT` | `192.168.122.x` | **Yes** (`ssh 192.168.122.x`) | pipeline `20_networking_nmcli.py:provision_nat` |
| LAN-visible server | `Bridge device br0` | `192.168.1.x` (router DHCP) | **Yes** | **Wired only**; rollback on loss (`rollback_bridge`) |
| Wi-Fi host, need LAN IP quickly | `Macvtap Bridge` on `wlp2s0` | LAN IP | **No** (host↔VM broken) | VM↔internet OK; pipeline warns |
| `qemu:///session` (Boxes) | `user` (slirp) | `10.0.2.15` | **No** without `hostfwd` | see [[SSHing into vm]] |

> [!tip] NAT subnet collision
> `20_*.py:host_owns_nat_subnet` warns if `192.168.122.0/24` already routed via another dev (Docker/LXD) — DHCP black-holes. Move NAT CIDR or disable conflicting route.

## Firewall (Aug 2026 = `nftables`)

Libvirt manages its own `nft` table (`inet libvirt_network`); no `iptables` shim. Only inject if you run filtering frontend that defaults `FORWARD` to `DROP`:

```bash
# UFW active?
sudo ufw status | head -1   # Status: active → needs rules
sudo ufw route allow in on virbr0
sudo ufw route allow out on virbr0
# also for br0:
sudo ufw route allow in on br0
sudo ufw route allow out on br0
sudo ufw reload
# firewalld: firewall-cmd --reload (zone auto-detected)
sudo nft list table inet libvirt_network 2>/dev/null | head -n 40
```

## Verify

```bash
virsh -c qemu:///system dumpxml win11 | grep -A2 "<interface"
virsh -c qemu:///system dumpxml archlinux | grep -A2 "<interface"
# expect: <model type='virtio'/> + <source network='default'/> or bridge='br0'
virsh -c qemu:///system net-list --all   # default active yes
ip -4 addr show virbr0 2>/dev/null || ip -4 addr show br0
virsh -c qemu:///system domifaddr win11 --source lease   # or --source agent if guest-agent present
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `e1000e` slow / high host CPU | switch to `virtio` — Windows needs `NetKVM` |
| `default` missing / `net-list --all` empty | [[Activating Network and Setting it to Autostart]] → `net-define` → `net-autostart` → `net-start` |
| LAN not inbound to VM | NAT is outbound-only by design — use `br0` bridge (Ethernet) |
| Guest has `10.0.2.15` | user-mode slirp (Boxes/`qemu:///session`) → import to `qemu:///system` or add `hostfwd` per [[SSHing into vm]] |
| Wi-Fi `br0` fails | expected — 802.11 STA 3-addr frame has no room for guest MAC → use NAT or `macvtap` |

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[Activating Network and Setting it to Autostart]], [[Network Bridging for LAN access]], [[Mount the VirtIO-Win ISO Image]], [[Install a Windows Virtual Machine on KVM]] (NetKVM), `20_networking_nmcli.py`.
