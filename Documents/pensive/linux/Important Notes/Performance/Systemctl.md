# `systemctl` on Arch Linux

`systemctl` is the administrative interface to `systemd`, the init system and service manager used by Arch Linux. It controls unit lifecycle, enablement, dependency wiring, targets, timers, sockets, and both system-wide and per-user service managers.

This reference is written for day-to-day administration, troubleshooting, and safe automation.

> [!note] `systemctl` manages **managers**, not just services
> `systemd` has two commonly used manager scopes:
>
> - **System manager** (`PID 1`): controls system units such as `NetworkManager.service`, `sshd.service`, mounts, sockets, timers, and targets.
> - **User manager** (`systemd --user`): controls per-user units such as `pipewire.service`, `wireplumber.service`, portals, notification daemons, and user timers.
>
> Use:
>
> ```bash
> systemctl ...
> sudo systemctl ...
> systemctl --user ...
> ```
>
> Administrative rights are usually required for the system manager.  
> System units are **not necessarily run as root**; they are managed by PID 1 and may run as `root`, a dedicated service user, or another configured account.

---

## Core model

### Unit types you will actually use

Common unit types:

- `.service` — long-running or one-shot services
- `.socket` — socket activation
- `.timer` — scheduled activation
- `.path` — filesystem path activation
- `.mount` / `.automount` — mount points
- `.target` — synchronization groups / boot states
- `.slice` — cgroup resource grouping
- `.scope` — externally created process groups

### Three different state concepts

Do not confuse these:

1. **Load state** — was the unit definition loaded successfully?
   - Example: `loaded`, `not-found`, `masked`, `error`

2. **Runtime state** — what is it doing now?
   - Example: `active`, `inactive`, `failed`, `activating`, `deactivating`
   - Detailed substate depends on unit type: `running`, `exited`, `dead`, `waiting`, etc.

3. **Enablement state** — should it start automatically when its activation path occurs?
   - Example: `enabled`, `disabled`, `static`, `masked`, `generated`, `indirect`

A unit can be:

- **enabled but currently inactive**
- **running but not enabled**
- **static** and still used normally via dependencies
- **masked**, which blocks all activation

### Common unit file locations on Arch

Packaged units normally live in:

- System units: `/usr/lib/systemd/system/`
- User units: `/usr/lib/systemd/user/`

Administrator overrides typically live in:

- System: `/etc/systemd/system/`
- User: `~/.config/systemd/user/`
- Global user config: `/etc/systemd/user/`

Inspect the effective on-disk definition with:

```bash
systemctl cat <unit>
systemctl --user cat <unit>
```

### Templates and instances

A template unit looks like:

```text
foo@.service
```

An instantiated unit looks like:

```text
foo@bar.service
```

`list-units` shows **loaded instances**, not uninstantiated templates.  
`list-unit-files` shows installed unit files, including templates.

---

## System manager vs user manager

### System manager

Use this for machine-wide services:

```bash
sudo systemctl status NetworkManager.service
sudo systemctl enable --now sshd.service
```

### User manager

Use this for per-user services:

```bash
systemctl --user status pipewire.service
systemctl --user enable --now syncthing.service
```

By default, the user manager usually exists while the user is logged in. To keep it running after logout, enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

Disable lingering:

```bash
sudo loginctl disable-linger "$USER"
```

> [!warning] `sudo systemctl --user ...` is usually the wrong command
> `sudo systemctl --user ...` talks to **root's** user manager, not yours.
>
> Correct patterns:
>
> ```bash
> systemctl --user status pipewire.service
> ```
>
> From root, target another user's user manager explicitly:
>
> ```bash
> sudo systemctl --user -M alice@.host status pipewire.service
> ```

---

## Everyday inspection

### Human-readable status

```bash
systemctl status <unit>
systemctl --user status <unit>
```

Shows:

- load state
- enablement state
- active state / substate
- main PID
- recent log lines
- cgroup / process tree

Example:

```bash
systemctl status NetworkManager.service
systemctl --user status wireplumber.service
```

### List loaded units

```bash
systemctl list-units
```

Shows units currently loaded in memory that are:

- active
- have pending jobs
- or have failed

Filter by type:

```bash
systemctl list-units --type=service
systemctl list-units --type=service --state=running
systemctl list-units --type=service --state=failed
```

