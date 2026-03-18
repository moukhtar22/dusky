# Disk Management, Benchmarking, Health Monitoring, and NVMe Diagnostics in Arch Linux

> [!note]
> This note is a permanent reference for inspecting storage devices, validating mounts, benchmarking I/O, checking drive health, and diagnosing NVMe power-management issues on Arch Linux.

> [!important]
> Device nodes such as `/dev/sdX`, `/dev/nvme0n1`, and `/dev/dm-0` are **not stable identifiers**. For `/etc/fstab`, scripts, and automation, prefer:
> - `UUID=...`
> - `PARTUUID=...`
> - `/dev/disk/by-id/...`

> [!warning]
> Benchmarking and low-level storage commands can be destructive or misleading:
> - Never write directly to a raw block device unless you intend to overwrite it.
> - Avoid benchmarking on a busy system.
> - Do **not** use `zram`, `tmpfs`, or RAM-backed filesystems when you intend to measure physical disk performance.
> - File-based benchmarks on compressed or CoW filesystems can be distorted unless you account for that.

---

## Core Packages

Install tools as needed:

```bash
sudo pacman -S --needed \
    pciutils \
    nvme-cli \
    smartmontools \
    sysstat \
    fio \
    hdparm \
    cryptsetup \
    udisks2 \
    ncdu \
    baobab \
    mdadm \
    lvm2 \
    parted
```

### Package Summary

| Package | Tools |
|---|---|
| `util-linux` | `lsblk`, `blkid`, `findmnt`, `mount`, `fdisk`, `blockdev` |
| `pciutils` | `lspci` |
| `nvme-cli` | `nvme` |
| `smartmontools` | `smartctl`, `smartd` |
| `sysstat` | `iostat`, `pidstat` |
| `fio` | realistic storage benchmarking |
| `hdparm` | ATA/SATA read tests and device info |
| `cryptsetup` | LUKS management |
| `udisks2` | `udisksctl` for desktop-friendly unlock/mount operations |
| `ncdu`, `baobab` | disk usage analysis |
| `mdadm` | Linux software RAID |
| `lvm2` | LVM inspection and management |
| `parted` | `partprobe` and partitioning utilities |

---

## Device Discovery and Topology

### Primary Discovery Commands

| Purpose | Command | Notes |
|---|---|---|
| List block devices | `lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,ROTA,TRAN` | Best general overview. `-e7` hides loop devices. |
| Show filesystem signatures | `blkid` | Displays UUID, PARTUUID, and filesystem type. |
| Show all mount sources and options | `findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS` | Prefer this over plain `mount` for inspection. |
| Show partition tables | `sudo fdisk -l` | Good for sizes, sector sizes, GPT/MBR layout. |
| Show partition tables with alignment-friendly tooling | `sudo parted -l` | Useful when working with GPT and modern disks. |
| Show on-disk signatures safely | `sudo wipefs -n /dev/sdX` | Non-destructive; lists filesystem/RAID/LUKS signatures. |
| List PCI storage controllers | `lspci -nn | grep -iE 'non-volatile memory|sata|ahci|raid|storage'` | Identifies NVMe, AHCI, RAID, etc. |
| List NVMe devices | `sudo nvme list` | NVMe namespaces and controller association. |
| List NVMe subsystems and paths | `sudo nvme list-subsys` | Useful on multi-path or multi-controller systems. |

### Recommended Starting View

```bash
lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,ROTA,TRAN
```

### Stable Naming

To inspect persistent device symlinks:

```bash
ls -l /dev/disk/by-id
ls -l /dev/disk/by-uuid
ls -l /dev/disk/by-partuuid
```

> [!tip]
> For scripts and `/etc/fstab`, `UUID=` and `PARTUUID=` are usually the most portable choices. For whole-disk references, `/dev/disk/by-id/...` is often ideal.

---

## Filesystems, Mount State, and Capacity

### Inspect Mounted Filesystems

```bash
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
```

To see the exact effective options for one mountpoint:

```bash
findmnt -no OPTIONS /mnt/media
```

To show usage by filesystem:

```bash
df -hT
```

### Validate `/etc/fstab`

After editing `/etc/fstab`, verify it before rebooting:

```bash
sudo findmnt --verify --verbose
sudo mount -a
```

> [!warning]
> `mount -a` attempts to mount all eligible entries that are not already mounted and not marked `noauto`. Run it only after reviewing your `fstab`.

