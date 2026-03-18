# External Monitor Brightness via DDC/CI on Arch Linux

## Overview

This note covers **hardware brightness control for external monitors** using **DDC/CI** with `ddcutil` on **Arch Linux**.

This works independently of **Xorg**, **Wayland**, **Hyprland**, or **UWSM** because `ddcutil` talks to the monitor over the kernel's **I²C/DDC** interface.

> [!note]
> This is for **external displays** connected over **DisplayPort, HDMI, or USB-C alt-mode paths that pass DDC/CI**.
>
> This is **not** the correct method for an internal laptop panel (`eDP`). For laptop backlight control, use `brightnessctl`, `light`, or another backlight tool.

> [!warning]
> `ddcutil setvcp 10 50` sets a **raw VCP value** of `50`.  
> It only equals **50% brightness** if the monitor's reported maximum for VCP `0x10` is `100`. Many monitors do use `0..100`, but this is **not guaranteed**.

---

## Prerequisites and Limitations

Before debugging software, verify the hardware path supports DDC/CI:

- **DDC/CI must be enabled in the monitor OSD**. Many monitors expose this as `DDC/CI`, `MCCS`, or a vendor-specific option.
- **KVM switches, docks, MST hubs, HDMI splitters, and some USB adapters** may block or break DDC/CI.
- **DisplayLink-based docks** usually do **not** expose standard DDC/CI.
- Some monitors respond on one input type but not another.
- Some monitors have buggy DDC/CI implementations and may fail intermittently.
- Proprietary **NVIDIA** setups can still be less reliable than AMD/Intel for DDC/CI, depending on GPU, connector routing, and driver branch.

---

## Install Required Packages

On Arch Linux, avoid partial upgrades. Install with a full sync/upgrade:

```bash
sudo pacman -Syu --needed ddcutil i2c-tools
```

### Package roles

- `ddcutil` — primary DDC/CI control utility
- `i2c-tools` — diagnostic tools such as `i2cdetect -l`

---

## Load the `i2c-dev` Kernel Module

`ddcutil` needs access to `/dev/i2c-*` device nodes.

Load the module now:

```bash
sudo modprobe i2c-dev
```

Make it persistent across reboots:

```bash
printf '%s\n' 'i2c-dev' | sudo tee /etc/modules-load.d/i2c-dev.conf >/dev/null
```

### Verify

```bash
lsmod | grep '^i2c_dev'
ls -l /dev/i2c-*
i2cdetect -l
```

> [!warning]
> `i2cdetect -l` is safe because it only lists adapters.
>
> Do **not** blindly probe every graphics-related bus with `i2cdetect -y`; indiscriminate probing is unnecessary and can confuse devices on some buses.

---

## Permissions for Non-Root Use

First, test access as your normal user **before** adding custom rules:

```bash
ddcutil detect
```

If that works, no additional permission changes are required.

> [!note]
> On current Arch systems, packaged udev rules may already provide sufficient access for the active desktop user. Only add local rules if regular-user access still fails.

### If regular-user access fails

Typical symptoms:

- `Permission denied` opening `/dev/i2c-*`
- `ddcutil detect` works with `sudo` but not as your user

Create the `i2c` group if it does not already exist:

```bash
getent group i2c >/dev/null || sudo groupadd --system i2c
```

Add your user to it:

```bash
sudo usermod -aG i2c "$(id -un)"
```

Install a local udev rule:

```udev
SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"
```

```bash
cat <<'EOF' | sudo tee /etc/udev/rules.d/99-local-i2c-permissions.rules >/dev/null
SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", GROUP="i2c", MODE="0660"
EOF
```

