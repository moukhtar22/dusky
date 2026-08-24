# Disk Management, Benchmarking, Health Monitoring & NVMe Diagnostics

> [!note] Scope
> Permanent reference for **device inspection, mounts, benchmarking, drive health, TRIM, LUKS handling, and NVMe power management** on Arch Linux. Updated **August 2026**, verified against kernel 7.x (`util-linux 2.42`, `nvme-cli 2.16`, `smartmontools 7.5`, `cryptsetup 2.8`).
>
> Related: [[Storage Stack]] · [[BTRFS]] · [[Bitlocker]] · [[Fixing Un Mountable NTFS drive]]

> [!info] This machine (verified August 2026)
> - `nvme0n1` — Intel SSDPEKNU512GZ: p1 LUKS2 (ext4 `browser` → `~/.config/mozilla`), p2 vfat `DUSKY_EFI` → `/boot`, p3 btrfs `DUSKY_ROOT` (root fs)
> - `nvme1n1` — Samsung SSD 980 1TB: p1 LUKS2 (ext4 → `/mnt/media`)
> - Both LUKS containers run with the `discards` flag active; swap is zram
> - Custom kernels are built without loop/exFAT support (`CONFIG_BLK_DEV_LOOP=n`, `CONFIG_EXFAT_FS=n`); `ntfs3` ships as a module

> [!warning]
> - Device nodes like `/dev/sdX`, `/dev/nvme0n1`, `/dev/dm-0` are **not stable** — this machine's root disk moved between `nvme1n1` and `nvme0n1` across boots while fstab comments still said `nvme1n1p3`. Only UUIDs kept it booting.
>   For `/etc/fstab`, scripts, automation use `UUID=`, `PARTUUID=`, or `/dev/disk/by-id/...`.
> - Never write to a raw block device unless you intend to destroy it.
> - Don't benchmark on a busy system, and never measure "disk" speed on `zram`/`tmpfs`.
> - File-based benchmarks on compressed/CoW filesystems are distorted unless accounted for.

---

## Core Packages

```bash
sudo pacman -S --needed \
    pciutils nvme-cli smartmontools sysstat fio hdparm \
    cryptsetup udisks2 ncdu gptfdisk parted dosfstools e2fsprogs
```

| Package | Tools |
|---|---|
| `util-linux` | `lsblk`, `blkid`, `findmnt`, `wipefs`, `mount`, `fdisk`, `cfdisk`, `blockdev` |
| `pciutils` / `nvme-cli` / `smartmontools` | `lspci` · `nvme` · `smartctl`, `smartd` |
| `sysstat` / `fio` / `hdparm` | `iostat`, `pidstat` · real benchmarks · ATA read tests |
| `cryptsetup` / `udisks2` | LUKS/bitlk CLI · desktop unlock/mount via Polkit |
| `ncdu` | terminal usage analyzer |
| `gptfdisk` / `parted` | `gdisk`, `sgdisk` · `partprobe` |
| `mdadm` / `lvm2` | software RAID · LVM (install only if used) |

---

## Device Discovery

| Purpose | Command |
|---|---|
| Best overview | `lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,FSVER,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,ROTA,TRAN` |
| Filesystem signatures + UUIDs | `blkid` |
| Mounts + effective options | `findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS` |
| Partition tables / sector size | `sudo fdisk -l` |
| Signatures on a disk (dry-run) | `sudo wipefs -n /dev/sdX` |
| Storage controllers | `lspci -nn \| grep -iE 'non-volatile memory\|sata\|ahci\|raid'` |
| NVMe namespaces/controllers | `sudo nvme list` · `sudo nvme list-subsys` |

`-e7` excludes loop devices. Persistent symlinks live in `/dev/disk/by-id`, `by-uuid`, `by-partuuid`:

```bash
ls -l /dev/disk/by-id /dev/disk/by-uuid /dev/disk/by-partuuid
```

