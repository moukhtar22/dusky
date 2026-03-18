# USB Runtime Power Management on Arch Linux

USB autosuspend is the USB-specific part of Linux **runtime power management (runtime PM)**. It allows the kernel to suspend an **idle USB device while the system is still running**. This can reduce power draw significantly on laptops, but some devices resume poorly and may exhibit lag, disconnects, missed wake events, or full resets.

> [!note]
> This note covers **per-device USB runtime PM** on Arch Linux. It is **not** about system sleep (`suspend`, `hibernate`) and it is **not** specific to Wayland, Hyprland, or UWSM.

> [!tip] Related power-policy tools
> `udev`, TLP, Powertop, and kernel command-line parameters can all influence USB runtime PM. Use **one clear source of truth** for persistent policy, or document precisely which tool is expected to win.

---

## When to Use Which Method

| Goal | Best method |
|---|---|
| Test whether autosuspend is causing a problem | Write directly to `/sys/.../power/control` |
| Permanently disable autosuspend for one specific device | `udev` rule |
| Permanently change autosuspend delay for one device | `udev` rule |
| Manage USB policy on laptops already using TLP | TLP configuration |
| Disable USB autosuspend globally | Kernel command line: `usbcore.autosuspend=-1` |

> [!warning]
> **Bus numbers** and **device numbers** from `lsusb` are **not stable identifiers**. They can change across boots, reconnects, and hub topology changes. For persistent matching, prefer:
> - `idVendor` + `idProduct`
> - `serial` when available
> - physical port path only when intentionally binding policy to a port

---

## Runtime PM Model and Relevant Sysfs Files

For a USB device such as `/sys/bus/usb/devices/1-4`, these files are the most important:

| Sysfs file | Meaning | Typical values |
|---|---|---|
| `power/control` | Runtime PM policy for the device | `auto`, `on` |
| `power/runtime_status` | Current runtime PM state | `active`, `suspended`, `suspending`, `resuming`, `unsupported` |
| `power/autosuspend_delay_ms` | Idle delay before autosuspend if policy is `auto` | integer milliseconds |
| `power/wakeup` | Whether the device may wake the system | `enabled`, `disabled` |

### Semantics

- `power/control=auto`  
  Runtime PM is allowed. The kernel may autosuspend the device when idle if the driver supports it.

- `power/control=on`  
  Runtime PM is disabled for the device. The kernel should keep it active.

- `power/autosuspend_delay_ms=5000`  
  If `power/control=auto`, the device must be idle for 5 seconds before autosuspend is attempted.

- Negative `power/autosuspend_delay_ms`  
  A negative delay disables autosuspend by idle timeout. In practice, **`power/control=on` is the clearer and stronger setting** when the goal is “never autosuspend this device”.

> [!note]
> Old guides may refer to `power/level`. That interface is obsolete. Use **`power/control`**.

> [!tip]
> `power/wakeup` is **separate** from autosuspend. A device can have autosuspend disabled and still be unable to wake the system, or vice versa.

---

## Prerequisites

### Required package

```bash
sudo pacman -S usbutils
```

This provides `lsusb`.

### Built-in tools

- `udevadm` is provided by `systemd`
- `journalctl` is provided by `systemd`
- `realpath`, `grep`, `printf`, `tee`, `cat` are in the base system

---

## Inspect the Current USB Power State

## Quick Checks

### Show the global USB autosuspend default

```bash
cat /sys/module/usbcore/parameters/autosuspend
```

This value is in **seconds** and represents the default idle delay applied by `usbcore` when devices are enumerated, unless overridden later by drivers or userspace policy.

- `2` is a common default
- negative values disable USB autosuspend globally

### List USB devices

```bash
lsusb
```

### Show the USB topology and bound drivers

```bash
lsusb -t
```

This is useful for identifying whether the problem might actually be the **hub**, **dock**, or **controller path**, not just the child device.

---

## Enumerate USB Devices and Their Runtime PM State

The following Bash script reads directly from sysfs and does **not** rely on incorrect `lsusb -s` assumptions.