Reload rules and retrigger devices:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=i2c-dev
```

Then **log out and back in**.

### Re-verify

```bash
id -nG
ls -l /dev/i2c-*
ddcutil detect
```

---

## Detect Connected Monitors

Use `ddcutil` to detect monitors and identify the correct bus:

```bash
ddcutil detect
```

Look for output like:

- monitor make/model
- EDID information
- `I2C bus: /dev/i2c-6`

### Prefer `--bus` over `--display`

Use the bus number from detection output:

```bash
ddcutil --bus 6 getvcp 10
```

This is preferable to:

```bash
ddcutil --display 1 getvcp 10
```

because `--display` numbers are assigned dynamically and can change after hotplugging, rebooting, docking changes, or monitor sleep/wake cycles.

> [!note]
> For a fixed desktop setup, I²C bus numbers are usually stable enough for scripts and Hyprland keybindings.  
> If you regularly change docks, MST topology, or cable routing, re-check the bus assignments after hardware changes.

---

## Basic DDC/CI Commands

### Read current brightness

VCP code `0x10` is the standard **Brightness / Luminance** control on most monitors:

```bash
ddcutil --bus 6 getvcp 10
```

Typical output:

```text
VCP code 0x10 (Brightness                    ): current value =    68, max value =   100
```

### Set brightness to a raw value

```bash
ddcutil --bus 6 setvcp 10 50
```

Again: `50` is a **raw** value, not universally `50%`.

### Check monitor capabilities

```bash
ddcutil --bus 6 capabilities
```

> [!note]
> Some monitors provide incomplete or incorrect capabilities strings.  
> A direct `getvcp 10` test is often more reliable than trusting the capabilities report alone.

---

## Quick Reference

| Task | Command |
|---|---|
| Detect monitors | `ddcutil detect` |
| Read brightness | `ddcutil --bus N getvcp 10` |
| Set raw brightness | `ddcutil --bus N setvcp 10 VALUE` |
| Show monitor capabilities | `ddcutil --bus N capabilities` |
| List I²C adapters | `i2cdetect -l` |

---

## Percentage-Aware Helper Script

Because monitor brightness ranges are not always `0..100`, a helper script is the safest way to work in percentages.

Save this as `~/.local/bin/ddc-brightness` and make it executable.

> [!NOTE]- scritp
> ```bash
> #!/usr/bin/env bash
> set -euo pipefail
> shopt -s inherit_errexit
> 
> usage() {
>   cat <<'EOF'
> Usage:
>   ddc-brightness --bus N [--bus N ...] get
>   ddc-brightness --bus N [--bus N ...] set-raw VALUE
>   ddc-brightness --bus N [--bus N ...] set-percent PERCENT
>   ddc-brightness --bus N [--bus N ...] inc-percent STEP
>   ddc-brightness --bus N [--bus N ...] dec-percent STEP
> 
> Examples:
>   ddc-brightness --bus 6 get
>   ddc-brightness --bus 6 set-percent 50
>   ddc-brightness --bus 6 --bus 7 inc-percent 5
> 
> Notes:
>   - Bus numbers come from `ddcutil detect`.
>   - This controls VCP code 0x10 (Brightness/Luminance).
>   - Using --bus is more stable than using ddcutil display numbers in scripts.
> EOF
> }
> 
> die() {
>   printf 'error: %s\n' "$*" >&2
>   exit 2
> }
> 
> declare -a buses=()
> action=''
> value=''
> 
> while (($#)); do
>   case $1 in
>     --bus)
>       [[ ${2-} =~ ^[0-9]+$ ]] || die "--bus requires a numeric I2C bus number"
>       buses+=("$2")
>       shift 2
>       ;;
>     get|set-raw|set-percent|inc-percent|dec-percent)
>       [[ -z $action ]] || die "only one action may be specified"
>       action=$1
>       shift
>       if [[ $action != get ]]; then
>         [[ ${1-} =~ ^[0-9]+$ ]] || die "$action requires a non-negative integer value"
>         value=$1
>         shift
>       fi
>       ;;
>     -h|--help)
>       usage
>       exit 0
>       ;;
>     *)
>       die "unknown argument: $1"
>       ;;
>   esac
> done
> 
> ((${#buses[@]} > 0)) || die "specify at least one --bus; use \`ddcutil detect\` to find it"
> [[ -n $action ]] || die "specify an action"
> 
> get_vcp10() {
>   local bus=$1 out re
>   out=$(ddcutil --bus "$bus" getvcp 10)
>   re='current value = *([0-9]+), max value = *([0-9]+)'
>   [[ $out =~ $re ]] || {
>     printf 'error: could not parse ddcutil output for bus %s:\n%s\n' "$bus" "$out" >&2
>     exit 1
>   }
>   printf '%s %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
> }
> 
> clamp() {
>   local val=$1 min=$2 max=$3
>   (( val < min )) && val=$min
>   (( val > max )) && val=$max
>   printf '%s\n' "$val"
> }
> 
> pct_to_raw() {
>   local pct=$1 max=$2
>   printf '%s\n' "$(( (pct * max + 50) / 100 ))"
> }
> 
> pct_delta_to_raw() {
>   local pct=$1 max=$2 raw
>   raw=$(( (pct * max + 50) / 100 ))
>   (( pct > 0 && raw < 1 )) && raw=1
>   printf '%s\n' "$raw"
> }
> 
> for bus in "${buses[@]}"; do
>   case $action in
>     get)
>       read -r current max < <(get_vcp10 "$bus")
>       (( max > 0 )) || die "bus $bus reported max=0 for VCP 0x10"
>       printf 'bus=%s current=%s max=%s percent=%s\n' \
>         "$bus" "$current" "$max" "$(( (current * 100 + max / 2) / max ))"
>       ;;
>     set-raw)
>       read -r _ max < <(get_vcp10 "$bus")
>       target=$(clamp "$value" 0 "$max")
>       ddcutil --bus "$bus" setvcp 10 "$target"
>       printf 'bus=%s set raw=%s\n' "$bus" "$target"
>       ;;
>     set-percent)
>       (( value >= 0 && value <= 100 )) || die "set-percent expects 0..100"
>       read -r _ max < <(get_vcp10 "$bus")
>       target=$(pct_to_raw "$value" "$max")
>       ddcutil --bus "$bus" setvcp 10 "$target"
>       printf 'bus=%s set percent=%s raw=%s/%s\n' "$bus" "$value" "$target" "$max"
>       ;;
>     inc-percent)
>       (( value >= 0 && value <= 100 )) || die "inc-percent expects 0..100"
>       read -r current max < <(get_vcp10 "$bus")
>       delta=$(pct_delta_to_raw "$value" "$max")
>       target=$(clamp "$(( current + delta ))" 0 "$max")
>       ddcutil --bus "$bus" setvcp 10 "$target"
>       printf 'bus=%s increased to raw=%s/%s\n' "$bus" "$target" "$max"
>       ;;
>     dec-percent)
>       (( value >= 0 && value <= 100 )) || die "dec-percent expects 0..100"
>       read -r current max < <(get_vcp10 "$bus")
>       delta=$(pct_delta_to_raw "$value" "$max")
>       target=$(clamp "$(( current - delta ))" 0 "$max")
>       ddcutil --bus "$bus" setvcp 10 "$target"
>       printf 'bus=%s decreased to raw=%s/%s\n' "$bus" "$target" "$max"
>       ;;
>   esac
> done
> ```

Make it executable:

```bash
chmod +x ~/.local/bin/ddc-brightness
```

### Example usage

```bash
~/.local/bin/ddc-brightness --bus 6 get
~/.local/bin/ddc-brightness --bus 6 set-percent 50
~/.local/bin/ddc-brightness --bus 6 inc-percent 5
~/.local/bin/ddc-brightness --bus 6 dec-percent 5
```

For multiple monitors:

```bash
~/.local/bin/ddc-brightness --bus 6 --bus 7 set-percent 40
```

---

## Hyprland Keybindings

Example Hyprland bindings for one or more external monitors:

```ini
# Replace the path and bus numbers with your actual values.
# Use `bind` instead of `binde` if your monitor dislikes rapid repeated DDC/CI writes.

