# Bluetooth — Common Commands

> [!info] Verified scope
> Verified against local `man` / `--help` on this machine:
> - `bluetoothctl 5.87` — `man bluetoothctl` (March 2024) + `bluetoothctl --help` + interactive `help`
> - `bluetoothd` (BlueZ 5.87) — `man bluetoothd`, service `bluetooth.service`
> - `rfkill` (util-linux 2.42.3) — `man rfkill` (2026-09-02)
> - `blueman 2.4.6-2` — `man blueman-manager`, `blueman-applet`, `blueman-sendto`, `blueman-services`, `blueman-adapters`
> - `btmgmt 5.87` — `btmgmt --help` (no man page), `btmgmt info` tested read-only
> - `btmon` — `man btmon`
>
> Conventions in this note:
> - `<MAC>` = device address like `68:EF:43:71:44:E9`
> - `[bluetooth]#` = inside interactive `bluetoothctl`
> - `bluetoothctl <cmd>` = one-shot from shell, no prompt needed

---

## Core Model

`bluetoothctl` is a frontend. `bluetoothd` owns the state over D-Bus (`org.bluez.Adapter`, `org.bluez.Device`).

- **Controller / adapter** = your radio (`hci0`, `0C:9A:3C:AD:51:14`)
- **Device** = remote thing (`dusk`, headphones, phone), addressed by `<MAC>`

| Flag | Meaning |
| :--- | :--- |
| `Paired` | pairing completed (re-running `pair` first removes old pairing) |
| `Bonded` | long-term keys stored; filter for `devices Bonded` |
| `Trusted` | auto-accept future connections; `untrust` = require approval |
| `Blocked` | rejected by BlueZ; cannot connect until `unblock` |
| `Connected` | active link right now |

> [!note]
> `info <MAC>` is the source of truth. Blueman icons mirror these flags: check `info` before trusting the GUI.

---

## 1. Service and Radio Kill-Switch First

If there is no adapter, the toggle does nothing, or scans return nothing, check these before touching devices.

```bash
systemctl status bluetooth
sudo systemctl enable --now bluetooth
sudo systemctl restart bluetooth

rfkill list bluetooth
sudo rfkill unblock bluetooth
```

| Action | Command | Description |
| :--- | :--- | :--- |
| Show state | `rfkill list [bluetooth\|all]` | soft + hard block per radio |
| Scriptable list | `rfkill --output ID,TYPE,SOFT,HARD` | stable columns; other columns: `DEVICE`, `TYPE-DESC` |
| Enable | `sudo rfkill unblock bluetooth` | clears soft-block |
| Disable | `sudo rfkill block bluetooth` | disables radio |
| Flip | `sudo rfkill toggle bluetooth` | enable if disabled and vice versa |
| Watch events | `rfkill event` | live radio kill events |

> [!warning] Hard-block vs soft-block
> `unblock` cannot fix a hard-block (physical switch, Fn-key, BIOS). If `rfkill list` shows `HARD: blocked`, fix hardware first.
>
> Valid `rfkill` types per `man rfkill`: `all`, `wlan`/`wifi`, `bluetooth`, `uwb`, `wimax`, `wwan`, `gps`, `fm`, `nfc`.

> [!tip]
> Config lives in `/etc/bluetooth/main.conf` (`man bluetoothd`). Service is `bluetooth.service` → `ExecStart=/usr/lib/bluetooth/bluetoothd`, `Type=dbus`.

---

## 2. `bluetoothctl` Basics

```bash
bluetoothctl                 # interactive: [bluetooth]#
bluetoothctl show            # one-shot, same syntax
bluetoothctl info <MAC>
bluetoothctl --timeout 10 scan on   # timeout for non-interactive use

# automation: heredoc or script file
bluetoothctl <<EOF
power on
scan on
EOF

bluetoothctl script run.txt  # from inside: script <filename>
```

| Command | Description |
| :--- | :--- |
| `help` | list commands; `menu <name>` enters a submenu (`advertise`, `scan`, `gatt`, `player`, etc.) |
| `version` | show version |
| `quit` / `exit` | leave interactive mode |
| `bluetoothctl --agent <cap> --timeout <sec> --monitor` | startup flags per `--help`: register agent, set timeout, enable monitor output |

