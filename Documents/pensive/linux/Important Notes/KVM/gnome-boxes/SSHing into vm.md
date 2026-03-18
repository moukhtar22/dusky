# SSH Access to QEMU/KVM Guests on Arch Linux  
## GNOME Boxes, virt-manager, libvirt, Hyprland, and user-vs-system networking

## Summary

If a guest shows an address like `10.0.2.15`, it is almost certainly using **QEMU user-mode networking** (`slirp`). That network is **outbound-only by default**:

- the guest can reach the internet through the host
- the **host cannot directly initiate connections** to `10.0.2.15`
- inbound access requires either:
  1. **host port forwarding**, or
  2. moving the VM to a **system libvirt network** such as `default` NAT or a real bridge

For Arch Linux hosts, the most robust long-term solution is:

- use **libvirt system connection** (`qemu:///system`)
- attach the VM to **`default` NAT**
- SSH to the guest’s `192.168.122.x` address from the host

For an **existing GNOME Boxes VM** that already lives in the **user session** (`qemu:///session`), the least disruptive fix is:

- keep user-mode networking
- add **loopback-only port forwarding**  
  `127.0.0.1:2222 -> guest:22`
- SSH with `ssh -p 2222 ... 127.0.0.1`

> [!warning]
> Do **not** use `sudo pacman -Syyu` as a normal update command.  
> On Arch, routine full upgrades are:
> ```bash
> sudo pacman -Syu
> ```
> `-Syyu` is only for forcing a package database refresh when you specifically need it.

---

## Recommended host packages

### Minimal practical stack

```bash
sudo pacman -Syu --needed \
  qemu-desktop libvirt virt-manager virt-install edk2-ovmf dnsmasq iptables-nft
```

### Optional but useful desktop/console tools

```bash
sudo pacman -S --needed \
  gnome-boxes virt-viewer spice spice-gtk spice-protocol swtpm
```

### Notes

- `virt-manager` = the best GUI for serious VM/network management
- `gnome-boxes` = simple desktop VM frontend; fine for quick VMs, limited for networking
- `dnsmasq` = DHCP/DNS for libvirt virtual networks like `default`
- `iptables-nft` = common compatibility requirement for libvirt-managed NAT/firewalling on Arch
- `edk2-ovmf` = UEFI firmware for guests
- `virt-install` = CLI import/create tool; useful for moving a Boxes disk into the system connection

> [!note]
> `wl-clipboard`, `xclip`, and `gvfs-dnssd` are **not required** for SSH connectivity to VMs.

---

## Hyprland / UWSM prerequisite: polkit agent

If you use `virt-manager` under Hyprland, especially when launched through UWSM, make sure a **polkit agent** is running. Without one, privileged actions such as managing the system libvirt instance may fail or silently do nothing.

A common Arch/Hyprland choice is:

```bash
sudo pacman -S --needed hyprpolkitagent
```

> [!note]
> Start your polkit agent as part of your normal Hyprland/UWSM session startup.  
> The exact launch method depends on your setup; the important part is that a polkit agent is present before using `virt-manager`.

---

## Network mode cheat sheet

| Mode | Typical address | Host can SSH to guest directly? | Best use | Caveats |
|---|---:|---:|---|---|
| **QEMU user-mode networking** (`slirp`) | `10.0.2.15` | **No** | Quick disposable VMs | Need host port forwarding for inbound access |
| **libvirt NAT network** (`default`) | `192.168.122.x` | **Yes** | Best default for development/lab VMs | LAN devices cannot directly initiate inbound unless you add more routing/forwarding |
| **Linux bridge** | your LAN, e.g. `192.168.1.x` | **Yes** | Guest must appear as a real LAN host | Best on **Ethernet**; Wi-Fi bridging often fails |
| **macvtap/direct** | your LAN | **Usually no, not from host itself** | Special cases | Host↔guest communication is a common limitation |

> [!important]
> If your only goal is **SSH from the host into the guest**, a **libvirt NAT network** is usually the correct answer.  
> You do **not** need a full bridge for that.

> [!warning]
> Bridging over Wi-Fi is unreliable on many consumer cards/APs because 802.11 station mode usually does not support normal layer-2 bridging of multiple MAC addresses. Prefer **libvirt NAT** unless you explicitly need LAN visibility.

---

## Guest-side SSH prerequisites

## 1. Arch ISO / live installer environment