```bash
#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

read_file() {
    local file=$1
    local default=${2:--}

    if [[ -r $file ]]; then
        tr -d '\n' <"$file"
    else
        printf '%s' "$default"
    fi
}

printf '%-10s %-9s %-9s %-12s %-8s %-8s %s\n' \
    'SYSNAME' 'BUS:DEV' 'VID:PID' 'CONTROL' 'STATUS' 'WAKEUP' 'DEVICE'

for dev in /sys/bus/usb/devices/*; do
    [[ -r $dev/idVendor && -r $dev/idProduct ]] || continue

    sysname=${dev##*/}
    vendor=$(read_file "$dev/idVendor")
    product_id=$(read_file "$dev/idProduct")

    busnum_raw=$(read_file "$dev/busnum" 0)
    devnum_raw=$(read_file "$dev/devnum" 0)
    busnum=$(printf '%03d' "$((10#$busnum_raw))")
    devnum=$(printf '%03d' "$((10#$devnum_raw))")

    control=$(read_file "$dev/power/control")
    status=$(read_file "$dev/power/runtime_status" "unsupported")
    wakeup=$(read_file "$dev/power/wakeup")
    manufacturer=$(read_file "$dev/manufacturer" "")
    product=$(read_file "$dev/product" "")
    name="${manufacturer:+$manufacturer }${product:-"(no product string)"}"

    printf '%-10s %-9s %-9s %-12s %-8s %-8s %s\n' \
        "$sysname" \
        "$busnum:$devnum" \
        "$vendor:$product_id" \
        "$control" \
        "$status" \
        "$wakeup" \
        "$name"
done
```

### Notes on the script

- It skips non-device entries such as interface nodes like `1-4:1.0`
- It reads identifiers from sysfs, which is the authoritative source for runtime PM state
- `BUS:DEV` is only for cross-checking with `lsusb`; do **not** use it for persistent matching

---

## Inspect One Specific Device

If you already know the sysfs name, for example `1-4`:

```bash
dev=/sys/bus/usb/devices/1-4

for f in \
    idVendor \
    idProduct \
    manufacturer \
    product \
    power/control \
    power/runtime_status \
    power/autosuspend_delay_ms \
    power/wakeup
do
    [[ -r "$dev/$f" ]] && printf '%-30s %s\n' "$f" "$(tr -d '\n' <"$dev/$f")"
done
```

### Find matchable attributes for `udev`

```bash
udevadm info --attribute-walk --path="$(realpath /sys/bus/usb/devices/1-4)"
```

This is the best way to discover stable match keys such as:

- `ATTR{idVendor}`
- `ATTR{idProduct}`
- `ATTR{serial}`
- sometimes parent attributes if matching by topology is necessary

---

## Temporary Changes for Testing

Changes written directly to sysfs are **not persistent**. They are lost after reboot and may also be overwritten later by TLP, Powertop, or other policy managers.

> [!warning]
> `sudo echo on > /sys/.../power/control` does **not** work reliably because the shell performs the redirection before `sudo` runs. Use `sudo tee` or a root shell.

### Disable autosuspend for one device

```bash
echo on | sudo tee /sys/bus/usb/devices/1-4/power/control
```

### Re-enable autosuspend for one device

```bash
echo auto | sudo tee /sys/bus/usb/devices/1-4/power/control
```

### Set autosuspend delay to 5 seconds

```bash
echo 5000 | sudo tee /sys/bus/usb/devices/1-4/power/autosuspend_delay_ms
echo auto  | sudo tee /sys/bus/usb/devices/1-4/power/control
```

### Verify the result

```bash
cat /sys/bus/usb/devices/1-4/power/control
cat /sys/bus/usb/devices/1-4/power/runtime_status
cat /sys/bus/usb/devices/1-4/power/autosuspend_delay_ms
```

> [!note]
> Setting `power/control=auto` does **not** force an immediate suspend. It only allows autosuspend once the device becomes idle.

---

## Persistent Per-Device Configuration with `udev`

For Arch systems that are **not** delegating USB policy to TLP, `udev` is the correct mechanism for stable, device-specific runtime PM policy.

### Recommended rule file

```bash
sudo install -Dm0644 /dev/null /etc/udev/rules.d/90-local-usb-runtime-pm.rules
sudo nvim /etc/udev/rules.d/90-local-usb-runtime-pm.rules
```

`90-...` is a good local rule prefix because it runs late enough to override most vendor-supplied rules in `/usr/lib/udev/rules.d/`.

---

## Example: Disable Autosuspend for a Specific Device

Example device:

```text
046d:c52b  Logitech Unifying Receiver
```

Rule:

```udev
ACTION=="add", SUBSYSTEM=="usb", DEVTYPE=="usb_device", ATTR{idVendor}=="046d", ATTR{idProduct}=="c52b", TEST=="power/control", ATTR{power/control}="on"
```

### Why this rule is correct

| Component | Purpose |
|---|---|
| `ACTION=="add"` | Apply when the USB device appears |
| `SUBSYSTEM=="usb"` | Restrict to USB |
| `DEVTYPE=="usb_device"` | Match the device node, not its individual interfaces |
| `ATTR{idVendor}=="046d"` | Match vendor ID |
| `ATTR{idProduct}=="c52b"` | Match product ID |
| `TEST=="power/control"` | Only apply if the attribute exists |
| `ATTR{power/control}="on"` | Disable runtime PM for that device |

