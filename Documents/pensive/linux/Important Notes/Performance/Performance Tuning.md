# Arch Linux Performance Tuning and Diagnostics

A measurement-first reference for diagnosing slow boot, sluggish interactive behavior, thermal throttling, battery drain, storage stalls, and misbehaving applications on Arch Linux.

This note assumes a modern Arch system using `systemd`, cgroup v2, and a current kernel as of **March 2026**.

> [!important] Performance tuning workflow
> 1. **Reproduce the problem consistently.**
> 2. **Record a baseline** at idle and during the problematic workload.
> 3. **Change one variable at a time.**
> 4. **Re-test under the same conditions**: same kernel, same power source, same ambient temperature, same workload.

> [!warning] Avoid cargo-cult “tuning”
> Large copied `sysctl`, kernel, or “gaming optimization” bundles often reduce stability, waste power, or make latency worse. Arch defaults are generally sane. Tune only what you can measure.

---

## Prerequisites and Baseline

Before tuning, ensure the system is not simply outdated or misconfigured.

### Minimum sanity checks

- Fully update the system:
```bash
sudo pacman -Syu
```

- Verify that the correct CPU microcode package is installed:
```bash
pacman -Q amd-ucode intel-ucode 2>/dev/null
```

- Record the running kernel:
```bash
uname -r
```

- On laptops, test **AC power and battery power separately**. CPU limits, fan curves, and power profiles can differ significantly.

### Recommended toolset

These packages are useful and available from the official Arch repositories:

```bash
sudo pacman -S --needed \
  btop cpupower lm_sensors linux-tools perf powertop \
  strace stress-ng sysstat
```

Optional sensor setup:

```bash
sudo sensors-detect
sensors
```

> [!note]
> `sensors-detect` is not always necessary on modern hardware; many sensor drivers load automatically. Run it only if `sensors` shows incomplete data.

### Quick baseline snapshot

Use this before making changes:

```bash
systemd-analyze time
lscpu
free -h
swapon --show
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,ROTA
journalctl -b -p warning --no-pager
```

---

## Boot and Session Startup Analysis

Boot time and “time until desktop is usable” are related but not identical.

- **Firmware/UEFI time** is outside Linux.
- **Kernel + system services** are visible through `systemd-analyze`.
- **Graphical session startup** is often a **user service** problem, especially on Wayland systems and Hyprland setups launched through UWSM.

> [!tip] High firmware time
> If `systemd-analyze time` shows large **firmware** time, Linux service tuning will not meaningfully improve total boot time. Check UEFI settings, firmware updates, and slow device initialization in firmware.

### System boot with `systemd-analyze`

| Command | Purpose |
|---|---|
| `systemd-analyze time` | Shows firmware, bootloader, kernel, and userspace startup time. Running `systemd-analyze` without a subcommand is equivalent. |
| `systemd-analyze blame` | Lists units by activation time. Good for identifying slow units, but **not** for determining what actually delayed login. |
| `systemd-analyze critical-chain` | Shows the dependency chain that affected boot completion the most. Usually more useful than `blame`. |
| `systemd-analyze critical-chain <unit>` | Shows the critical chain for a specific unit such as `graphical.target` or `network-online.target`. |
| `systemd-analyze plot > boot.svg` | Generates an SVG timeline of the boot sequence. Useful for visual analysis. |

Example:

```bash
systemd-analyze time
systemd-analyze blame
systemd-analyze critical-chain
systemd-analyze critical-chain graphical.target
systemd-analyze plot > boot.svg
```

> [!note] Interpreting `blame`
> `systemd-analyze blame` is often misunderstood:
> - It does **not** account for parallel startup.
> - A unit with a large time may not delay the desktop at all.
> - `Type=simple` services often appear “fast” even if the program they launch is still doing work in the background.
> - `Type=oneshot` services can look especially slow.

### User-session startup with `systemd --user`

For desktop login delay, shell startup lag, portal issues, or compositor session stalls, inspect the **user manager**:

```bash
systemd-analyze --user blame
systemd-analyze --user critical-chain
systemctl --user list-unit-files --state=enabled --type=service
systemctl --user list-units --state=running --type=service
journalctl --user -b --no-pager
```

> [!important] Hyprland + UWSM
> If Hyprland is launched via **UWSM**, a large part of “startup performance” lives in `systemd --user`, not the system boot graph.
>
> Prefer long-lived session components as **user services** rather than starting the same programs from:
> - `hyprland.conf` `exec-once`
> - shell startup files
> - XDG autostart entries
> - UWSM/systemd user units
>
> Duplicate startup paths are a common cause of slow login, duplicate applets, and inconsistent behavior.

