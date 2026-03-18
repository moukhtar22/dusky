# Hardware & Resource Diagnostics on Arch Linux

> [!summary] Quick reference
> | Task | Primary command |
> | --- | --- |
> | PCI devices + bound driver | `lspci -nnk` |
> | USB topology | `lsusb -t` |
> | Block devices + filesystems | `lsblk -e7 -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL` |
> | Resolve a path to its mount | `findmnt -T /path` |
> | Current-boot kernel/device logs | `journalctl -b -k` |
> | Full hardware inventory | `sudo lshw -short` |
> | Listening ports | `ss -ltnup` or `sudo lsof -nP -iTCP -sTCP:LISTEN` |
> | Deleted files still consuming space | `sudo lsof +L1` |
> | Busy mount / unmount failure | `sudo fuser -vm /mountpoint` |
> | CPU, memory, and I/O pressure | `vmstat 1`, `iostat -xz 1`, `/proc/pressure/*` |

This note is a permanent reference for **hardware discovery**, **driver verification**, **kernel-side troubleshooting**, and **live resource diagnostics** on Arch Linux.

## Packages and prerequisites

A standard Arch install already includes most foundational tools from `util-linux`, `procps-ng`, `iproute2`, and `systemd`. Install the common diagnostic extras if they are missing:

```bash
sudo pacman -S pciutils usbutils lshw lsof dmidecode hwinfo lm_sensors smartmontools nvme-cli sysstat iotop
```

### Tool-to-package map

| Tool | Package |
| --- | --- |
| `lspci` | `pciutils` |
| `lsusb`, `usb-devices` | `usbutils` |
| `lshw` | `lshw` |
| `lsof` | `lsof` |
| `dmidecode` | `dmidecode` |
| `hwinfo` | `hwinfo` |
| `sensors`, `sensors-detect` | `lm_sensors` |
| `smartctl` | `smartmontools` |
| `nvme` | `nvme-cli` |
| `iostat`, `pidstat`, `mpstat` | `sysstat` |
| `iotop` | `iotop` |

> [!note]
> Many commands in this note produce **partial output without root**. For complete results—especially for `lshw`, `dmidecode`, `lsof`, `smartctl`, `nvme`, and some `lsusb -v` or `dmesg` use cases—run them via `sudo`.

---

## Fast triage workflow

For a new hardware problem or general system inspection, start here:

```bash
journalctl -b -p warning..alert
journalctl -b -k

lspci -nnk
lsusb -t

lsblk -e7 -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL
findmnt

lscpu
free -h
swapon --show --output=NAME,TYPE,SIZE,USED,PRIO

df -hT
df -ih
```

### What this gives you

- **`journalctl -b -k`**: kernel messages for the current boot; preferred over raw `dmesg` for persistent, timestamped logs.
- **`lspci -nnk`**: PCI devices, numeric IDs, and the driver currently bound.
- **`lsusb -t`**: USB topology and negotiated speeds.
- **`lsblk` / `findmnt`**: storage layout, filesystems, and mount relationships.
- **`lscpu` / `free -h` / `swapon`**: CPU topology, RAM, and swap state.
- **`df -hT` / `df -ih`**: space and inode usage.

---

## Hardware enumeration

## PCI devices: `lspci`

`lspci` is the primary tool for enumerating **PCI/PCIe devices** such as GPUs, NICs, Wi-Fi cards, NVMe controllers, SATA/AHCI controllers, USB controllers, and audio devices.

### Core commands

```bash
lspci
lspci -nn
lspci -nnk
lspci -tv
lspci -D
sudo lspci -vv
```

### Most useful options

| Option | Meaning |
| --- | --- |
| `-n` | Show numeric vendor/device IDs only |
| `-nn` | Show names plus numeric IDs |
| `-k` | Show kernel driver in use and candidate modules |
| `-t` | Show a bus tree |
| `-D` | Show full PCI domain:bus:device.function addresses |
| `-vv` / `-vvv` | Very verbose device details; root recommended |

### High-value examples

#### Show all PCI devices with driver bindings

```bash
lspci -nnk
```

