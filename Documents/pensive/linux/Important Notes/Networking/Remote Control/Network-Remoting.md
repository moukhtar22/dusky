# Remote Access on Arch Linux + Hyprland + UWSM
## Part 1 of 3 — Foundation, Tailscale, and OpenSSH

> [!abstract] Scope
> This part establishes the control-plane and network foundation for reliable remote access on **Arch Linux + Hyprland + UWSM**:
> - Base assumptions and architecture
> - Wayland/UWSM prerequisite verification
> - Portal and `uinput` groundwork for later Sunshine use
> - Tailscale setup for CGNAT-safe connectivity
> - OpenSSH setup over Tailscale for shell access and recovery
>
> **Validated for current Arch Linux practices as of 2026-03.**

> [!warning] Reality checks
> - **Tailscale solves reachability, not physics.** It eliminates port-forwarding and CGNAT problems, but WAN latency is still limited by your real network path. Claims like “<5 ms from anywhere” are not realistic over public cellular/internet links.
> - **Sunshine on Hyprland requires an active logged-in graphical session.** If the machine reboots to a display manager, TTY, or locked-down non-graphical state, remote desktop is unavailable until the Hyprland user session exists.
> - **Moonlight discovery over Tailscale is often unreliable or absent.** Manual host entry by Tailscale IP or MagicDNS name is normal.
> - **This part does not start Sunshine yet.** It prepares the host so Part 2 can focus entirely on Sunshine/Moonlight and Wayland capture.

---

## Architecture

| Layer | Component | Role |
|---|---|---|
| Connectivity | Tailscale | Encrypted mesh VPN, NAT traversal, CGNAT-safe reachability |
| Shell access | OpenSSH | Remote administration, recovery path, file transfer |
| Wayland session | Hyprland via UWSM | Compositor and session environment |
| Wayland capture plumbing | PipeWire + `xdg-desktop-portal-hyprland` | Required later for Sunshine screen capture |
| Input injection | `uinput` | Required later for Sunshine keyboard/mouse control |
| Remote desktop | Sunshine + Moonlight | Covered in Part 2 |
| Temporary virtual display | Hyprland headless output + `wayvnc` | Covered in Part 3 |

---

## Design goals

- Work from **LAN or WAN** without router port forwarding
- Work behind **CGNAT**
- Preserve a clean **Wayland-native** Hyprland workflow
- Keep **SSH** available as a low-bandwidth fallback even if graphical streaming fails
- Minimize fragile or outdated practices

---

## Assumptions

- You are running a current **Arch Linux** system.
- You use **Hyprland** as your compositor.
- Hyprland is launched through **UWSM** or an equivalent session mechanism that properly exports the user environment into the systemd user manager.
- You have **physical access** for the initial setup and first verification.
- You are using a normal, non-root desktop user for the Wayland session.

> [!note]
> If you intend to rely on remote desktop after reboot, also plan how the machine reaches a logged-in Hyprland session:
> - manual local login,
> - auto-login into a graphical session,
> - or SSH access first, followed by local/session orchestration.
>
> Sunshine cannot capture a Wayland desktop that does not yet exist.

---

## Pre-flight audit

### 1. Avoid partial upgrades

On Arch, do **not** install packages into a stale system state.

```bash
sudo pacman -Syu
```

> [!warning]
> Avoid `pacman -Sy <package>` without a full upgrade. Partial upgrades are unsupported on Arch and are a common source of broken dependencies.

---

### 2. Confirm time, hostname, and basic network sanity

WireGuard-based systems, TLS, and package management all behave badly when time is wrong.

```bash
timedatectl status
hostnamectl status
ip route
```

Verify:
- system clock is synchronized,
- the machine has a normal default route,
- the hostname is what you expect.

---

### 3. Verify the Hyprland/UWSM user environment

Sunshine will later run best as a **systemd user service**, so the user manager must know the active Wayland session variables.

```bash
systemctl --user show-environment | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS)='
```

Expected minimum signals:
- `WAYLAND_DISPLAY=...`
- `XDG_SESSION_TYPE=wayland`
- `XDG_CURRENT_DESKTOP=Hyprland`

> [!warning]
> If `WAYLAND_DISPLAY` is missing from the user manager environment, Sunshine may start later but fail to capture.
>
> On systems not fully integrating the session environment into user systemd, import it manually:
>
> ```bash
> systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
> ```
>
> Under a proper UWSM-managed session, this usually should not be necessary.

---

### 4. Verify portal baseline for later Wayland capture

`xdg-desktop-portal-hyprland` is the correct Wayland screencast backend for Hyprland. The older wlroots portal backend is a common source of capture conflicts.

Check current portal packages:

```bash
pacman -Q xdg-desktop-portal xdg-desktop-portal-hyprland xdg-desktop-portal-gtk 2>/dev/null
```

If they are missing, install them now:

```bash
sudo pacman -S --needed xdg-desktop-portal xdg-desktop-portal-hyprland xdg-desktop-portal-gtk
```

If `xdg-desktop-portal-wlr` is installed, remove it:

```bash
sudo pacman -Rns xdg-desktop-portal-wlr
```

Then restart the relevant user services:

```bash
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
```

> [!note]
> Keeping `xdg-desktop-portal-gtk` is normal and recommended. It provides useful desktop integration such as chooser dialogs and fallback portal functionality. It is **not** a conflict.

---

### 5. Pre-stage `uinput` for later remote input injection

Sunshine uses `uinput` to synthesize keyboard and mouse events. This is required for full remote control.

Load the module immediately and persist it across reboots:

```bash
sudo modprobe uinput
printf '%s\n' uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
```

Check whether the Sunshine package already provides a suitable udev rule:

```bash
pacman -Ql sunshine 2>/dev/null | grep -iE 'udev|rules' || true
```

If you do **not** already have an appropriate rule, install a safe local fallback:

```bash
sudo install -Dm644 /dev/stdin /etc/udev/rules.d/99-local-uinput-uaccess.rules <<'EOF'
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"
EOF
```

