# 🌐 KVM Default Network Provisioning (Arch Linux)

By default, all virtual machines (VMs) on your host are connected to a virtual network named **'default'**. This network uses **NAT** (Network Address Translation) to allow your VMs to communicate with the outside world.

> [!INFO] What is NAT?
> 
> Think of NAT like your home router.
> 
> - **Outbound:** Your VMs can browse the internet, download updates, and ping external servers seamlessly.
>     
> - **Inbound:** Devices outside your computer (like your phone or another laptop) _cannot_ see or connect to the VMs directly.
>     
> 
> This is perfect for desktop usage (browsing, testing) but not for hosting public servers.

## 1. Initial Diagnosis

Always begin by checking the current state of your virtualization networks. Open your terminal and run:

```
sudo virsh net-list --all
```

**What you are looking for:**

If the list is completely empty, or if `default` is listed but its State is `inactive` and Persistent is `no`, your network needs to be provisioned. Proceed to Step 2.

## 2. The Bulletproof Provisioning Method

Arch Linux is a rolling release, and depending on your installation, upstream default templates can sometimes be missing. To make this completely reliable across fresh reinstalls, we will forcefully clear any broken state and inject a pristine XML configuration directly.

_(Note: We intentionally omit the `<uuid>` and `<mac>` tags below so libvirt securely auto-generates fresh ones tailored to your exact hardware)._

Copy and paste this entire block into your terminal:

```
# 1. Clean up any corrupted or transient states (errors here are safe to ignore)
sudo virsh net-destroy default >/dev/null 2>&1
sudo virsh net-undefine default >/dev/null 2>&1

# 2. Inject the modern XML configuration into a temporary file
cat <<EOF > /tmp/libvirt-default.xml
<network>
  <name>default</name>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
EOF

# 3. Permanently define the network in libvirt
sudo virsh net-define /tmp/libvirt-default.xml

# 4. Clean up the temporary file
rm /tmp/libvirt-default.xml
```

> [!SUCCESS] Network Defined
> 
> Your network is now permanently etched into your system's configuration.

## 3. Enable and Start

With the network persistently defined, instruct your system to start it right now, and ensure it always starts automatically on future reboots.

```
# Set the network to automatically start when your machine boots
sudo virsh net-autostart default

# Start the network immediately
sudo virsh net-start default
```

_(Note: Modern libvirt relies on `dnsmasq` and `iptables-nft` to handle the DHCP and NAT translation backend. Ensure those packages are installed on your Arch system if `net-start` ever fails on a fresh install)._

## 4. Firewall Routing Configuration

Modern libvirt heavily utilizes `nftables`. Depending on which frontend firewall you use, it may blindly drop the traffic moving across your virtual bridge (`virbr0`), preventing your VMs from accessing the internet.

Follow the instructions for your active firewall below:

### 🛡️ If using UFW (Uncomplicated Firewall)

UFW's default `FORWARD` policy drops everything. We must explicitly trust the `virbr0` interface:

```
# Allow UFW to forward traffic entering the virtual bridge
sudo ufw route allow in on virbr0

# Allow UFW to forward traffic exiting the virtual bridge
sudo ufw route allow out on virbr0

# Reload UFW to apply the new routing rules
sudo ufw reload
```

### 🛡️ If using firewalld (Alternative)

If you ever migrate to `firewalld`, it is deeply integrated with libvirt. You usually just need to force firewalld to pick up the libvirt zone file:

```
# Reload firewalld so it detects the libvirt zone
sudo firewall-cmd --reload

# (Optional) Ensure virbr0 is bound to the libvirt zone
sudo firewall-cmd --get-active-zones
```

## 5. Final Verification & IP Assignments

Let's do a final check to guarantee everything is perfect. Run the initial diagnostic command again:

```
sudo virsh net-list --all
```

**Expected Perfect Output:**

|   |   |   |   |
|---|---|---|---|
|**Name**|**State**|**Autostart**|**Persistent**|
|default|active|yes|yes|

### Checking the Subnet

The `default` network acts as a DHCP server. To see what IP range your VMs will pull from, query the active XML:

```
sudo virsh net-dumpxml default | grep -A 4 "<ip"
```

**What this tells you:**

- **Host IP (`192.168.122.1`):** Your Arch Linux host's internal address on the virtual network.
    
- **DHCP Range (`.2` to `.254`):** The pool of IP addresses dynamically assigned to your VMs.
    

> [!TIP] Need external access?
> 
> If you need devices on your physical LAN to communicate directly with your VMs (e.g., hosting a web server accessible to others), NAT will not work.
> 
> Refer to [[Network Bridging for LAN access]] to set up a full Bridge connection.