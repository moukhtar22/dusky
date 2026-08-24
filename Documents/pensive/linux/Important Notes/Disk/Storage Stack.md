# Storage Stack — How a Byte Reaches the Disk

> [!note] Scope
> The mental model behind every other note in this folder: the layers storage is built from, what each device name actually means, why names change, and the exact path from power button to mounted filesystem. **Read this first** — [[Disk]], [[BTRFS]], [[Bitlocker]], and [[Fixing Un Mountable NTFS drive]] each cover one layer in depth.

> [!info] This machine (verified August 2026)
> - Two NVMe controllers (`nvme0`, `nvme1`), both formatted 512-byte sectors
> - Stack instances: `nvme0n1p3` → btrfs directly · `nvme0n1p1` → LUKS2 → ext4 · `nvme1n1p1` → LUKS2 → ext4 · `nvme0n1p2` → vfat ESP
> - Firmware UEFI → **systemd-boot** → custom kernels (`linux-dusky-gaming` / `-battery`)
> - RAM-disks too: `zram0` (swap), `zram1` (ext4 at `/mnt/zram1`) — they occupy `/dev` and `df` like real disks but vanish at power-off

---

## The Stack

Each layer only ever talks to its neighbors. A tool that operates at one layer is meaningless at another — this explains half of all disk mistakes (`ntfsfix` against a *locked* BitLocker partition, `chkdsk` advice for an encrypted container, benchmarking zram thinking it's an SSD).

```text
path            /home/dusk/file.txt
                │  resolved by the VFS through mount points
    ────────────┼──────────────────────────────
mount point     /home                       ← mount(8), fstab
subvolume       @home (id 257)              ← btrfs-only layer
filesystem      btrfs on UUID c7c2a1a4-…    ← checksums, compression
partition       p3 (GPT entry, PARTUUID)    ← fdisk/sfdisk
disk            /dev/nvme0n1                ← PCIe controller nvme0
```

| Layer | Created by | Appears as | On this machine |
|---|---|---|---|
| Hardware | — | `/dev/nvme0n1`, `/dev/sda` | Intel 512G, Samsung 980 |
| Partition table (GPT) | `fdisk`, `sfdisk` | `/dev/nvme0n1p3` | p1 LUKS · p2 ESP · p3 root |
| Encryption (LUKS2/dm-crypt) | `cryptsetup luksFormat` | `/dev/mapper/luks-<uuid>` | mozilla + media volumes |
| Filesystem | `mkfs.btrfs/ext4/…` | has a `UUID=` | btrfs, ext4, vfat |
| Subvolume | `btrfs subvolume create` | directory-like | `@`, `@home`, `@snapshots`, … |
| Mount point | `mount`, fstab | a path in the tree | `/`, `/home`, `/.snapshots` |

---

## Decoding Device Names

| Name | Meaning |
|---|---|
| `nvme0n1p3` | NVMe **controller 0**, **namespace 1**, **partition 3** |
| `sda2` | SCSI/SATA/USB disk **a** (letter = detection order!), partition 2 |
| `mmcblk0p1` | SD/eMMC block 0, partition 1 |
| `zram0` | compressed RAM disk — no hardware behind it |
| `loop7` | file pretending to be a block device (absent from custom kernels here) |
| `dm-0` | device-mapper's internal numbering — **transient**; always prefer `/dev/mapper/<name>` |

The mapper names you'll meet: `luks-<uuid>` (systemd-cryptsetup default), `<name-you-chose>` (manual `cryptsetup open`), `bitlk-<uuid>` (UDisks BitLocker unlocks).

---

## Why Names Change — Persistent Naming

`sda` vs `sdb` and `nvme0` vs `nvme1` are assigned by **probe order**, which depends on hardware init timing — driver load order, enclosure enumeration, even boot firmware updates change it. This machine really did move its root disk from `nvme1n1` to `nvme0n1` across one boot; only the fact that fstab used `UUID=` kept it bootable (the comments in the file still say `nvme1n1p3` — comments lie, UUIDs don't).

The kernel keeps stable symlinks under `/dev/disk/`:

| Directory | Anchored to | Best for |
|---|---|---|
| `by-uuid` | filesystem signature | fstab mounts |
| `by-partuuid` | GPT partition entry (survives reformatting!) | fstab when a partition gets reformatted often |
| `by-id` | model + serial or EUI/WWWN | scripts addressing whole disks |
| `by-label` | human-set label | quick manual work |
| `by-path` | physical connector/slot | pinning to a port |

```bash
ls -l /dev/disk/by-id          # nvme-eui.… -> ../../nvme0n1
```

---

## Partition Tables

| | GPT | MBR |
|---|---|---|
| Max disk | 9.4 ZB (64-bit LBA) | 2 TiB |
| Max partitions | 128 default | 4 primary |
| Integrity | CRC32 of table + backup copy at end of disk | none — single fragile copy |
| IDs | every partition gets a GUID | nothing comparable |

Modern tools align the first partition at 1 MiB automatically — never fight this. Every UEFI system also carries an **ESP**: a small FAT32 partition holding bootloaders/kernels (here: `DUSKY_EFI`, mounted at `/boot`).

---

## Stacking Order Matters

- **LUKS below the filesystem**: everything above it (files, snapshots, scrubs, `df`) sees plaintext; everything below (the SSD) sees ciphertext. TRIM must be explicitly allowed to pierce it — see [[Disk#TRIM / Discard]]
- **Resize rule**: the filesystem must always fit inside its partition — grow outside-in, shrink inside-out — see [[BTRFS#Resizing]]
- btrfs multi-device and btrfs RAID profiles replace mdadm/LVM at the filesystem layer; don't stack both without meaning it.

---

## From Power Button to Prompt (verified chain)

1. **UEFI firmware** executes the bootloader from the ESP: systemd-boot reads `/boot/loader/loader.conf`
2. A **loader entry** (`arch-linux.conf`, `…-dusky-gaming.conf`) names kernel + initrd + options
3. **Kernel command line** locates the root — see `/proc/cmdline`: `root=UUID=c7c2a1a4-… rootfstype=btrfs rootflags=subvol=/@` — the root subvolume `@` is chosen *at boot*, not by fstab
4. **initramfs** (early userspace) loads storage drivers, assembles the root device by UUID, then `switch_root`s into it
5. **systemd** translates `/etc/fstab` into mount units and mounts everything else: `@home`, `@var_log`, `@snapshots`, … plus generated swaps
6. Anything not unlocked at boot waits for later: this machine's LUKS volumes are opened post-boot by `drive_manager` using keyfiles, after which their fstab/automount entries fire

When boot drops to an emergency shell, diagnose **downward through the stack**: does `blkid` still show the filesystem? (yes → mount-layer problem; no → encryption/partition/hardware layer problem).

---

## Mounts Are a Runtime Tree

A mount point is not a property of the disk — it's a live entry in the kernel's mount table. Consequences worth internalizing:

- `findmnt` shows the tree; stacked mounts **hide** whatever was underneath (overmounting)
- One filesystem can appear at many paths simultaneously — every `@var_*` mount here is the *same* btrfs showing different subvolumes
- `/.snapshots` and `/home/.snapshots` being their own mounts is deliberate: snapshot stores live outside `@`/`@home` so restores stay atomic ([[BTRFS]])

---

## Which Note Covers Which Question

| Question | Note |
|---|---|
| What is this device, how fast, how healthy? | [[Disk]] |
| CoW, NOCOW, compression, snapshots, ENOSPC, resizing | [[BTRFS]] |
| Open a Windows-encrypted volume | [[Bitlocker]] |
| NTFS mounts fail / metadata damage | [[Fixing Un Mountable NTFS drive]] |
