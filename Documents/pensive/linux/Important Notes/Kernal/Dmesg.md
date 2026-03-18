# `dmesg`: Inspecting the Linux Kernel Message Buffer

## Overview

`dmesg` is the standard Linux utility for reading and controlling the **kernel message buffer** (`kmsg`), the in-memory circular log used by the kernel for boot-time and runtime diagnostics.

Use `dmesg` when you need to inspect **kernel-space** events such as:

- early boot initialization
- driver probe/load/unload activity
- PCI/USB device detection
- DRM/KMS/GPU issues
- firmware load failures
- storage and filesystem errors
- suspend/resume problems
- kernel warnings, oopses, and crashes

On Arch Linux, `dmesg` comes from **`util-linux`**.

---

## What `dmesg` Actually Reads

The kernel maintains a **RAM-backed circular buffer** for log messages.

### Properties of the kernel ring buffer

- **Fixed-size and circular**  
  When the buffer fills, the kernel overwrites the oldest messages.

- **Volatile**  
  It is normally lost on reboot or power loss.

- **Available very early**  
  It exists before most userspace services are fully running, which makes it valuable for early boot and driver initialization failures.

- **Not archival storage**  
  On a noisy system, important messages can be overwritten quickly.

> [!warning]
> `dmesg` is a view of the **current in-memory kernel log**, not a durable historical record. For previous boots or long-term retention, use `journalctl -k` with persistent journaling enabled.

On modern Linux systems, `dmesg` typically reads from `/dev/kmsg`; older compatibility paths use the kernel `syslog(2)` interface.

---

## `dmesg` vs. `journalctl -k` on Arch Linux

On a systemd-based Arch system, both tools are useful, but they solve slightly different problems.

### Practical distinction

| Need | Best Tool | Why |
|---|---|---|
| Inspect the current in-memory kernel buffer immediately | `dmesg` | Direct, fast, minimal |
| Review kernel messages from the current boot with rich filtering | `journalctl -k -b` | Better time/priority filtering |
| Review kernel messages from previous boots | `journalctl -k -b -1` | `dmesg` cannot survive reboot |
| Follow live kernel messages | `dmesg -w` or `journalctl -kf` | Both work; `journalctl` is often easier to correlate with wall-clock time |
| Investigate crashes after reboot | `journalctl -k -b -1`, `pstore`, `kdump`, `netconsole` | `dmesg` is volatile |

### Important nuance about persistence

`journalctl -k` is only persistent **if the journal is stored on disk**. On Arch, that usually means `/var/log/journal/` exists or `Storage=persistent` is configured for `systemd-journald`.

> [!note] Enable persistent journal storage on Arch
>
> ```bash
> sudo mkdir -p /var/log/journal
> sudo systemd-tmpfiles --create --prefix /var/log/journal
> sudo journalctl --flush
> ```
>
> After this, future boots will retain journal data on disk unless separately reconfigured.

> [!note]
> `journalctl -k` usually contains the same kernel messages as `dmesg` for the current boot because `systemd-journald` imports kernel messages very early. However, for severe lockups, abrupt power loss, or panic scenarios, the final messages may never reach persistent storage.

---

## Permissions and Security

Reading the kernel log may be restricted.

### Common behavior

If access is restricted, running `dmesg` as a normal user may fail with:

```text
dmesg: read kernel buffer failed: Operation not permitted
```

This is controlled by the kernel setting:

```bash
sysctl kernel.dmesg_restrict
```

If it is `1`, unprivileged users cannot read the kernel log buffer.

### Recommended practice

Use:

```bash
sudo dmesg
```

rather than weakening the system’s kernel log restrictions.

> [!warning]
> Avoid permanently disabling `kernel.dmesg_restrict` on multi-user or exposed systems. Kernel logs may reveal hardware layout, memory addresses, driver state, and other information useful to attackers.

---

## Timestamp Semantics

Understanding timestamps is critical when correlating events.

### Default output

By default, `dmesg` prints timestamps as **seconds since boot** using the kernel’s monotonic time base.

Example style:

```text
[    3.812451] nvme nvme0: 8/0/0 default/read/poll queues
```

This format is often the **most reliable for event ordering**.

### Human-readable timestamp modes

| Option | Effect | Notes |
|---|---|---|
| `-T`, `--ctime` | Convert timestamps to wall-clock time | Convenient, but translation is best-effort |
| `-e`, `--reltime` | Show local time and delta between messages | Good for interactive analysis |
| `-d`, `--show-delta` | Show time delta between lines | Useful for timing gaps during boot or resume |
| `-t`, `--notime` | Remove timestamps | Mostly for simplified output or scripting |
| `-H`, `--human` | Human-friendly formatting | May enable readable formatting and a pager |