For the **official Arch installation ISO**, OpenSSH is available in the live environment. Do **not** assume it is running; verify it.

Inside the guest:

```bash
passwd
systemctl is-active --quiet sshd || systemctl start sshd
ip -4 -br address
ss -ltn | grep ':22'
```

### What this does

- `passwd` sets a root password for the current live session
- `systemctl ... sshd` ensures SSH is actually running
- `ip -4 -br address` shows the guest IP(s)
- `ss -ltn | grep ':22'` confirms something is listening on TCP 22

> [!warning]
> Using a root password over SSH is acceptable for a **disposable installer/live ISO** in a private lab.  
> It is **not** a good long-term configuration for an installed system.

---

## 2. Installed Arch guest

For a normal installed guest, enable OpenSSH properly:

```bash
sudo pacman -S --needed openssh
sudo systemctl enable --now sshd
```

Then verify:

```bash
ip -4 -br address
ss -ltn | grep ':22'
```

### Preferred long-term SSH practice

- create a normal user
- use SSH keys
- avoid root password login

---

## Preferred method: use the system libvirt connection and `default` NAT

This is the clean, maintainable setup.

## Why this is the right default

A **system** libvirt VM (`qemu:///system`) can attach to libvirt-managed networks such as:

- `default` NAT network (`virbr0`)
- real Linux bridges
- other managed virtual networks

With `default` NAT:

- guest gets an address like `192.168.122.x`
- host usually appears as `192.168.122.1`
- host can SSH directly to the guest

---

## Step 1: enable libvirt on the host

### Traditional daemon style

```bash
sudo systemctl enable --now libvirtd.service
```

### If your installation uses modular libvirt daemons instead

```bash
sudo systemctl enable --now \
  virtqemud.socket \
  virtnetworkd.socket \
  virtstoraged.socket \
  virtlogd.socket \
  virtproxyd.socket
```

> [!note]
> Arch may expose either the traditional `libvirtd` service or the modular libvirt sockets depending on package/version details.  
> If `libvirtd.service` exists, it remains the simplest broadly compatible path.

---

## Step 2: grant your user access

```bash
sudo usermod -aG libvirt "$USER"
```

Then **log out and log back in**.

> [!important]
> Group membership changes do not fully apply to your graphical session until you re-login.

---

## Step 3: start the default virtual network

Check whether the `default` network exists:

```bash
sudo virsh net-list --all
```

If it exists but is inactive:

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

If it does **not** exist, define it from the shipped template:

```bash
sudo virsh net-define /usr/share/libvirt/networks/default.xml
sudo virsh net-start default
sudo virsh net-autostart default
```

Verify:

```bash
sudo virsh net-info default
```

You should see a running NAT network, typically backed by `virbr0`.

---

## Step 4: create or import the VM under `qemu:///system`

### Best option: create a new VM in `virt-manager`

1. Open `virt-manager`
2. Connect to **QEMU/KVM** system connection
3. Create a VM normally
4. Set its NIC to:

```text
Virtual network 'default' : NAT
```

### Result

The guest will usually receive a `192.168.122.x` lease.

---

## Step 5: discover the guest IP

### Inside the guest

```bash
ip -4 -br address
```

### From the host with libvirt

```bash
virsh -c qemu:///system net-dhcp-leases default
```

Or for a specific domain:

```bash
virsh -c qemu:///system domifaddr <vm-name> --source lease
```

> [!note]
> `ip neighbor` is not the best discovery method. It only shows entries the host already learned, so it can miss quiet guests.

---

## Step 6: SSH to the guest

Example:

```bash
ssh root@192.168.122.145
```

Or, for a normal installed guest:

```bash
ssh youruser@192.168.122.145
```

---

## Moving an existing GNOME Boxes VM into the system connection

GNOME Boxes usually creates VMs in the **user session** (`qemu:///session`), not the **system** connection. That is why the VM is stuck on user-mode networking.

### Find the disk used by the Boxes VM

```bash
virsh -c qemu:///session list --all
virsh -c qemu:///session domblklist --details "<boxes-vm-name>"
```

### Copy the disk into system storage

```bash
sudo install -d -m 0755 /var/lib/libvirt/images
sudo cp --reflink=auto /path/to/boxes-disk.qcow2 /var/lib/libvirt/images/boxes-import.qcow2
sudo chown root:root /var/lib/libvirt/images/boxes-import.qcow2
```