---

## 3. Adapter

```bash
bluetoothctl list
bluetoothctl show
bluetoothctl select 0C:9A:3C:AD:51:14
bluetoothctl power on
bluetoothctl pairable on
bluetoothctl discoverable on
```

| Command | Description |
| :--- | :--- |
| `list` | list controllers |
| `show [ctrl]` | controller props: `Powered`, `Discoverable`, `Pairable`, alias |
| `select <ctrl>` | pick default controller (multi-radio only) |
| `power <on/off>` | power controller |
| `pairable <on/off>` | accept or reject pairing requests |
| `discoverable <on/off>` | let others discover you |
| `discoverable-timeout [sec]` | no arg = show; `0` = unlimited |
| `system-alias <name>` | set controller alias; quote names with spaces |
| `reset-alias` | reset controller alias (usually hostname) |

---

## 4. Scan, List, Inspect

```bash
bluetoothctl scan on
# wait 10-15 s, then:
bluetoothctl devices
bluetoothctl scan off
bluetoothctl info 68:EF:43:71:44:E9
```

| Command | Description |
| :--- | :--- |
| `scan <on/off/bredr/le>` | `on` = LE + Classic; `le` / `bredr` = one bearer only; `off` stops |
| `devices [Paired/Bonded/Trusted/Connected]` | list known devices, optionally filtered |
| `info [dev]` | full device state: `Paired`, `Trusted`, `Blocked`, `Connected`, UUIDs |

> [!tip]
> For LE devices, keep a scan running before `pair` / `connect`. No scan report = `le-connection-abort-by-local` on connect.

Object paths also work wherever `<MAC>` works:

```bash
bluetoothctl info /org/bluez/hci0/dev_68_EF_43_71_44_E9
```

---

## 5. Pairing (Agent Required)

```bash
bluetoothctl agent on
bluetoothctl default-agent
bluetoothctl scan on
bluetoothctl pair 68:EF:43:71:44:E9
```

| Command | Description |
| :--- | :--- |
| `agent <on/off/auto/capability>` | register auth handler. Capabilities: `DisplayOnly`, `DisplayYesNo`, `KeyboardDisplay`, `KeyboardOnly`, `NoInputNoOutput` |
| `default-agent` | make current agent the default; run after `agent on` |
| `pair [dev]` | pair; if already paired, old pairing is removed first |
| `cancel-pairing [dev]` | abort in-progress pairing |

> [!note]
> If `[dev]` is omitted, the command applies to the currently selected device in interactive mode. Prefer explicit `<MAC>` in scripts.
>
> Typical picks: phones / headsets → `DisplayYesNo`; keyboards → `KeyboardOnly`; headless → `NoInputNoOutput`.

---

## 6. Trust, Block, Connect, Remove

```bash
bluetoothctl trust 68:EF:43:71:44:E9
bluetoothctl connect 68:EF:43:71:44:E9
bluetoothctl disconnect 68:EF:43:71:44:E9
bluetoothctl unblock 68:EF:43:71:44:E9
bluetoothctl set-alias "My Headphones"
bluetoothctl remove 68:EF:43:71:44:E9
```

| Command | Description |
| :--- | :--- |
| `trust [dev]` / `untrust [dev]` | trust = auto-connect allowed; untrust = manual approval |
| `block [dev]` / `unblock [dev]` | set BlueZ `Blocked`; blocked devices cannot connect |
| `connect <dev> [uuid]` | connect all auto-connectable profiles, or one profile: `a2dp-sink`, `a2dp-source`, `hfp-hf`, `hfp-ag`, `ftp`, `spp`, or UUID (`0x110E`, full 128-bit) |
| `disconnect [dev] [uuid]` | disconnect all profiles, or one profile |
| `set-alias <alias>` | rename device alias |
| `remove <dev>` | forget device; `<dev>` is required here |

> [!important] Full reset order
> For a misbehaving device: `disconnect` → `remove` → `scan on` → `pair` → `trust` → `connect`.

