# Managing XDG Autostart Entries on Arch Linux (Wayland / Hyprland / UWSM)

> [!note]
> To disable a package-provided XDG autostart entry for **your user only**, create a file with the **same basename** in `~/.config/autostart/` containing:
>
> ```ini
> [Desktop Entry]
> Hidden=true
> ```
>
> Example: `~/.config/autostart/blueman.desktop` overrides `/etc/xdg/autostart/blueman.desktop`.

> [!warning]
> **XDG autostart is not system boot.** It controls applications started for a **graphical user session** at login.  
> If something starts at actual boot time, inspect **systemd system services** instead:
>
> ```bash
> systemctl list-unit-files --state=enabled
> ```

## Scope

On Arch Linux, an application that “starts automatically” may come from several different mechanisms. Identify the correct one before changing anything.

| Mechanism | Scope | Common locations | Disable method |
|---|---|---|---|
| **XDG autostart** | Graphical login/session start | `~/.config/autostart/`, `/etc/xdg/autostart/` | Per-user override with `Hidden=true` |
| **systemd --user service** | User session | `~/.config/systemd/user/`, `/usr/lib/systemd/user/` | `systemctl --user disable --now ...` |
| **Hyprland config** | Compositor-managed session startup | `~/.config/hypr/hyprland.conf`, sourced fragments | Remove or change `exec`, `exec-once`, etc. |
| **systemd system service** | System boot / machine-wide background service | `/etc/systemd/system/`, `/usr/lib/systemd/system/` | `systemctl disable --now ...` |

> [!tip]
> In Hyprland sessions started through **UWSM**, applications may still appear as `systemd --user` services even when their source is an XDG autostart `.desktop` file. The correct override point is still the `.desktop` entry unless the app is actually managed by a native user unit.

---

## How XDG autostart works

### Standard directories

The XDG Autostart specification uses:

- User-specific directory:
  - `${XDG_CONFIG_HOME:-$HOME/.config}/autostart`
- System directories:
  - each entry in `${XDG_CONFIG_DIRS:-/etc/xdg}`, with `/autostart` appended

On a default Arch installation, this usually means:

- User: `~/.config/autostart`
- System: `/etc/xdg/autostart`

### Precedence and overrides

XDG config precedence is:

1. `~/.config/autostart`
2. each system config directory in `$XDG_CONFIG_DIRS`, from left to right

If multiple entries share the **same filename**, the higher-precedence file overrides the lower-precedence one.

Example:

- `~/.config/autostart/blueman.desktop`
- `/etc/xdg/autostart/blueman.desktop`

The user file wins because the basename is identical: `blueman.desktop`.

> [!important]
> The filename must match **exactly**.  
> `blueman.desktop` overrides `blueman.desktop`.  
> `blueman-disabled.desktop` overrides **nothing**.

### Keys that matter

Common `.desktop` keys relevant to autostart:

- `Hidden=true`
  - **Portable, spec-compliant way to disable** the entry
  - Prevents autostart of lower-precedence entries with the same basename
- `OnlyShowIn=...`
  - Start only in the listed desktop environments
- `NotShowIn=...`
  - Do not start in the listed desktop environments
- `TryExec=...`
  - Skip autostart if the specified executable is missing
- `Exec=...`
  - Command to execute

### Hyprland / Wayland implications

In a Hyprland session:

- `XDG_CURRENT_DESKTOP` is typically `Hyprland`
- An entry such as `OnlyShowIn=GNOME;KDE;` will **not** autostart in Hyprland
- An entry with `NotShowIn=Hyprland;` will also **not** autostart

Check your current desktop identifier with:

```bash
printf '%s\n' "${XDG_CURRENT_DESKTOP:-unset}"
```

> [!note]
> If a file exists in `/etc/xdg/autostart` but has `OnlyShowIn=` that excludes Hyprland, it was never the cause of startup in your Hyprland session.

### What **not** to use as a disable method

These are commonly misunderstood:

- `NoDisplay=true`
  - Hides an app from menus; does **not** disable autostart
- `X-GNOME-Autostart-enabled=false`
  - GNOME-specific; not portable across environments