> [!warning] `--ctime` is not perfect
> `dmesg -T` translates boot-relative timestamps using the current system clock. It can be misleading:
>
> - before RTC/NTP time has been corrected
> - around suspend/resume transitions
> - if the system clock changed after boot
>
> For precise ordering, prefer the default monotonic timestamps.

---

## Output and Filtering Options That Matter

### Severity filtering

Kernel log levels are more reliable than grepping for words like `error`.

Use:

```bash
sudo dmesg --level=warn,err,crit,alert,emerg
```

instead of relying only on:

```bash
sudo dmesg | grep -Ei 'error|fail|warn'
```

because many serious kernel messages do **not** contain those literal words.

### Decoding priorities and facilities

Use:

```bash
sudo dmesg --decode
```

or:

```bash
sudo dmesg -x
```

to decode message prefixes into readable facility/priority labels.

### Time-window filtering

Current `util-linux` `dmesg` supports time filtering:

```bash
sudo dmesg --since '10 min ago'
sudo dmesg --since '2026-03-17 08:00:00' --until '2026-03-17 08:10:00'
```

### Follow modes

| Option | Behavior |
|---|---|
| `-w`, `--follow` | Print current buffer, then wait for new messages |
| `-W`, `--follow-new` | Wait and print **only** new messages |

`--follow-new` is usually the better choice when reproducing a single event.

---

## High-Value Command Reference

| Task | Command | Notes |
|---|---|---|
| View the full current buffer | `sudo dmesg` | Raw default view |
| View readable output without a pager | `sudo dmesg --human --nopager` | Good interactive default |
| Show only the newest lines | `sudo dmesg \| tail -n 50` | Quick recent context |
| Show wall-clock time | `sudo dmesg --ctime \| less` | Easy correlation with other logs |
| Show warnings and errors only | `sudo dmesg --level=warn,err,crit,alert,emerg --decode --ctime` | Better than plain text grep |
| Follow the kernel log live | `sudo dmesg --follow` | Existing buffer + future messages |
| Follow only new events | `sudo dmesg --follow-new --human --nopager` | Best for plug/unplug, resume, module loading |
| Filter by recent time window | `sudo dmesg --since '15 min ago' --ctime` | Focused troubleshooting |
| Search for a subsystem | `sudo dmesg --ctime \| grep -Ei 'usb|xhci|uas'` | Example for USB |
| Export machine-readable output | `sudo dmesg --json \| jq` | Best for automation if `jq` is installed |
| Decode exact raw prefixes | `sudo dmesg --raw` | Useful for bug reports or parser development |

> [!tip]
> For scripted parsing, prefer `--json` or `--raw`. Avoid parsing `--human` or `--ctime` output.

---

## Common Troubleshooting Workflows

## Watch a single event happen

For device hotplug, resume, module insertion, or monitor detection:

```bash
sudo dmesg --follow-new --human --nopager
```

Then reproduce the event.

Examples:

- plug in a USB device
- connect/disconnect a monitor
- suspend/resume the machine
- run `modprobe <module>`
- start a display manager or compositor

---

## USB and external device debugging

```bash
sudo dmesg --follow-new --human --nopager
```

Then plug in the device.

To review historical USB-related kernel messages:

```bash
sudo dmesg --ctime | grep -Ei 'usb|xhci|ehci|uhci|uas'
```

Related tools:

```bash
lsusb
udevadm monitor --kernel --udev --property
```

Use `dmesg` for the **kernel-side** event and `udevadm monitor` for the **udev/userspace** side.

---

## GPU, DRM, and Wayland startup issues

For black screens, flicker, failed monitor detection, or session startup problems involving GPU drivers:

```bash
sudo dmesg --ctime --decode | grep -Ei 'drm|kms|modeset|edid|dp|hdmi|amdgpu|i915|nouveau|nvidia|firmware'
```

This is especially useful when debugging:

- early KMS failures
- GPU firmware loading issues
- monitor hotplug/EDID problems
- suspend/resume graphics regressions
- display issues before a compositor fully starts

> [!note]
> `dmesg` only shows **kernel-space** information. A compositor crash, portal issue, or [[UWSM]] user unit failure is usually **not** a `dmesg` problem unless the GPU/DRM stack emitted a kernel message.
>
> For Hyprland/UWSM users, pair kernel inspection with:
>
> ```bash
> journalctl --user -b
> journalctl -b
> ```

---

## Storage and filesystem errors

To focus on likely disk, NVMe, or filesystem problems:

```bash
sudo dmesg --level=warn,err,crit,alert,emerg --ctime | grep -Ei 'nvme|ata|ahci|scsi|i/o|timeout|reset|ext4|btrfs|xfs|f2fs'
```

Common patterns worth noticing:

- `I/O error`
- `reset`
- `timeout`
- `link down`
- `aborted command`
- `metadata corruption`
- `read-only filesystem`

For NVMe-specific issues:

```bash
sudo dmesg --ctime | grep -Ei 'nvme|pcie|aer|timeout|abort'
```

---

## Network and Wi-Fi driver problems

```bash
sudo dmesg --ctime | grep -Ei 'firmware|iwlwifi|ath|rtw|mt76|r8169|e1000e|igc|ixgbe|netdev|link is'
```

This can expose:

- firmware load failures
- NIC resets
- PCIe link problems
- driver crashes
- resume-related network failures

---

## Investigate the previous boot

`dmesg` cannot show a prior boot. Use the journal:

```bash
journalctl -k -b -1
```

Warnings and above only:

```bash
journalctl -k -b -1 -p warning
```

Previous-boot logs are available only if the journal was persisted.

> [!warning]
> If the system hard-locked, panic-rebooted, or lost power abruptly, the final messages may be missing even from the journal. For serious crash forensics, use `pstore`, `kdump`, `netconsole`, or a serial console.

---

## Save a Clean Snapshot

For later analysis or bug reports, save a timestamped snapshot without color codes:

```bash
ts=
printf -v ts '%(%F_%H-%M-%S)T' -1
sudo dmesg --ctime --decode --color=never >"$HOME/dmesg-$ts.log"
```

For machine-readable export:

```bash
ts=
printf -v ts '%(%F_%H-%M-%S)T' -1
sudo dmesg --json >"$HOME/dmesg-$ts.json"
```

---

## Easily Misunderstood or Destructive Options

### `--clear` and `--read-clear`

| Option | Effect |
|---|---|
| `-C`, `--clear` | Clear the kernel ring buffer |
| `-c`, `--read-clear` | Print the buffer, then clear it |

> [!warning]
> These options destroy evidence. Do not use them unless you intentionally want a clean buffer for a controlled reproduction test.

### `--console-level`

```bash
sudo dmesg --console-level warn
```

This changes the **kernel console log level**. It does **not** simply filter what `dmesg` displays.

Use it only if you understand the difference between:

- what is stored in the kernel log buffer
- what is printed to the active console

### `grep` is not a severity filter

This is a weak first pass:

```bash
sudo dmesg | grep -Ei 'error|fail|warn'
```

A better first pass is:

```bash
sudo dmesg --level=warn,err,crit,alert,emerg --decode --ctime
```

Then refine with `grep`.

---

## Kernel Log Levels

These are the standard kernel/syslog severities.

| Level | Name | Meaning |
|---|---|---|
| 0 | `emerg` | System is unusable |
| 1 | `alert` | Immediate action required |
| 2 | `crit` | Critical condition |
| 3 | `err` | Error condition |
| 4 | `warn` | Warning condition |
| 5 | `notice` | Normal but significant condition |
| 6 | `info` | Informational message |
| 7 | `debug` | Debug-level output |

Example:

```bash
sudo dmesg --level=err,warn
```

When using `--decode`, these become easier to interpret.

---

## Arch Linux Quick Reference

| Goal | Command |
|---|---|
| Current kernel log buffer | `sudo dmesg` |
| Current boot kernel messages from journal | `journalctl -k -b` |
| Previous boot kernel messages | `journalctl -k -b -1` |
| Current boot warnings and errors | `journalctl -k -b -p warning` |
| Live follow from kernel buffer | `sudo dmesg -w` |
| Live follow from journal | `journalctl -kf` |

---

## Related Tools

Use these alongside `dmesg` when narrowing down a kernel or device issue.

| Tool | Use |
|---|---|
| `journalctl -k` | Kernel messages in the systemd journal |
| `journalctl --user -b` | User-session problems, compositor logs, portals, UWSM user services |
| `udevadm monitor --kernel --udev --property` | Correlate kernel and udev device events |
| `lspci -k` | See PCI devices and the drivers bound to them |
| `lsusb` | Inspect USB topology and devices |
| `lsmod` / `modinfo` / `modprobe` | Module state, metadata, and manual loading |
| `pstore` | Recover panic/oops data across reboot when supported |
| `kdump` | Full kernel crash dump infrastructure |
| `netconsole` / serial console | Capture logs from systems that hard-lock or crash before storage is flushed |

---

## Rule of Thumb

- Use **`dmesg`** for immediate, current, kernel-side inspection.
- Use **`journalctl -k`** for historical analysis, time filtering, and previous boots.
- Use **`journalctl --user -b`** for compositor, session, portal, and UWSM userspace failures.
- If the machine crashes hard enough that logs disappear, move to **`pstore`**, **`kdump`**, **`netconsole`**, or **serial logging**.