#### Inspect graphics devices specifically

```bash
lspci -nnk | grep -A3 -E 'VGA|3D|Display'
```

#### Inspect network-related PCI devices

```bash
lspci -nnk | grep -A3 -E 'Ethernet|Network'
```

> [!tip]
> `lspci` confirms that the system has **enumerated** the PCI device. It does **not** by itself prove that the device is functional, has firmware loaded, or is working correctly under load.

### If device names are missing or outdated

Refresh the PCI ID database:

```bash
sudo update-pciids
```

---

## USB devices: `lsusb`, `usb-devices`

Use `lsusb` for USB enumeration and topology. This is the USB equivalent of `lspci`.

### Core commands

```bash
lsusb
lsusb -t
sudo lsusb -v
usb-devices
```

### Recommended usage

#### Show all USB devices

```bash
lsusb
```

#### Show bus topology and speeds

```bash
lsusb -t
```

#### Show verbose descriptors

```bash
sudo lsusb -v
```

#### Show parsed per-device information

```bash
usb-devices
```

### When hotplug is failing

Monitor kernel and udev events live:

```bash
udevadm monitor --kernel --udev --property
```

If names look stale or generic, refresh the USB ID database:

```bash
sudo update-usbids
```

---

## Block devices, partitions, and mounts

For storage inventory, `lsblk` and `findmnt` are usually more useful than `lshw`.

### Core commands

```bash
lsblk
lsblk -f
lsblk -e7 -o NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL
blkid
findmnt
findmnt -T /path/to/file
```

### Recommended usage

#### Show block devices and filesystems

```bash
lsblk -f
```

#### Show a fuller hardware-oriented view

```bash
lsblk -e7 -o NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL
```

#### Resolve an arbitrary path to its mount

```bash
findmnt -T /path/to/file
```

#### Read filesystem signatures directly

```bash
sudo blkid
```

> [!note]
> `lsblk` reads topology and mount information from sysfs and userspace metadata. `blkid` is the authoritative tool for on-disk filesystem signatures and UUIDs.

---

## CPU, memory, firmware, and platform identity

### CPU and topology

```bash
lscpu
lscpu -e=CPU,CORE,SOCKET,NODE,ONLINE
```

Useful for:
- verifying SMT/hyperthreading layout
- checking NUMA node topology
- confirming virtualization flags and CPU capabilities

### Memory and swap overview

```bash
free -h
swapon --show --output=NAME,TYPE,SIZE,USED,PRIO
zramctl
```

> [!tip]
> In `free -h`, the most important field is usually **`available`**, not **`free`**.

### Firmware / SMBIOS / DMI

```bash
hostnamectl
sudo dmidecode -t system -t bios -t baseboard -t memory
```

`hostnamectl` provides a fast summary on modern systemd systems, including:
- hardware vendor/model
- firmware version
- architecture
- virtualization status

`dmidecode` reads SMBIOS/DMI tables from firmware and is often useful for:
- motherboard and BIOS versions
- memory slot population
- chassis and system serials

> [!warning]
> DMI/SMBIOS data is firmware-supplied and is not always correct. Vendors sometimes omit fields or ship incorrect values. Treat it as informative, not infallible.

---

## Full hardware inventory: `lshw`

`lshw` provides a broad system inventory by combining data from **sysfs**, **DMI/SMBIOS**, device metadata, and kernel-exposed information. It is useful as a **single consolidated report**, but it is not always the most authoritative tool for a specific subsystem.

### Recommended commands

```bash
sudo lshw -short
sudo lshw -businfo
sudo lshw
sudo lshw -class display -class network -class storage
sudo lshw -json -sanitize > hardware-inventory.json
```

### Useful modes

| Command | Purpose |
| --- | --- |
| `sudo lshw -short` | Concise summary |
| `sudo lshw -businfo` | Bus-centric view |
| `sudo lshw` | Full detailed report |
| `sudo lshw -class ...` | Filter by hardware class |
| `sudo lshw -json -sanitize` | Machine-readable inventory safe to share |

### Important accuracy notes