For a reliable per-user disable, use:

```ini
[Desktop Entry]
Hidden=true
```

---

## Disable a single XDG autostart entry

### Recommended method: minimal user override

For simply disabling an autostart entry, **do not copy the full system file unless you need to modify other keys**.

A minimal override is better because it:

- avoids carrying stale upstream settings
- survives package updates more cleanly
- is easier to audit
- is exactly what the spec expects for a user-level disable

### Example: disable `blueman.desktop`

#### 1. Confirm the system entry exists

```bash
ls -1 /etc/xdg/autostart | grep -Fx 'blueman.desktop'
```

Optional: inspect the entry first.

```bash
grep -E '^(Name|Exec|OnlyShowIn|NotShowIn|TryExec|Hidden)=' \
  /etc/xdg/autostart/blueman.desktop
```

#### 2. Create the user autostart directory

```bash
install -d -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
```

#### 3. Create the override file with the same basename

```bash
cat > "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop" <<'EOF'
[Desktop Entry]
Hidden=true
EOF
```

#### 4. Optional: validate the file

Requires `desktop-file-utils`.

```bash
desktop-file-validate "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop"
```

#### 5. Apply the change

For a clean test, **log out and log back in**.

If the application is already running, stop it manually in the current session.

> [!tip]
> On many modern systemd-based sessions, `.desktop` autostarts may be materialized as generated `systemd --user` units. The `Hidden=true` override still remains the correct fix.

---

## Re-enable an entry

Remove the user override:

```bash
rm -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop"
```

After the next login, the system entry from `/etc/xdg/autostart/blueman.desktop` will be effective again.

---

## When copying the full file is appropriate

Copy the full `.desktop` file only if you intend to modify behavior such as:

- `Exec=`
- `OnlyShowIn=`
- `NotShowIn=`
- `TryExec=`

Example:

```bash
install -d -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
cp -- /etc/xdg/autostart/blueman.desktop \
  "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/"
```

Then edit the copied file and add or change keys **inside the existing** `[Desktop Entry]` section.

> [!warning]
> Do **not** add a second `[Desktop Entry]` section to an existing file.  
> `.desktop` files are INI-style files; duplicate sections are not the correct way to override keys.

---

## Safer bulk audit of XDG autostart entries

Before disabling everything, review what exists:

```bash
IFS=: read -r -a xdg_config_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"

for dir in "${xdg_config_dirs[@]}"; do
  autostart_dir="$dir/autostart"
  [[ -d $autostart_dir ]] || continue
  find "$autostart_dir" -maxdepth 1 -type f -name '*.desktop' -print
done | sort
```

You can also inspect which entries are desktop-specific:

```bash
grep -H -E '^(OnlyShowIn|NotShowIn)=' /etc/xdg/autostart/*.desktop 2>/dev/null
```

> [!warning]
> Blindly disabling **all** XDG autostarts can break parts of the graphical session, including:
>
> - Bluetooth applets
> - network tray applets
> - notification daemons
> - polkit agents
> - secret/keyring helpers
> - clipboard managers
>
> In Hyprland, this can make the session appear “mostly fine” while silently breaking important integration.

---

## Bulk-disable all system XDG autostarts for the current user

The following Bash script creates minimal per-user `Hidden=true` override stubs for every unique system autostart entry it finds in `${XDG_CONFIG_DIRS:-/etc/xdg}`.

- Uses robust filename handling
- Honors XDG config directory order
- Skips entries you already override, unless `--force` is used
- Supports `--dry-run`

```bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: disable-all-xdg-autostarts.sh [--dry-run] [--force]

  --dry-run   Show what would be disabled without writing files
  --force     Overwrite existing user overrides in ~/.config/autostart
EOF
}

dry_run=0
force=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --force)   force=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$arg" >&2
      usage >&2
      exit 64
      ;;
  esac
done

target_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
IFS=: read -r -a config_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"

declare -A seen=()

if (( ! dry_run )); then
  install -d -- "$target_dir"
fi

for cfg_dir in "${config_dirs[@]}"; do
  autostart_dir="$cfg_dir/autostart"
  [[ -d $autostart_dir ]] || continue

  while IFS= read -r -d '' file; do
    base=${file##*/}

    # Respect XDG_CONFIG_DIRS precedence: first match wins.
    if [[ ${seen[$base]+_} ]]; then
      continue
    fi
    seen["$base"]=1

    dest="$target_dir/$base"

    if [[ -e $dest && $force -eq 0 ]]; then
      printf 'skip    %s (user override already exists)\n' "$base"
      continue
    fi

    if (( dry_run )); then
      printf 'would disable %s\n' "$base"
      continue
    fi

    tmp=$(mktemp)
    printf '%s\n%s\n' '[Desktop Entry]' 'Hidden=true' >"$tmp"
    mv -f -- "$tmp" "$dest"

    printf 'disabled %s\n' "$base"
  done < <(
    find "$autostart_dir" -maxdepth 1 -type f -name '*.desktop' -print0 | sort -z
  )
done
```

### Undo a bulk disable

Remove the override stubs you created:

```bash
rm -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/"*.desktop
```

If you have manually maintained user autostart files in the same directory, remove only the specific stubs you created instead of deleting everything.

---

## Troubleshooting

## An application still starts after `Hidden=true`

The startup source is probably **not** the XDG autostart entry, or the application is also being started by another mechanism.

### 1. Check for a user systemd service

```bash
app='blueman'

systemctl --user list-unit-files --no-pager | grep -i -- "$app" || true
systemctl --user list-units --all --no-pager | grep -i -- "$app" || true
```

If found, disable the actual user unit instead.

### 2. Check Hyprland configuration

```bash
grep -R -n -F -- "$app" ~/.config/hypr 2>/dev/null
```

Look for:

- `exec =`
- `exec-once =`
- sourced config fragments

### 3. Check your user autostart override filename

The basename must match the system entry exactly.

Good:

- `~/.config/autostart/blueman.desktop`

Ineffective:

- `~/.config/autostart/blueman-disabled.desktop`

### 4. Check whether the XDG entry even applies to Hyprland

```bash
grep -E '^(OnlyShowIn|NotShowIn)=' /etc/xdg/autostart/blueman.desktop
printf '%s\n' "${XDG_CURRENT_DESKTOP:-unset}"
```

If the entry is limited to GNOME, KDE, XFCE, etc., it was not responsible for startup in Hyprland.

### 5. Check for on-demand activation

Some components can still appear later via:

- D-Bus activation
- socket activation
- native `systemd --user` dependencies
- application-specific wrappers

Disabling XDG autostart only prevents **login-time autostart**, not all possible activation paths.

### 6. Inspect the user journal

```bash
journalctl --user -b --no-pager | grep -i -- 'autostart\|blueman' || true
```

This is often the fastest way to see whether the source was:

- an XDG autostart-generated unit
- a native user unit
- Hyprland startup logic
- another session component

---

## Best practice for Hyprland + UWSM

For **your own** long-running session services, prefer **systemd --user units** over large `exec-once` chains in Hyprland config.

Use XDG autostart overrides mainly for:

- package-provided desktop entries
- applets installed under `/etc/xdg/autostart`
- per-user disabling without touching package-owned files

This gives:

- clearer startup ownership
- better logging via `journalctl --user`
- restart/supervision
- cleaner integration with UWSM-managed sessions

> [!note]
> `Hidden=true` is the correct per-user override for XDG autostart entries.  
> If the application is really managed elsewhere, disable it at that source instead.

---

## Quick reference

### Disable one entry

```bash
install -d -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

cat > "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop" <<'EOF'
[Desktop Entry]
Hidden=true
EOF
```

### Re-enable one entry

```bash
rm -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop"
```

### Check whether a file is desktop-specific

```bash
grep -E '^(OnlyShowIn|NotShowIn)=' /etc/xdg/autostart/blueman.desktop
```

### Check current desktop identity

```bash
printf '%s\n' "${XDG_CURRENT_DESKTOP:-unset}"
```

### Validate a `.desktop` file

```bash
desktop-file-validate "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/blueman.desktop"
```
