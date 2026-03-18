# Hyprland Runtime CLI Reference (`hyprctl` and `Hyprland`)

> [!summary]
> This note is a corrected, script-safe reference for controlling and inspecting a running Hyprland session on Arch Linux, current as of **March 2026**.

> [!warning] Critical corrections
> - `hyprctl workspaces` lists **workspaces**, **not outputs/monitors**.
> - To list outputs, use `hyprctl monitors` or `hyprctl monitors all`.
> - `hyprctl activewindow` returns the **full focused-window record**, not just the title/name.
> - `hyprctl keyword ...` changes **runtime state only**; it does **not** rewrite `~/.config/hypr/hyprland.conf`.

## Tool roles

### `hyprctl`
`hyprctl` is the runtime control and inspection client for a **running** Hyprland instance.

Use it to:

- inspect state
- query windows, outputs, workspaces, devices, layers, binds
- reload config
- dispatch actions
- apply runtime-only config changes
- read logs and config errors

> [!important]
> `hyprctl` does **not** start Hyprland. It only talks to an already-running instance.

### `Hyprland`
`Hyprland` is the compositor binary itself.

Use it to:

- start a session
- verify a config file without starting a session
- print binary version information
- print system/build information
- enter safe mode or use advanced socket handover options

> [!note]
> The upstream binary name is `Hyprland`. If your system also provides a lowercase alias such as `hyprland`, it is functionally equivalent.

### UWSM context
When Hyprland is started through **UWSM** or a display manager, you normally do **not** launch `Hyprland` manually from inside an existing graphical session. In that setup:

- `hyprctl` works normally
- shells launched inside the session usually inherit the correct Hyprland environment automatically
- out-of-band shells, timers, and user services may need explicit instance targeting

---

## Instance targeting

`hyprctl` must know **which Hyprland instance** to talk to.

Inside a normal Hyprland session, this is usually automatic because the environment contains:

- `HYPRLAND_INSTANCE_SIGNATURE`
- `WAYLAND_DISPLAY`

To inspect running instances:

```bash
hyprctl instances
```

To target a specific instance explicitly:

```bash
hyprctl --instance <signature> monitors
hyprctl -i <index> monitors
```

Examples:

```bash
hyprctl -i 0 monitors
hyprctl --instance "$HYPRLAND_INSTANCE_SIGNATURE" activewindow
```

> [!tip]
> Prefer targeting by **instance signature** over numeric index when scripting. Index order is less stable and easier to mis-target.

> [!warning]
> If `hyprctl` reports that no instance is available, you are usually either:
> - outside the Hyprland session environment, or
> - targeting the wrong instance in a multi-session setup.

---

## Global `hyprctl` flags

| Flag | Meaning | Notes |
|---|---|---|
| `-j` | JSON output | Use this for scripting. Human-readable output is not a stable API. |
| `-r` | Refresh state after issuing command | Mainly useful around mutating commands. |
| `--batch` | Execute multiple commands separated by `;` | Good for atomic-ish runtime tweaks from shell scripts. |
| `--instance`, `-i` | Target a specific Hyprland instance | Accepts instance signature or index. |
| `--quiet`, `-q` | Suppress output | Useful in scripts when only exit status matters. |

---

## Quick reference

| Task | Command |
|---|---|
| Reload config | `hyprctl reload` |
| Reload config without monitor reprobe | `hyprctl reload config-only` |
| Show focused window | `hyprctl activewindow` |
| Show active workspace | `hyprctl activeworkspace` |
| List active outputs | `hyprctl monitors` |
| List active + inactive outputs | `hyprctl monitors all` |
| List workspaces | `hyprctl workspaces` |
| List normal windows/clients | `hyprctl clients` |
| List layer-shell surfaces | `hyprctl layers` |
| List input devices | `hyprctl devices` |
| Show config parse errors | `hyprctl configerrors` |
| Tail Hyprland log | `hyprctl rollinglog -f` |
| Show runtime version of running compositor | `hyprctl version` |
| Show installed binary version | `Hyprland --version` |
| Verify config without starting Hyprland | `Hyprland --verify-config` |

---

## High-value commands

### Reload and validation

#### Reload the running configuration

```bash
hyprctl reload
```

This reparses the config and reapplies it to the running instance.

#### Reload config without reloading monitor state

```bash
hyprctl reload config-only
```