Reload udev rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=misc
```

Verify the node exists:

```bash
ls -l /dev/uinput
```

> [!warning]
> Do **not** casually add your user to the `input` group.
>
> That group can grant broad raw access to physical input devices and is usually unnecessary on a normal desktop seat. Prefer a proper `uaccess`/udev rule unless you have a specific verified reason to do otherwise.

---

### 6. Check for VPN or routing conflicts

Tailscale can coexist with other VPNs, but **full-tunnel VPNs** and route-manipulating clients frequently interfere with authentication, peer routing, DNS, or local tethering.

Inspect current interfaces:

```bash
ip -o link show
```

Inspect current routes:

```bash
ip route
```

Common conflict examples:
- Cloudflare WARP
- full-tunnel OpenVPN
- other WireGuard tunnels
- vendor VPN clients that rewrite DNS or default routes

If troubleshooting later points to a conflict, disconnect the other VPN first. For Cloudflare WARP, for example:

```bash
warp-cli disconnect
```

> [!note]
> A conflicting VPN is **not guaranteed** to break Tailscale, but it is one of the first things to check if:
> - `tailscale up` hangs,
> - peer traffic blackholes,
> - DNS resolution fails unexpectedly,
> - or local tethered subnets become unreachable.

---

## Tailscale — CGNAT-safe connectivity layer

## Why Tailscale

Tailscale gives the machine a stable identity inside your tailnet and provides:
- encrypted peer-to-peer connectivity where possible,
- NAT traversal,
- fallback relay transport when direct traversal fails,
- optional MagicDNS naming,
- remote reachability without manual inbound router configuration.

This is the correct tool in this stack for:
- SSH over the internet,
- Moonlight/Sunshine reachability across CGNAT,
- accessing the system from phones, laptops, and tablets.

---

### 1. Install Tailscale

```bash
sudo pacman -S --needed tailscale
```

Enable and start the daemon:

```bash
sudo systemctl enable --now tailscaled.service
```

Verify daemon state:

```bash
systemctl --no-pager --full status tailscaled.service
```

If you want recent logs:

```bash
journalctl -u tailscaled.service -b --no-pager | tail -n 80
```

---

### 2. Optional but recommended: use `systemd-resolved` for MagicDNS and split DNS

If you want reliable **MagicDNS** and per-domain resolver handling, `systemd-resolved` is the cleanest modern baseline on Arch.

First, inspect the current `/etc/resolv.conf`:

```bash
ls -l /etc/resolv.conf
```

If it is a plain file and you want a rollback point, back it up:

```bash
sudo cp -a /etc/resolv.conf /etc/resolv.conf.bak.$(date +%Y%m%d-%H%M%S)
```

Enable `systemd-resolved`:

```bash
sudo systemctl enable --now systemd-resolved.service
```

Point `/etc/resolv.conf` at the resolved stub:

```bash
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
```

Validate:

```bash
resolvectl status
```

> [!warning]
> Do **not** overwrite `/etc/resolv.conf` blindly if you intentionally use another resolver manager and understand its behavior.
>
> This step is recommended for clean Tailscale DNS behavior, but it is not mandatory if you deliberately manage DNS another way.

> [!note]
> If you only plan to use raw Tailscale IPs and do not care about MagicDNS, you may choose to skip this section.

---

### 3. Optional: tell NetworkManager to ignore `tailscale0`

Many systems do **not** need this. Tailscale generally manages its own interface correctly. Add this only if you observe NetworkManager interfering with routes or interface state.

Create a drop-in:

```bash
sudo install -Dm644 /dev/stdin /etc/NetworkManager/conf.d/96-tailscale.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:tailscale0
EOF
```

Reload NetworkManager:

```bash
sudo systemctl reload NetworkManager.service || sudo systemctl restart NetworkManager.service
```

> [!note]
> This is a targeted workaround, not a universal requirement. If your system behaves correctly without it, you do not need it.

---

### 4. Authenticate the machine into your tailnet

Use QR login if you want to avoid browser permission issues on the host:

```bash
sudo tailscale up --qr
```

Alternative text/browser flow:

```bash
sudo tailscale up
```

What this does:
- brings the node online,
- generates or reuses its machine identity,
- associates it with your tailnet after you approve login.

> [!warning]
> Do **not** pile on advanced flags unless you actually need them.
>
> For a normal personal workstation, avoid adding things like:
> - `--accept-routes`
> - `--advertise-routes`
> - `--advertise-exit-node`
> - `--exit-node`
>
> until you intentionally design around those features.

---

### 5. Verify Tailscale health

Check overall state:

```bash
tailscale status
```

Get the node’s tailnet IPv4:

```bash
tailscale ip -4
```

Get the tailnet IPv6, if needed:

```bash
tailscale ip -6
```

Run a connectivity diagnostic:

```bash
tailscale netcheck
```

What to look for:
- the node is logged in,
- peers appear in `tailscale status`,
- `tailscale netcheck` completes without obvious transport failure,
- you receive a valid `100.x.y.z` IPv4 address.

> [!note]
> The Tailscale IP is stable for the current node identity, but it is **not permanently immutable** across identity resets, node deletion/re-registration, or some administrative changes. Treat it as stable, not sacred.

---

### 6. Test from another Tailscale device

From a second device already joined to the same tailnet:

```bash
tailscale ping <your-hostname-or-ip>
```

Examples:

```bash
tailscale ping 100.x.y.z
tailscale ping yourhost
```

If MagicDNS is enabled, you can also test OS-level resolution:

```bash
getent hosts yourhost
```

> [!note]
> `tailscale ping` tests tailnet reachability more directly than ordinary ICMP `ping`, and is often the better first check.

---

### 7. Firewall handling

Arch does **not** enable a firewall by default. Only do this section if you actually run one.

#### Firewalld

If you trust your tailnet policy and want the simplest behavior, trust the Tailscale interface:

```bash
sudo firewall-cmd --permanent --zone=trusted --add-interface=tailscale0
sudo firewall-cmd --reload
```

Why this is common:
- Tailscale traffic is already authenticated and encrypted,
- you often want multiple services reachable over the tailnet,
- it avoids repeatedly opening per-port rules later.

> [!note]
> Trusting `tailscale0` is a convenience decision. If you prefer stricter host-level filtering, open only specific services instead of trusting the whole interface.

#### UFW

Allow inbound traffic specifically on the Tailscale interface:

```bash
sudo ufw allow in on tailscale0
```

If you want to allow **only SSH** for now:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
```

> [!note]
> For later Sunshine use, you may either:
> - trust/allow the entire Tailscale interface, or
> - add only the specific Sunshine ports you intend to expose.
>
> Part 2 will cover the Sunshine side.

#### Custom nftables / iptables

If you maintain your own ruleset, allow either:
- all inbound traffic on `tailscale0`, or
- only the specific services you want on `tailscale0`.

Because custom rulesets vary, do **not** paste generic nftables commands blindly unless they match your actual tables and chains.

---

### 8. Common Tailscale problems

#### `tailscale up` hangs or never completes
Check:
- another VPN is still active,
- DNS is broken,
- the system clock is wrong,
- `tailscaled` is actually running.

Useful diagnostics:

```bash
systemctl --no-pager --full status tailscaled.service
journalctl -u tailscaled.service -b --no-pager | tail -n 100
tailscale netcheck
```

---

#### No QR code or auth state is confused

Log out and reinitiate login:

```bash
sudo tailscale logout
sudo tailscale up --qr
```

> [!warning]
> `tailscale logout` removes the current login state for this node. Do not run it casually on a remote-only machine unless you have another way back in.

---

#### MagicDNS names do not resolve
Check:
- whether `systemd-resolved` is active,
- whether `/etc/resolv.conf` points where you expect,
- whether Tailscale DNS is enabled in your tailnet policy/admin settings,
- whether you are testing with the correct device name.

Useful checks:

```bash
ls -l /etc/resolv.conf
resolvectl status
tailscale status
```

---

#### Tailnet traffic works, but local WAN/LAN routing behaves strangely
Common causes:
- another VPN client is rewriting routes,
- NetworkManager is interfering with interface state,
- a firewall is filtering `tailscale0`,
- you are using an exit node or subnet-router configuration unintentionally.

Check:

```bash
ip route
tailscale status
```

---

## OpenSSH — management and recovery path over Tailscale

> [!abstract]
> SSH is the low-bandwidth, high-reliability control plane for this system. Set it up even if your real goal is Sunshine/Moonlight. When desktop capture breaks, SSH is what gets you back in.

---

### 1. Install OpenSSH

```bash
sudo pacman -S --needed openssh
```

Generate host keys if they do not already exist:

```bash
sudo ssh-keygen -A
```

Validate server configuration syntax before enabling anything:

```bash
sudo sshd -t
```

> [!warning]
> If `sshd -t` reports errors, fix them before starting the service. Do not troubleshoot connection failures against an invalid config.

---

### 2. Understand Arch’s two activation models: `sshd.service` vs `sshd.socket`

Arch can run SSH in either of two ways:

#### `sshd.service`
- Traditional always-running daemon
- Simpler to reason about
- Best choice for a straightforward remote-admin host

#### `sshd.socket`
- Socket-activated by systemd
- Starts `sshd` on demand when a connection arrives
- Valid choice, but slightly easier to misconfigure if you later change ports

> [!warning]
> If you use **socket activation**, the listen port comes from `sshd.socket` (`ListenStream=`), which can override what you think you configured in `sshd_config`.

For a simple workstation/server setup, prefer `sshd.service`.

Disable socket activation if it is active, then enable the service:

```bash
sudo systemctl disable --now sshd.socket 2>/dev/null || true
sudo systemctl enable --now sshd.service
```

Check status:

```bash
systemctl --no-pager --full status sshd.service
```

If you intentionally want socket activation instead:

```bash
sudo systemctl disable --now sshd.service 2>/dev/null || true
sudo systemctl enable --now sshd.socket
```

Check status:

```bash
systemctl --no-pager --full status sshd.socket
```

---

### 3. Determine the effective SSH port

If you use normal service mode, inspect the resolved config:

```bash
sudo sshd -T | awk '/^port / {print $2; exit}'
```

If you use socket activation, inspect the socket unit:

```bash
systemctl cat sshd.socket | grep -E '^[[:space:]]*ListenStream='
```

If you never changed anything, the expected default is:

```text
22
```

Verify a listener exists:

```bash
ss -tlnp | grep -E ':(22)\b'
```

> [!note]
> Under socket activation, you may see `systemd` holding the port instead of a persistent `sshd` process. That is normal.

---

### 4. Install your client public key before disabling passwords

On the **client device** you will connect from, generate a strong ED25519 key if you do not already have one:

```bash
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519
```

This creates:
- private key: `~/.ssh/id_ed25519`
- public key: `~/.ssh/id_ed25519.pub`

Show the public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

On the **Arch host**, as the target login user, install that public key into `authorized_keys`.

If you are already logged in locally as that user:

```bash
install -d -m 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
printf '%s\n' 'ssh-ed25519 AAAA...your-public-key... comment' >> ~/.ssh/authorized_keys
```

Verify permissions:

```bash
ls -ld ~/.ssh
ls -l ~/.ssh/authorized_keys
```

Expected:
- `~/.ssh` is `700`
- `authorized_keys` is `600`

> [!warning]
> Append the **public** key, never the private key. Public keys usually start with:
> - `ssh-ed25519`
> - `ecdsa-sha2-...`
> - or, less preferably today, `ssh-rsa`

---

### 5. Lock SSH to public-key authentication

Once at least one working public key is installed, create a hardening drop-in:

```bash
sudo install -Dm644 /dev/stdin /etc/ssh/sshd_config.d/10-publickey-only.conf <<'EOF'
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
```

Validate again:

```bash
sudo sshd -t
```

Restart the active SSH unit.

If using `sshd.service`:

```bash
sudo systemctl restart sshd.service
```

If using `sshd.socket`:

```bash
sudo systemctl restart sshd.socket
```

> [!warning]
> Do **not** disable password authentication until you have verified a working key for at least one user account you can use.

---

### 6. Optional: restrict SSH exposure to Tailscale only

There are two common models:

#### Model A — Listen normally, rely on Tailscale reachability and firewalling
This is the simplest and usually sufficient approach:
- `sshd` listens on the normal host interfaces,
- your firewall only permits the traffic you want,
- Tailscale provides the secure path from the internet.

#### Model B — Add host-level restrictions
This is stricter, but requires more care:
- firewall allows SSH only on `tailscale0`,
- or `sshd` listens only where you explicitly want it.

For most users of this stack, **Model A with Tailscale + a sensible firewall is the better tradeoff**.

---

### 7. Firewall rules for SSH

If you already trusted `tailscale0` in firewalld, that is enough for SSH over Tailscale.

If you prefer a narrower rule set:

#### Firewalld — allow only the SSH service in the active zone

If the host should accept SSH generally:

```bash
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
```

If you specifically want Tailscale traffic trusted, the earlier `tailscale0` trusted-interface rule is cleaner.

#### UFW — allow SSH only on Tailscale

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
```

> [!note]
> If you changed the SSH port, replace `22` accordingly.

---

### 8. Verify SSH end-to-end over Tailscale

Find the Tailscale IP:

```bash
tailscale ip -4
```

From another device on the same tailnet, connect:

```bash
ssh youruser@100.x.y.z
```

If MagicDNS is working, you can also use the hostname:

```bash
ssh youruser@yourhost
```

For verbose connection diagnostics:

```bash
ssh -v youruser@100.x.y.z
```

Successful verification means:
- Tailscale reachability is working,
- DNS or raw-IP routing is working,
- `sshd` is listening,
- key authentication works,
- you now have a recovery/control path for the machine.

---

### 9. Useful SSH diagnostics

#### Show the effective server configuration

```bash
sudo sshd -T | less
```

This is often more useful than reading `sshd_config` directly because it shows the resolved final values.

#### Check whether the daemon/socket is active

```bash
systemctl --no-pager --full status sshd.service sshd.socket
```

#### Check recent SSH logs

```bash
journalctl -u sshd.service -b --no-pager | tail -n 80
```

If using socket activation, also inspect:

```bash
journalctl -u sshd.socket -b --no-pager | tail -n 80
```

#### Check the listener

```bash
ss -tlnp | grep ssh
```

---

### 10. Common SSH failures

#### `Permission denied (publickey)`
Usually means:
- the wrong private key is being offered,
- the public key is not in `authorized_keys`,
- file permissions are wrong,
- the login username is wrong,
- `PasswordAuthentication no` is set and you do not actually have a valid key installed.

Check on the host:

```bash
ls -ld ~/.ssh
ls -l ~/.ssh/authorized_keys
```

And from the client:

```bash
ssh -v youruser@100.x.y.z
```

---

#### SSH works locally/LAN but not over Tailscale
Check:
- `tailscale status`
- `tailscale ping <host>`
- firewall rules on `tailscale0`
- whether the client device is actually connected to the same tailnet or shared device path

---

#### SSH appears configured, but nothing listens on the port
Check whether you enabled the wrong unit:

```bash
systemctl --no-pager --full status sshd.service sshd.socket
```

Common mistake:
- editing `sshd_config`,
- but using `sshd.socket`,
- and forgetting that `ListenStream=` in the socket unit controls the port.

---

#### SSH listens only on localhost
Check resolved config:

```bash
sudo sshd -T | grep '^listenaddress'
```

If you see only `127.0.0.1` or `::1`, remote access will fail.

---

## Validation checklist for Part 1

Run through this checklist before proceeding to Part 2:

- [ ] System fully upgraded with `pacman -Syu`
- [ ] Hyprland/UWSM user environment exposes Wayland session variables to the user systemd manager
- [ ] `xdg-desktop-portal-hyprland` is installed
- [ ] `xdg-desktop-portal-wlr` is removed if previously installed
- [ ] `uinput` loads successfully and persists across reboot
- [ ] `tailscaled.service` is enabled and running
- [ ] `tailscale up` completed successfully
- [ ] `tailscale status` shows a healthy node
- [ ] Another device on the tailnet can reach this host
- [ ] `openssh` is installed
- [ ] `sshd.service` or `sshd.socket` is intentionally chosen and working
- [ ] Public-key SSH login over Tailscale works from another device

---

## State at the end of Part 1

At this point, the machine should have:

1. a functioning **Tailscale** identity and reachable tailnet address,
2. a working **SSH** path for recovery and administration,
3. the correct **portal** baseline for Hyprland,
4. `uinput` prepared for later Sunshine input injection.

Part 2 will build on this foundation to configure:
- Sunshine as a **systemd user service**,
- Wayland/PipeWire capture under Hyprland,
- Moonlight pairing,
- hardware encode selection,
- and first-run permission handling.


# Remote Access on Arch Linux + Hyprland + UWSM
## Part 2 of 3 — Sunshine, Wayland Capture, Hardware Encoding, and Moonlight

> [!abstract] Scope
> This part builds the actual remote desktop stack on top of Part 1:
> - install and run **Sunshine** correctly on **Hyprland + Wayland**
> - verify **PipeWire**, **portals**, and **audio**
> - configure the correct **capture backend**
> - select and validate **hardware encoding**
> - pair **Moonlight** clients over **Tailscale**
> - harden the host for unattended remote use
>
> **Validated for Arch Linux practices as of 2026-03.**

> [!warning] Read this before proceeding
> - Sunshine must run in the **same user context** as the logged-in Hyprland session.
> - On Wayland, Sunshine capture depends on **PipeWire + `xdg-desktop-portal-hyprland`** and a **local authorization prompt** the first time capture is requested.
> - A user service enabled with `systemctl --user enable sunshine` starts when that **user logs in**, not at cold boot before login.
> - `loginctl enable-linger` is **not** a substitute for a real graphical session. Lingering can keep the user manager alive after logout, but it does not create a capturable Wayland desktop by itself.

---

## Part 2 prerequisites

Before continuing, Part 1 should already be complete:

- [ ] Tailscale is installed and working
- [ ] SSH over Tailscale works
- [ ] `xdg-desktop-portal-hyprland` is installed
- [ ] `xdg-desktop-portal-wlr` is removed if it had been installed
- [ ] `uinput` is available and persistent
- [ ] You can log in locally to a normal Hyprland session

---

## What Sunshine needs on Hyprland

On this stack, Sunshine depends on five things:

1. **A live logged-in graphical session**  
   Sunshine cannot capture a desktop that does not exist yet.

2. **A user systemd service**  
   On Wayland, Sunshine should run as your desktop user, not as root and not as a system service.

3. **PipeWire**  
   Provides the actual screen/audio media streams used by Wayland portals.

4. **`xdg-desktop-portal-hyprland`**  
   Mediates secure screen capture requests from Sunshine to the compositor.

5. **`uinput`**  
   Lets Sunshine inject remote mouse and keyboard input.

---

## Install Sunshine and supporting packages

Prefer the official Arch package unless you are intentionally testing an upstream fix.

```bash
sudo pacman -S --needed sunshine
```

Useful supporting tools:

```bash
sudo pacman -S --needed libva-utils
```

> [!note]
> `sunshine-git` is **not** the default recommendation. Use the official repository package first. Only switch to `-git` if you have a specific upstream fix you need and you are intentionally accepting the extra churn.

---

## Verify the Wayland capture stack

## 1. Verify PipeWire, WirePlumber, and PulseAudio compatibility

Sunshine expects a modern Linux desktop media stack. On Arch Wayland, that usually means:
- `pipewire`
- `wireplumber`
- `pipewire-pulse`

Check the user services:

```bash
systemctl --user --no-pager --full status pipewire.service pipewire-pulse.service wireplumber.service
```

Check PulseAudio-compatible access:

```bash
pactl info | sed -n '1,20p'
```

Expected signs:
- a working server is reported,
- the server string references PipeWire or a PulseAudio-compatible stack,
- no connection error occurs.

List sinks and sources if you have multiple audio devices:

```bash
pactl list short sinks
pactl list short sources
```

> [!warning]
> If `pactl info` fails, fix the desktop audio stack before blaming Sunshine. Remote audio capture on Wayland is not independent of your local audio server state.

---

## 2. Verify portal services

Check portal service health:

```bash
systemctl --user --no-pager --full status xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
```

If needed, restart them:

```bash
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
```

If you want recent logs:

```bash
journalctl --user -u xdg-desktop-portal.service -u xdg-desktop-portal-hyprland.service -b --no-pager | tail -n 120
```

> [!note]
> On Hyprland, the correct screencast backend is `xdg-desktop-portal-hyprland`. If the wrong portal backend wins, capture may fail, hang, or produce a black screen.

---

## 3. Re-check the user systemd environment

Sunshine needs the systemd user manager to know about the active Wayland session.

```bash
systemctl --user show-environment | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS)='
```

Expected minimum:
- `WAYLAND_DISPLAY=...`
- `XDG_SESSION_TYPE=wayland`
- `XDG_CURRENT_DESKTOP=Hyprland`

If these are missing, import them from the current session, then restart the portal stack and Sunshine later:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
```

