# Dusky VNC

`vnc_setup.py` shares a physical Hyprland screen on TCP **5902**.
`second_display.py` creates a separate 1280×720 screen on TCP **5901**,
to the right of the other displays. Portrait mode uses 720×1280.
Both displays accept clients on phones, tablets and other PCs. Run these scripts
on the Linux/Hyprland server; install a viewer on the receiving device.
The `second_display.py` filename identifies the secondary-display script.
If a setup script is renamed or moved, rerun its setup to regenerate the
installed service's launch and cleanup paths. A stale cleanup path can stop
WayVNC while leaving its virtual monitor attached to Hyprland.
Port 5900 is deliberately avoided because local QEMU consoles commonly use it.

Run setup as the desktop user in a running Hyprland session:

```sh
./vnc_setup.py
./second_display.py             # optional separate screen
./vnc_setup.py status
./second_display.py status
./second_display.py orientation portrait
./second_display.py orientation landscape
```

The scripts deploy their own service files on first run; no manual unit-file
installation is required. `vnc_setup.py` writes `dusky_vnc_desktop.service`.
`second_display.py` writes `dusky_vnc_display.service` and also deploys the
desktop service. Units go in `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`,
then setup reloads systemd and enables/starts them. The service TUI discovers
these installed units and toggles them; it is not the deployment step.

Setup creates the configuration and user units, prioritizes a UFW allowance for
TCP ports 5901 and 5902 when UFW is installed, and enables/starts the services. It requires
`wayvnc` (including `wayvncctl` and PAM support), `python-rich`, `openssl`,
`hyprland`, systemd, and `iproute2`. Setup detects missing tools and Rich,
then installs only missing package targets through `sudo pacman -S --needed`.
Only pacman runs with elevated privileges; configuration and services remain
owned by the desktop user. Include packages in the offline ISO payload to avoid
downloads; otherwise repositories must be reachable and the pacman databases
usable. Setup does not refresh databases or initiate a system upgrade. WayVNC is included
in both the ISO generator and offline installer's network package lists.

The VNC allowance precedes UFW user rules, including existing VNC or broad deny
rules. Repeating setup reuses the first-position allowance. Other ports and the
firewall's policies are preserved; an inactive firewall stays inactive. Custom
UFW before-rules, independent nftables/firewalld rules, and router isolation
are outside this automatic UFW configuration. Setup does not reset firewalls.

