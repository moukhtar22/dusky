# Managing and Monitoring RAM on Arch Linux

> [!summary]
> For fast triage on a modern Arch Linux system:
> ```bash
> free -h -w
> swapon --show --output NAME,TYPE,SIZE,USED,PRIO
> ps -eo pid,user,%mem,rss,command --sort=-rss | head -n 16
> vmstat 1
> cat /proc/pressure/memory
> zramctl
> df -hT
> ```
> The most important memory metric for day-to-day capacity checks is usually **`available`**, not **`free`**.

> [!warning]
> Low **`free`** RAM is normal on Linux. Unused memory is aggressively repurposed for page cache and other reclaimable data.  
> Investigate memory pressure when **`available` is low**, **swap activity is sustained**, **PSI rises**, or the system becomes unresponsive.

## Scope

This note applies to current Arch Linux systems as of **March 2026**, including desktops running **Wayland**, **Hyprland**, and sessions managed by **systemd/UWSM**. Memory accounting is kernel- and systemd-based; the same core tools work regardless of compositor.

## Optional Tools

Most core commands below are already present on a standard Arch install:

- `free`, `ps`, `vmstat` → `procps-ng`
- `df`, `du` → `coreutils`
- `swapon`, `zramctl`, `findmnt` → `util-linux`
- `systemd-cgtop`, `systemd-cgls`, `journalctl` → `systemd`

Useful optional packages:

```bash
sudo pacman -S --needed htop btop smem inxi zram-generator
```

| Tool | Purpose |
|---|---|
| `htop` / `btop` | Interactive process monitoring |
| `smem` | More accurate per-process memory accounting via **PSS** |
| `inxi` | Fast system summary; convenient but not authoritative |
| `zram-generator` | Preferred systemd-based zram setup on Arch |

---

## Quick Memory Checks

### Overall RAM and Swap

```bash
free -h -w
swapon --show --output NAME,TYPE,SIZE,USED,PRIO
```

- `free -h -w` gives a human-readable overview of physical memory and swap.
- `-w` splits **buffers** and **cache** into separate columns instead of combining them into `buff/cache`.
- `swapon --show` lists active swap devices/files, including zram if configured.

For a continuously updating view:

```bash
watch -n 1 'free -h -w; echo; swapon --show --output NAME,TYPE,SIZE,USED,PRIO'
```

### Interpreting `free`

Example:

```text
               total        used        free      shared     buffers       cache   available
Mem:            31Gi       4.8Gi        18Gi       650Mi       210Mi       7.2Gi        25Gi
Swap:           32Gi       256Mi        31Gi
```

| Column | Meaning |
|---|---|
| `total` | RAM visible to the kernel |
| `used` | Derived value; includes non-free memory and is **not** the best health indicator |
| `free` | Completely unused RAM |
| `shared` | Shared memory / tmpfs / `memfd` / `shmem` usage |
| `buffers` | Block device metadata buffers |
| `cache` | Page cache and reclaimable cached data |
| `available` | Kernel estimate of memory that can be allocated without heavy swapping |

> [!tip]
> For capacity planning and troubleshooting, trust **`available`** much more than **`free`**.

> [!note]
> On Wayland systems, especially with browsers, Electron apps, portals, Xwayland, and `wl_shm` clients, the **`shared`** column can increase because of tmpfs- and `memfd`-backed shared memory. This is normal.

---

## Real-Time Memory Pressure

### `vmstat`

```bash
vmstat 1
```

Watch these columns:

| Column | Meaning | What to look for |
|---|---|---|
| `si` | Swap in | Sustained non-zero values indicate pressure |
| `so` | Swap out | Sustained non-zero values indicate pressure |
| `r` | Runnable tasks | High values may indicate CPU contention too |
| `wa` | I/O wait | Can rise during swap thrashing |
| `free` / `buff` / `cache` | Instant memory breakdown | Less important than `si/so` and responsiveness |

If `si`/`so` stay non-zero for long periods and the desktop stutters, the system is under real memory pressure.

### PSI: Pressure Stall Information

```bash
cat /proc/pressure/memory
```

Example:

```text
some avg10=0.00 avg60=0.12 avg300=0.05 total=12345678
full avg10=0.00 avg60=0.03 avg300=0.01 total=2345678
```

- `some`: at least one task was stalled waiting on memory pressure
- `full`: all non-idle tasks were stalled

Sustained non-zero `avg10` / `avg60` values correlate strongly with visible slowdown.

> [!tip]
> PSI is often a better indicator of desktop pain than raw “used RAM”.

---

## Finding What Is Using Memory

## Process-Level View with `ps`

Sort processes by **RSS** (resident memory currently in RAM):

```bash
ps -eo pid,user,%mem,rss,command --sort=-rss | head -n 16
```

Notes:

- `rss` is reported in **KiB**
- `RSS` is useful for quick ranking, but it **overstates** usage when many pages are shared
- Browsers, Electron, and GUI toolkits often share libraries and memory mappings

### More Accurate Accounting with `smem`

Install if needed:

```bash
sudo pacman -S --needed smem
```

Sort by **PSS**:

```bash
smem -s pss -r | head -n 16
```

Important terms:

| Metric | Meaning |
|---|---|
| `USS` | Unique Set Size; private memory only |
| `PSS` | Proportional Set Size; shared pages divided fairly among processes |
| `RSS` | Resident Set Size; shared pages counted in full for every process |

> [!tip]
> If you want the least misleading “who is actually costing me RAM?” answer, use **PSS** from `smem`.

### Single-Process Deep Dive

For one PID:

```bash
grep -E '^(Rss|Pss|Private_(Clean|Dirty)|Shared_(Clean|Dirty)|Swap):' /proc/<PID>/smaps_rollup
```

This gives a compact summary from the kernel's per-mapping accounting.

Alternative:

```bash
pmap -x <PID> | tail -n 1
```

> [!note]
> Access to `/proc/<PID>/smaps*` may require `sudo` if the process belongs to another user or if `/proc` is mounted with restrictive options such as `hidepid`.

---

## Cgroup / systemd View

Modern Arch uses **cgroups v2**, and this view is often more useful than raw process lists on a desktop.

### Top-Like Cgroup View

```bash
systemd-cgtop
```

This shows resource usage per cgroup rather than per PID.

### Tree View

```bash
systemd-cgls
```

In a **UWSM-managed Wayland session**, many desktop components and application launches are visible as systemd user scopes/services, so cgroup-based views can make leaks or heavy apps easier to identify than a flat `ps` output.

> [!tip]
> If Hyprland, portals, notification daemons, browsers, or launchers were started as systemd-managed user units/scopes, `systemd-cgtop` can reveal which subtree is growing even when the individual process list is noisy.

---

## `inxi`: Convenient Summary, Not Primary Accounting

`inxi` is useful for a fast human-readable overview, but it is not the most precise tool for memory attribution.

Install:

```bash
sudo pacman -S --needed inxi
```

Useful commands:

```bash
inxi -m
inxi -t m10
```

- `inxi -m` → memory summary
- `inxi -t m10` → top 10 processes by memory

> [!note]
> `sudo` is **not normally required** for `inxi` on a default Arch install. Use it only if you need broader visibility or your `/proc` access is restricted.

---

## Swap: What to Check

### Show Active Swap

```bash
swapon --show --output NAME,TYPE,SIZE,USED,PRIO
cat /proc/swaps
```

### Important Interpretation

- **Some swap use is normal**
- Linux may move cold anonymous pages to swap even when RAM is not fully exhausted
- Swap use alone is not a problem; **sustained swap I/O and rising PSI are**

If swap is active and the system remains responsive, that may be acceptable behavior.

---

## ZRAM

## What ZRAM Is

**zram** creates a **compressed block device in RAM**. The most common use is **swap on zram**, which improves effective memory capacity by compressing infrequently used pages.

### Why Use It

- Reduces or delays disk-backed swapping
- Helps low-memory systems significantly
- Often improves responsiveness under memory pressure
- Particularly useful on laptops and desktops without fast dedicated swap media

### Trade-Offs