### Mount an Entry Already Defined in `/etc/fstab`

If `/mnt/media` exists as an `fstab` mountpoint:

```bash
sudo mount /mnt/media
```

This works because `mount` can resolve the entry by mountpoint.

---

## Benchmarking and I/O Performance

## Benchmarking Rules That Matter

> [!important]
> A single throughput number is rarely enough. For meaningful results:
> - Benchmark on an idle system.
> - Prefer `fio` over `dd`.
> - Use a test size large enough to escape cache effects.
> - For SSDs, use larger write tests if you care about **sustained** performance beyond pseudo-SLC cache.
> - For Btrfs or other compressed/CoW filesystems, benchmark in a directory with compression disabled and ideally `NOCOW`, or benchmark an unmounted raw device only if you fully understand the risks.

### Use `fio` for Real Benchmarks

`fio` is the standard tool for storage benchmarking.

#### Safe Sequential Write and Read Test on a Mounted Filesystem

```bash
readonly TESTDIR=/mnt/test
readonly TESTFILE="$TESTDIR/fio.bin"

mkdir -p -- "$TESTDIR"

fio --name=seqwrite \
    --filename="$TESTFILE" \
    --size=4G \
    --rw=write \
    --bs=1M \
    --ioengine=io_uring \
    --direct=1 \
    --iodepth=16 \
    --refill_buffers=1 \
    --randrepeat=0 \
    --group_reporting \
    --fsync_on_close=1

fio --name=seqread \
    --filename="$TESTFILE" \
    --size=4G \
    --rw=read \
    --bs=1M \
    --ioengine=io_uring \
    --direct=1 \
    --iodepth=16 \
    --group_reporting

rm -f -- "$TESTFILE"
```

#### Safe Random Mixed Read/Write Test

```bash
readonly TESTDIR=/mnt/test
readonly TESTFILE="$TESTDIR/fio-rand.bin"

mkdir -p -- "$TESTDIR"

fio --name=randrw \
    --filename="$TESTFILE" \
    --size=4G \
    --rw=randrw \
    --rwmixread=70 \
    --bs=4k \
    --ioengine=io_uring \
    --direct=1 \
    --iodepth=32 \
    --time_based=1 \
    --runtime=30 \
    --refill_buffers=1 \
    --randrepeat=0 \
    --group_reporting

rm -f -- "$TESTFILE"
```

> [!note]
> - `--direct=1` bypasses the page cache.
> - `--ioengine=io_uring` is appropriate on modern Arch kernels. If unavailable in your environment, fall back to `--ioengine=libaio`.
> - For HDDs, lower queue depths such as `1-4` are often more representative of real workloads.

### `dd` for Quick Spot Checks

`dd` is acceptable for a quick sanity check, but it is **not** a full benchmark tool.

#### Write Test to a Regular File

```bash
dd if=/dev/zero of=/mnt/test/dd-test.bin bs=1M count=4096 oflag=direct status=progress conv=fdatasync
```

#### Read Test from That File

```bash
dd if=/mnt/test/dd-test.bin of=/dev/null bs=1M iflag=direct status=progress
```

#### Cleanup

```bash
rm -f -- /mnt/test/dd-test.bin
```

> [!warning]
> - Write tests overwrite the target file.
> - Do **not** set `of=/dev/sdX` or `of=/dev/nvme0n1` unless you intend to destroy data.
> - `dd` does not give good latency, queue-depth, or mixed-workload insight.

### `hdparm` Read Test for SATA/ATA Devices

For a quick non-destructive read test on a SATA/ATA disk:

```bash
sudo hdparm -t /dev/sdX
```

> [!note]
> - `hdparm -t` performs a buffered device read speed test.
> - `hdparm -T` measures cache/buffer performance, **not** real disk throughput.
> - `hdparm` is primarily relevant to ATA/SATA devices. For NVMe, use `fio`.

---

## Continuous I/O Monitoring

### Per-Device Utilization and Latency

```bash
iostat -dx 1
```

Useful columns include:

- `r/s`, `w/s` — read/write IOPS
- `rkB/s`, `wkB/s` — throughput
- `await` — average time per I/O request
- `aqu-sz` — average queue depth
- `%util` — useful, but not the only saturation indicator

### Per-Process Disk I/O

```bash
pidstat -d 1
```

