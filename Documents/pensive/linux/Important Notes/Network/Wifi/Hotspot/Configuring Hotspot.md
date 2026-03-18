# Wi-Fi Hotspot with NetworkManager on Arch Linux

> [!info] Scope
> This reference assumes **Arch Linux** and **NetworkManager only**.  
> It uses `nmcli` and NetworkManager's built-in connection sharing. It does **not** use manual `hostapd`, standalone `dnsmasq` configuration, or hand-written NAT/firewall rules.

> [!note]
> `nmcli` operations usually work as a regular desktop user via Polkit. If your environment does not grant that permission, prepend `sudo` to the `nmcli` commands below.

---

## What NetworkManager Does in Hotspot/Shared Mode

When a hotspot profile uses `ipv4.method shared`, NetworkManager handles the usual hotspot plumbing automatically:

- puts the selected Wi-Fi interface into **AP mode**
- assigns the hotspot interface a **private IPv4 subnet**
- starts a **private `dnsmasq` instance** for DHCP/DNS
- enables **forwarding/NAT** for clients
- sends client traffic through the host's **current default route**

> [!important]
> The hotspot does **not** bind to a specific uplink interface.  
> Clients use whatever connection currently provides the host's **default route**.

---

## Prerequisites

### Required Packages

Install the required tools:

```bash
sudo pacman -S --needed networkmanager dnsmasq iw
```

Enable and start NetworkManager:

```bash
sudo systemctl enable --now NetworkManager.service
```

> [!note]
> Install `dnsmasq`, but do **not** enable `dnsmasq.service` just for this use case.  
> NetworkManager starts its own private `dnsmasq` instance for shared connections.

---

## Pre-Flight Checks

### Verify the Wi-Fi Device Supports AP Mode

Quick check with NetworkManager:

```bash
nmcli -f GENERAL.DEVICE,GENERAL.TYPE,WIFI-PROPERTIES.AP device show wlp2s0
```

Look for:

```text
WIFI-PROPERTIES.AP: yes
```

Detailed check with `iw`:

```bash
iw list
```

In the output, confirm that `AP` appears under `Supported interface modes`:

```text
Supported interface modes:
	 * managed
	 * AP
	 * monitor
```

If `AP` is missing, that adapter/driver/firmware stack cannot host a hotspot under Linux.

---

### Check for Wi-Fi Radio Blocking

If the device is soft- or hard-blocked, hotspot activation will fail.

```bash
rfkill list
rfkill unblock wifi
```

---

### Identify the Hotspot Interface and Current Uplink

List devices:

```bash
nmcli device status
```

Check the current default route:

```bash
ip route show default
```

Typical interpretation:

- **Hotspot interface**: the Wi-Fi device that will broadcast the AP, e.g. `wlp2s0`
- **Uplink**: whatever interface owns the current default route, e.g. `enp3s0`, `wwan0`, `usb0`, `tun0`, or another Wi-Fi adapter

> [!warning] One Wi-Fi radio is not automatically both uplink and hotspot
> If you are currently connected to Wi-Fi and want to broadcast a hotspot from the **same physical radio**, the hardware must support concurrent **`managed` + `AP`** operation.
>
> Check `iw list` and inspect **`valid interface combinations`**.
>
> Even when concurrent operation is supported, both roles often must share the **same channel**. If concurrent mode is not supported, starting the hotspot will disconnect the existing Wi-Fi client connection.

---

## Recommended Defaults

For maximum compatibility:

- use **2.4 GHz** first
- use channel **1**, **6**, or **11**
- use a **WPA2/WPA-PSK** passphrase of at least 8 characters
- keep `ipv4.method shared`
- disable IPv6 on the hotspot unless you explicitly need to design for it

> [!tip]
> In NetworkManager, `band bg` means **2.4 GHz**.  
> It does **not** force legacy 802.11b/g only operation.

---

## Method 1: Fast One-Shot Hotspot Creation

This is the simplest way to create and immediately activate a hotspot.

### 2.4 GHz Example

```bash
nmcli device wifi hotspot \
  ifname wlp2s0 \
  con-name hotspot-2g \
  ssid "MyArchHotspot" \
  password "supersecretpassword" \
  band bg \
  channel 6
```

### 5 GHz Example

```bash
nmcli device wifi hotspot \
  ifname wlp2s0 \
  con-name hotspot-5g \
  ssid "MyArchHotspot-5G" \
  password "supersecretpassword" \
  band a \
  channel 36
```

### Notes

- `ifname` selects the Wi-Fi interface that will broadcast the hotspot.
- `con-name` sets the **NetworkManager profile name**.
- `ssid` sets the broadcast network name.
- `password` sets the passphrase.
- `band bg` selects **2.4 GHz**.
- `band a` selects **5 GHz**.
- `channel` is optional but recommended for repeatable behavior.