Useful session-specific checks:

```bash
journalctl --user -b -u xdg-desktop-portal.service --no-pager
journalctl --user -b -u xdg-desktop-portal-hyprland.service --no-pager
systemctl --user status xdg-desktop-portal.service xdg-desktop-portal-hyprland.service
```

### Managing services safely

List enabled services and sockets:

```bash
systemctl list-unit-files --state=enabled --type=service
systemctl list-unit-files --state=enabled --type=socket

systemctl --user list-unit-files --state=enabled --type=service
systemctl --user list-unit-files --state=enabled --type=socket
```

Disable a service immediately and prevent it from starting next boot:

```bash
sudo systemctl disable --now NetworkManager-wait-online.service
```

Check what depends on a target before disabling related services:

```bash
systemctl list-dependencies --reverse network-online.target
```

> [!warning] Disable carefully
> A service can be slow and still be necessary. Before disabling anything:
> - confirm what requires it
> - read the unit with `systemctl cat <unit>`
> - inspect logs with `journalctl -b -u <unit>`
>
> Also remember that a `.socket` unit can start a service even when the `.service` is disabled.

### Common startup bottlenecks

#### `*-wait-online.service`

On personal desktops, `NetworkManager-wait-online.service` is frequently unnecessary and can slow boot. It should only be disabled if nothing important needs `network-online.target`.

#### Problematic mounts in `/etc/fstab`

Slow or missing disks, removable drives, and network mounts commonly delay boot.

Look for mount-related delays:

```bash
systemd-analyze blame | grep -E '\.(mount|automount|swap)$'
```

Useful `fstab` options for unreliable or non-essential mounts:

- `nofail` — do not fail boot if the mount is unavailable
- `x-systemd.device-timeout=1s` — reduce wait time for missing devices
- `x-systemd.automount` — mount on first access instead of during boot
- `_netdev` — mark as network-dependent
- `x-systemd.mount-timeout=` — set a reasonable timeout for slow network mounts

> [!note]
> `nofail` prevents boot failure; it does **not** make a broken mount usable. Use it only for non-critical filesystems.

---

## CPU Throughput, Frequency Scaling, and Thermals

### Quick inspection

```bash
lscpu
cpupower frequency-info
sensors
```

Useful questions:

- Is the CPU reaching expected frequencies?
- Is the system thermally constrained?
- Is a conservative power profile active?
- Are all cores online?

### Stress-testing with `stress-ng`

`stress-ng` is excellent for thermal and stability testing, and for relative throughput comparisons **when the workload is fixed**.

Recommended all-core example:

```bash
stress-ng --cpu 0 --cpu-method matrixprod --timeout 60s --metrics-brief
```

Recommended single-core example pinned to CPU 4:

```bash
taskset -c 4 stress-ng --cpu 1 --cpu-method matrixprod --timeout 60s --metrics-brief
```

> [!caution] Single-core benchmarking
> If you pin `stress-ng` to one core with `taskset`, use **`--cpu 1`**.
>
> Using `--cpu 0` would spawn workers for all CPUs and force them to contend on one pinned core, invalidating the result.

> [!note] About `bogo ops/s`
> `stress-ng` reports **bogo ops/s**. This is useful for **relative comparison** only when all of the following stay constant:
> - same `stress-ng` version
> - same stressor method
> - same power profile
> - similar thermal conditions
>
> It is **not** a universal cross-system benchmark.

### Monitoring during load

Use a live monitor while stress-testing or benchmarking:

```bash
btop
```

Also useful:

```bash
watch -n1 sensors
```

### Checking power profile and governor

If `power-profiles-daemon` is installed and enabled:

```bash
powerprofilesctl get
powerprofilesctl list
powerprofilesctl set balanced
```

Inspect CPU frequency scaling:

```bash
cpupower frequency-info
```

Temporarily force a governor, if supported by the active scaling driver:

```bash
sudo cpupower frequency-set -g performance
```

> [!warning] Choose one power-management authority
> Do **not** let multiple tools fight over CPU and power settings. Typical conflicts include:
> - `power-profiles-daemon`
> - `TLP`
> - vendor utilities
> - custom `cpupower` scripts/services
>
> Use one primary policy manager and verify results after changes.

### Detecting thermal throttling