- Uses CPU time for compression/decompression
- Does **not** replace disk-backed swap for **hibernation**
- The configured zram “disk size” is a **logical limit**, not equal to physical RAM consumption

> [!warning]
> If you use **suspend-to-disk / hibernation**, you still need a real swap partition or swap file large enough for the hibernation image.  
> **zram swap cannot store a hibernation image.**

---

## Recommended Arch Setup: `zram-generator`

Install:

```bash
sudo pacman -S --needed zram-generator
```

Create `/etc/systemd/zram-generator.conf`:

```ini
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
swap-priority = 100
fs-type = swap
```

This is a sensible starting point for many desktop systems:

- `ram / 2` → logical zram size equal to half of physical RAM
- `zstd` → strong default balance of compression ratio and speed on modern CPUs
- `swap-priority = 100` → prefer zram over lower-priority disk swap

Apply the configuration by rebooting, or reload systemd and start the generated unit:

```bash
sudo systemctl daemon-reload
sudo systemctl start systemd-zram-setup@zram0.service
```

Verify:

```bash
systemctl status systemd-zram-setup@zram0.service
swapon --show --output NAME,TYPE,SIZE,USED,PRIO
zramctl
```

> [!note]
> There is no universally perfect zram size. `ram / 2` is a good baseline, but workloads with large compressible memory footprints may benefit from more. Monitor with `zramctl` before tuning.

---

## Monitoring ZRAM with `zramctl`

`zramctl` is part of **util-linux** and is normally already installed on Arch.

### Basic Status

```bash
zramctl
```

### Custom Output

```bash
zramctl -o NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL,COMP-RATIO,STREAMS,MOUNTPOINT
```

Example:

```text
NAME       ALGORITHM DISKSIZE DATA   COMPR  TOTAL  COMP-RATIO STREAMS MOUNTPOINT
/dev/zram0 zstd          16G   2.4G   520M   640M        3.75      16 [SWAP]
```

### Column Meanings

| Column | Meaning |
|---|---|
| `NAME` | zram device name |
| `ALGORITHM` | Compression algorithm in use |
| `DISKSIZE` | Maximum logical uncompressed size |
| `DATA` | Current uncompressed data stored |
| `COMPR` | Current compressed payload size |
| `TOTAL` | Actual RAM consumed, including metadata and allocator overhead |
| `COMP-RATIO` | Compression ratio reported by `zramctl` |
| `STREAMS` | Concurrent compression streams |
| `MOUNTPOINT` | Usually `[SWAP]` if used as swap |

### How to Read It

- Compare **`DATA`** with **`TOTAL`**
- `TOTAL` is the number that matters most for real RAM cost
- `COMPR` is smaller because it excludes some overhead and fragmentation

If `DATA` is much larger than `TOTAL`, compression is working well.

---

## `tmpfs`, `ramfs`, and “RAM Disk” Terminology

These are not the same thing.

### `tmpfs`

`tmpfs` is the normal Linux memory-backed filesystem.

Key properties:

- Uses memory **on demand**
- **Not preallocated**
- Pages can be **swapped out**
- Has a configurable **size limit**
- Commonly used for `/tmp`, `/run`, `/dev/shm`, and sometimes browser caches

Check tmpfs mounts:

```bash
findmnt -t tmpfs
df -hT /tmp /dev/shm
```

> [!note]
> `tmpfs` uses virtual memory, not “RAM only”. Under pressure, tmpfs pages may be swapped.

### `ramfs`

`ramfs` is usually **not** what you want:

- unswappable
- no size limit by default
- can consume all available memory and destabilize the system

> [!warning]
> Avoid `ramfs` unless you specifically need its semantics and understand the risk. For almost all cases, use **`tmpfs`** instead.

### Traditional Block RAM Disk

A true block-device RAM disk also exists in Linux, but it is much less common on modern desktops. For temporary high-speed storage, `tmpfs` is usually the correct choice.

---

## Disk Space Monitoring

Disk space is not RAM, but it still affects system health, logging, package upgrades, and swap files.

### Filesystem Usage

```bash
df -hT
```

Useful because it shows:

- filesystem type
- total size
- used space
- available space
- mount point