Niche but real (from interactive `help` / `man bluetoothctl`):

| Command | Description |
| :--- | :--- |
| `advertise <on/off/peripheral/broadcast>` | LE advertising mode; details in `man bluetoothctl-advertise` |
| `bearer <dev> [last-seen/bredr/le]` | get or force preferred bearer for dual-mode devices |
| `wake [dev] [on/off]` | get or set wake support |

---

## 7. Blocked-Device Fix

Symptom: `info` shows `Blocked: yes`, Blueman shows a red block badge, connect fails.

```bash
bluetoothctl unblock 68:EF:43:71:44:E9
bluetoothctl info 68:EF:43:71:44:E9 | grep -E "Blocked|Trusted|Paired|Connected"
```

Blueman equivalent: select device → `Device > Unblock`, or Right-click → `Unblock`.

> [!tip]
> If the badge stays after `unblock`, quit and reopen Blueman. State in `bluetoothctl info` wins over the icon.

---

## 8. Blueman GUI

| Command | Description |
| :--- | :--- |
| `blueman-manager` | device manager window (no CLI options per man) |
| `blueman-applet` | tray applet (no CLI options per man) |
| `blueman-adapters [hci0]` | adapter properties dialog; optional `hci0`, `hci1`, … tab selector |
| `blueman-services` | graphical dialog for local services (audio, network, transfer) |
| `blueman-sendto [--device=MAC] [files…]` | OBEX file sender; without `--device` shows a picker |

---

## 9. Low-Level Debug: `btmgmt`, `btmon`

`btmgmt` speaks kernel MGMT directly. Read-only queries work as user; state changes need root.

```bash
btmgmt info
btmgmt con
sudo btmgmt power on
sudo btmgmt discov on
sudo btmgmt find
sudo btmgmt stop-find
sudo btmgmt pair 68:EF:43:71:44:E9
sudo btmgmt unpair 68:EF:43:71:44:E9
sudo btmgmt block 68:EF:43:71:44:E9
sudo btmgmt unblock 68:EF:43:71:44:E9

sudo btmon
sudo btmon -w trace.btsnoop
btmon -r trace.btsnoop
```

| Command | Description |
| :--- | :--- |
| `btmgmt info` | controller info and current settings |
| `btmgmt con` | list MGMT-level connections |
| `btmgmt find` / `stop-find` | MGMT discovery start / stop |
| `btmgmt power/discov/connectable/bondable/pairable <on/off>` | low-level toggles; prefer `bluetoothctl` unless debugging |
| `btmgmt pair/unpair/block/unblock <MAC>` | low-level device actions |
| `btmon -w <file>` / `-r <file>` | capture / replay HCI traces in `btsnoop` format (Wireshark-readable) |

> [!warning]
> Prefer `bluetoothctl` for daily use. `btmgmt` bypasses `bluetoothd` policy and can leave BlueZ state surprising.

---

## 10. Quick Recipes

```bash
# reconnect known headphones from scratch
bluetoothctl power on
bluetoothctl remove 4C:87:5D:A7:EE:3E
bluetoothctl scan on    # wait, then:
bluetoothctl scan off
bluetoothctl pair 4C:87:5D:A7:EE:3E
bluetoothctl trust 4C:87:5D:A7:EE:3E
bluetoothctl connect 4C:87:5D:A7:EE:3E

# expose PC to phone for 2 minutes
bluetoothctl discoverable-timeout 120
bluetoothctl discoverable on
bluetoothctl pairable on

# audio sink only
bluetoothctl connect 4C:87:5D:A7:EE:3E a2dp-sink

# health check
rfkill list bluetooth
bluetoothctl show
bluetoothctl devices Connected
```

---

## Further Reading

- `man bluetoothctl` (+ `man bluetoothctl-scan`, `bluetoothctl-advertise`, `bluetoothctl-gatt`, `bluetoothctl-player`)
- `man bluetoothd` + `/etc/bluetooth/main.conf`
- `man rfkill`
- `man btmon`
- `man blueman-manager`, `man blueman-sendto`, `man blueman-services`
- `btmgmt --help`, `bluetoothctl --help`