Keep the server and receiving device on the same Wi-Fi or Ethernet network.
Install [RealVNC/RVNC Viewer](https://www.realvnc.com/en/connect/download/viewer/)
on iPhone/Android. On Linux/Wayland, use [Remmina](https://remmina.org/) with
its **VNC** protocol (libvncserver plugin). Install it on an Arch receiving PC with
`sudo pacman -S --needed remmina libvncserver`. Prefer the receiving-PC helper
below: it sets up certificate trust before opening a saved VNC connection.
Remmina runs natively on Wayland; `GDK_BACKEND=wayland remmina` explicitly
selects that backend if needed. The standalone `gvncviewer` shipped with
gtk-vnc 1.5.0 failed this server's authentication request in testing; Remmina's
GVNC plugin also interrupted its connection attempt and left a black window.
Use Remmina's standard **VNC** plugin (libvncserver), which remains native Wayland.
[TigerVNC](https://tigervnc.org/) is an alternative on Windows/macOS or Linux
with an X11 display. Linux TigerVNC 1.16.2 requires X11/XWayland and fails with
`Can't open display` in a Wayland-only session. Other viewers must support
RSA-AES or VeNCrypt.
Enter the address shown by `status` and sign in with the **server's** Linux
username and password. RVNC uses `SERVER_IP:5902` for desktop sharing or
`SERVER_IP:5901` for the secondary display. TigerVNC uses an explicit port after
two colons: `SERVER_IP::5902` or `SERVER_IP::5901`, as documented in its
[viewer manual](https://tigervnc.org/doc/vncviewer.html). A viewer with separate
address/port fields takes the IP and port separately.
No SIM, mobile data, DNS lookup, Tailscale,
or internet connection is required for local VNC. RVNC Viewer on iOS was
confirmed working on this machine. Remmina 1.4.43 with libvncserver 0.9.15 was
launched natively on Wayland. The old server certificate (only `CN=WayVNC`)
failed its verification over Tailscale. Setup now issues certificates with
the server hostname, localhost, LAN addresses and Tailscale address in their
subject alternative names, retaining the RSA key when possible. Setup, service
startup and `--reconnect` refresh missing names; `--remote` also refreshes the
desktop certificate after Tailscale joins. If addresses change while a server
is running, rerun setup or reconnect before connecting to its new address.
Desktop login/video confirmation is
still pending on the other PC, which needs this certificate update.
Password-only legacy VNC authentication is not enabled.

### Receiving Arch PC setup

Run `vnc_viewer.py` on the PC you will control the server **from**:

```sh
./vnc_viewer.py                              # install missing packages, ask for the server address
./vnc_viewer.py SERVER_IP:5902 --username SERVER_USER
./vnc_viewer.py TAILSCALE_IP:5902 --username SERVER_USER
./vnc_viewer.py SERVER_IP:5901                # separate monitor
./vnc_viewer.py --install-only               # install without opening a window
./vnc_viewer.py SERVER_IP:5902 --reset-trust   # intentional server identity replacement
./vnc_viewer.py TAILSCALE_IP:5902 --quality fast
```

The receiving-PC helper installs missing `remmina`, `openssl`, `libvncserver` and
`python-rich` using sudo pacman; the viewer runs as your desktop user with the
Wayland backend. It saves connections under `${XDG_DATA_HOME:-$HOME/.local/share}/remmina`
and disables the applet in Remmina's preferences before launching with
`--no-tray-icon`: Remmina otherwise creates its login-autostart file before
processing that flag. Existing generated applet entries are removed, and the
viewer runs on demand. This app-generated autostart path is ignored
by the dotfiles repository and is not shipped by this setup.
It preserves existing connection preferences and retrieves the server's TLS
certificate without sending credentials, saves it on first use, and checks
the certificate and connection address before opening Remmina. Future runs use
that saved trust. Renewed certificates are accepted automatically only when the
public key is unchanged and the new certificate passes name/time verification;
`--reset-trust` explicitly replaces identity trust. This is trust on first
use, so the first connection must reach the intended server. Certificates are
saved beside profiles as `.crt` files and configured as Remmina's CA certificate.
You should see a Linux username/password prompt, without a certificate-files
form. Enter passwords in Remmina. If verification reports an IP/name mismatch,
rerun the updated server setup; leaving certificate fields empty cannot fix it.
The helper configures no server services, firewall rules or network profiles. Remmina handles reopening
connections and removing saved entries. Downloads require internet or cached packages;
viewing over an existing LAN does not.

Every helper launch defaults to **fast**, including existing saved connections:
Remmina's **Medium** preset prefers Tight/JPEG compression with JPEG quality 5
and compression level 3, prioritizing smaller updates over image quality.
`--quality balanced` selects Good (JPEG quality 7, compression level 2);
`--quality best` selects Best's lossless encodings. Other saved preferences
are preserved. Change quality inside Remmina to apply it during an active session;
the helper reapplies its requested preset on the next launch. A relayed Tailscale connection with
high round-trip latency will still have delayed input regardless of encoding.
Use `tailscale ping SERVER_TAILSCALE_IP` and `tailscale netcheck` to distinguish
relay/network limitations from viewer settings. No routing changes are needed
to check this.

Setup and status show a numbered Rich "Connect another device" guide with
mobile/desktop viewer download links, exact address/port, server Linux username, iPhone local-network
permission and login steps. There is no web UI or PIN pairing for this VNC setup.

For remote access, sign in to Tailscale on the server and receiving device,
use the same tailnet, and select the server's Tailscale address from `status`.
Tailnet access rules must permit the connection. `--remote` prepares Tailscale
on the server. Remote access needs internet; local streaming over an existing
Wi-Fi, Ethernet, hotspot or tethering network does not. This does not require a
separate PC-to-PC script.

Recovery controls work in both scripts:

```sh
./vnc_setup.py --diagnose
./vnc_setup.py --reconnect
./vnc_setup.py --clients
./vnc_setup.py --disconnect CLIENT_ID
./vnc_setup.py --disconnect-all
./second_display.py --diagnose
./second_display.py --reconnect
```

`--reconnect` restarts the selected display and disconnects its viewers.
Restarting the master also restores an enabled secondary display; a disabled secondary
display stays disabled. Reconnecting the secondary display restarts only that display
when the master is healthy. `--disconnect`/`--disconnect-all` leave the server
running and drop only the selected display's sessions. Reopen the connection
in the viewer afterwards. WayVNC has no saved-pairing database; delete saved
addresses inside the receiving device's viewer. Existing positional actions remain supported.

The main switch controls both Dusky VNC services:

```sh
systemctl --user enable --now dusky_vnc_desktop.service
systemctl --user disable --now dusky_vnc_desktop.service
```

The service TUI already uses `--now`. Plain `enable`/`disable` controls automatic
startup only; `--now` also starts/stops the running services. Disabling the main
service stops both servers and removes the secondary monitor. The secondary service
keeps its enablement preference, so it returns with the main service next time.
The main service checks its enabled state at startup, so starting the secondary
service cannot implicitly start a disabled VNC system.
To turn off only the separate screen, use `./second_display.py stop`.

The units own the WayVNC processes. There is no persistent Python supervisor
after startup, no periodic CLI status polling, and no VNC process when stopped.
Failures restart with a delay increasing from 5 to 30 seconds. Each server has
its own control socket, and status checks both capture state and the RFB greeting.
Saved secondary-display dimensions are preserved when viewers request resizing.

`offline` requires a disconnected, AP-capable second Wi-Fi adapter and dnsmasq.
The optional actions install missing NetworkManager/dnsmasq or Tailscale packages.
It refuses to replace an active Wi-Fi connection, creates a manual hotspot, and
keeps it from becoming the IPv4 default route. Spare-adapter hotspot activation
has not been exercised on this machine. `remote` separately enables Tailscale
and requires internet for initial sign-in; Tailscale is shared network
infrastructure and is not stopped by the VNC switch. Tailscale's existing
routing/DNS preferences are preserved; the remote workflow was not activated
during this audit.

If a connection times out, inspect the relevant service journal and verify the
receiving device is using the displayed address/port. An active listener and local RFB
check do not prove that a router permits traffic between Wi-Fi clients. Guest
networks/client isolation can still block access. IPv6-only networks and
cross-subnet router configurations were not tested; these scripts listen on IPv4.

## Verification on 2026-10-01

The renamed service files were removed temporarily and both actual `--setup`
commands recreated them, enabled them and reached Ready. The subsequent
disable/stop removed the secondary monitor and ownership record. Both renamed
services were left disabled/inactive with zero main PID; routes stayed unchanged.

After the secondary script was renamed, the installed unit still referenced
the removed `phone_display.py` path. Its stop hook failed and left `DUSKY-PHONE`
attached. The unit now uses `second_display.py` for both launch and cleanup.
A complete three-service on/off cycle passed afterwards: only the physical
screen remained, both virtual monitors and ownership records were removed,
all services were inactive/disabled with zero main PID, and routes stayed unchanged.

Tested with Python 3.14.7, Rich 15.0.0, systemd 262, Hyprland 0.56.2,
WayVNC 0.10.1, NeatVNC 1.0.1, NetworkManager 1.58.1, UFW 0.36.2,
OpenSSL 3.6.5, and kernel 7.3.0-rc5-dusky-battery.
The final ISO's released kernel and package versions still need validation.
Upstream WayVNC [0.10.2](https://github.com/any1/wayvnc/releases/tag/v0.10.2)
fixes resource cleanup/capture-switch crashes; use a consistent distribution
package set with those fixes when finalizing the ISO. The installed repository
metadata currently offers 0.10.1; no package upgrade was performed.

- Three main-service off/on cycles: both PIDs became zero, both listeners closed,
  and the owned secondary output/state were removed; both servers returned on enable.
- A final off/on cycle confirmed that attempting to start the secondary service
  while the master was disabled left both services inactive with zero PIDs;
  enabling the master restored both servers.
- Forced crash of each server: automatic recovery; a main-server crash also
  stopped/recreated the secondary display.
- Portrait and landscape: expected monitor dimensions and capture readiness.
- Both servers: successful TLS/PAM authentication and a complete 1280×720 raw
  framebuffer update (3,686,400 bytes each).
- 200 RFB/VeNCrypt negotiations with 16 parallel workers: all passed.
- iPhone at 192.168.29.119: authenticated RVNC connection to desktop port 5902;
  user confirmed the desktop displayed correctly. Port 5901 was also confirmed
  to show the separate monitor with working cursor control. Android testing is
  deferred.
- Thirty-second sample: idle secondary-display server used 0 measured CPU seconds and
  22.12 MiB in its service cgroup. Desktop VNC with an iPhone connected used
  0.468375 CPU seconds (1.56% of one core) and 28.29 MiB. These are short
  observations on this machine, not throughput or latency benchmarks.
- The existing Wi-Fi connection and default route remained unchanged.
- The single-adapter `offline` refusal was exercised without network changes.
- Repeating setup left both server PIDs unchanged and preserved the active
  iPhone connection; existing UFW rules were reused.
- Second pass: five isolated UFW scenarios (fresh default deny, broad source
  deny, individual port denies, equivalent grouped deny, and an allowance
  behind a deny) all allowed both VNC ports while port 5903 stayed blocked.
  Each repeated configuration left the rule list unchanged. Tests used separate
  mount/network namespaces and copied firewall files; the kernel lacks veth,
  so isolated loopback ingress was evaluated through the UFW user chain.
- Second pass: repeated setup through both `python` and `python3` preserved
  server PIDs; one additional complete master off/on cycle passed, including
  the disabled-master/secondary-start check. Wi-Fi and routes remained unchanged.
- Existing offline profiles must be inactive AP profiles before reuse;
  connected client profiles and active hotspots are rejected before modification.
- Removed ignored `deny`/`unlock_time` options from this machine's existing
  WayVNC PAM profile. Both servers then passed TLS/PAM login and full framebuffer
  transfer again without PAM warnings. Setup leaves packaged PAM profiles alone.

Focused, non-mutating regressions:

```sh
python -m unittest discover -s user_scripts/networking/vnc -p 'test_*.py'
```

Thirty-two server tests cover first-run deployment into empty directories,
fragmented/wrong/closed/timed-out RFB responses, malformed
control replies, active/idle Wi-Fi selection, offline refusal, settings reads,
atomic writes, cleanup ownership recovery, firewall idempotence/error reporting,
offline profile reuse, stable service commands across Python aliases, batched
dependency installation, installation failures, targeted viewer disconnection,
reconnect service preferences, recovering a disabled master without reconfiguring
a healthy one, and flag dispatch. Diagnostics, master and secondary reconnect
and empty-session disconnect were exercised live; individual viewer disconnect
was checked with fixtures rather than disconnecting an authenticated phone.
Tailscale certificate refresh also preserves the VNC off switch. Unit syntax passed
`systemd-analyze --user verify`.

## Native receiving-PC verification on 2026-10-02

- All 40 VNC tests passed, including eight receiving-PC tests covering package
  installation failure, address syntax, saved preferences, native Wayland
  launch, TLS bootstrap, certificate renewal with the same key, rejection of a
  replacement key, clear certificate mismatch errors and applet suppression
  with existing preferences preserved.
- A temporary real WayVNC server passed first-use certificate setup and repeated
  verified TLS handshakes. Both installed VNC services then refreshed their
  certificates at startup and passed the receiving helper's TLS check.
- The actual receiving helper opened Remmina's Linux username/password form
  directly, without a certificate-files form; Hyprland reported `xwayland: false`.
- A fresh-preferences launch confirmed that pre-disabling the applet prevented
  creation of `remmina-applet.desktop`; the generated file is absent from the
  Git index and ignored, so it will not be deployed with the dotfiles.
- Both services were disabled/stopped after testing, the temporary viewer/server
  exited, the secondary monitor was removed, and Wi-Fi routes were unchanged.
- The other PC's old certificate was confirmed to fail with an IP address
  mismatch for its Tailscale address. That PC needs the updated server scripts
  and another setup run before remote login/video can be verified.
