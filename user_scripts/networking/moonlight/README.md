# Moonlight secondary display

This script uses **Sunshine**, not the separate Moonshine project. It creates
the `DUSKY-MOONLIGHT` Hyprland monitor to the right of the desktop and streams
it to a Moonlight client on a phone, tablet or another PC.
Run setup on the Linux/Hyprland server; the receiving device can use iOS,
Android, Windows, macOS or Linux. Landscape is 1280×720; portrait is 720×1280. Audio
streaming stays disabled, matching the existing display-only workflow.

```sh
./moonlight_setup.py
./moonlight_setup.py status
./moonlight_setup.py orientation portrait
./moonlight_setup.py orientation landscape
./moonlight_setup.py stop
```

On first run, setup creates `dusky_moonlight_display.service` in
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`, reloads systemd, and enables
and starts it. No manual unit deployment is needed. The service TUI discovers
and toggles the installed unit after setup.

Direct flags work too:

```sh
./moonlight_setup.py --status
./moonlight_setup.py --pair
./moonlight_setup.py --reconnect
./moonlight_setup.py --diagnose
./moonlight_setup.py --test-display
./moonlight_setup.py --clients
./moonlight_setup.py --forget-client CLIENT_ID
./moonlight_setup.py --forget-all
./moonlight_setup.py --orientation portrait
./moonlight_setup.py --stop
```

`--reconnect` restarts the stream without removing clients. `--clients` lists
the exact IDs accepted by `--forget-client`. Removing clients briefly stops
Sunshine to avoid concurrent state writes, preserves web UI credentials and
other clients, saves a backup, and resumes only if it was already running.
Also remove the old host in Moonlight before fresh pairing. `--forget-all`
explicitly removes every saved client. These controls affect the PC's Sunshine
state; they cannot delete entries inside the receiving device's Moonlight client.

`--diagnose` reports service state, default route, protocol response, monitor
workspace and duplicate pairings, with specific next steps. `--test-display`
uses kitty to put a bright window on the streamed workspace; close the window
normally after testing. Kitty is optional for streaming itself.

Run as the desktop user in Hyprland. Required packages are Sunshine,
python-rich, Hyprland, systemd and iproute2, plus the GPU drivers/encoding
libraries and input-device access supplied by the Sunshine package. Include
Sunshine and its dependencies in the offline ISO payload. When Sunshine is
missing, setup installs it from a configured pacman repository, or falls back
to AUR `sunshine-bin` using Paru/Yay as the normal user. If neither helper is
installed, setup installs Git/base-devel with sudo and builds/installs Paru
using unprivileged `makepkg -si`. Only package installation uses elevated
privileges; the script and Sunshine stay owned by the desktop user.
Use `--package /path/to/sunshine.pkg.tar.zst` for a local Sunshine package.
Missing Hyprland/systemd/iproute2, Rich, VA-API tools and browser-opening tools
are installed with `sudo pacman -S --needed`. Existing credentials and pairings
are retained. The generated config/settings honor `XDG_CONFIG_HOME`.
Automatic downloads require working repositories/AUR and internet access.
Setup uses existing pacman databases; it does not refresh them or perform a
system upgrade. An offline ISO should still include packages and dependencies.

Install Moonlight on the receiving device, using the
[official downloads page](https://moonlight-stream.org/): **Moonlight Game
Streaming** from App Store/Google Play for iPhone/Android, or the Moonlight
desktop client for Windows/macOS/Linux. Connect the server and receiving device
to the same Wi-Fi or Ethernet network, then add the server IP displayed by
`--status` in Moonlight, with no port suffix. On the **streaming server**, open
`https://localhost:47990`, set up/sign in to Sunshine's web UI, and enter the PIN
shown by the receiving device's Moonlight client. Local streaming needs no SIM,
mobile data or internet connection.
Launch **Desktop**. The separate monitor can initially be empty.
An empty workspace may be entirely black. Move a window there or use
`--test-display`; this streams a secondary display rather than mirroring the
server's physical screen.
Automatic discovery requires the system's Avahi infrastructure; adding the
IP manually works without it. Setup does not enable an extra discovery daemon.

The Rich "Connect another device" guide includes mobile and desktop clients.
It shows the current server IP, local-network permission on iPhone, web UI
account/PIN steps, and the separate-monitor behavior. Downloading the client needs
internet access; local streaming afterwards does not.

For remote access, install/sign in to Tailscale on the server and receiving
device, join the same tailnet, and allow the connection in its access rules.
Add the **server's Tailscale IP** in Moonlight; `--status` shows it when available.
Moonlight setup does not install or configure Tailscale automatically.
Remote access requires internet. A working local Wi-Fi, Ethernet, hotspot or
tethering network can carry streaming without internet. The same setup handles
PC-to-PC connections; desktop-client and remote streaming remain untested here.