### Import it as a new system VM

```bash
virt-install \
  --connect qemu:///system \
  --name boxes-import \
  --memory 4096 \
  --vcpus 4 \
  --disk path=/var/lib/libvirt/images/boxes-import.qcow2,format=qcow2,bus=virtio \
  --network network=default,model=virtio \
  --graphics spice \
  --video virtio \
  --boot uefi \
  --import
```

Adjust RAM, CPU, and storage path as needed.

> [!note]
> Keeping system-managed VM disks under `/var/lib/libvirt/images/` avoids the usual permissions confusion.

---

## Fallback method for an existing Boxes VM: loopback-only port forwarding

Use this when:

- the VM already exists in **GNOME Boxes**
- it is on **user-mode networking**
- you want **SSH now** without rebuilding the VM

This keeps the guest on user networking, but forwards a host port to guest SSH.

### Result

- host listens on `127.0.0.1:2222`
- traffic is forwarded to guest `:22`
- connect with:

```bash
ssh -p 2222 root@127.0.0.1
```

> [!important]
> This is the correct fix for a **session VM you want to keep**.  
> Trying to switch a Boxes VM directly to libvirt `default` NAT inside the user session is usually the wrong mental model.

---

## Step 1: open the VM through the user session in `virt-manager`

1. Open `virt-manager`
2. Go to **File → Add Connection**
3. Select:
   - **Hypervisor:** `QEMU/KVM`
   - **User session:** enabled
4. Open the VM from that connection

---

## Step 2: enable XML editing

In `virt-manager`:

1. **Edit → Preferences**
2. Enable **XML editing**

---

## Step 3: shut the VM down fully

Do **not** edit the NIC while the domain is running.

---

## Step 4: remove the existing user-mode NIC

In the VM details:

1. select the current network interface
2. remove it

This avoids ending up with two NICs.

> [!note]
> If you intentionally keep the old NIC and add a second one, the guest will have multiple interfaces. That can work, but it is unnecessary for this use case.

---

## Step 5: add the QEMU XML namespace if needed

In the **Overview → XML** editor, ensure the `<domain>` element has the `qemu` namespace.

### Example

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
```

If `xmlns:qemu=...` is already present, do not add it again.

---

## Step 6: add the port-forwarded user NIC

At the end of the domain XML, just before `</domain>`, add:

```xml
<qemu:commandline>
  <qemu:arg value='-netdev'/>
  <qemu:arg value='user,id=hostssh,hostfwd=tcp:127.0.0.1:2222-:22'/>
  <qemu:arg value='-device'/>
  <qemu:arg value='virtio-net-pci,netdev=hostssh'/>