> [!tip]
> `iostat` shows what devices are doing; `pidstat` helps identify **which processes** are causing it.

---

## Drive Health Monitoring

## Recommended Tools

- `smartctl` for SATA, SAS, USB-attached disks, and also NVMe
- `nvme-cli` for NVMe-native logs and features

### Comprehensive SMART / Health Report

#### SATA / SAS / USB-SATA

```bash
sudo smartctl -x /dev/sdX
```

#### NVMe

```bash
sudo smartctl -x /dev/nvme0
sudo nvme smart-log /dev/nvme0 -H
```

> [!important]
> For NVMe, use the **controller device** such as `/dev/nvme0`, not a partition like `/dev/nvme0n1p1`.

### Quick Health Status

```bash
sudo smartctl -H /dev/sdX
sudo smartctl -H /dev/nvme0
```

> [!warning]
> A reported overall health of `PASSED` is **not** a complete diagnosis. Always inspect the detailed attributes/logs.

### What to Watch For

| Signal | Meaning |
|---|---|
| SATA `Reallocated_Sector_Ct` increasing | Media deterioration |
| SATA `Current_Pending_Sector` > 0 | Unstable sectors; take seriously |
| SATA `Offline_Uncorrectable` > 0 | Unreadable sectors detected |
| SATA `UDMA_CRC_Error_Count` increasing | Often cable, connector, or backplane issue rather than media failure |
| NVMe `critical_warning` non-zero | Serious controller/media condition |
| NVMe `media_errors` increasing | Real media/controller errors |
| NVMe `percentage_used` | Endurance estimate; can exceed 100 |
| NVMe `num_err_log_entries` | Cumulative and sometimes noisy; correlate with kernel logs |

### SMART Self-Tests

#### SATA / SAS

Short test:

```bash
sudo smartctl -t short /dev/sdX
```

Extended test:

```bash
sudo smartctl -t long /dev/sdX
```

View self-test results:

```bash
sudo smartctl -l selftest /dev/sdX
```

#### NVMe Device Self-Test

Short self-test:

```bash
sudo nvme device-self-test /dev/nvme0 --start=1
```

Extended self-test:

```bash
sudo nvme device-self-test /dev/nvme0 --start=2
```

View self-test log:

```bash
sudo nvme self-test-log /dev/nvme0
```

### Enable Background Monitoring

```bash
sudo systemctl enable --now smartd.service
```

`smartd` reads `/etc/smartd.conf`.

### USB Bridge Caveat

Many USB-to-SATA/NVMe bridges require an explicit device type. Let `smartctl` probe first:

```bash
sudo smartctl --scan-open
```

If needed, use the hinted device type, for example:

```bash
sudo smartctl -d sat -x /dev/sdX
```

> [!note]
> Some USB bridges expose only partial SMART data, and some do not forward vendor-specific logs correctly.

---

## NVMe Reference

## Controller vs Namespace

> [!important]
> NVMe naming is easy to misuse:
> - `/dev/nvme0` = **controller**
> - `/dev/nvme0n1` = **namespace block device**
> - `/dev/nvme0n1p1` = **partition**
>
> Many `nvme-cli` management commands target the **controller** (`/dev/nvme0`), not a partition.

### Core NVMe Inspection Commands

List controllers and namespaces:

```bash
sudo nvme list
sudo nvme list-subsys
```

Identify controller capabilities:

```bash
sudo nvme id-ctrl /dev/nvme0 -H
```

Identify namespace properties:

```bash
sudo nvme id-ns /dev/nvme0n1 -H
```

Show error log:

```bash
sudo nvme error-log /dev/nvme0
```

Show firmware slot information:

```bash
sudo nvme fw-log /dev/nvme0
```

---

## NVMe Power Management

Modern NVMe behavior is controlled by several layers:

1. **NVMe Power Management Feature** — current host-selected power state
2. **APST** — Autonomous Power State Transitions inside the controller
3. **PCIe ASPM** — link-level power saving on the PCIe bus
4. **Linux Runtime PM** — kernel-managed runtime suspend/resume of the device

### Inspect Current NVMe Power Features

Current power management feature:

```bash
sudo nvme get-feature /dev/nvme0 --feature-id=0x02 -H
```

APST configuration:

```bash
sudo nvme get-feature /dev/nvme0 --feature-id=0x0c -H
```

Check whether the controller supports APST:

```bash
sudo nvme id-ctrl /dev/nvme0 -H | grep -i apsta
```

Show controller power-state descriptors and number of power states:

```bash
sudo nvme id-ctrl /dev/nvme0 -H | grep -iE 'npss|ps[[:space:]]+[0-9]+'
```

### Linux NVMe APST Tuning

Check the kernel's current APST latency policy:

```bash
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
```

> [!note]
> On Linux, APST is normally managed automatically. The main tuning knob is `nvme_core.default_ps_max_latency_us`, not manual forcing of a specific NVMe power state.

### Runtime PM State

```bash
cat /sys/class/nvme/nvme0/device/power/control
cat /sys/class/nvme/nvme0/device/power/runtime_status
cat /sys/class/nvme/nvme0/device/power/runtime_suspended_time
```

Typical `power/control` values:

- `auto` — runtime power management enabled
- `on` — runtime power management disabled

### PCIe ASPM Inspection

First locate the NVMe PCI device:

```bash
lspci -nn | grep -i 'non-volatile memory controller'
```

Then inspect link capabilities and controls:

```bash
sudo lspci -vv -s 01:00.0 | grep -E 'LnkCap:|LnkCtl:|LnkSta:|L1SubCap:|L1SubCtl1:|ASPM'
```

Check the global kernel ASPM policy:

```bash
cat /sys/module/pcie_aspm/parameters/policy
```

> [!warning]
> ASPM availability is influenced by firmware/BIOS, platform quirks, and the endpoint/root-port pair. Linux cannot always enable states that firmware or hardware does not permit.

### Practical Guidance

- Prefer the platform defaults unless you are **actively diagnosing** a problem.
- Manual `nvme set-feature ... --feature-id=0x02` changes are often temporary and may be overridden by resets or the kernel.
- If a system shows NVMe timeouts, resets, or suspend/resume instability, APST or ASPM may be involved.

### Diagnostic-Only Boot Parameters

If you need to test whether power management is causing errors:

- Disable NVMe APST:

```text
nvme_core.default_ps_max_latency_us=0
```

- Disable PCIe ASPM globally:

```text
pcie_aspm=off
```

> [!warning]
> These are **diagnostic** settings, not good defaults. They can increase power draw significantly, especially on laptops.

### First Places to Look When NVMe Is Misbehaving

```bash
journalctl -k -b | grep -iE 'nvme|pcie|aer|timeout|reset'
sudo nvme smart-log /dev/nvme0 -H
sudo nvme error-log /dev/nvme0
```

Also verify SSD firmware is current.

---

## SSD / NVMe Maintenance: TRIM and Discard

### Check Discard Support End-to-End

```bash
lsblk -D
```

If the stack supports discard, you will see non-zero discard granularity/max values.

### Run TRIM Manually

```bash
sudo fstrim -av
```

### Enable Periodic TRIM

```bash
sudo systemctl enable --now fstrim.timer
```

> [!note]
> Periodic `fstrim.timer` is generally the simplest and safest default on Arch for SSDs and NVMe drives.

### Online `discard` vs Periodic `fstrim`

- Periodic `fstrim` is usually preferred.
- Continuous online discard is supported on modern filesystems, but it can add write-path overhead depending on workload and stack.
- On Btrfs, `discard=async` is reasonable if you intentionally want online discard.

### TRIM Through LUKS / dm-crypt

TRIM does **not** pass through an encrypted mapping unless you allow it.

Examples:

- one-shot unlock:

```bash
sudo cryptsetup open --allow-discards /dev/nvme1n1p1 extdisk
```

- persistent configuration via `/etc/crypttab`:

```text
extdisk UUID=<luks-uuid> none discard
```

> [!warning]
> Allowing discard through encryption leaks some information about which blocks are unused. For many personal systems this is acceptable; for higher-threat environments, evaluate the tradeoff carefully.

---

## Common Operations

## Unlock and Mount Encrypted Volumes

### Desktop-Friendly Method: `udisksctl`

Unlock a LUKS device:

```bash
udisksctl unlock -b /dev/nvme1n1p1
```

This returns the created mapped device, typically something like `/dev/dm-2`.

Mount the unlocked filesystem:

```bash
udisksctl mount -b /dev/dm-2
```

Unmount and lock it again:

```bash
udisksctl unmount -b /dev/dm-2
udisksctl lock -b /dev/nvme1n1p1
```