> [!note]
> `df` reports **all mounted filesystems**, including **`tmpfs`**.  
> A `tmpfs` line is **memory-backed**, not disk-backed.

### Directory-Level Usage

When `df` shows a filesystem is full, use `du` to find where the space went:

```bash
du -xh --max-depth=1 /var 2>/dev/null | sort -h
```

Common high-growth locations on Arch:

- `/var/cache/pacman/pkg`
- `/var/log`
- `/home/*/.cache`
- container / VM images
- build directories

### Btrfs Note

If your root filesystem is **Btrfs**, `df` is only a first approximation. For allocator/profile-aware details, use:

```bash
btrfs filesystem usage -T /
```

---

## Kernel Memory and “Missing RAM”

If no user process seems large enough to explain memory usage, inspect kernel-side consumers.

```bash
grep -E '^(Slab|SReclaimable|SUnreclaim|KernelStack|PageTables|Shmem):' /proc/meminfo
```

For slab caches:

```bash
sudo slabtop
```

Common explanations:

- large page cache
- growing slab caches
- tmpfs / shared memory
- kernel objects from containers, filesystems, networking, or drivers

> [!note]
> GPU VRAM is separate from normal system RAM and is not shown by `free` or `ps`.  
> If you are debugging graphical slowdowns on Hyprland/Wayland, check both **RAM** and **VRAM**.

---

## OOM and Memory-Failure Troubleshooting

### Kernel OOM Killer Messages

```bash
journalctl -k -b -g -i 'oom|out of memory'
```

### `systemd-oomd` Events

If `systemd-oomd` is enabled on your system:

```bash
journalctl -u systemd-oomd -b
oomctl
```

`systemd-oomd` uses cgroup-aware memory pressure signals and may terminate memory-hungry workloads before the kernel OOM killer acts.

---

## Practical Interpretation Guide

| Symptom | Usually Means | Check |
|---|---|---|
| `used` is high but `available` is also high | Normal Linux caching behavior | `free -h -w` |
| `free` is low but no slowdown | Usually fine | `available`, `vmstat 1`, PSI |
| Swap is non-zero but stable | Often normal | `swapon --show`, `vmstat 1` |
| `si`/`so` stay active and desktop stutters | Real memory pressure / thrashing | `vmstat 1`, PSI, `zramctl` |
| `shared` is high on Wayland | Often tmpfs / `memfd` / shared buffers | `free -h`, `findmnt -t tmpfs` |
| No process looks large enough | Kernel memory or shared pages involved | `/proc/meminfo`, `slabtop`, `smem` |
| `df` shows a full tmpfs | Memory-backed filesystem is full, not disk | `df -hT`, `findmnt -t tmpfs` |
| zram `DATA` is much larger than `TOTAL` | zram compression is effective | `zramctl` |

---

## Minimal Command Reference

```bash
# Overall memory
free -h -w

# Swap devices
swapon --show --output NAME,TYPE,SIZE,USED,PRIO

# Top processes by RSS
ps -eo pid,user,%mem,rss,command --sort=-rss | head -n 16

# More accurate per-process memory (PSS)
smem -s pss -r | head -n 16

# Real-time pressure
vmstat 1
cat /proc/pressure/memory

# Cgroup/systemd view
systemd-cgtop
systemd-cgls

# ZRAM
zramctl
zramctl -o NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL,COMP-RATIO,STREAMS,MOUNTPOINT

# tmpfs mounts
findmnt -t tmpfs
df -hT /tmp /dev/shm

# Disk usage
df -hT
du -xh --max-depth=1 /var 2>/dev/null | sort -h

# Btrfs-specific disk accounting
btrfs filesystem usage -T /

# OOM diagnostics
journalctl -k -b -g -i 'oom|out of memory'
journalctl -u systemd-oomd -b
oomctl
```

> [!tip]
> If you only remember three things:
> 1. Use **`available`**, not `free`, to judge RAM headroom  
> 2. Use **`smem`** or **PSS** when `RSS` is misleading  
> 3. Use **PSI**, **swap activity**, and **responsiveness** to decide whether memory pressure is real