binde = , XF86MonBrightnessUp, exec, /home/<user>/.local/bin/ddc-brightness --bus 6 --bus 7 inc-percent 5
binde = , XF86MonBrightnessDown, exec, /home/<user>/.local/bin/ddc-brightness --bus 6 --bus 7 dec-percent 5
```

> [!note]
> DDC/CI writes can be slow compared with internal backlight control.  
> Some monitors do not handle rapid repeated writes well. If brightness keys feel unreliable with `binde`, switch to `bind` and tap the key instead of holding it.

### UWSM note

When running Hyprland through **UWSM**, prefer **absolute paths** in keybindings unless you are certain your `PATH` is correctly exported into the systemd user environment.

This avoids failures where `~/.local/bin` is available in an interactive shell but not inside the UWSM-managed session.

---

## Troubleshooting

### `ddcutil detect` shows no monitors

Check:

1. DDC/CI is enabled in the monitor OSD.
2. The monitor is connected directly, not through a problematic dock/KVM/adapter.
3. The monitor is awake.
4. The connector path actually passes DDC/CI.
5. `i2c-dev` is loaded and `/dev/i2c-*` exists.

Useful checks:

```bash
ls -l /dev/i2c-*
i2cdetect -l
ddcutil detect
```

If needed, test with elevated privileges only for debugging:

```bash
sudo ddcutil detect
```

If `sudo` works but your user does not, the issue is permissions, not monitor support.

---

### `Permission denied` on `/dev/i2c-*`

Fix user/group/udev permissions as described in [[#Permissions for Non-Root Use]].

---

### `getvcp 10` says unsupported

Possible causes:

- The monitor does not expose standard brightness control via VCP `0x10`
- The monitor's DDC/CI implementation is buggy
- The capabilities string is incomplete or wrong
- The DDC path is partially broken through a dock/adapter

Try:

```bash
ddcutil --bus 6 capabilities
ddcutil --bus 6 getvcp 10
```

If `capabilities` does not mention brightness but `getvcp 10` still works, trust the direct `getvcp` result.

---

### Brightness changes work on one input but not another

This is common. DDC/CI behavior can vary between:

- HDMI vs DisplayPort
- direct cable vs dock
- docked vs undocked laptop
- different GPU-owned output ports on hybrid graphics systems

---

### Commands are slow or flaky

Some monitors have poor DDC/CI implementations.

Symptoms:

- intermittent I/O errors
- failures during rapid repeats
- monitor only responds when fully awake

Mitigations:

- avoid holding the brightness key down
- use `bind` instead of `binde` in Hyprland
- reduce the step frequency
- connect directly instead of through a dock/KVM
- test a different cable or port

---

### Internal laptop panel does not respond

That is expected. Use a backlight tool instead, for example:

```bash
sudo pacman -Syu --needed brightnessctl
brightnessctl set 5%+
brightnessctl set 5%-
```

---

## Minimal Working Sequence

If all you need is the shortest correct setup path:

```bash
sudo pacman -Syu --needed ddcutil i2c-tools
sudo modprobe i2c-dev
printf '%s\n' 'i2c-dev' | sudo tee /etc/modules-load.d/i2c-dev.conf >/dev/null
ddcutil detect
ddcutil --bus 6 getvcp 10
ddcutil --bus 6 setvcp 10 50
```

If `ddcutil detect` fails as a regular user but succeeds with `sudo`, fix the permissions and udev rule first.

---

## Summary

- `ddcutil` is the correct tool for **external monitor** brightness via **DDC/CI**
- load `i2c-dev`
- ensure non-root access to `/dev/i2c-*`
- use `ddcutil detect` to find the correct bus
- prefer `--bus N` over `--display N` in scripts
- treat VCP `0x10` values as **raw values**, not guaranteed percentages
- use a helper script for percentage-based control and Hyprland keybindings