> [!note]
> In an interactive desktop session, `udisksctl` often works through Polkit without `sudo`. On a TTY or minimal environment, root privileges may still be required.

### Low-Level Method: `cryptsetup`

Open with a chosen mapper name:

```bash
sudo cryptsetup open /dev/nvme1n1p1 extdisk
```

Mount it:

```bash
sudo mount /dev/mapper/extdisk /mnt/media
```

Unmount and close:

```bash
sudo umount /mnt/media
sudo cryptsetup close extdisk
```

### Re-Read a Partition Table After Changes

If you changed a partition table and need the kernel to re-read it:

```bash
sudo blockdev --rereadpt /dev/sdX
sudo partprobe /dev/sdX
```

> [!warning]
> If partitions are in use, the kernel may refuse to reload the table. Unmount users first or reboot.

---

## HDD-Specific Performance Notes

These points apply to **spinning disks**, not SSDs/NVMe.

- Sequential throughput is usually higher at the **outer tracks** near the beginning of the disk.
- Putting hot sequential data near the start of the disk can improve throughput.
- Partition size by itself does **not** magically improve performance.
- Performance improves only when your frequently accessed data stays within a smaller, faster region and average seek distance is reduced.

> [!note]
> On SSDs and NVMe drives, partition placement does not provide the same physical-layout advantage.

---

## Disk Usage Analysis

### CLI

Filesystem usage:

```bash
df -hT
```

Directory usage, limited to one filesystem:

```bash
du -xhd1 / | sort -h
```

Interactive terminal UI:

```bash
ncdu /
```

### GUI

Install and run GNOME Disk Usage Analyzer:

```bash
sudo pacman -S --needed baobab
```

> [!note]
> On Btrfs, `df`, `du`, and allocator-level usage can differ. For Btrfs-specific space accounting, use:
> ```bash
> sudo btrfs filesystem usage /
> ```

---

## RAID and LVM Quick Inspection

### Linux Software RAID (`mdadm`)

Show active arrays:

```bash
cat /proc/mdstat
```

Inspect one array:

```bash
sudo mdadm --detail /dev/md0
```

### LVM

Show physical volumes:

```bash
sudo pvs
```

Show volume groups:

```bash
sudo vgs
```

Show logical volumes and backing devices:

```bash
sudo lvs -a -o +devices
```

> [!warning]
> RAID improves availability and/or performance depending on level, but **RAID is not backup**.

---

## Kernel Logs and Failure Triage

When storage problems appear, inspect the kernel log first:

```bash
journalctl -k -b --no-pager
```

Filter for common storage-related errors:

```bash
journalctl -k -b | grep -iE 'nvme|ata|ahci|aer|i/o error|timeout|reset|crc|medium error|ext4-fs error|btrfs|xfs'
```

### Common Interpretation Hints

- `I/O error`, `medium error` — likely media or transport failure
- `UDMA CRC` — usually cable/backplane/connector issue
- `nvme timeout`, `controller reset`, `AER` — often link, firmware, or power-management related
- Filesystem errors (`EXT4-fs error`, `BTRFS`, `XFS`) may be the **result**, not the root cause

---

## Minimal Command Cheat Sheet

### Identify Disks

```bash
lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,ROTA,TRAN
blkid
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS
sudo fdisk -l
lspci -nn | grep -iE 'non-volatile memory|sata|ahci|raid|storage'
sudo nvme list
```

### Benchmark

```bash
iostat -dx 1
pidstat -d 1
sudo hdparm -t /dev/sdX
```

### Health

```bash
sudo smartctl -x /dev/sdX
sudo smartctl -x /dev/nvme0
sudo nvme smart-log /dev/nvme0 -H
sudo systemctl enable --now smartd.service
```

### NVMe Power

```bash
sudo nvme get-feature /dev/nvme0 --feature-id=0x02 -H
sudo nvme get-feature /dev/nvme0 --feature-id=0x0c -H
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
cat /sys/module/pcie_aspm/parameters/policy
sudo lspci -vv -s 01:00.0
```

### Mount / Encrypt

```bash
sudo mount /mnt/media
udisksctl unlock -b /dev/nvme1n1p1
sudo cryptsetup open /dev/nvme1n1p1 extdisk
```

### TRIM

```bash
lsblk -D
sudo fstrim -av
sudo systemctl enable --now fstrim.timer
```