> [!tip]
> If multiple identical devices share the same `VID:PID`, add a more specific match if available, for example:
> - `ATTR{serial}=="..."` for a unique unit
> - physical port matching only when intentionally tied to a port

---

## Example: Keep Autosuspend Enabled but Use a Longer Delay

```udev
ACTION=="add", SUBSYSTEM=="usb", DEVTYPE=="usb_device", ATTR{idVendor}=="1234", ATTR{idProduct}=="5678", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="add", SUBSYSTEM=="usb", DEVTYPE=="usb_device", ATTR{idVendor}=="1234", ATTR{idProduct}=="5678", TEST=="power/autosuspend_delay_ms", ATTR{power/autosuspend_delay_ms}="5000"
```

This keeps runtime PM enabled but delays autosuspend by 5 seconds.

> [!note]
> Some devices or drivers do not expose `power/autosuspend_delay_ms`. In that case, only `power/control` can be managed.

---

## Reload and Apply `udev` Rules

### Reload rules

```bash
sudo udevadm control --reload
```

### Re-apply to currently attached USB devices

Apply to all USB devices:

```bash
sudo udevadm trigger --subsystem-match=usb --action=add
sudo udevadm settle
```

Or apply only to one known device, for example `1-4`:

```bash
sudo udevadm trigger --sysname-match=1-4 --action=add
sudo udevadm settle
```

### Verify rule evaluation

```bash
sudo udevadm test "$(realpath /sys/bus/usb/devices/1-4)"
```

Then inspect the live state:

```bash
cat /sys/bus/usb/devices/1-4/power/control
cat /sys/bus/usb/devices/1-4/power/runtime_status
```

> [!tip]
> `udevadm test` is excellent for debugging rule matching. It shows which rules are parsed and what assignments are made.

---

## Global USB Autosuspend Policy

If the goal is to change the **default behavior for all USB devices**, use the kernel command line.

### Disable USB autosuspend globally

```text
usbcore.autosuspend=-1
```

### Set the global default idle delay to 5 seconds

```text
usbcore.autosuspend=5
```

### Check the live value

```bash
cat /sys/module/usbcore/parameters/autosuspend
```

> [!warning]
> Global disable is a blunt instrument. It may increase idle power draw and reduce battery life noticeably on laptops.

### Arch-specific note

On Arch, `usbcore` is typically built into the kernel, so a `modprobe.d` option is **not** the preferred method for setting `usbcore.autosuspend`. Use the **kernel command line** instead.

How to set the kernel command line depends on your boot setup:

- **GRUB**: edit `/etc/default/grub`, then regenerate `grub.cfg`
- **systemd-boot**: edit the relevant loader entry or your unified-kernel-image command-line source, depending on your setup

---

## Interaction with TLP and Powertop

## TLP

If TLP is installed and enabled, it may manage USB autosuspend and **override manual or `udev`-applied settings** later, especially on boot, resume, or AC/BAT transitions.

### Inspect TLP USB policy

```bash
sudo tlp-stat -u
```

### Recommended TLP configuration pattern

Edit:

```bash
sudo nvim /etc/tlp.conf
```

Example:

```ini
USB_AUTOSUSPEND=1
USB_DENYLIST="046d:c52b"
```

Then apply:

```bash
sudo systemctl restart tlp.service
```

> [!tip]
> If TLP already owns system-wide laptop power policy, prefer using **TLP’s native USB options** instead of layering separate `udev` overrides.

## Powertop

`powertop --auto-tune` changes live runtime PM settings but is **not a configuration system** by itself. If you run it automatically at boot, it may override previous USB power settings.

> [!warning]
> Do not combine unmanaged Powertop autotuning with TLP and custom `udev` rules unless you have explicitly defined precedence and verified the resulting state.

---

## Troubleshooting

## The device still suspends even though the rule looks correct

Check for overrides from higher-level tools:

```bash
systemctl is-enabled tlp.service
systemctl is-active tlp.service
ps -ef | grep -E 'powertop|tlp'
```

Then inspect the live values again:

```bash
cat /sys/bus/usb/devices/1-4/power/control
cat /sys/bus/usb/devices/1-4/power/runtime_status
```

If `power/control` reverts after boot or resume, another manager is writing to sysfs.

---

## The wrong sysfs node was targeted

Do **not** target interface nodes like:

```text
1-4:1.0
```

Target the USB **device** node:

```text
1-4
```

Use:

```bash
ls -1 /sys/bus/usb/devices/
```

or the inventory script above to confirm.

---

## The problem is actually the hub or dock

A child device may be innocent; the suspend/resume issue can be caused by the **USB hub**, **dock**, or **receiver** upstream.

Use:

```bash
lsusb -t
```

Common examples:

- flaky wireless input devices -> the **receiver** is the device to tune
- devices behind a dock -> the **dock or hub** may need autosuspend disabled
- entire branch disconnects -> inspect the parent hub/controller path

---

## Wake from sleep does not work

This is usually related to **wakeup policy**, not just autosuspend.

Check:

```bash
cat /sys/bus/usb/devices/1-4/power/wakeup
```

Enable if appropriate:

```bash
echo enabled | sudo tee /sys/bus/usb/devices/1-4/power/wakeup
```

Also remember:

- firmware/BIOS settings may block USB wake
- not every sleep state supports every wake source
- enabling wake does not guarantee a broken device will resume cleanly from runtime suspend

---

## `runtime_status` stays `active`

This does **not** necessarily mean configuration failed.

Possible reasons:

- the device is genuinely busy
- the driver does not support runtime PM
- the device is marked active due to periodic traffic
- autosuspend delay has not elapsed
- another policy manager forced `power/control=on`

Check:

```bash
cat /sys/bus/usb/devices/1-4/power/control
cat /sys/bus/usb/devices/1-4/power/runtime_status
```

If `runtime_status` is `unsupported`, the device/driver does not participate in runtime PM in the expected way.

---

## Watch kernel and udev events live

### Kernel log

```bash
sudo journalctl -kf
```

### udev event monitor

```bash
sudo udevadm monitor --udev --property
```

Useful search in the current boot log:

```bash
journalctl -k -b | grep -Ei 'usb|xhci|reset|runtime suspend|runtime resume|autosuspend'
```

Look for:

- repeated resets
- disconnect/reconnect loops
- xHCI or hub errors
- resume failures after idle

---

## Stable Matching Strategies

Use the most stable matching key available.

### Preferred order

1. `idVendor` + `idProduct`
2. `serial` if multiple identical devices exist
3. physical port only if the policy should follow the port

### Match by serial example

```udev
ACTION=="add", SUBSYSTEM=="usb", DEVTYPE=="usb_device", ATTR{idVendor}=="1234", ATTR{idProduct}=="5678", ATTR{serial}=="ABCDEF012345", TEST=="power/control", ATTR{power/control}="on"
```

### Match by physical port example

```udev
ACTION=="add", SUBSYSTEM=="usb", DEVTYPE=="usb_device", KERNEL=="1-4", TEST=="power/control", ATTR{power/control}="on"
```

> [!warning]
> Port-based matching is topology-dependent. It may break if you move the device, insert a hub, change docks, or update hardware.

---

## Minimal Reference Commands

### Show all USB devices and drivers

```bash
lsusb
lsusb -t
```

### Show global USB autosuspend default

```bash
cat /sys/module/usbcore/parameters/autosuspend
```

### Disable autosuspend temporarily for one device

```bash
echo on | sudo tee /sys/bus/usb/devices/1-4/power/control
```

### Re-enable autosuspend temporarily

```bash
echo auto | sudo tee /sys/bus/usb/devices/1-4/power/control
```

### Set per-device delay temporarily

```bash
echo 5000 | sudo tee /sys/bus/usb/devices/1-4/power/autosuspend_delay_ms
```

### Inspect one device

```bash
udevadm info --attribute-walk --path="$(realpath /sys/bus/usb/devices/1-4)"
```

### Reload and trigger `udev`

```bash
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=usb --action=add
sudo udevadm settle
```

### Test a `udev` rule

```bash
sudo udevadm test "$(realpath /sys/bus/usb/devices/1-4)"
```

---

## Recommended Practice Summary

- Use **sysfs writes** only for short-lived testing
- Use **`udev`** for persistent per-device policy on systems not managed by TLP
- Use **TLP native configuration** if TLP is already your power-policy owner
- Use **kernel command-line `usbcore.autosuspend=`** only for global defaults
- Verify live behavior using:
  - `power/control`
  - `power/runtime_status`
  - `lsusb -t`
  - `udevadm test`
  - `journalctl -kf`

> [!success]
> For a flaky USB mouse, keyboard receiver, audio interface, webcam, serial adapter, or dock, the most reliable fix is usually:
> 1. identify the exact USB device or hub,
> 2. test with `power/control=on`,
> 3. persist the result with a targeted `udev` rule or TLP denylist entry.