> [!tip]
> `UUID=`/`PARTUUID=` are the portable choices for fstab; whole-disk references belong in scripts as `/dev/disk/by-id/...`.

---

## Mounts, Capacity, fstab

```bash
findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS   # everything
findmnt -no OPTIONS /mnt/media            # one mountpoint's effective options
df -hT                                    # usage by filesystem
```

After editing `/etc/fstab`, validate **before rebooting**:

```bash
sudo findmnt --verify --verbose
```

Mount any entry already defined in fstab by naming its mountpoint:

```bash
sudo mount /mnt/media
```

> [!warning]
> `sudo mount -a` mounts every eligible non-`noauto` entry. Review the file first.

---

## Partitioning & Formatting

```bash
# Partition interactively (GPT): fdisk or cfdisk
sudo fdisk /dev/sdX

# Non-interactive GPT example: one partition filling the disk, type Linux
sudo sgdisk -n 1:0:0 -t 1:8300 /dev/sdX

# Re-read the table after changes
sudo partprobe /dev/sdX
```

```bash
# Create filesystems (label optional but recommended)
sudo mkfs.btrfs -L DUSKY_DATA /dev/sdX1
sudo mkfs.ext4 -L DATA        /dev/sdX2
sudo mkfs.vfat -F 32 -n EFI   /dev/sdX3      # ESP only
sudo mkfs.ntfs --quick -L WIN /dev/sdX4
```

> [!note]
> exFAT needs kernel support (`CONFIG_EXFAT_FS`/module + `exfatprogs`). The custom `dusky-*` kernels here ship **without it** — stock `linux` has it.

### Per-Medium Tuning

The medium dictates the flags more than the filesystem does:

| Medium | What limits it | Good moves | Toxic moves |
|---|---|---|---|
| Internal SSD/NVMe | write amplification (FTL erase-before-write) | compression, async TRIM, `noatime` | defragmenting, sync discard, `autodefrag` |
| Rotational HDD | seek time | contiguous writes, `autodefrag` (Btrfs), longer journal `commit=` | any TRIM/discard, NOCOW on small files |
| USB flash / SD card | cheap NAND, weak FTL, sudden removal | `uid=`/`gid=` synthesis, `errors=remount-ro`, minimal metadata churn | the `flush` mount option (halves throughput, burns endurance) |