Show loaded inactive units too:

```bash
systemctl list-units --all
```

> [!note]
> `list-units` does **not** show every installed unit on disk.  
> Use `list-unit-files` for that.

### List installed unit files

```bash
systemctl list-unit-files
systemctl --user list-unit-files
```

Useful filtered examples:

```bash
systemctl list-unit-files --state=enabled
systemctl list-unit-files '*.service' --state=enabled
systemctl --user list-unit-files '*.service' --state=enabled
```

### Machine-readable properties

Use `show` for scripts and exact state queries:

```bash
systemctl show <unit>
systemctl show -P ActiveState <unit>
systemctl show -P SubState <unit>
systemctl show -P UnitFileState <unit>
```

Examples:

```bash
systemctl show -P ActiveState sshd.service
systemctl show -P UnitFileState sshd.service
systemctl --user show -P FragmentPath pipewire.service
```

### Check state with exit codes

```bash
systemctl is-active <unit>
systemctl is-enabled <unit>
systemctl is-failed <unit>
```

Quiet form for scripts:

```bash
systemctl is-active --quiet sshd.service
systemctl is-failed --quiet sshd.service
```

> [!warning] `is-enabled` is not a pure boolean
> `systemctl is-enabled` may return success for states such as:
>
> - `enabled`
> - `alias`
> - `static`
> - `indirect`
> - `generated`
> - `transient`
>
> If you need the exact meaning, inspect the printed state or use:
>
> ```bash
> systemctl show -P UnitFileState <unit>
> ```

### Dependency inspection

```bash
systemctl list-dependencies <unit>
systemctl list-dependencies --reverse <unit>
```

Examples:

```bash
systemctl list-dependencies graphical.target
systemctl list-dependencies --reverse pipewire.service
```

### Timers, sockets, paths, automounts

```bash
systemctl list-timers --all
systemctl list-sockets
systemctl list-paths
systemctl list-automounts
```

---

## Lifecycle management

### Start, stop, restart

```bash
sudo systemctl start <unit>
sudo systemctl stop <unit>
sudo systemctl restart <unit>
```

User manager equivalents:

```bash
systemctl --user start <unit>
systemctl --user stop <unit>
systemctl --user restart <unit>
```

### Reload configuration of the service process

```bash
sudo systemctl reload <unit>
sudo systemctl reload-or-restart <unit>
sudo systemctl try-restart <unit>
```

Use cases:

- `reload` — ask the service itself to re-read its own config
- `reload-or-restart` — reload if supported, otherwise restart
- `try-restart` — restart only if already running

> [!warning] `reload` is **not** `daemon-reload`
> These are different operations:
>
> - `systemctl reload foo.service`  
>   Reloads the **service application's own config**
>
> - `systemctl daemon-reload`  
>   Reloads **systemd unit definitions and dependency graph**

### Kill processes in a unit

```bash
sudo systemctl kill <unit>
sudo systemctl kill -s SIGUSR1 <unit>
sudo systemctl kill --kill-whom=main <unit>
```

Useful when a service supports signal-driven behavior.

### Reset failure state

```bash
sudo systemctl reset-failed <unit>
sudo systemctl reset-failed
```

This clears the failed state and also resets start-rate limiting counters for the unit.

---

## Enablement, autostart, and masking

### Enable / disable

```bash
sudo systemctl enable <unit>
sudo systemctl disable <unit>
sudo systemctl enable --now <unit>
sudo systemctl disable --now <unit>
```

Semantics:

- `enable` — create activation symlinks defined by the unit's `[Install]` section
- `disable` — remove those symlinks
- `--now` — also start or stop the unit immediately

### Recreate vendor-default symlinks

```bash
sudo systemctl reenable <unit>
```

Useful when symlink layout or aliases became inconsistent.

### Presets

```bash
sudo systemctl preset <unit>
sudo systemctl preset-all
```

Applies preset policy from preset files. Useful in image-building or policy-driven environments; less commonly used in routine Arch desktop administration.

### Mask / unmask

```bash
sudo systemctl mask <unit>
sudo systemctl unmask <unit>
sudo systemctl mask --now <unit>
```

Masking creates a symlink to `/dev/null`, preventing all activation paths, including dependency-based activation.