> [!important]
> Do **not** assume the connection name equals the SSID.  
> Set `con-name` explicitly if you want predictable lifecycle management.

---

## Method 2: Deterministic Persistent Profile Creation

Use this when you want a clean, auditable, repeatable profile with explicit settings.

### Create the Profile

```bash
nmcli connection add \
  type wifi \
  ifname wlp2s0 \
  con-name hotspot-2g \
  ssid "MyArchHotspot"
```

### Configure It as an Access Point

```bash
nmcli connection modify hotspot-2g \
  connection.autoconnect no \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  802-11-wireless.channel 6 \
  802-11-wireless-security.key-mgmt wpa-psk \
  802-11-wireless-security.psk "supersecretpassword" \
  ipv4.method shared \
  ipv4.addresses 10.42.42.1/24 \
  ipv6.method disabled
```

### Activate It

```bash
nmcli connection up hotspot-2g
```

### Why This Method Is Better for Long-Term Use

- profile name is explicit and stable
- band/channel are fixed
- subnet is fixed
- autoconnect behavior is explicit
- security settings are visible and auditable

> [!note]
> If you omit `ipv4.addresses`, NetworkManager will usually select an available subnet in the `10.42.x.0/24` range automatically.  
> Setting it explicitly avoids conflicts and makes troubleshooting easier.

> [!note]
> `ipv6.method disabled` is a deliberate simplification.  
> NetworkManager hotspot sharing is primarily straightforward for IPv4. If you need client IPv6, design and test that separately.

---

## Hotspot Lifecycle Management

### List All Profiles

```bash
nmcli -f NAME,UUID,TYPE,DEVICE connection show
```

### List Active Connections

```bash
nmcli -f NAME,TYPE,DEVICE connection show --active
```

### Start the Hotspot

```bash
nmcli connection up hotspot-2g
```

### Stop the Hotspot

```bash
nmcli connection down hotspot-2g
```

### Delete the Hotspot Profile

```bash
nmcli connection delete hotspot-2g
```

> [!warning]
> Deleting the profile removes the saved configuration and stored PSK.  
> Recreate it if needed.

---

## Viewing and Modifying Hotspot Settings

### Show Full Profile

```bash
nmcli connection show hotspot-2g
```

### Show the Stored Passphrase

```bash
nmcli --show-secrets -g 802-11-wireless-security.psk connection show hotspot-2g
```

### Change the SSID

```bash
nmcli connection modify hotspot-2g \
  802-11-wireless.ssid "NewSSID"
```

### Change the Password

```bash
nmcli connection modify hotspot-2g \
  802-11-wireless-security.psk "newstrongpassphrase"
```

### Change Band/Channel

```bash
nmcli connection modify hotspot-2g \
  802-11-wireless.band bg \
  802-11-wireless.channel 11
```

### Reapply Changes

If the hotspot is already active, bounce the connection:

```bash
nmcli connection down hotspot-2g
nmcli connection up hotspot-2g
```

---

## Verification and Inspection

### Confirm Which Connection Is Active on the Wi-Fi Interface

```bash
nmcli -f GENERAL.DEVICE,GENERAL.CONNECTION,GENERAL.STATE,IP4.ADDRESS device show wlp2s0
```

### Check the Host's Default Route

```bash
ip route show default
```

### See Connected Clients

```bash
sudo iw dev wlp2s0 station dump
```

This is useful for verifying that clients are actually associated to the AP.

---

## Band and Channel Selection Guidance

| Goal | Recommended Setting | Notes |
|---|---|---|
| Maximum compatibility | `band bg`, channel `1`, `6`, or `11` | Best choice for phones, laptops, and IoT devices |
| Higher throughput, less crowding | `band a`, channel `36`, `40`, `44`, or `48` | Requires 5 GHz-capable clients |
| Avoid surprises | Set channel explicitly | Prevents auto-selection from changing behavior between activations |

> [!warning]
> Avoid DFS channels unless you specifically need them and understand the implications.  
> DFS channels can introduce AP startup delays and client compatibility problems.

---

## Autostart Behavior

If you want the hotspot profile to start automatically when the interface becomes available:

```bash
nmcli connection modify hotspot-2g connection.autoconnect yes
```

If you want it to remain manual-only:

```bash
nmcli connection modify hotspot-2g connection.autoconnect no
```

> [!note]
> For laptops, `connection.autoconnect no` is usually the safer default.  
> Unintended AP activation can be confusing and may disrupt normal Wi-Fi usage.

---

## Troubleshooting

## 1. `AP` Mode Is Not Supported

Symptoms:

- activation fails immediately
- NetworkManager reports unsupported mode
- `WIFI-PROPERTIES.AP` is `no`

Checks:

```bash
nmcli -f GENERAL.DEVICE,GENERAL.TYPE,WIFI-PROPERTIES.AP device show wlp2s0
iw list
```

Resolution:

- confirm the adapter/driver/firmware actually supports AP mode
- update kernel and firmware packages
- use a different Wi-Fi adapter if necessary

---

## 2. Starting the Hotspot Disconnects Existing Wi-Fi

Cause:

- same physical Wi-Fi radio is being used for both:
  - client uplink (`managed`)
  - hotspot (`AP`)

Resolution:

- use **Ethernet**, USB tethering, or another uplink
- use a **second Wi-Fi adapter**
- verify concurrent mode support in `iw list`

---

## 3. Clients Connect but Have No Internet

Checks:

```bash
ip route show default
nmcli -f NAME,TYPE,DEVICE connection show --active
sudo journalctl -u NetworkManager -b
```

Common causes:

- host itself has no working uplink
- `dnsmasq` is not installed
- a custom firewall/VPN policy is blocking forwarded traffic
- another tool flushed or replaced NetworkManager's transient NAT/forwarding rules
- the chosen hotspot subnet overlaps with another routed network or VPN

Resolution ideas:

- confirm the host can browse normally
- confirm the host has a valid default route
- install `dnsmasq` if missing
- set a different hotspot subnet, e.g. `10.99.0.1/24`
- review VPN kill-switch rules if a VPN is active

> [!important]
> With `ipv4.method shared`, you normally do **not** need to manually set `net.ipv4.ip_forward` or add NAT rules yourself.  
> If you do manage firewalling manually, make sure you are not breaking NetworkManager's sharing rules.

---

## 4. The Hotspot Is Active but Not Visible to Some Devices

Common causes:

- using 5 GHz with clients that only support 2.4 GHz
- using a DFS channel
- regulatory-domain restrictions
- some IoT clients require plain WPA2-PSK on 2.4 GHz

Checks:

```bash
iw reg get
nmcli connection show hotspot-2g
```

Resolution:

- switch to `band bg`
- use channel `1`, `6`, or `11`
- avoid DFS channels on 5 GHz
- keep the security settings simple and compatible

---

## 5. Wi-Fi Is Soft- or Hard-Blocked

Checks:

```bash
rfkill list
```

Fix:

```bash
rfkill unblock wifi
```

---

## 6. Multiple Uplinks Exist and the Wrong One Is Being Shared

Symptoms:

- hotspot clients use the wrong network path
- clients bypass or ignore the uplink you expected

Cause:

- NetworkManager shares the host's **current default route**

Checks:

```bash
ip route show default
nmcli -f NAME,TYPE,DEVICE connection show --active
```

Resolution:

- adjust which connection owns the default route
- if needed, tune route metrics on the relevant uplink profiles

---

## 7. Logs Are Needed

For hotspot failures, NetworkManager logs are usually the fastest source of truth:

```bash
sudo journalctl -u NetworkManager -b
```

For live monitoring while you retry activation:

```bash
sudo journalctl -fu NetworkManager
```

---

## Security Notes

- Prefer a strong passphrase; do not run an open hotspot unless you intentionally want one.
- For broad compatibility, WPA2/WPA-PSK remains the safest default in mixed environments.
- Some modern clients support WPA3, but AP-mode support and interoperability still vary by hardware, driver, and client stack. Use WPA3 only after explicit validation.
- NetworkManager stores system connection profiles under:

```text
/etc/NetworkManager/system-connections/
```

These files are sensitive because they may contain secrets.

---

## Quick Reference

### Fast 2.4 GHz Hotspot

```bash
nmcli device wifi hotspot \
  ifname wlp2s0 \
  con-name hotspot-2g \
  ssid "MyArchHotspot" \
  password "supersecretpassword" \
  band bg \
  channel 6
```

### Start / Stop / Delete

```bash
nmcli connection up hotspot-2g
nmcli connection down hotspot-2g
nmcli connection delete hotspot-2g
```

### Show Password

```bash
nmcli --show-secrets -g 802-11-wireless-security.psk connection show hotspot-2g
```

### Show Connected Clients

```bash
sudo iw dev wlp2s0 station dump
```

---

## Recommended Baseline

For the least-friction setup on Arch Linux with NetworkManager:

- hotspot interface: your Wi-Fi device, e.g. `wlp2s0`
- band: `bg`
- channel: `6`
- security: `wpa-psk`
- IPv4: `shared`
- IPv6: `disabled`
- subnet: `10.42.42.1/24`
- autoconnect: `no`

This produces the most predictable and portable hotspot profile for routine local sharing.