Look for thermal or machine-check messages:

```bash
journalctl -k -b --no-pager | grep -Ei 'throttl|thermal|mce'
```

If performance drops as temperatures rise, compare:

- initial frequency under load
- sustained frequency after 1–5 minutes
- temperature plateau
- fan behavior

A high initial score followed by rapid frequency collapse usually indicates thermal or power-limit throttling.

---

## Power Consumption and Battery Drain

### `turbostat` for supported Intel systems

`turbostat` is part of `linux-tools` and is one of the best low-level power/frequency monitors on **modern Intel** CPUs.

Discover supported columns first:

```bash
sudo turbostat --list
```

Example on a supported system:

```bash
sudo turbostat -i 1 --show Busy%,Bzy_MHz,PkgWatt
```

> [!note]
> `turbostat` is primarily an Intel tool. Some fields, especially package power readings such as `PkgWatt`, may be missing or not meaningful on AMD systems.

### Generic battery and power-supply data

For portable systems, direct sysfs reads are often the most reliable generic method:

```bash
grep . /sys/class/power_supply/BAT*/{status,power_now,current_now,voltage_now} 2>/dev/null
```

Common units:

- `power_now` → microwatts (`µW`)
- `current_now` → microamps (`µA`)
- `voltage_now` → microvolts (`µV`)

Not all batteries expose all three files.

### `powertop`

Launch interactive analysis:

```bash
sudo powertop
```

> [!warning] `powertop --auto-tune`
> `powertop --auto-tune` applies temporary tunings until reboot. It can also conflict with `TLP`, `power-profiles-daemon`, or your own udev/systemd tuning. Use it as a diagnostic aid, not as a blind permanent fix.

---

## Memory Pressure and Swap Behavior

“System feels slow” is often memory pressure, not CPU shortage.

### First checks

```bash
free -h
swapon --show --bytes
vmstat 1
grep . /proc/pressure/{cpu,io,memory}
```

### How to interpret the data

- In `vmstat 1`:
  - `si` / `so` > 0 repeatedly means active swap I/O
  - high `wa` means CPU is waiting on I/O
- In `/proc/pressure/memory`:
  - rising `some` means tasks are frequently delayed by memory pressure
  - rising `full` means the system is stalling severely

### Practical guidance

- If the system swaps under normal desktop use, consider:
  - reducing memory-heavy background services
  - enabling or resizing swap appropriately
  - using **zram** on low-memory systems
- Do not tune `vm.swappiness` blindly before confirming that swap behavior is actually the problem.

> [!tip]
> On modern Arch desktops and laptops, **zram** is often a better first improvement than aggressive swap sysctl tweaking when responsiveness under memory pressure matters.

---

## Storage and I/O Bottlenecks

Storage latency often feels like “random slowness”.

### Device and filesystem overview

```bash
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,ROTA,DISC-GRAN,DISC-MAX
```

### Real-time I/O diagnostics

From `sysstat`:

```bash
iostat -xz 1
pidstat -dur 1
```

What to watch:

- `iostat`
  - high `%util`
  - high `await`
  - queue buildup
- `pidstat`
  - which process is generating I/O
  - whether the same process is also CPU-heavy or faulting memory

Check the kernel log for device or filesystem problems:

```bash
journalctl -k -b -p warning --no-pager
```

### SSD/NVMe maintenance

If the device supports discard/TRIM, enable periodic TRIM:

```bash
sudo systemctl enable --now fstrim.timer
```

> [!note]
> Periodic TRIM is normal maintenance for SSD/NVMe devices. It is not a cure for every performance issue, but it helps preserve long-term write performance.

---

## Application-Level Diagnostics

When one application is slow, trace the application directly instead of tuning the whole system.

### `strace`

Use `strace` when you need to know:

- which files are being accessed
- whether a process is blocked on syscalls
- whether it is repeatedly failing on permissions, sockets, or IPC
- which child processes it spawns

#### Trace from launch

```bash
strace -f -tt -T -o trace.log myapp
```

#### Attach to a running process

```bash
strace -f -tt -T -o trace.log -p <PID>
```

#### Summarize syscall time by type

```bash
strace -c -f myapp
```

#### Trace file activity only

```bash
strace -f -e trace=file -tt -T -o trace.log myapp
```

> [!note]
> `-f` is important for modern desktop applications because they often spawn helper processes. Without it, you may miss the actual source of the delay.