- Root is strongly recommended; otherwise output may be incomplete.
- `lshw` is excellent for inventory, but for **driver binding** prefer:
  - `lspci -k`
  - `lsusb -v`
  - `lsblk`
  - `udevadm info`
  - `journalctl -b -k`
- Some modern devices may be described more clearly by subsystem-specific tools than by `lshw`.

> [!note]
> If you need a second opinion, `hwinfo --short` is a useful complementary inventory tool.

---

## Kernel, driver, and hotplug diagnostics

Enumeration alone is not enough. The next step is to confirm:
1. which driver bound to the device
2. whether firmware loaded
3. whether the kernel logged errors during probe or runtime

### Kernel logs

```bash
journalctl -b -k
journalctl -b -p warning..alert
sudo dmesg --level=err,warn
```

### Driver inspection

```bash
lsmod
modinfo <module_name>
```

Examples:

```bash
modinfo amdgpu
modinfo iwlwifi
modinfo nvme
```

> [!note]
> `lsmod` only lists **loadable kernel modules** currently loaded. Drivers built directly into the kernel will not appear there.

### Udev inspection

```bash
udevadm info --query=all --name=/dev/nvme0n1
udevadm info --query=all --name=/dev/dri/card0
udevadm monitor --kernel --udev --property
```

### GPU-specific checks

```bash
lspci -nnk | grep -A3 -E 'VGA|3D|Display'
ls -l /dev/dri
journalctl -b -k | grep -iE 'drm|amdgpu|i915|xe|nvidia|nouveau'
```

This helps verify:
- the GPU is enumerated
- the DRM device nodes exist
- the expected driver actually initialized

---

## Sensors and storage health

## Thermal and fan sensors

```bash
sensors
watch -n 1 sensors
sudo sensors-detect
```

> [!warning]
> Run `sensors-detect` only if sensor data is missing and you understand the prompts. It probes hardware monitoring chips and may suggest loading additional kernel modules.

## Disk health: SMART and NVMe

### SATA / SAS / many USB bridges

```bash
sudo smartctl -x /dev/sdX
```

### NVMe

```bash
sudo nvme list
sudo nvme smart-log /dev/nvme0
sudo nvme id-ctrl /dev/nvme0
```

> [!note]
> `smartctl` also supports NVMe, but `nvme-cli` exposes more controller-specific detail.

> [!note]
> Some USB-to-SATA bridges require an explicit device type, for example:
> ```bash
> sudo smartctl -d sat -x /dev/sdX
> ```

---

## Live resource diagnostics

## CPU and memory usage

### Interactive overview

```bash
top
top -o %CPU
top -o %MEM
```

### Snapshot of heavy processes

```bash
ps -eo pid,ppid,user,%cpu,%mem,rss,stat,comm --sort=-%cpu | head
ps -eo pid,ppid,user,%cpu,%mem,rss,stat,comm --sort=-%mem | head
```

### System-wide trends

```bash
free -h
vmstat 1
mpstat -P ALL 1
pidstat -dru 1
```

Interpretation highlights:
- `vmstat`: non-zero `si`/`so` indicates swap-in/swap-out activity
- `mpstat -P ALL 1`: per-CPU utilization and imbalance
- `pidstat -dru 1`: per-process disk, CPU, and memory activity

## Filesystem space and I/O

```bash
df -hT
df -ih
du -xhd1 /var | sort -h
iostat -xz 1
sudo iotop -oPa
```

### What to use when

- **`df -hT`**: free space by filesystem
- **`df -ih`**: inode exhaustion
- **`du -xhd1`**: large directories without crossing filesystem boundaries
- **`iostat -xz 1`**: device throughput, queueing, and utilization
- **`iotop -oPa`**: processes actively doing I/O

> [!tip]
> If writes fail but `df -h` still shows free space, check `df -ih`. You may be out of **inodes**, not bytes.

## Pressure Stall Information (PSI)

Modern Linux kernels expose resource pressure in `/proc/pressure/`.

```bash
for res in cpu io memory; do
  printf '== %s ==\n' "$res"
  cat "/proc/pressure/$res"
done
```