> [!warning]
> If Sunshine starts but behaves like it cannot “see” Wayland, missing user-manager environment is one of the first things to check.

---

## Run Sunshine the correct way

## 1. Use the user service, not a system/root service

Enable and start Sunshine in the **logged-in desktop user** context:

```bash
systemctl --user enable --now sunshine.service
```

Check status:

```bash
systemctl --user --no-pager --full status sunshine.service
```

Check logs:

```bash
journalctl --user -u sunshine.service -b --no-pager | tail -n 120
```

> [!warning]
> Do **not** run Sunshine as a root system service for a Hyprland desktop session. On Wayland, capture and portal permissions are tied to the user session.

> [!note]
> `enable --now` means:
> - start it immediately for the current logged-in user
> - start it automatically on future logins for that user
>
> It does **not** mean “before graphical login exists.”

---

## 2. If Sunshine starts too early or with stale state

In a proper UWSM session, this usually works cleanly. If Sunshine starts before portals or the session environment are fully ready, manually restart it after logging into Hyprland:

```bash
systemctl --user restart sunshine.service
```

For combined recovery after portal or environment changes:

```bash
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-hyprland.service sunshine.service
```

---

## Access the Sunshine web UI

Open the local admin UI on the host:

```text
https://localhost:47990
```

What to expect:
- a certificate warning on first use is normal,
- Sunshine will ask you to create a web admin username and password,
- this admin account is **separate** from Moonlight pairing.

> [!warning]
> The Sunshine web UI is an admin surface. Use a strong password and do not expose it more broadly than necessary.

### Safer remote access to the web UI: SSH tunnel

Because Part 1 already set up SSH, the cleanest remote admin path is to tunnel the local Sunshine UI instead of exposing it directly.

From a client machine:

```bash
ssh -L 47990:127.0.0.1:47990 youruser@100.x.y.z
```

Then browse locally on the client:

```text
https://localhost:47990
```

This keeps the web UI bound to localhost on the host side while still letting you administer it remotely.

---

## Configure Sunshine for Hyprland / Wayland

## 1. Choose the correct capture backend

In the Sunshine web UI, go to the **Audio/Video** section or the equivalent capture configuration area in your installed version.

For Hyprland, the correct capture path is the **Wayland / PipeWire / portal-backed** method.

Do **not** use KMS as your default Hyprland desktop capture method.

> [!warning]
> On Hyprland, “black screen” or “capture starts but shows nothing” is very often caused by selecting a KMS-oriented capture path instead of the normal Wayland/portal path.

### Practical rule

- **Use Wayland/portal capture** for your normal logged-in Hyprland desktop
- only troubleshoot KMS if you have a very specific reason and understand its limitations

---

## 2. First-run local authorization is mandatory

The first time Sunshine requests screen capture on Wayland, Hyprland should present a **local** screen-share / screencast permission dialog.

You must approve it **while physically at the machine**.

Recommended first authorization procedure:

1. Log in locally to Hyprland
2. Ensure Sunshine is running
3. Start Moonlight from another device on the same LAN or over Tailscale
4. Start a stream
5. Look at the host screen
6. Approve the capture prompt
7. If offered, enable persistence or “remember” for future requests

> [!warning]
> If you leave home before this step is completed, you can lock yourself out of remote desktop even though Tailscale and Sunshine are otherwise working.

---

## 3. Verify Sunshine configuration storage

Sunshine’s user configuration is normally stored under:

```bash
ls -la ~/.config/sunshine
```

The main config file is typically:

```text
~/.config/sunshine/sunshine.conf
```

> [!note]
> If you edit Sunshine configuration manually, stop the service first to avoid the UI and service racing with your changes.

---

## Hardware encoding on Linux

## General rules

Sunshine can encode video using:
- **hardware acceleration** for low CPU load and better efficiency,
- or **software encoding** as a fallback.

Recommended order:
- **Intel / AMD on Linux:** usually **VA-API**
- **NVIDIA on Linux:** usually **NVENC**
- **software encoding:** temporary fallback for diagnostics or unsupported hardware

> [!note]
> Software encoding is useful as a diagnostic baseline:
> - if software encoding works but hardware encoding fails, your capture path is likely fine and the problem is specifically in the hardware encode stack.

---

## Identify your GPUs and render nodes

Do **not** blindly assume `/dev/dri/renderD128` is always the iGPU. It often is, but it is not guaranteed.

Inspect render-node mappings:

```bash
ls -l /dev/dri/by-path/*-render
```

Example output pattern:

```text
... pci-0000:00:02.0-render -> ../renderD128
... pci-0000:01:00.0-render -> ../renderD129
```

A more detailed inspection:

```bash
for n in /dev/dri/renderD*; do
  printf '\n== %s ==\n' "$n"
  udevadm info --query=property --name="$n" | grep -E '^(DEVNAME|ID_PATH|ID_VENDOR_FROM_DATABASE|ID_MODEL_FROM_DATABASE|ID_VENDOR_ID|ID_MODEL_ID)=' || true
done
```

This lets you map:
- the integrated GPU,
- the discrete GPU,
- and the correct render node to feed into Sunshine when using VA-API.

---

## Intel VA-API

### 1. Install the correct VA-API driver

For most modern Intel GPUs, install:

```bash
sudo pacman -S --needed intel-media-driver libva-utils
```

For older Intel generations that still need the legacy VA-API driver, use:

```bash
sudo pacman -S --needed libva-intel-driver libva-utils
```

> [!note]
> Modern Intel systems generally want `intel-media-driver` and the `iHD` driver path. Older hardware may require the legacy `i965` path instead.

---

### 2. Verify VA-API against the intended render node

Replace the device path with the actual render node you identified earlier.

```bash
vainfo --display drm --device /dev/dri/renderD128
```

A healthy result should enumerate codecs and profiles. It should **not** fail immediately with driver initialization errors.

---

### 3. If Intel auto-detection picks the wrong VA driver

Use a **Sunshine service override**, not a global Hyprland environment change, unless you truly want the whole session affected.

Create a user-service drop-in:

```bash
systemctl --user edit sunshine.service
```

For modern Intel:

```ini
[Service]
Environment=LIBVA_DRIVER_NAME=iHD
```

For legacy Intel:

```ini
[Service]
Environment=LIBVA_DRIVER_NAME=i965
```

Reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart sunshine.service
```

> [!warning]
> Setting `LIBVA_DRIVER_NAME` globally in `hyprland.conf` is broader than necessary. Prefer the narrower per-service override unless you have a specific reason to force VA-API behavior for the whole session.

---

### 4. Configure Sunshine for Intel VA-API

In the Sunshine web UI:
- set the hardware encoder to **VA-API**
- if the UI exposes an adapter/device field, point it to the intended render node, for example:
  - `/dev/dri/renderD128`
  - or whichever render node actually belongs to the Intel GPU on your system

Recommended use case:
- on hybrid laptops, use the **Intel iGPU** for encoding if you want the discrete GPU to stay asleep and save power.

---

## AMD VA-API

For AMD on the Mesa stack, ensure the VA-API path is present and testable.

Install the usual validation package:

```bash
sudo pacman -S --needed libva-utils
```

If your system is missing the Mesa VA-API userspace, install:

```bash
sudo pacman -S --needed libva-mesa-driver
```

Validate on the correct render node:

```bash
vainfo --display drm --device /dev/dri/renderD128
```

Then in Sunshine:
- choose **VA-API**
- point the adapter/device field to the AMD render node if needed

> [!note]
> On Linux, AMD hardware encoding in this workflow is typically handled through the VA-API path rather than a Windows-style AMF workflow.

---

## NVIDIA NVENC

For NVIDIA, Sunshine normally uses **NVENC**, not VA-API.

Check that the proprietary userspace stack is healthy:

```bash
nvidia-smi
```

In Sunshine:
- choose **NVENC**
- leave the GPU selection on auto unless you have a specific multi-GPU reason to force it

> [!warning]
> If you use NVENC on a hybrid laptop, the NVIDIA GPU will wake up and consume power. If your priority is battery life and keeping the dGPU asleep, use the Intel iGPU VA-API path instead.

> [!note]
> If NVENC is unavailable in Sunshine even though the driver is installed, inspect Sunshine logs first. The issue is often missing or mismatched NVIDIA userspace components rather than a Hyprland-specific problem.

---

## Choosing the right encoder strategy

### Best-efficiency laptop setup
- Hyprland on iGPU
- Sunshine on **Intel VA-API**
- dGPU remains asleep when not needed

### Maximum NVIDIA encode performance
- Sunshine on **NVENC**
- best when plugged in and you accept higher power draw

### Safe diagnostic mode
- Sunshine on **software encoding**
- use only to prove the rest of the capture path works

---

## Audio considerations

Sunshine streams **system audio**, not just one application window by default.

Before pairing clients, verify:
- your expected output device is the current default sink
- system audio plays locally
- `pactl info` works

List current defaults and devices:

```bash
pactl info | grep -E '^(Default Sink|Default Source):'
pactl list short sinks
pactl list short sources
```

If the wrong device is default, change it before troubleshooting Sunshine audio.

> [!note]
> Bluetooth headsets, USB docks, and HDMI audio devices often change the default sink automatically. Many “Sunshine audio bugs” are just the host using the wrong output device.

---

## Moonlight client setup over Tailscale

## 1. Install Tailscale on the client

On the client device:
- install and sign into Tailscale
- confirm it can reach the host node

If available on that client platform, test:

```bash
tailscale ping 100.x.y.z
```

---

## 2. Install Moonlight

Install Moonlight on the client platform:
- Windows/macOS/Linux laptop
- Android phone/tablet
- iPhone/iPad

> [!note]
> Host auto-discovery often does not work across Tailscale. Manual host entry is expected.

---

## 3. Add the host manually

In Moonlight:
- choose **Add PC** or equivalent
- enter either:
  - the host’s Tailscale IP from `tailscale ip -4`, or
  - the MagicDNS hostname if you enabled and verified it

Example:

```text
100.x.y.z
```

---

## 4. Pair Moonlight with Sunshine

Start pairing from Moonlight. It will show a PIN.

Then in Sunshine:
- approve the pairing request in the web UI or pairing section,
- enter the PIN if prompted by your version/UI flow.

> [!note]
> The Sunshine **web admin login** and the Moonlight **pairing PIN** are separate things. Do not confuse them.

---

## 5. Start with conservative stream settings

For first validation, avoid chasing maximum quality immediately.

Good first-pass profiles:

### Reliable baseline
- **1080p**
- **60 FPS**
- moderate bitrate
- hardware encoding enabled if already validated

### Mobile-data baseline
- **720p or 1080p**
- **30–60 FPS**
- lower bitrate than LAN/Wi-Fi use

### After the baseline works
Increase gradually:
- 1440p / 4K
- HEVC / AV1 if both ends support it
- higher bitrate

> [!warning]
> If you change too many variables at once, troubleshooting becomes harder. Prove 1080p60 first, then optimize.

---

## Host availability and power management

A perfect Sunshine config still fails if the host sleeps, suspends, or tears down the desktop session.

## 1. Decide how the machine stays awake

Common failure modes:
- laptop lid close triggers suspend,
- `hypridle` suspends after inactivity,
- AC/battery policies stop the session,
- the machine reboots but nobody logs into Hyprland.

---

## 2. Closed-lid laptop operation

If this is a laptop and you expect remote use with the lid closed, configure `systemd-logind` accordingly.

Edit:

```bash
sudoedit /etc/systemd/logind.conf
```

Common values for remote-host use:

```ini
[Login]
HandleLidSwitch=ignore
HandleLidSwitchDocked=ignore
```

Apply:

```bash
sudo systemctl restart systemd-logind.service
```

> [!warning]
> Closed-lid operation can increase thermals depending on the chassis and airflow. Do not enable it blindly on a machine that runs hot.

---

## 3. Reconcile Hyprland idle policy

If you use `hypridle`, inspect its config and make sure it does not suspend the machine at a time when you expect remote access.

Typical places to check:

```bash
ls ~/.config/hypr/hypridle.conf ~/.config/hypridle.conf 2>/dev/null
```

Look for listeners that:
- suspend,
- hibernate,
- lock aggressively,
- or disable outputs in ways that break your intended remote workflow.

> [!note]
> Locking the session and suspending the machine are different problems:
> - a lock screen may still allow recovery depending on your setup,
> - suspension kills remote access until wake.

---

## 4. Understand the login requirement

If the machine reboots and stops at:
- a display manager login screen,
- TTY login,
- or no graphical session at all,

then Sunshine may be running as a user service later, but there is still **no active Hyprland session to capture**.

Options if you need post-reboot graphical availability:
- manually log in before leaving,
- configure auto-login to a graphical session,
- or accept that SSH is your recovery path and Sunshine becomes available only after a local/graphical login.

---

## Validation and first full rehearsal

Before depending on this remotely, do a complete local rehearsal.

## 1. Reboot the host

```bash
sudo reboot
```

Why:
- re-tests `uinput` persistence
- confirms Tailscale auto-start
- confirms Sunshine user service behavior after a fresh login
- flushes one-time setup illusions

---

## 2. After reboot, log into Hyprland normally

Then verify:

```bash
systemctl --user is-active sunshine.service
systemctl --user is-active pipewire.service
systemctl --user is-active wireplumber.service
systemctl --user is-active xdg-desktop-portal.service
systemctl --user is-active xdg-desktop-portal-hyprland.service
tailscale status
```

All should be healthy.

---

## 3. Start Moonlight from another device

Use a client that is already connected to Tailscale.

Start a stream and confirm:
- the host appears reachable,
- pairing succeeds,
- the portal prompt appears locally if this is the first run,
- video appears,
- mouse and keyboard input work,
- system audio is present.

---

## 4. Check Sunshine logs after the first successful stream

```bash
journalctl --user -u sunshine.service -b --no-pager | tail -n 120
```

What to look for:
- selected capture backend
- portal / PipeWire success
- chosen encoder
- device/adapter selection
- warnings about capture or input

---

## Troubleshooting

## Black screen

Most common causes:
- wrong capture backend selected
- portal conflict or portal service not healthy
- no active graphical Hyprland session
- first-run permission not granted locally

Check:

```bash
systemctl --user --no-pager --full status xdg-desktop-portal.service xdg-desktop-portal-hyprland.service sunshine.service
journalctl --user -u sunshine.service -u xdg-desktop-portal.service -u xdg-desktop-portal-hyprland.service -b --no-pager | tail -n 200
```

Primary fix path:
1. ensure `xdg-desktop-portal-wlr` is absent
2. restart portal services
3. restart Sunshine
4. use the Wayland/portal capture path, not KMS
5. retry locally and approve the capture dialog

---

## Sunshine web UI loads, but no stream starts

Check:
- Tailscale connectivity from client to host
- Moonlight pairing state
- Sunshine logs
- whether the host actually has a logged-in Hyprland session

Useful checks:

```bash
tailscale status
systemctl --user status sunshine.service
journalctl --user -u sunshine.service -b --no-pager | tail -n 120
```

---

## Input does not work

Check:
- `uinput` module is loaded
- `/dev/uinput` exists
- you completed the udev rule setup from Part 1
- you rebooted after making persistent changes if needed

Commands:

```bash
lsmod | grep '^uinput'
ls -l /dev/uinput
```

Load immediately if needed:

```bash
sudo modprobe uinput
```

> [!warning]
> If video works but input does not, this is usually a `uinput` / permissions problem, not a network problem.

---

## No audio in Moonlight

Check the host first:

```bash
pactl info | sed -n '1,20p'
pactl list short sinks
pactl list short sources
```

Common causes:
- wrong default sink
- broken `pipewire-pulse`
- Bluetooth/HDMI device changes
- local audio stack failure unrelated to Sunshine

---

## Pairing gets stuck or PIN is rejected repeatedly

Reset the pairing from **both sides**:
- delete the host entry or pairing on the Moonlight client
- remove the old pairing entry in Sunshine
- initiate pairing again

> [!note]
> Stale pairings are common after reinstalling Sunshine, resetting config, or re-registering the host in a way that changes its identity.

---

## Hardware encoding fails, but Sunshine otherwise works

Diagnostic sequence:
1. switch Sunshine temporarily to software encoding
2. if the stream works, the capture path is good
3. validate the hardware stack separately:
   - Intel/AMD: `vainfo --display drm --device /dev/dri/renderD*`
   - NVIDIA: `nvidia-smi`
4. re-select the correct render node or encoder
5. inspect Sunshine logs

---

## The wrong GPU is being used

Symptoms:
- unexpected dGPU wakeups
- high power draw
- poor battery life
- encode failures on hybrid systems

Fix strategy:
- map render nodes explicitly with `/dev/dri/by-path/*-render`
- choose the intended encoder path in Sunshine
- for Intel VA-API, set a per-service `LIBVA_DRIVER_NAME` override only if needed

---

## Sunshine starts, but still cannot capture after login

Try this recovery sequence from the logged-in Hyprland session:

```bash
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-hyprland.service sunshine.service
```

Then retry the stream locally.

---

## Recommended state at the end of Part 2

By now, the host should have:

- [ ] Sunshine installed from the official Arch repo
- [ ] PipeWire, WirePlumber, and portal services healthy
- [ ] Sunshine running as a **user** service in the Hyprland session
- [ ] correct Wayland/portal capture configured
- [ ] first-run screen-capture permission granted locally
- [ ] hardware encoder selected and validated, or software fallback proven
- [ ] Moonlight paired successfully over Tailscale
- [ ] a verified test stream with working video, audio, and input
- [ ] power/idle policy adjusted so the host remains remotely usable

---

## State at the end of Part 2

At this point you should have a functioning remote desktop stack:

- **Tailscale** provides secure WAN reachability
- **SSH** remains your admin and recovery path
- **Sunshine** captures and streams the Hyprland desktop
- **Moonlight** connects and controls the machine remotely

Part 3 will cover the remaining advanced workflows:
- **iPhone-over-USB headless monitor via `wayvnc`**
- Hyprland **headless outputs**
- VNC over USB tethering
- and the remaining remote-access edge cases, teardown, and advanced operational notes.

# Remote Access on Arch Linux + Hyprland + UWSM
## Part 3 of 3 — iPhone USB Headless Display, Guest SSH Access, Tailscale Reset/Teardown, and Advanced Operations

> [!abstract] Scope
> This final part covers the remaining advanced workflows and operational tasks:
> - using an **iPhone as a temporary Hyprland monitor over USB tethering**
> - creating and managing **Hyprland headless outputs**
> - serving that headless output through **`wayvnc`**
> - granting a **guest user SSH access over Tailscale**
> - safely **disabling, resetting, or uninstalling Tailscale**
> - advanced operational and recovery notes for the full stack
>
> **Validated for Arch Linux practices as of 2026-03.**

> [!warning] Important distinction
> The iPhone workflow in this part is **not** a native “USB monitor” in the DisplayPort/Alt-Mode sense.
>
> It works by:
> - creating a **network link over USB** via iPhone tethering,
> - creating a **virtual headless monitor** in Hyprland,
> - then sending that virtual desktop to the iPhone using **VNC**.
>
> This is useful, but it is a **network remote-display workflow**, not a true zero-copy hardware display link.

---

## Choosing the right remote-access method

Use the right tool for the job:

| Use case | Best tool | Why |
|---|---|---|
| Remote shell/admin/recovery | OpenSSH over Tailscale | Lowest overhead, most reliable |
| Full remote desktop from anywhere | Sunshine + Moonlight | Best latency/quality for WAN use |
| Temporary extra display on iPhone over cable | Hyprland headless output + `wayvnc` over USB tethering | Practical local virtual monitor |
| Share shell access with another person | Guest SSH user + Tailscale sharing | Safer than sharing your own account |

> [!note]
> `wayvnc` is **not** a replacement for Sunshine. Use `wayvnc` for the specific “temporary virtual monitor” use case, especially over a local USB-tether link. Use Sunshine for your primary full remote-desktop workflow.

---

# iPhone as a Temporary Hyprland Monitor over USB

## Architecture

This workflow creates a **LAN over USB** and then runs a VNC server only on that link.

### Data path

1. iPhone connects by USB
2. iPhone **Personal Hotspot** exposes a USB network interface
3. Linux sees that interface through the **`ipheth`** driver
4. Hyprland creates a **headless output**
5. `wayvnc` exports only that headless output
6. A VNC client app on the iPhone connects to the host over the USB network

### Result

Your iPhone becomes a **temporary virtual second screen** for Hyprland.

---

## What this workflow is good for

- a quick second monitor when traveling
- a control/status panel
- chat, logs, dashboards, terminals, music controls
- lightweight GUI access when you do not want a full remote-desktop stack

## What it is not good for

- lowest-latency gaming
- color-critical or compression-sensitive work
- replacing a real wired monitor
- secure exposure on general LAN/WAN without additional VNC hardening

---

## Prerequisites

## Required packages

Install the actual minimum needed for the manual workflow:

```bash
sudo pacman -S --needed wayvnc dhcpcd
```

## Usually already present

These are typically already installed on Arch desktop systems:

- `iproute2`
- `systemd`
- Hyprland itself

## Optional diagnostic tools

These are useful, but **not required** just for USB tethering:

```bash
sudo pacman -S --needed usbmuxd libimobiledevice
```

> [!note]
> `usbmuxd` and `libimobiledevice` are commonly recommended in generic iPhone/Linux guides, but they are **not strictly required** for basic USB tethering itself. The actual network side is handled by the kernel’s `ipheth` driver.

---

## Step 1 — Prepare the iPhone side

On the iPhone:

1. Connect it to the Linux machine by USB
2. Unlock the phone
3. If prompted, tap **Trust This Computer**
4. Open **Settings > Personal Hotspot**
5. Enable **Allow Others to Join**

This is what causes the iPhone to expose a USB network interface to Linux.

> [!warning]
> If the phone is locked, not trusted, or Personal Hotspot is disabled, the host may never see the interface even though the cable is physically connected.

---

## Step 2 — Verify the Linux USB tether interface appears

First check whether the kernel sees a new network interface:

```bash
ip link
```

You are looking for a new interface such as:
- `enp0s20f0u1`
- `eth1`
- `usb0`

The exact name varies by hardware and naming policy.

### Confirm that it is really the iPhone tether interface

Inspect the driver behind the interface:

```bash
readlink -f /sys/class/net/<interface>/device/driver
```

Example:

```bash
readlink -f /sys/class/net/enp0s20f0u1/device/driver
```

Expected result should end with something like:

```text
.../drivers/ipheth
```

If no interface appears, check kernel messages:

```bash
dmesg | grep -iE 'ipheth|iphone|usb'
```

If needed, try loading the driver manually:

```bash
sudo modprobe ipheth
```

Then reconnect the iPhone USB cable and re-check `ip link`.

> [!note]
> On many systems `ipheth` is already available or auto-loaded. Manually loading it is only needed if detection does not happen automatically.

---

## Step 3 — Bring the interface up and obtain an IP address

Once the iPhone tether interface exists, bring it up:

```bash
sudo ip link set dev <interface> up
```

Example:

```bash
sudo ip link set dev enp0s20f0u1 up
```

Now request an IPv4 lease from the iPhone:

```bash
sudo dhcpcd -4 -w <interface>
```

Example:

```bash
sudo dhcpcd -4 -w enp0s20f0u1
```

Verify the assigned address:

```bash
ip -4 addr show dev <interface>
```

Typical iPhone tethering addresses are in the `172.20.10.0/28` or nearby range. The host often receives something like:

```text
172.20.10.2
```

and the iPhone itself is commonly:

```text
172.20.10.1
```

You can test basic reachability:

```bash
ping -c 3 172.20.10.1
```

> [!warning]
> If you already have another DHCP client managing that interface, do not run multiple DHCP clients against it at once. If troubleshooting gets messy, disconnect/reconnect the phone and restart the interface cleanly.

---

## Step 4 — Understand firewall implications

Even though this is “just USB tethering,” it is still a network path. A local firewall can block VNC.

If you bind `wayvnc` only to the tether address, that already reduces exposure significantly. But host firewall rules may still matter.

### Firewalld

Trust only the tether interface or open VNC explicitly.

To trust the interface:

```bash
sudo firewall-cmd --permanent --zone=trusted --add-interface=<interface>
sudo firewall-cmd --reload
```

Or open only TCP 5900 in the active zone:

```bash
sudo firewall-cmd --permanent --add-port=5900/tcp
sudo firewall-cmd --reload
```

### UFW

Open the VNC port:

```bash
sudo ufw allow 5900/tcp
```

Or, if you want to scope it to the interface:

```bash
sudo ufw allow in on <interface> to any port 5900 proto tcp
```

> [!warning]
> VNC is not something you want broadly exposed. For this workflow, **bind `wayvnc` to the USB tether IP only**, not `0.0.0.0`.

---

## Step 5 — Create a Hyprland headless output

Now create a virtual monitor inside Hyprland.

Run this **inside the logged-in Hyprland user session**:

```bash
hyprctl output create headless
```

Hyprland will create a new virtual output with a name like:

```text
HEADLESS-1
```

List monitors to confirm:

```bash
hyprctl monitors
```

Look for a monitor named `HEADLESS-1`, `HEADLESS-2`, and so on.

> [!note]
> Headless outputs are ephemeral. They do not survive a Hyprland restart unless you recreate them.

---

## Step 6 — Configure the headless output resolution and scale

Set the new monitor’s mode and scale.

General syntax:

```bash
hyprctl keyword monitor "<NAME>,<RESOLUTION>,auto,<SCALE>"
```

### Good starting presets

#### Phone-friendly compact panel
```bash
hyprctl keyword monitor "HEADLESS-1,1080x960,auto,2"
```

#### Standard 1080p panel
```bash
hyprctl keyword monitor "HEADLESS-1,1920x1080,auto,1"
```

#### Larger touch-friendly UI
```bash
hyprctl keyword monitor "HEADLESS-1,1080x960,auto,3"
```

### Choosing scale

- `1` = most workspace, smallest UI
- `2` = balanced high-DPI feel
- `3` = larger controls, easier on a phone screen

> [!note]
> The best choice depends on:
> - your iPhone screen size,
> - whether you want fingertip readability,
> - whether you use an external keyboard,
> - and what kind of applications you place on that display.

---

## Step 7 — Start `wayvnc` bound only to the USB tether IP

Find the host’s tether IP first:

```bash
ip -4 addr show dev <interface>
```

Suppose the host IP is:

```text
172.20.10.2
```

Now start `wayvnc` on that IP, bound only to the headless output:

```bash
wayvnc 172.20.10.2 5900 --output=HEADLESS-1 --max-fps=30
```

What each part does:

- `172.20.10.2` — bind only to the USB tether address
- `5900` — VNC port
- `--output=HEADLESS-1` — export only that virtual monitor
- `--max-fps=30` — conservative and stable starting point

> [!warning]
> Start with `--max-fps=30`, especially on hybrid-GPU laptops or when stability matters more than raw smoothness. Raise to 60 later only after the baseline works.

### If you want to try 60 FPS later

```bash
wayvnc 172.20.10.2 5900 --output=HEADLESS-1 --max-fps=60
```

> [!note]
> Whether 60 FPS is stable depends on:
> - GPU stack,
> - compositor behavior,
> - overall system load,
> - and the VNC client app.

---

## Step 8 — Connect from the iPhone VNC app

On the iPhone, open any VNC client app available on iOS.

Add a connection to:

```text
172.20.10.2:5900
```

where `172.20.10.2` is the **Linux host’s** tether IP, not the iPhone’s.

You should see the Hyprland headless monitor.

> [!note]
> The iPhone app connects **to the host**, so the destination is the host’s tether IP address assigned by the iPhone hotspot DHCP server.

---

## Step 9 — Move windows onto the headless output

Once the headless monitor exists, it behaves like another Hyprland monitor. You can move workspaces or windows onto it using normal Hyprland behavior.

Useful checks:

```bash
hyprctl monitors
hyprctl clients
```

If you already have monitor rules or workspace bindings in your Hyprland config, they may affect where new windows appear.

---

## Cleanup for the iPhone VNC workflow

When finished:

### Stop `wayvnc`
If it is running in the foreground, use `Ctrl+C`.

If it is backgrounded:

```bash
pkill -x wayvnc
```

### Remove the headless output

```bash
hyprctl output remove HEADLESS-1
```

Replace `HEADLESS-1` with the actual name you created.

### Optionally release the tether lease

```bash
sudo dhcpcd -k <interface>
sudo ip link set dev <interface> down
```

Example:

```bash
sudo dhcpcd -k enp0s20f0u1
sudo ip link set dev enp0s20f0u1 down
```

---

## Persistence and automation notes

> [!note]
> This workflow is intentionally documented as a **manual** procedure. That is the most reliable way to understand it and debug it.
>
> In practice:
> - the USB interface name can vary,
> - the headless monitor name can vary,
> - and the whole stack is session-dependent.

If you later automate it, your automation must handle:
- dynamic interface detection
- DHCP
- headless-output creation
- monitor naming
- `wayvnc` startup/cleanup
- firewall exceptions if applicable

---

## Troubleshooting the iPhone USB monitor workflow

## No tether interface appears at all

Check:
- iPhone unlocked
- “Trust This Computer” accepted
- Personal Hotspot enabled
- `ipheth` loaded

Commands:

```bash
ip link
dmesg | grep -iE 'ipheth|iphone|usb'
sudo modprobe ipheth
```

---

## Interface exists, but no IP address is assigned

Check:
- hotspot is still active on the iPhone
- the interface is up
- DHCP was actually requested

Commands:

```bash
sudo ip link set dev <interface> up
sudo dhcpcd -4 -w <interface>
ip -4 addr show dev <interface>
```

---

## VNC connection times out or is refused

Check:
- `wayvnc` is actually running
- it is bound to the expected IP/port
- firewall is not blocking 5900
- you are connecting to the **host’s** IP, not the iPhone’s

Commands:

```bash
ss -tln | grep ':5900'
ip -4 addr show dev <interface>
```

---

## Black or empty screen in VNC

Most common causes:
- wrong output name
- the headless monitor was created but no windows/workspace are on it
- the headless output was removed or renamed
- `wayvnc` was started before the output existed

Check:

```bash
hyprctl monitors
hyprctl clients
```

Then restart `wayvnc` against the correct output.

---

## Hyprland crashes or becomes unstable at higher FPS

Reduce frame rate:

```bash
wayvnc 172.20.10.2 5900 --output=HEADLESS-1 --max-fps=30
```

If necessary, test even lower values such as `20`.

---

## `hyprctl` or `wayvnc` fails when launched over SSH

If you are trying to operate the headless output from an SSH session rather than from a local terminal inside Hyprland, you must target the correct user session environment.

### Determine the desktop user and UID

```bash
id youruser
```

Example:

```bash
id dusk
```

### Set the runtime directory

```bash
export XDG_RUNTIME_DIR=/run/user/<uid>
```

Example:

```bash
export XDG_RUNTIME_DIR=/run/user/1000
```

### Find the Hyprland instance socket/signature

Inspect the Hyprland runtime directory:

```bash
ls -la "$XDG_RUNTIME_DIR"/hypr
```

The directory name there is typically the active `HYPRLAND_INSTANCE_SIGNATURE`.

Export it:

```bash
export HYPRLAND_INSTANCE_SIGNATURE=<signature>
```

Then test:

```bash
hyprctl monitors
```

> [!warning]
> SSHing in as root and then trying to control a user’s Wayland compositor without targeting that user session properly is a common failure mode.

---

# Guest SSH Access over Tailscale

## Goal

Allow another person to SSH into this machine **without**:
- giving them your own account,
- sharing your local password,
- or exposing your machine publicly to the internet.

This is a two-layer problem:

1. **Network reachability** — Tailscale sharing or same-tailnet access
2. **Host authentication** — a dedicated local UNIX user with SSH keys

---

## Layer 1 — Give them network reachability with Tailscale sharing

If the other person is **not** already in your tailnet, the cleanest method is to share the specific device from the Tailscale admin console.

### Concept

You generate a share/invite link for this machine.  
They accept it with **their own Tailscale account**.  
They gain reachability to **this device**, not general access to your entire tailnet.

> [!note]
> Exact admin-console button labels can change over time, but the workflow is generally:
> - open the Tailscale admin console
> - find the device
> - choose the share action
> - generate or copy the sharing link
> - send it to the other person

> [!warning]
> Device sharing availability depends on your tailnet policy and plan features. If your admin console does not show sharing options, check your tailnet policy or account capabilities.

Once they accept the share, they should be able to reach the host’s Tailscale address.

But that still does **not** let them log in. SSH access is a separate layer.

---

## Layer 2 — Create a dedicated guest UNIX account

Never give a guest your own shell account unless you intentionally want them to have all the same file-level access you do.

Create a separate account:

```bash
sudo useradd -m -s /bin/bash guest
```

Lock password login for that account:

```bash
sudo passwd -l guest
```

This ensures there is no usable local password for the account.

> [!note]
> `passwd -l` locks password authentication for the account. It does **not** prevent SSH public-key authentication.

---

## Install the guest’s SSH public key

Assume the guest sends you their public key, for example:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... guest@example
```

Create the `.ssh` directory with correct ownership and permissions:

```bash
sudo install -d -m 700 -o guest -g guest /home/guest/.ssh
```

Install the key:

```bash
sudo install -m 600 -o guest -g guest /dev/stdin /home/guest/.ssh/authorized_keys <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... guest@example
EOF
```

Verify:

```bash
sudo ls -ld /home/guest /home/guest/.ssh
sudo ls -l /home/guest/.ssh/authorized_keys
```

Expected:
- home directory exists
- `/home/guest/.ssh` is `700`
- `authorized_keys` is `600`
- file ownership is `guest:guest`

---

## Restrict what the guest can do in SSH

Create an SSH config drop-in specifically for this guest:

```bash
sudo install -Dm644 /dev/stdin /etc/ssh/sshd_config.d/20-guest-user.conf <<'EOF'
Match User guest
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    X11Forwarding no
    AllowAgentForwarding no
    AllowTcpForwarding no
    PermitTunnel no
EOF
```

Validate SSH config:

```bash
sudo sshd -t
```

Reload the active SSH unit.

If you use service mode:

```bash
sudo systemctl reload sshd.service
```

If you use socket activation and want to ensure new behavior is picked up cleanly:

```bash
sudo systemctl restart sshd.socket
```

> [!note]
> These restrictions still provide a normal shell, but disable common “extra transport” features like SSH port forwarding and tunnels for that guest account.

---

## Optional account expiry

If the access should be temporary, set an expiry date:

```bash
sudo chage -E 2026-12-31 guest
```

Check it:

```bash
sudo chage -l guest
```

---

## Test the guest login

From a machine that can reach the host over Tailscale:

```bash
ssh guest@100.x.y.z
```

Or if MagicDNS works:

```bash
ssh guest@yourhost
```

> [!warning]
> Test the guest account yourself before relying on it. Most guest-login failures are simple permission mistakes in `~guest/.ssh` or `authorized_keys`.

---

## Optional: file-transfer-only guest instead of shell access

If you want the guest to upload/download files but **not** receive a shell, use a different `Match User` policy with `ForceCommand internal-sftp`.

Example:

```bash
sudo install -Dm644 /dev/stdin /etc/ssh/sshd_config.d/21-guest-sftp-only.conf <<'EOF'
Match User guestfiles
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    ChrootDirectory %h
    ForceCommand internal-sftp
    X11Forwarding no
    AllowAgentForwarding no
    AllowTcpForwarding no
    PermitTunnel no
EOF
```

> [!warning]
> A proper SFTP-only chroot requires stricter directory ownership rules than a normal shell account. Use this only if you actually want an SFTP jail and understand the ownership requirements.

---

## Revoke guest access

There are several layers you can revoke:

### Remove the SSH key only

```bash
sudoedit /home/guest/.ssh/authorized_keys
```

Remove the relevant key, then save.

### Expire or lock the user

```bash
sudo usermod --expiredate 1 guest
```

or:

```bash
sudo passwd -l guest
```

### Remove the account completely

```bash
sudo userdel -r guest
```

### Remove Tailscale sharing

Revoke the device share from the Tailscale admin console so the external user loses network reachability as well.

> [!note]
> For clean revocation, remove **both**:
> - the shared network access on the Tailscale side
> - and the guest key/account on the host side

---

# Tailscale Disable, Reset, and Uninstall

> [!warning]
> If you are connected **through Tailscale right now**, disabling or resetting it will cut off your session immediately unless you have another path back in.

---

## Option 1 — Soft disable

Use this if you want to stop using Tailscale temporarily but keep the software and identity state.

Bring the interface down:

```bash
sudo tailscale down
```

Stop and disable the daemon:

```bash
sudo systemctl disable --now tailscaled.service
```

To re-enable later:

```bash
sudo systemctl enable --now tailscaled.service
sudo tailscale up
```

> [!note]
> `tailscale down` disables network participation for the node but does not remove the package or necessarily erase the login state.

---

## Option 2 — Log out the node cleanly

If you want the node to leave the tailnet but keep the installed software:

```bash
sudo tailscale logout
```

Then stop the daemon if desired:

```bash
sudo systemctl stop tailscaled.service
```

To join again later:

```bash
sudo systemctl start tailscaled.service
sudo tailscale up --qr
```

---

## Option 3 — Reset the node identity completely

Use this if you want the machine to come back as a **fresh node identity**.

Stop the service:

```bash
sudo systemctl stop tailscaled.service
```

Remove the local state:

```bash
sudo rm -rf /var/lib/tailscale
```

Start the daemon again:

```bash
sudo systemctl start tailscaled.service
```

Re-authenticate:

```bash
sudo tailscale up --qr
```

> [!warning]
> This is effectively a re-registration. Expect:
> - a new node identity
> - possible loss of old node-specific approvals/settings
> - and likely a different tailnet IP than before

---

## Option 4 — Full uninstall

Stop and remove the package:

```bash
sudo systemctl disable --now tailscaled.service
sudo pacman -Rns tailscale
```

Remove local state if still present:

```bash
sudo rm -rf /var/lib/tailscale
sudo rm -rf /var/cache/tailscale
```

If you created a NetworkManager ignore rule only for Tailscale, remove it:

```bash
sudo rm -f /etc/NetworkManager/conf.d/96-tailscale.conf
sudo systemctl reload NetworkManager.service || sudo systemctl restart NetworkManager.service
```

If you previously trusted `tailscale0` in Firewalld, remove that rule:

```bash
sudo firewall-cmd --permanent --zone=trusted --remove-interface=tailscale0
sudo firewall-cmd --reload
```

If you added a UFW rule specifically for `tailscale0`, remove it with the matching numbered or textual rule via `ufw status numbered`.

> [!warning]
> Do **not** remove `uinput`, portal packages, or other remote-desktop dependencies here if you still plan to use Sunshine by some other connectivity method.

---

# Advanced Operational Notes

## Running the full stack safely

At this point, your system has three distinct remote-access layers:

1. **SSH over Tailscale** — admin and recovery
2. **Sunshine + Moonlight over Tailscale** — full desktop
3. **Hyprland headless output + `wayvnc` over USB** — local temporary virtual monitor

Treat them differently:

- SSH is your **recovery plane**
- Sunshine is your **daily remote-desktop plane**
- `wayvnc` USB headless is your **situational/auxiliary plane**

---

## Best-practice remote workflow

### Before you leave the machine
Verify:
- Tailscale connected
- SSH works from another device
- Sunshine service active in the user session
- first-run portal permission already granted
- lid-close / idle policy won’t suspend the machine unexpectedly

### If Sunshine later fails remotely
Use SSH first:

```bash
ssh youruser@100.x.y.z
```

Then inspect/recover:

```bash
systemctl --user status sunshine.service
journalctl --user -u sunshine.service -b --no-pager | tail -n 120
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-hyprland.service sunshine.service
```

### If you only need shell access for someone else
Do **not** involve Sunshine.  
Use:
- Tailscale sharing
- guest UNIX user
- SSH public key only

---

## Layered troubleshooting mindset

When something breaks, debug by layer:

### Layer 1 — Physical / session
- Is the machine powered on?
- Did it suspend?
- Is Hyprland actually running?
- Is the user logged in?

### Layer 2 — Network
- Is Tailscale up?
- Can the client reach the host?
- Is USB tethering active for the iPhone workflow?

### Layer 3 — Service
- Is `sshd` running?
- Is `sunshine.service` running?
- Is `wayvnc` listening on the correct IP?

### Layer 4 — Wayland/media
- Are portals healthy?
- Is PipeWire healthy?
- Was the capture permission granted?
- Is the correct output being captured/exported?

### Layer 5 — Device/encode/input
- Is the correct encoder selected?
- Does `uinput` exist?
- Is audio going to the expected sink?
- Is the right headless monitor name in use?

This layered approach prevents random, ineffective changes.

---

## Fast recovery command set

These are the most useful “get me back to a known good state” commands.

### Tailscale health

```bash
tailscale status
tailscale netcheck
tailscale ip -4
```

### SSH health

```bash
systemctl status sshd.service sshd.socket
ss -tlnp | grep ssh
sudo sshd -t
```

### Sunshine / portals / media

```bash
systemctl --user status sunshine.service
systemctl --user status xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
systemctl --user status pipewire.service pipewire-pulse.service wireplumber.service
journalctl --user -u sunshine.service -u xdg-desktop-portal.service -u xdg-desktop-portal-hyprland.service -b --no-pager | tail -n 200
```

### Hyprland outputs

```bash
hyprctl monitors
hyprctl clients
```

### USB tether / VNC

```bash
ip link
ip -4 addr
ss -tln | grep ':5900'
```

---

# Final Checklist for the Entire 3-Part Series

Use this as the complete end-state verification.

## Foundation
- [ ] Arch system fully updated
- [ ] Hyprland runs correctly under UWSM
- [ ] user systemd environment contains the needed Wayland variables
- [ ] `xdg-desktop-portal-hyprland` installed
- [ ] `xdg-desktop-portal-wlr` removed if previously present
- [ ] `uinput` loads and persists

## Tailscale / SSH
- [ ] `tailscaled.service` enabled and healthy
- [ ] `tailscale up` completed successfully
- [ ] another device can reach the host over Tailscale
- [ ] OpenSSH installed and validated
- [ ] public-key SSH works over Tailscale

## Sunshine / Moonlight
- [ ] Sunshine installed from the official Arch repo
- [ ] PipeWire, WirePlumber, and portal services healthy
- [ ] Sunshine runs as a **user service**
- [ ] Wayland/portal capture path selected
- [ ] first-run capture permission granted locally
- [ ] video, audio, and input all work from Moonlight
- [ ] encoder selection validated
- [ ] power/idle/lid policy won’t unexpectedly suspend the host

## Optional USB iPhone monitor workflow
- [ ] iPhone USB tether interface appears on Linux
- [ ] DHCP lease acquired from the iPhone
- [ ] Hyprland headless output can be created and configured
- [ ] `wayvnc` binds only to the tether IP
- [ ] iPhone VNC client can connect and display the headless monitor

## Optional guest access
- [ ] guest reachability provided via Tailscale sharing or same-tailnet membership
- [ ] dedicated guest UNIX account created
- [ ] guest uses SSH public key only
- [ ] guest restrictions applied through `sshd_config.d`
- [ ] guest access can be revoked cleanly

---

## State at the end of Part 3

You now have a complete remote-access reference for:

- **CGNAT-safe connectivity** with Tailscale
- **secure shell administration** with OpenSSH
- **high-quality remote desktop** with Sunshine + Moonlight on Hyprland
- **temporary iPhone-as-monitor** operation via USB tethering + `wayvnc`
- **guest access delegation** without exposing your personal account
- **clean reset and uninstall procedures** for Tailscale

This completes the 3-part reference.