When setup finishes with no saved clients, it automatically opens Sunshine's
local web UI in the PC's default browser. `--pair` always opens it and shows
the connection guide and IP again. Repeated setup with saved clients, status
checks and service startup do not open browser tabs. Missing/failed browser
launch falls back to the exact URL for manual opening. The browser may show
a certificate warning for Sunshine's local self-signed certificate.

Sunshine 2026.914 can store the same client certificate repeatedly when
re-pairing, then reject it as "Client certificate identity is not enabled".
Setup detects duplicates and restarts the service to repair them before
Sunshine starts. Credentials and other clients remain intact; the original
state is backed up as `sunshine_state.json.before-dedup`. Disabled identities
stay disabled. If this version fails after entering another PIN, rerun setup
and retry the host before pairing again. The supplied newer source fixes the
duplicate insertion inside Sunshine itself.

The service is the on/off switch:

```sh
systemctl --user enable --now dusky_moonlight_display.service
systemctl --user disable --now dusky_moonlight_display.service
```

Plain enable/disable affects startup; `--now` also starts/stops the running
service. When stopped, Sunshine exits and its owned monitor is removed.
Python replaces itself with Sunshine after setup; no Python supervisor remains.
Crash restarts back off from 5 to 30 seconds. Cleanup keeps ownership state if
Hyprland is temporarily unreachable or monitor removal fails, allowing recovery.

Setup prioritizes UFW TCP 47984/47989/48010 and UDP 47998–48000 allowances,
ahead of UFW user denies. Other ports and default policies are preserved.
Custom before-rules, independent firewalls and Wi-Fi client isolation are
outside this automatic configuration. Web UI port 47990 is used locally for
pairing; setup does not add a LAN allowance for it.

Wi-Fi setup does not alter NetworkManager profiles. Optional `usb` explicitly
prepares an iPhone USB profile matched by the `ipheth` driver, with no IPv4
default route or automatic DNS and IPv6 disabled. It requires NetworkManager.
USB hardware activation was not exercised during this audit.

Sunshine's source supports the stable output name, so `output_name` remains
`DUSKY-MOONLIGHT`. For connected Intel/AMD graphics, setup probes H.264 High
encoding with `vainfo` and selects both VA-API and the tested render node.
Otherwise Sunshine performs its normal encoder selection. GPU identifiers are
discovered each setup rather than fixed to a machine. Installed Sunshine
2026.914.233613 and the supplied Sunshine master source were checked; the
development checkout was not built or installed.

## Audit verification, 2026-10-01

- Thirty-one focused regressions passed: first-run service deployment into an
  empty directory, GameStream responses/errors/timeouts,
  settings reads, cleanup recovery, config preservation/idempotence, firewall
  idempotence, setup leaving USB profiles alone, and duplicate certificate
  repair preserving credentials, backups and disabled clients, and setup
  restarting only when that repair is needed, client removal preserving state
  and service state, recovery after a failed write, action-flag dispatch,
  first-setup browser launch, skipping it for saved clients, and browser-failure
  fallback to manual pairing, repository/AUR installation selection, batched
  dependency installation, unprivileged helper bootstrap and install failures.
  Package installation paths were tested with fixtures; installed dependencies
  were preserved on this machine rather than removed/reinstalled for testing.
- Live portrait and landscape dimensions, complete service off/on, and forced
  SIGKILL recovery passed. Off left zero main PID, no HTTP listener, and no
  owned monitor/state file.
- With the actual service file temporarily absent, `--setup` recreated it,
  enabled/started Sunshine and reached Ready. Afterwards the service was
  disabled/stopped again and its virtual monitor and ownership record were gone.
- Four isolated UFW scenarios passed: default deny, broad deny, equivalent
  grouped denies, and allowances behind denies. All three streaming TCP and
  all three UDP ports were reachable; unrelated TCP/UDP ports stayed blocked.
  Repeating configuration left the rule list unchanged. Tests used copied
  firewall files in separate mount/network namespaces.
- 200 GameStream server-info requests with 16 workers passed in 0.076 seconds.
  This checks HTTP handling, not video throughput or latency.
- Sunshine successfully probed H.264 and HEVC VA-API encoding on this machine.
- Wi-Fi stayed connected and IPv4 routes remained unchanged.
- After repairing the iPhone pairing, Sunshine logged `CLIENT CONNECTED`
  and started HEVC VA-API streaming at the client's requested 30 fps.
  The iPhone user confirmed the bright test window was visible and interactive.
  The earlier black screen was an empty monitor workspace. Live `--diagnose`
  and `--test-display` checks passed without restarting the active stream.

```sh
python -m unittest discover -s user_scripts/networking/moonlight -p 'test_*.py'
```