Use this when you are changing non-monitor settings and want to avoid unnecessary monitor reprobe, reconfiguration, or display flicker.

> [!tip]
> Prefer `reload config-only` while tuning gaps, borders, animations, input, binds, or decoration settings.

#### Validate config without starting Hyprland

```bash
Hyprland --verify-config
```

To validate a specific file:

```bash
Hyprland --verify-config --config "$HOME/.config/hypr/hyprland.conf"
```

This is the safest way to catch syntax/config errors before restarting or launching a session.

#### Show current config parser errors

```bash
hyprctl configerrors
```

Use this immediately after a reload if something did not apply as expected.

---

### Inspect current session state

#### Focused window

```bash
hyprctl activewindow
```

Returns the focused window’s properties, not just its title.

Script-safe example:

```bash
hyprctl -j activewindow | jq -r '.title'
```

#### Active workspace

```bash
hyprctl activeworkspace
```

JSON example:

```bash
hyprctl -j activeworkspace | jq -r '.name'
```

#### Outputs / monitors

```bash
hyprctl monitors
```

Shows **active outputs** only.

To include inactive outputs too:

```bash
hyprctl monitors all
```

#### Workspaces

```bash
hyprctl workspaces
```

Lists current workspaces and their properties.

#### Clients / windows

```bash
hyprctl clients
```

Lists normal application windows known to Hyprland.

> [!note]
> Bars, OSDs, launchers, lock screens, and other layer-shell surfaces usually do **not** appear in `clients`; inspect those with `hyprctl layers`.

#### Layer-shell surfaces

```bash
hyprctl layers
```

Useful for diagnosing panels, overlays, launchers, notifications, lock screens, and other layer surfaces.

#### Input devices

```bash
hyprctl devices
```

Lists input devices recognized by Hyprland, including keyboards, pointers, and other supported input classes when present.

---

## Runtime actions

### Dispatch a Hyprland dispatcher

```bash
hyprctl dispatch <dispatcher> [args...]
```

Examples:

```bash
hyprctl dispatch workspace 2
hyprctl dispatch exec kitty
hyprctl dispatch fullscreen 1
```

Dispatcher names and arguments follow the same dispatcher model used in Hyprland keybinds.

> [!note]
> If you know a dispatcher from a `bind = ...` line, you can usually call it directly with `hyprctl dispatch`.

### Apply a config keyword dynamically

```bash
hyprctl keyword <name> <value>
```

Examples:

```bash
hyprctl keyword general:gaps_in 6
hyprctl keyword decoration:rounding 8
hyprctl keyword monitor 'eDP-1,preferred,auto,1.25'
```

Important properties of `keyword`:

- applies at runtime immediately
- does **not** write to your config file
- may be replaced by the file value on the next reload
- requires correct quoting when the value contains spaces or commas

### Batch multiple commands

```bash
hyprctl --batch 'keyword general:gaps_in 6; keyword general:gaps_out 12; reload config-only'
```

This is the preferred way to apply several runtime updates from a script.

### Kill mode

```bash
hyprctl kill
```

Enters click-to-kill mode. Press `Escape` to leave kill mode.

### Set cursor theme and size

```bash
hyprctl setcursor Bibata-Modern-Ice 24
```

This reloads the cursor manager with the requested theme and size.

### Switch keyboard layout for a device

```bash
hyprctl switchxkblayout --help
```

This command exists, but the exact argument form depends on the targeted keyboard and layout index. Use its subcommand help before scripting it.

### Built-in notifications

```bash
hyprctl notify --help
hyprctl dismissnotify
```

> [!note]
> `hyprctl notify` uses **Hyprland’s built-in notification system**, not the freedesktop.org D-Bus notification protocol. It is not a replacement for `notify-send`.

---

## Script-safe JSON usage

For anything automated, use `-j` and parse JSON with `jq`.

> [!important]
> Do **not** parse the human-readable output of `hyprctl` in production scripts. It is for humans, not a stable machine interface.

### Focused window title

```bash
hyprctl -j activewindow | jq -r '.title'
```

### Active workspace name

```bash
hyprctl -j activeworkspace | jq -r '.name'
```

### List output names

```bash
hyprctl -j monitors all | jq -r '.[].name'
```

### List client address, class, and title

```bash
hyprctl -j clients | jq -r '.[] | "\(.address)\t\(.class)\t\(.title)"'
```

### Bash 5.3+ example: collect workspace names safely