> [!warning]
> `mask` is stronger than `disable`.
>
> - `disable` stops automatic start via installation symlinks
> - `mask` makes starting the unit impossible until it is unmasked

### Static units

Some units are `static` and cannot be enabled because they have no `[Install]` section.

Typical behavior:

- started manually
- pulled in by other units
- activated by `.socket`, `.timer`, or `.path` units

Check:

```bash
systemctl is-enabled <unit>
systemctl show -P UnitFileState <unit>
```

> [!tip] Enable the correct companion unit
> For activation-driven setups, enable the activating unit:
>
> - timer-based service → enable the `.timer`
> - socket-activated service → enable the `.socket`
> - path-activated service → enable the `.path`
>
> Example:
>
> ```bash
> sudo systemctl enable --now fstrim.timer
> ```

---

## Editing units safely

### Preferred method: `systemctl edit`

Create a drop-in override:

```bash
sudo systemctl edit <unit>
systemctl --user edit <unit>
```

This creates or edits:

- system: `/etc/systemd/system/<unit>.d/override.conf`
- user: `~/.config/systemd/user/<unit>.d/override.conf`

### Replace the whole unit file

```bash
sudo systemctl edit --full <unit>
```

Use this only when a drop-in is insufficient. A full override is harder to maintain across package updates.

### View the composed configuration

```bash
systemctl cat <unit>
```

This shows the main fragment plus drop-ins in the order systemd reads them.

### Revert to vendor defaults

```bash
sudo systemctl revert <unit>
```

This removes drop-ins and local overrides for vendor-provided units and also unmaskes the unit if it was masked.

### Scripted drop-in editing

```bash
sudo systemctl edit --drop-in=limits.conf --stdin myservice.service <<'EOF'
[Service]
CPUWeight=200
MemoryMax=1G
EOF
```

---

## When `daemon-reload` is required

Run this after **manually** creating, modifying, removing, or renaming unit files or drop-ins on disk:

```bash
sudo systemctl daemon-reload
systemctl --user daemon-reload
```

This causes systemd to:

- re-read unit files
- re-run generators
- rebuild the dependency tree

> [!note]
> You usually **do not** need to run `daemon-reload` after:
>
> - `systemctl edit`
> - `systemctl enable`
> - `systemctl disable`
> - `systemctl reenable`
> - `systemctl mask`
> - `systemctl unmask`
>
> Those commands normally reload manager configuration automatically unless `--no-reload` was used.  
> `systemctl edit --global` is a special case: it does not reload a current user manager.

### `daemon-reexec`

```bash
sudo systemctl daemon-reexec
```

Re-executes the systemd manager process while preserving state. This is rarely needed outside debugging or certain package-upgrade edge cases.

---

## Logging and troubleshooting

### First commands to run

```bash
systemctl --failed
systemctl status <unit>
journalctl -u <unit> -b -e
```

For user services:

```bash
systemctl --user --failed
systemctl --user status <unit>
journalctl --user -u <unit> -b -e
```

### `journalctl` patterns that matter

Current boot:

```bash
journalctl -u <unit> -b
journalctl --user -u <unit> -b
```

Follow logs live:

```bash
journalctl -fu <unit>
journalctl --user -fu <unit>
```

Jump to end:

```bash
journalctl -u <unit> -e
```

Show only recent entries:

```bash
journalctl -u <unit> --since "30 min ago"
```

Show boot-wide errors:

```bash
journalctl -b -p err..alert
```

### Exact failure properties

When `status` is not enough:

```bash
systemctl show -p Result -p ExecMainCode -p ExecMainStatus -p ActiveState -p SubState <unit>
```

Example:

```bash
systemctl show -p Result -p ExecMainStatus myservice.service
```

### Verify unit file syntax

For hand-written units, validate with:

```bash
systemd-analyze verify /etc/systemd/system/myservice.service
```

Also verify drop-ins or user units by passing their paths.

> [!warning]
> `systemctl status` is for humans.  
> `systemctl show` is for scripts and exact property inspection.

### Useful troubleshooting workflow

1. Check whether the system is degraded:
   ```bash
   systemctl is-system-running
   ```
2. List failed units:
   ```bash
   systemctl --failed
   ```
3. Inspect the failing unit:
   ```bash
   systemctl status <unit>
   ```