> [!warning]
> Attaching to a running process may require the same UID or root, depending on ptrace restrictions and how the process was launched.

### `perf`

Use `perf` when the application is CPU-hot and you need real profiling rather than syscall tracing.

#### Quick counter-based measurement

```bash
perf stat -r 5 -- your_command
```

#### Sample a running workload

```bash
sudo perf record -g -- your_command
sudo perf report
```

#### Live hotspot view

```bash
sudo perf top
```

> [!note]
> Depending on `kernel.perf_event_paranoid` and the event type, some `perf` commands may require root or relaxed perf permissions.

---

## Resource Control and Throttling

The original “limit a PID” approach is better replaced by **systemd/cgroup-based control** on modern Arch.

### Preferred: launch the workload in a constrained transient scope

Example: cap CPU and memory for a command launched from the current user session:

```bash
systemd-run --user --scope \
  -p CPUQuota=50% \
  -p MemoryHigh=1G \
  -p MemoryMax=2G \
  some-command
```

> [!note] `CPUQuota=` semantics
> `CPUQuota=100%` means **one full CPU worth** of time.
>
> Examples:
> - `50%` = half of one CPU
> - `200%` = two CPUs worth
> - `400%` = four CPUs worth

### Apply limits to an existing systemd unit

Temporary runtime limit:

```bash
sudo systemctl set-property --runtime some.service CPUQuota=50%
systemctl --user set-property --runtime app.service MemoryHigh=1G
```

### Persistent unit override

Create an override:

```bash
systemctl --user edit app.service
```

Example drop-in:

```ini
[Service]
CPUQuota=50%
MemoryHigh=1G
MemoryMax=2G
IOWeight=200
```

Then restart the service:

```bash
systemctl --user restart app.service
```

### Inspect cgroup usage

```bash
systemd-cgtop
```

### Lightweight scheduling tools

For non-critical ad hoc deprioritization:

```bash
nice -n 10 some-command
ionice -c2 -n7 some-command
```

> [!note]
> `nice` affects CPU scheduling priority. `ionice` affects block I/O scheduling priority where supported. Neither is a hard cap.

> [!warning]
> For arbitrary already-running non-systemd processes, cgroup control is easiest if you **restart the program under `systemd-run --scope`**. Retrofitting precise limits onto random existing PIDs is less clean than controlling the service or scope that launched them.

---

## Quick Triage Matrix

| Symptom | First tools to use | Common causes |
|---|---|---|
| Slow boot | `systemd-analyze time`, `critical-chain`, `journalctl -b -p warning` | wait-online units, broken mounts, slow firmware |
| Slow login to desktop / Hyprland | `systemd-analyze --user blame`, `journalctl --user -b`, portal service logs | duplicate autostarts, slow user services, portal/polkit/session issues |
| CPU slower after a minute of load | `stress-ng`, `sensors`, `turbostat`/`cpupower` | thermal throttling, restrictive power profile |
| Fans loud, battery draining fast | `powertop`, battery sysfs, `powerprofilesctl`, `turbostat` | runaway background tasks, aggressive power profile, conflicting power tools |
| UI stutter while apps are open | `vmstat 1`, PSI files, `free -h`, `swapon --show` | memory pressure, swap thrash, I/O stalls |
| System “hangs” during file operations | `iostat -xz 1`, `pidstat -dur 1`, kernel journal | failing drive, saturated storage, bad mount options |
| One application is slow | `strace`, `perf stat`, `perf record/report` | syscall waits, file/IPC issues, CPU hotspots |

---

## Arch-Specific Guidance

- Keep firmware, kernel, and microcode current before tuning.
- Treat alternative kernels such as `linux-zen` as **benchmarkable options**, not guaranteed upgrades.
- On modern desktop systems, interactive latency problems are often caused by:
  - memory pressure
  - user-session startup duplication
  - portal/session-service delays
  - thermal or power limits
  - storage stalls  
  rather than by a single obviously “slow” boot service.

---

## Related Notes

- [[Install Kernel and Base Packages]] — kernel choices, microcode, and base system components
- [[Disk Swap]] — swap fundamentals and operational guidance
- [[Tuning Swap Performance (Advanced)]] — swap priority, swappiness, and advanced memory-pressure tuning

> [!tip]
> If this system uses Hyprland with UWSM, keep a separate note for:
> - user service design
> - portal configuration
> - display manager or greetd startup path
> - duplicate autostart elimination
>
> Those are often the real source of “desktop performance” issues on otherwise fast systems.