This is useful for diagnosing:
- brief stalls that do not show up clearly in averages
- I/O contention
- memory reclaim pressure

---

## Network sockets and port ownership

For socket inspection, `ss` is usually faster than `lsof`. Use `lsof` when you want **process + file-descriptor context**.

### `ss` examples

```bash
ss -ltnup
ss -tunap
```

### `lsof` examples

```bash
sudo lsof -nP -iTCP -sTCP:LISTEN
sudo lsof -nP -iTCP:443
sudo lsof -nP -i :53
sudo lsof -nP -U
```

Interpretation:
- `-nP`: disable DNS and service-name resolution; faster and less ambiguous
- `-iTCP -sTCP:LISTEN`: only listening TCP sockets
- `-iTCP:443`: anything using TCP port 443
- `-U`: UNIX domain sockets

> [!note]
> Without root, both `ss` and `lsof` may hide process ownership for sockets belonging to other users or privileged services.

---

## Open files and file descriptors: `lsof`

`lsof` shows which processes have which **file descriptors** open. This includes:
- regular files
- directories
- block devices
- character devices
- pipes
- sockets
- anonymous inodes and other kernel-backed objects

It is one of the most valuable tools for diagnosing:
- `umount: target is busy`
- `Address already in use`
- `Too many open files`
- disk space not reclaimed after deleting logs or databases

## General guidance

```bash
lsof
```

Running `lsof` with no filters is often slow and noisy. In practice, always add filters and usually add `-nP`.

### Common output fields

| Field | Meaning |
| --- | --- |
| `COMMAND` | Process name |
| `PID` | Process ID |
| `USER` | Owner |
| `FD` | File descriptor or special entry (`cwd`, `txt`, `mem`, `rtd`, etc.) |
| `TYPE` | File type (`REG`, `DIR`, `CHR`, `IPv4`, `IPv6`, `unix`, etc.) |
| `NAME` | Path, socket endpoint, or object description |

### `FD` values to know

| FD | Meaning |
| --- | --- |
| `cwd` | Current working directory |
| `rtd` | Process root directory |
| `txt` | Program text / executable |
| `mem` | Memory-mapped file |
| `0`, `1`, `2`, ... | Regular numeric file descriptors |

Suffixes like `r`, `w`, or `u` indicate read, write, or read/write mode.

---

## Common `lsof` workflows

### Find which process has a specific file open

```bash
sudo lsof -nP -- /path/to/file
```

Use `--` before the path to stop option parsing safely.

### Find open files in a directory

```bash
sudo lsof +d /path/to/directory
```

This checks the directory itself and entries immediately under it.

### Find open files anywhere under a directory tree

```bash
sudo lsof +D /path/to/directory
```

> [!warning]
> `lsof +D` is **expensive**. It recursively walks the directory tree before reporting results and can be slow on large, remote, or deeply nested filesystems.

### Find which process is listening on a TCP port

```bash
sudo lsof -nP -iTCP:80 -sTCP:LISTEN
```

This is the correct form when you need the **listener**, not every socket that happens to use port `80`.

### Find any socket using a port

```bash
sudo lsof -nP -i :80
```

This may include:
- listeners
- clients connected to that port
- loopback traffic

### List files opened by a specific PID

```bash
sudo lsof -nP -p <PID>
```

### Show a process's working directory, executable, and mappings

```bash
sudo lsof -nP -a -p <PID> -d cwd,txt,mem
```

### List UNIX domain sockets

```bash
sudo lsof -nP -U
```

### Find deleted files still held open

```bash
sudo lsof +L1
```

This is the preferred method for finding files whose directory entry has been removed but whose storage is still held by a running process.

> [!important]
> `sudo lsof +L1` is better than:
> ```bash
> sudo lsof | grep '(deleted)'
> ```
> because `+L1` directly filters for unlinked files with link count `< 1` and avoids brittle text matching.

---

## Diagnosing `umount: target is busy`

When an unmount fails, `lsof` is useful, but `fuser` is often the quickest first step.

### Fast check

```bash
sudo fuser -vm /mountpoint
```

### Detailed path-based inspection