4. Read full logs:
   ```bash
   journalctl -u <unit> -b -e
   ```
5. If you edited unit files manually, reload definitions:
   ```bash
   sudo systemctl daemon-reload
   ```
6. Reset rate limits / failure state if needed:
   ```bash
   sudo systemctl reset-failed <unit>
   ```
7. Start again and watch logs:
   ```bash
   sudo systemctl start <unit>
   journalctl -fu <unit>
   ```

---

## Targets and boot state

### Default boot target

```bash
systemctl get-default
sudo systemctl set-default graphical.target
sudo systemctl set-default multi-user.target
```

### Switch targets immediately

```bash
sudo systemctl isolate multi-user.target
sudo systemctl isolate graphical.target
```

> [!warning] `isolate` is disruptive
> `isolate` stops units not part of the target you switch to.  
> Running it from a graphical session can terminate the session you are currently using.

### Rescue and emergency modes

```bash
sudo systemctl rescue
sudo systemctl emergency
```

Use only when intentionally entering maintenance states.

---

## Power and host-level actions

Common host commands:

```bash
sudo systemctl reboot
sudo systemctl poweroff
sudo systemctl halt
sudo systemctl suspend
sudo systemctl hibernate
sudo systemctl hybrid-sleep
sudo systemctl suspend-then-hibernate
```

Check overall system state:

```bash
systemctl is-system-running
systemctl is-system-running --wait
```

---

## User services on Arch Wayland / Hyprland / UWSM

On modern Arch Wayland setups, especially with **Hyprland** and **UWSM**, many desktop/session components are best managed as **user units** rather than shell-spawned background commands.

Typical user-managed services include:

- `pipewire.service`
- `wireplumber.service`
- `xdg-desktop-portal.service`
- compositor-specific portals such as `xdg-desktop-portal-hyprland.service`
- notification daemons
- clipboard managers
- sync agents
- user timers

Inspect them with:

```bash
systemctl --user status pipewire.service wireplumber.service
systemctl --user status xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
```

> [!tip] Prefer user units over compositor `exec-once` for long-lived daemons
> User units give you:
>
> - restart policy
> - logging in the journal
> - dependency ordering
> - clean stop/start behavior
> - easy inspection with `systemctl --user`

### Session-bound vs always-on user services

Choose the right lifecycle:

- **Always-on per-user background service**  
  Enable it in the user manager, optionally with lingering.

- **GUI-session-only service**  
  Bind it to the graphical session target instead of starting it globally at user-manager startup.

Under UWSM-backed sessions, `graphical-session.target` is the correct conceptual anchor for GUI-only user services.

### Environment handling for user services

User services do **not** automatically inherit your interactive shell setup from `.bashrc`, `.zshrc`, etc.

Prefer:

- `environment.d` files
- explicit unit `Environment=` / `EnvironmentFile=`
- UWSM-managed session environment
- targeted import of specific variables when necessary

If needed, import specific session variables into the user manager:

```bash
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE
```

> [!warning]
> Importing the entire client environment wholesale is deprecated and error-prone.  
> Import only the variables that are actually needed.

---

## Safe scripting patterns

### Do not parse `status`

Bad idea:

```bash
systemctl status sshd.service | grep running
```

Preferred:

```bash
systemctl is-active --quiet sshd.service
systemctl show -P ActiveState sshd.service
systemctl show -P SubState sshd.service
```

### Use explicit unit names

Prefer:

```bash
systemctl restart sshd.service
```

over:

```bash
systemctl restart sshd
```

Abbreviated names work, but explicit suffixes are clearer and safer in scripts.

### Use machine-readable output

Example Bash snippet:

```bash
#!/usr/bin/env bash
set -euo pipefail

unit=${1:?usage: unit-state UNIT}

active=$(systemctl show -P ActiveState -- "$unit")
sub=$(systemctl show -P SubState -- "$unit")
file_state=$(systemctl show -P UnitFileState -- "$unit")

printf '%s\t%s\t%s\t%s\n' "$unit" "$active" "$sub" "$file_state"
```

### Be careful with glob patterns

Patterns only match units currently loaded in memory.

Example:

```bash
systemctl stop 'sshd@*.service'
```

This only affects loaded matching instances. It does **not** mean “all possible installed instances”.

### Return codes are useful, but limited