ext4 specifics (this machine's `/mnt/media`): rely on `fstrim.timer`, never the mount-time `discard` flag — ext4's discard is synchronous and stalls the I/O queue on deletes. Useful set: `noatime,lazytime,commit=20` (larger window = fewer journal flushes, up to 20 s of data loss on power cut).

exFAT for >32 GB cross-platform flash:

```fstab
UUID=<exfat-uuid>  /mnt/usb  exfat  uid=1000,gid=1000,dmask=0022,fmask=0133,noatime,nofail,x-systemd.automount  0  0
```

---

## Benchmarking

> [!important]
> One throughput number proves little. Benchmark idle and prefer `fio` over `dd`. `--direct=1` bypasses the page cache, but SSDs still front writes with their own pseudo-SLC cache — only runs much larger than that reveal **sustained** speed. Expect CoW/compressed-Btrfs numbers to differ from raw-device truth.

### `fio` — Real Benchmarks

Sequential write then read:

```bash
readonly TESTDIR=/mnt/test TESTFILE=/mnt/test/fio.bin
mkdir -p -- "$TESTDIR"

fio --name=seqwrite --filename="$TESTFILE" --size=4G \
    --rw=write --bs=1M --ioengine=io_uring --direct=1 --iodepth=16 \
    --refill_buffers=1 --randrepeat=0 --group_reporting --fsync_on_close=1

fio --name=seqread --filename="$TESTFILE" --size=4G \
    --rw=read --bs=1M --ioengine=io_uring --direct=1 --iodepth=16 \
    --group_reporting

rm -f -- "$TESTFILE"
```

Random 70/30 mix (latency/IOPS profile):

```bash
fio --name=randrw --filename="$TESTFILE" --size=4G \
    --rw=randrw --rwmixread=70 --bs=4k --ioengine=io_uring \
    --direct=1 --iodepth=32 --time_based=1 --runtime=30 \
    --refill_buffers=1 --randrepeat=0 --group_reporting

rm -f -- "$TESTFILE"
```

> [!note]
> - `--direct=1` bypasses the page cache; `io_uring` is standard on current kernels (fall back to `libaio` only if unavailable).
> - For HDDs use queue depths 1–4 and `--bs` matching real workloads.

### `dd` — Spot Check Only

```bash
dd if=/dev/zero of=/mnt/test/dd.bin bs=1M count=4096 oflag=direct status=progress conv=fdatasync
dd if=/mnt/test/dd.bin of=/dev/null bs=1M iflag=direct status=progress
rm -f /mnt/test/dd.bin
```

> [!warning]
> `of=` pointing at a block device destroys it. `dd` says nothing about latency or mixed workloads.

### `hdparm` — Quick Read Test

```bash
sudo hdparm -t /dev/sdX     # buffered device read (works on NVMe too)
```

`-T` measures host cache, not the disk.

---

## I/O Monitoring

```bash
iostat -dx 1        # per-device utilization
pidstat -d 1        # per-process I/O
```

Key `iostat -dx` columns (sysstat ≥ 12.5 splits latency per direction):

| Column | Meaning |
|---|---|
| `r/s`, `w/s`, `d/s`, `f/s` | read/write/discard/flush IOPS |
| `rkB/s`, `wkB/s` | throughput |
| `r_await`, `w_await` | average ms per request, per direction |
| `aqu-sz` | average queue depth |
| `%util` | busy fraction — not the whole saturation story |

---

## Drive Health

### Reports

```bash
sudo smartctl -x /dev/sdX          # SATA/SAS/USB full report
sudo smartctl -x /dev/nvme0        # NVMe full report (controller device!)
sudo nvme smart-log /dev/nvme0 -H  # NVMe-native log with decoded thresholds
sudo smartctl --scan-open          # probe types, esp. behind USB bridges
```

If a USB bridge hides SMART, hint the transport: `sudo smartctl -d sat -x /dev/sdX`.

Quick status: `sudo smartctl -H /dev/sdX` / `sudo smartctl -H /dev/nvme0`.

> [!warning]
> `PASSED` ≠ healthy. Read the attributes/logs. A USB bridge may forward only partial data.

### What to Watch

| Signal | Meaning |
|---|---|
| SATA `Reallocated_Sector_Ct` rising | media deteriorating |
| SATA `Current_Pending_Sector` > 0 | unstable sectors — take seriously |
| SATA `UDMA_CRC_Error_Count` rising | cable/connector, usually not media |
| NVMe `critical_warning` ≠ 0 | serious controller/media condition |
| NVMe `media_errors` rising | real media errors |
| NVMe `percentage_used` | endurance consumed (can exceed 100) |

### Self-Tests

SATA/SAS:

```bash
sudo smartctl -t short /dev/sdX       # short offline test
sudo smartctl -t long  /dev/sdX       # extended
sudo smartctl -l selftest /dev/sdX    # results
```

NVMe (codes: `1` short · `2` extended · `0xf` abort):

```bash
sudo nvme device-self-test /dev/nvme0 -s 1
sudo nvme device-self-test /dev/nvme0 -s 2
sudo nvme self-test-log /dev/nvme0
```

### Background Monitoring

```bash
sudo systemctl enable --now smartd.service   # reads /etc/smartd.conf
```

Full TUI for live SMART + I/O across all drives: `~/user_scripts/drives/dusky_disk_monitor_io.py` · SSD wear/over-provisioning audit: `~/user_scripts/drives/drive_health/dusky_drive_health.py`.

---

## NVMe Reference

> [!important]
> `/dev/nvme0` = controller · `/dev/nvme0n1` = namespace · `/dev/nvme0n1p1` = partition.
> Most `nvme-cli` admin commands target the **controller**.

```bash
sudo nvme list                          # devices + firmware + usage
sudo nvme id-ctrl /dev/nvme0 -H         # controller capabilities
sudo nvme id-ns   /dev/nvme0n1 -H       # namespace properties
sudo nvme error-log /dev/nvme0          # error log entries
sudo nvme fw-log /dev/nvme0             # firmware slots
```

### Power Management Layers

1. **NVMe Power Management feature** (0x02) — host-selected power state
2. **APST** (0x0c) — autonomous transitions inside the controller
3. **PCIe ASPM** — link-level power saving
4. **Linux runtime PM** — kernel suspend/resume of the PCI device

```bash
# Current power state + APST configuration
sudo nvme get-feature /dev/nvme0 --feature-id=0x02 -H
sudo nvme get-feature /dev/nvme0 --feature-id=0x0c -H

# APST support & power-state descriptors
sudo nvme id-ctrl /dev/nvme0 -H | grep -iE 'apsta|npss'
sudo nvme id-ctrl /dev/nvme0 -H | grep -E '^ps[[:space:]]+[0-9]+'

# Kernel APST latency knob and runtime PM state
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
cat /sys/class/nvme/nvme0/device/power/control     # auto | on
cat /sys/class/nvme/nvme0/device/power/runtime_status
```

PCIe ASPM:

```bash
lspci -nn | grep -i 'non-volatile memory controller'
sudo lspci -vv -s 02:00.0 | grep -E 'LnkCap:|LnkCtl:|LnkSta:'
cat /sys/module/pcie_aspm/parameters/policy
```

> [!warning]
> Firmware/platform can veto ASPM regardless of kernel settings — this laptop's ACPI FADT declares ASPM unsupported even with `pcie_aspm=force` on the cmdline.

Diagnostic-only boot parameters (high power cost, laptops suffer):

```text
nvme_core.default_ps_max_latency_us=0   # disable APST
pcie_aspm=off                           # disable PCIe ASPM
```

When NVMe misbehaves, look first at:

```bash
journalctl -k -b | grep -iE 'nvme|pcie|aer|timeout|reset'
sudo nvme smart-log /dev/nvme0 -H
sudo nvme error-log /dev/nvme0
```

Then update controller firmware if outdated.

---

## TRIM / Discard

```bash
lsblk -D                        # discard support end-to-end (non-zero = supported)
sudo fstrim -av                 # trim all mounted supported filesystems
systemctl enable --now fstrim.timer   # weekly TRIM — the Arch default recommendation
```

For ext4 volumes, scheduled TRIM (`fstrim.timer`, weekly) is the right default — ext4's mount-time `discard` option works synchronously and stalls the queue on deletes. Btrfs needs neither: it enables asynchronous discard by itself ([[BTRFS]]).

### TRIM Through LUKS

Discards do not pass through dm-crypt unless allowed:

```bash
# One-shot
sudo cryptsetup open --allow-discards /dev/nvme1n1p1 extdisk

# Persistently (survives reboots, stored in LUKS2 metadata)
sudo cryptsetup open --allow-discards --persistent /dev/nvme1n1p1 extdisk
```

Or in `/etc/crypttab`: `extdisk UUID=<luks-uuid> none discard`.

> [!note]
> systemd-cryptsetup (fstab/crypttab unlocks) enables discards by default on modern Arch — this machine's containers already show `flags: discards`. Manual `cryptsetup open` without flags does not.

### LUKS Performance on NVMe

dm-crypt by default hands encryption work to kernel worker threads. On drives with single-digit-µs latency, the context switch costs more than the drive access — bypass it in `/etc/crypttab` (kernel ≥ 5.9, systemd ≥ 248):

```text
media  UUID=<luks-uuid>  /etc/keys/media.key  no-read-workqueue,no-write-workqueue,discard
```

At format time, consider enlarging dm-crypt's crypto sector so each 4 KiB is one encryption operation instead of eight 512 B ones (irreversible — set it when creating the container):

```bash
sudo cryptsetup luksFormat --type luks2 --sector-size 4096 /dev/nvme1n1p1
```

Check what the hardware reports first — `cat /sys/block/nvme0n1/queue/{logical,physical}_block_size`; both NVMe drives here report **512**, where the default is already aligned and the gain is smaller.

This machine unlocks via keyfiles through `~/user_scripts/drives/drive_manager/drive_manager.py` (`drives.toml` maps outer/inner UUIDs → keyfile hints); the crypttab line above is the declarative equivalent.

> [!warning]
> Passing discards leaks which blocks are unused. Acceptable for personal systems; consider carefully elsewhere.

---

## Common Operations

### Unlock & Mount LUKS

Desktop-friendly (Polkit, no sudo in an active session):

```bash
udisksctl unlock -b /dev/nvme1n1p1     # prints the mapper device
udisksctl mount  -b /dev/mapper/luks-<uuid>
udisksctl unmount -b /dev/mapper/luks-<uuid>
udisksctl lock   -b /dev/nvme1n1p1
```

Low-level:

```bash
sudo cryptsetup open /dev/nvme1n1p1 extdisk
sudo mount /dev/mapper/extdisk /mnt/media
sudo umount /mnt/media
sudo cryptsetup close extdisk
```

### Partition Table Re-read

```bash
sudo partprobe /dev/sdX                # preferred
sudo blockdev --rereadpt /dev/sdX
```

> [!warning]
> In-use partitions block re-reading. Unmount users or reboot.

---

## Usage Analysis

```bash
df -hT                     # filesystem level
du -xhd1 / | sort -h       # one directory level, stay on one filesystem
ncdu -x /                  # interactive (-x keeps to one filesystem)
```

On Btrfs, `df`/`du` lie politely — allocator truth comes from `sudo btrfs filesystem usage /` ([[BTRFS]]).

---

## RAID / LVM Inspection

```bash
cat /proc/mdstat                      # active arrays
sudo mdadm --detail /dev/md0
sudo pvs && sudo vgs && sudo lvs -a -o +devices
```

> [!warning]
> RAID is not backup.

---

## Failure Triage

```bash
journalctl -k -b --no-pager
journalctl -k -b | grep -iE 'nvme|ata|ahci|aer|i/o error|timeout|reset|crc|medium error|ext4-fs error|btrfs|xfs'
```

- `I/O error`, `medium error` → media/transport failure
- `UDMA CRC` → cable/backplane
- `nvme timeout`, `controller reset`, `AER` → link/firmware/power-management
- Filesystem errors are often the *symptom*, not the cause

---

## Cheat Sheet

```bash
# Identify
lsblk -e7 -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,ROTA,TRAN
blkid && findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS && sudo wipefs -n /dev/sdX

# Monitor
iostat -dx 1 && pidstat -d 1

# Health
sudo smartctl -x /dev/nvme0 && sudo nvme smart-log /dev/nvme0 -H
sudo systemctl enable --now smartd.service fstrim.timer

# NVMe power
sudo nvme get-feature /dev/nvme0 --feature-id=0x02 -H
sudo nvme get-feature /dev/nvme0 --feature-id=0x0c -H
cat /sys/module/nvme_core/parameters/default_ps_max_latency_us
cat /sys/module/pcie_aspm/parameters/policy

# Mount / encrypt
sudo findmnt --verify --verbose
udisksctl unlock -b /dev/nvme1n1p1
sudo cryptsetup open /dev/nvme1n1p1 extdisk

# TRIM
lsblk -D && sudo fstrim -av
```