```bash
sudo lsof +D /mountpoint
```

### Also verify the mount relationship

```bash
findmnt -T /mountpoint
```

> [!note]
> `fuser` is provided by `psmisc` on Arch. It is typically installed on standard systems, but install it if missing.

---

## Diagnosing `Too many open files`

### Check the shell limits

```bash
ulimit -Sn
ulimit -Hn
```

### Check the system-wide file limit

```bash
cat /proc/sys/fs/file-max
cat /proc/sys/fs/file-nr
```

### Check a specific process

```bash
sudo lsof -p <PID>
sudo ls /proc/<PID>/fd | wc -l
```

### Check the limit applied to a systemd service

```bash
systemctl show <unit>.service -p LimitNOFILE
```

Typical causes:
- descriptor leak in the application
- too-low service limit
- too many open sockets, watchers, or mapped files

---

## `lsof` alternatives and complements

### `lsfd`

`lsfd` from `util-linux` is a newer, more script-friendly file-descriptor inspector.

```bash
lsfd -p <PID>
```

Use it when:
- you want cleaner structured output
- you are focused on a specific process
- `lsof` output is more detail than you need

### `ss`

Prefer `ss` when the problem is specifically about sockets and ports:

```bash
ss -ltnup
ss -tunap
```

---

## Practical troubleshooting playbooks

## 1. A newly installed PCI device is not working

```bash
lspci -nnk
journalctl -b -k
modinfo <expected_driver>
```

What to verify:
- the device appears in `lspci`
- the expected driver is bound under `Kernel driver in use`
- the kernel log does not show probe, firmware, or IOMMU errors

## 2. A USB device is not detected reliably

```bash
lsusb
lsusb -t
udevadm monitor --kernel --udev --property
journalctl -b -k | grep -i usb
```

What to verify:
- the device appears on the USB bus
- negotiated speed and topology look normal
- hotplug events reach udev
- the kernel log does not show resets, disconnect storms, or power errors

## 3. A filesystem will not unmount

```bash
findmnt -T /mountpoint
sudo fuser -vm /mountpoint
sudo lsof +D /mountpoint
```

Most common causes:
- current working directory still inside the mount
- shell, file manager, terminal, or service holding files open
- bind mounts or nested mounts

## 4. `df` says the disk is full but `du` does not

```bash
df -hT
du -xhd1 /var | sort -h
sudo lsof +L1
```

Common causes:
- deleted-but-open files
- separate mount points excluded by `du -x`
- Btrfs snapshots or reflinked extents

If the filesystem is Btrfs, also inspect usage with:

```bash
sudo btrfs filesystem usage /
sudo btrfs subvolume list /
```

## 5. A port is already in use

```bash
ss -ltnup
sudo lsof -nP -iTCP:<PORT> -sTCP:LISTEN
```

Use `ss` for a fast overview and `lsof` when you need exact process/file-descriptor context.

---

## Accuracy and edge cases

> [!warning]
> Hardware **enumeration is not the same as functionality**. A device can appear in `lspci` or `lsusb` and still fail due to:
> - missing firmware
> - wrong driver
> - runtime power management issues
> - ACS/IOMMU quirks
> - bad cabling, power, or hardware faults

> [!note]
> In containers or services with separate namespaces, visibility depends on the namespace in which you run the diagnostic command. For containerized workloads, run the tool inside the container or enter the relevant namespaces with `nsenter`.

> [!note]
> Inventory tools may expose serial numbers, MAC addresses, and other identifiers. Use sanitizing options before sharing output publicly:
> ```bash
> sudo lshw -json -sanitize
> ```

---

## Recommended baseline command set

If only a minimal toolkit is needed, these commands cover most real-world cases:

```bash
journalctl -b -k
lspci -nnk
lsusb -t
lsblk -f
findmnt -T /path
lscpu
free -h
df -hT
ss -ltnup
sudo lshw -short
sudo lsof +L1
sudo smartctl -x /dev/sdX
sudo nvme smart-log /dev/nvme0
```

These are the high-value defaults for day-to-day administration, hardware verification, and troubleshooting on Arch Linux.