`systemctl` uses LSB-style return codes. For robust automation:

- prefer `is-active --quiet`
- prefer `show -P ...`
- do not assume every nonzero code maps cleanly to one semantic state

---

## High-value commands to memorize

### Inspect

```bash
systemctl status <unit>
systemctl show -P ActiveState <unit>
systemctl cat <unit>
systemctl list-units --type=service
systemctl list-unit-files --state=enabled
systemctl --failed
journalctl -u <unit> -b -e
```

### Operate

```bash
sudo systemctl start <unit>
sudo systemctl stop <unit>
sudo systemctl restart <unit>
sudo systemctl reload-or-restart <unit>
sudo systemctl reset-failed <unit>
```

### Autostart / policy

```bash
sudo systemctl enable --now <unit>
sudo systemctl disable --now <unit>
sudo systemctl mask --now <unit>
sudo systemctl unmask <unit>
sudo systemctl reenable <unit>
```

### Edit safely

```bash
sudo systemctl edit <unit>
sudo systemctl revert <unit>
sudo systemctl daemon-reload
```

### User services

```bash
systemctl --user status <unit>
systemctl --user enable --now <unit>
systemctl --user list-unit-files --state=enabled
journalctl --user -u <unit> -b
sudo loginctl enable-linger "$USER"
```

---

## Quick reference table

| Task | Command |
|---|---|
| Show detailed status | `systemctl status <unit>` |
| Show exact property value | `systemctl show -P <property> <unit>` |
| View effective unit file and drop-ins | `systemctl cat <unit>` |
| List running services | `systemctl list-units --type=service --state=running` |
| List failed services | `systemctl list-units --type=service --state=failed` |
| List installed enabled unit files | `systemctl list-unit-files --state=enabled` |
| Start now | `sudo systemctl start <unit>` |
| Stop now | `sudo systemctl stop <unit>` |
| Restart now | `sudo systemctl restart <unit>` |
| Reload service config | `sudo systemctl reload <unit>` |
| Reload unit definitions | `sudo systemctl daemon-reload` |
| Enable for future boots/logins | `sudo systemctl enable <unit>` |
| Enable and start now | `sudo systemctl enable --now <unit>` |
| Disable future autostart | `sudo systemctl disable <unit>` |
| Prevent any activation | `sudo systemctl mask <unit>` |
| Undo mask | `sudo systemctl unmask <unit>` |
| Clear failure state | `sudo systemctl reset-failed <unit>` |
| Read logs for a unit | `journalctl -u <unit>` |
| Read logs for current boot only | `journalctl -u <unit> -b` |
| Follow logs live | `journalctl -fu <unit>` |
| Manage user unit | `systemctl --user ...` |
| Keep user manager alive after logout | `sudo loginctl enable-linger "$USER"` |

---

## Practical examples

### Enable common system services

```bash
sudo systemctl enable --now NetworkManager.service bluetooth.service sshd.service
```

### Check why a service failed

```bash
systemctl status sshd.service
journalctl -u sshd.service -b -e
systemctl show -p Result -p ExecMainStatus sshd.service
```

### Inspect user audio stack

```bash
systemctl --user status pipewire.service wireplumber.service
journalctl --user -u pipewire.service -b -e
```

### Edit a unit safely and reload automatically

```bash
sudo systemctl edit myservice.service
sudo systemctl restart myservice.service
```

### After manual on-disk edits

```bash
sudo systemctl daemon-reload
sudo systemctl restart myservice.service
```

---

## Best practices

- Prefer `systemctl edit` over manually copying packaged unit files.
- Prefer `show` or `is-active --quiet` in scripts; do not parse `status`.
- Enable the activating unit (`.timer`, `.socket`, `.path`) when applicable.
- Use `mask` only when you intentionally want to block all activation.
- Use user units for per-user desktop/session daemons.
- Under Hyprland/UWSM, prefer systemd user services over ad-hoc compositor startup lines for long-lived background processes.
- After hand-editing unit files on disk, run `daemon-reload`.
- Verify custom units with `systemd-analyze verify`.

---

## See local capabilities

Arch ships recent `systemd`, but exact available verbs and options depend on the installed version. Check locally:

```bash
systemctl --version
systemctl --help
man systemctl
man systemd.unit
man systemd.service
man systemd.timer
man journalctl
```