</qemu:commandline>
```

### Why this XML is correct

- `hostfwd=tcp:127.0.0.1:2222-:22`
  - binds only to **loopback**
  - does **not** expose guest SSH on your LAN
- `virtio-net-pci`
  - best default NIC for modern Linux guests
- **no hardcoded PCI slot**
  - avoids brittle `q35` vs `i440fx` slot-address nonsense
  - let QEMU assign the device cleanly

> [!warning]
> Do **not** use:
> - `hostfwd=tcp::2222-:22` unless you explicitly want the port exposed on all host interfaces
> - manually forced PCI addresses like `addr=0x07` or `addr=0x14` unless you have a very specific reason

> [!note]
> If the guest OS lacks virtio NIC support, use `e1000e` instead of `virtio-net-pci`.  
> Arch Linux guests support virtio out of the box.

---

## Step 7: boot the VM and connect

Inside the guest, ensure SSH is ready:

```bash
passwd
systemctl is-active --quiet sshd || systemctl start sshd
ss -ltn | grep ':22'
```

Then from the host:

```bash
ssh -p 2222 root@127.0.0.1
```

If you chose a different host port, adjust accordingly.

---

## SSH host key warnings when reusing `127.0.0.1:2222`

If you recreate or reinstall the guest, its SSH host key will change. That is normal for disposable VMs and especially common with live ISOs.

Remove the stale key like this:

```bash
ssh-keygen -R '[127.0.0.1]:2222'
```

> [!note]
> Quoting is safe and recommended in **Bash, Zsh, Fish, and other shells**.  
> There is no reason to maintain separate quoted/unquoted variants.

---

## Firewall guidance

## Do not “fix” SSH by flushing firewall rules

These are bad habits:

```bash
sudo ufw disable
sudo iptables -F
sudo iptables -X
sudo iptables -P INPUT ACCEPT
```

They are overly broad, destroy policy state, and are especially wrong on systems using `nftables`.

> [!warning]
> Never use blanket firewall flushes as a first-line VM networking fix.

---

## Correct approach: allow SSH explicitly inside the guest

### If the guest uses `ufw`

```bash
sudo ufw allow 22/tcp
```

### If the guest uses `firewalld`

```bash
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
```

### If the guest uses raw `nftables`

Allow TCP/22 in the guest’s input chain according to your ruleset design.

> [!note]
> Arch does **not** enable a firewall by default.  
> If SSH still fails on a stock Arch guest, the problem is more likely:
> - `sshd` is not running
> - the VM is on user-mode networking without port forwarding
> - you are using the wrong address/port

---

## Troubleshooting checklist

## 1. Guest shows `10.0.2.15`

That means **user-mode networking**. The host cannot SSH directly to that address.

Use either:

- **system libvirt NAT** for a real guest IP reachable from host, or
- **host port forwarding** to `127.0.0.1:<port>`

---

## 2. Port-forwarded SSH still fails

Check whether the host port is listening:

```bash
ss -ltn | grep ':2222'
```

If nothing is listening:

- the VM may not have started
- XML may be invalid
- the chosen port may already be in use

Try another port, e.g. `2223`.

---

## 3. SSH daemon is not actually running in the guest

Inside the guest:

```bash
systemctl status sshd --no-pager
ss -ltn | grep ':22'
```

If needed:

```bash
systemctl start sshd
```

---

## 4. You are connecting to `localhost` and it resolves to IPv6 first

If your forward is bound only on IPv4, use the literal IPv4 loopback:

```bash
ssh -p 2222 root@127.0.0.1
```

This is more reliable than `localhost` for QEMU host forwards.

---

## 5. `virt-manager` cannot authenticate or network actions fail silently

Likely causes:

- no running polkit agent in Hyprland
- your user is not in the `libvirt` group
- you added the group but did not re-login

---

## 6. The `default` network is missing or inactive

Check:

```bash
sudo virsh net-list --all
sudo virsh net-info default
```

Start it if needed:

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

Define it from the template if necessary:

```bash
sudo virsh net-define /usr/share/libvirt/networks/default.xml
```

---

## 7. You selected “bridge” on Wi‑Fi and nothing works

That is a common failure mode.

Use:

- **libvirt `default` NAT** for host↔guest SSH
- a real **bridge** only when you truly need LAN visibility and your host networking supports it

---

## Recommended decision tree

## I only need SSH from host into the VM

Use **system libvirt + `default` NAT**.

---

## I already created the VM in GNOME Boxes and need SSH immediately

Use **loopback port forwarding** on the **user-session** VM:

```text
127.0.0.1:2222 -> guest:22
```

---

## I need the guest to appear as a real device on my LAN

Use a **true Linux bridge** on **Ethernet**, not Wi-Fi, unless you know your wireless stack supports it.

---

## Anti-patterns to avoid

> [!warning]
> Avoid all of the following:
> - `pacman -Syyu` for normal upgrades
> - assuming `10.0.2.15` is host-reachable
> - expecting Boxes user-session VMs to use the system `default` NAT network automatically
> - flushing `iptables`/disabling `ufw` as generic troubleshooting
> - hardcoding Q35/i440fx PCI slot addresses for injected QEMU NICs
> - binding host forwards to all interfaces unless you deliberately want LAN exposure

---

## Canonical commands

### Guest on system libvirt NAT

```bash
ssh user@192.168.122.145
```

### Existing Boxes VM with loopback port forwarding

```bash
ssh -p 2222 root@127.0.0.1
```

### Remove stale host key for the forwarded endpoint

```bash
ssh-keygen -R '[127.0.0.1]:2222'
```

### See DHCP leases on the libvirt NAT network

```bash
virsh -c qemu:///system net-dhcp-leases default
```

### Show addresses reported for a domain

```bash
virsh -c qemu:///system domifaddr <vm-name> --source lease
```

---

## Final rule

If the guest is on **`10.0.2.15`**, stop trying to SSH to that address from the host.  
Either:

1. **move the VM to `qemu:///system` + `default` NAT**, or  
2. **forward a host loopback port to guest SSH**.