```bash
#!/usr/bin/env bash
set -euo pipefail

mapfile -t workspace_names < <(
  hyprctl -j workspaces | jq -r 'sort_by(.id)[] | .name'
)

printf '%s\n' "${workspace_names[@]}"
```

### Bash 5.3+ example: guard against missing session context

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ -z ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
  printf 'No HYPRLAND_INSTANCE_SIGNATURE in environment.\n' >&2
  printf 'Available instances:\n' >&2
  hyprctl instances >&2
  exit 1
fi

hyprctl --instance "$HYPRLAND_INSTANCE_SIGNATURE" -j activewindow \
  | jq -r '.title'
```

---

## `hyprctl` command catalogue

### Query / inspection commands

| Command | Purpose | Notes |
|---|---|---|
| `activewindow` | Show focused window and its properties | Returns a full object/record, not just title. |
| `activeworkspace` | Show active workspace and its properties | Useful for status bars and scripts. |
| `animations` | Show configured animation and bezier data | Runtime inspection only. |
| `binds` | List all registered binds | Good for verifying generated/configured binds. |
| `clients` | List windows/clients and their properties | Application windows only. |
| `configerrors` | List current config parsing errors | Check after `reload`. |
| `cursorpos` | Show pointer position in global layout coordinates | Useful for automation/debugging. |
| `decorations <window_regex>` | Show decoration info for matching windows | Advanced window-state inspection. |
| `devices` | List connected input devices known to Hyprland | Includes more than just keyboards/mice on supported hardware. |
| `getoption <option>` | Query the current value of a config option | Example: `general:gaps_in`. |
| `getprop ...` | Query a window property | Use `hyprctl getprop --help` for exact syntax. |
| `globalshortcuts` | List registered global shortcuts | Useful when debugging shortcut integrations. |
| `instances` | List all running Hyprland instances | Use with `--instance`/`-i`. |
| `layers` | List layer-shell surfaces | Panels, lock screens, overlays, launchers, notifications, etc. |
| `layouts` | List available layouts | Includes plugin-provided layouts if loaded. |
| `monitors` | List active outputs and their properties | `monitors all` includes inactive outputs. |
| `rollinglog` | Print the compositor log tail | Supports `-f` / `--follow`. |
| `splash` | Show current splash | Mostly cosmetic/debug. |
| `systeminfo` | Show system/runtime info from the running instance | Requires a running compositor. |
| `version` | Show version/commit/flags of the running instance | Can differ from `Hyprland --version` after package upgrades until restart. |
| `workspacerules` | List workspace rules | Good for validating generated configs. |
| `workspaces` | List workspaces and their properties | Not a monitor/output listing command. |

### Action / mutation commands

| Command | Purpose | Notes |
|---|---|---|
| `dismissnotify [amount]` | Dismiss built-in Hyprland notifications | Optional count limit. |
| `dispatch <dispatcher> [args...]` | Invoke a dispatcher directly | Same dispatcher family used by keybinds. |
| `hyprpaper ...` | Send a request to `hyprpaper` | Requires relevant component/runtime support. |
| `hyprsunset ...` | Send a request to `hyprsunset` | Requires relevant component/runtime support. |
| `keyword <name> <value>` | Set a config keyword dynamically | Runtime only; not persisted to disk. |
| `kill` | Enter click-to-kill mode | Exit with `Escape`. |
| `notify ...` | Show built-in Hyprland notification | Not D-Bus desktop notification. |
| `output ...` | Manage fake/headless outputs on supported backends | See subcommand help. |
| `plugin ...` | Issue a plugin request | Exact behavior depends on loaded plugins. |
| `reload [config-only]` | Reload config | `config-only` skips monitor reload. |
| `setcursor <theme> <size>` | Change cursor theme and size at runtime | Reloads cursor manager. |
| `seterror <color> <message...>` | Set Hyprland error string | Clears on config reload. Debug use. |
| `setprop ...` | Set a window property | See subcommand help for exact syntax. |
| `switchxkblayout ...` | Change keyboard layout index for a keyboard | See subcommand help. |

### Subcommand help
Some commands are intentionally terse in top-level help. For exact argument syntax, ask the subcommand itself:

```bash
hyprctl dispatch --help
hyprctl output --help
hyprctl setprop --help
hyprctl getprop --help
hyprctl switchxkblayout --help
hyprctl notify --help
```

---

## `Hyprland` binary reference

### Most useful invocations

#### Show help

```bash
Hyprland --help
```

#### Show binary version

```bash
Hyprland --version
```

#### Show binary version in JSON

```bash
Hyprland --version-json
```

#### Show system/build information

```bash
Hyprland --systeminfo
```

This is useful for:

- build commit/tag
- library ABI/build info
- kernel details
- GPU identification
- OS release data

#### Verify config without launching

```bash
Hyprland --verify-config
```

#### Verify a specific config file

```bash
Hyprland --verify-config --config "$HOME/.config/hypr/hyprland.conf"
```

#### Start in safe mode

```bash
Hyprland --safe-mode
```

Use this for recovery/debugging when normal startup is broken.

---

## Advanced `Hyprland` startup flags

| Flag | Purpose | Notes |
|---|---|---|
| `--config FILE`, `-c FILE` | Use a specific config file | Useful for testing alternate configs. |
| `--socket NAME` | Set the Wayland socket name for socket handover | Advanced integration/handover use. |
| `--wayland-fd FD` | Use an inherited Wayland socket FD for handover | Not a normal manual-launch option. |
| `--watchdog-fd FD` | Used by startup/session wrappers | Typically not used directly. |
| `--safe-mode` | Start in safe mode | Recovery/debug path. |
| `--systeminfo` | Print system information | Does not require a running session. |
| `--verify-config` | Check config and exit | Safe preflight check. |
| `--i-am-really-stupid` | Skip root-user protection | Do not use for normal operation. |

> [!warning]
> `--socket`, `--wayland-fd`, and `--watchdog-fd` are **advanced startup/handover flags**. They are not the normal way to choose the control socket used by `hyprctl`.

> [!warning]
> Never run Hyprland as root in normal use. The existence of `--i-am-really-stupid` is not an endorsement.

---

## Version and package-state nuances

### Running instance version vs installed binary version

These are not always the same immediately after an upgrade.

- `hyprctl version` reports the **running compositor instance**
- `Hyprland --version` reports the **installed binary on disk**

After a package upgrade, they can differ until you fully restart the Hyprland session.

### Arch Linux partial-upgrade warning

If `Hyprland --systeminfo` or `hyprctl systeminfo` shows suspicious dependency mismatches such as:

- `built against X`
- `system has Y`

then on Arch that usually means your system is out of sync, or the running session predates the current package set.

> [!warning]
> Arch Linux does **not** support partial upgrades. If package/library versions look inconsistent:
> 1. run a full upgrade with `pacman -Syu`
> 2. log out completely
> 3. start a fresh Hyprland session

---

## Troubleshooting workflow

### `hyprctl` cannot find or reach an instance

```bash
echo "${HYPRLAND_INSTANCE_SIGNATURE:-<unset>}"
echo "${WAYLAND_DISPLAY:-<unset>}"
hyprctl instances
```

If necessary, target explicitly:

```bash
hyprctl --instance <signature> monitors
```

### Config changes did not apply

```bash
hyprctl reload
hyprctl configerrors
hyprctl rollinglog -f
```

### Need to validate before launch/restart

```bash
Hyprland --verify-config --config "$HOME/.config/hypr/hyprland.conf"
```

### Need more session diagnostics under UWSM/systemd

```bash
journalctl --user -b --grep='Hyprland|uwsm'
```

> [!tip]
> Use `hyprctl rollinglog -f` for compositor-side runtime issues and `journalctl --user -b` for user-session / UWSM / systemd integration issues.

---

## Recommended habits

1. **Use `-j` for all scripts.**
2. **Use `hyprctl monitors` for outputs, not `workspaces`.**
3. **Use `reload config-only` when you are not changing monitor definitions.**
4. **Use `Hyprland --verify-config` before restarting a broken session.**
5. **Target a specific instance explicitly in multi-session environments.**
6. **Treat `hyprctl` runtime changes as ephemeral unless you also edit the config file.**

---

## Minimal command set worth memorizing

```bash
hyprctl reload
hyprctl reload config-only
hyprctl activewindow
hyprctl activeworkspace
hyprctl monitors
hyprctl monitors all
hyprctl workspaces
hyprctl clients
hyprctl layers
hyprctl devices
hyprctl configerrors
hyprctl rollinglog -f
hyprctl version
Hyprland --verify-config
Hyprland --systeminfo
```

