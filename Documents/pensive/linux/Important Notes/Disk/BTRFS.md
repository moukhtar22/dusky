# Btrfs Filesystem Management

> [!note] Scope
> Permanent reference for **CoW/NOCOW policy, subvolumes, snapshots, compression, integrity checks, and modern `fstab`** on Arch Linux. Updated **August 2026** against kernel 7.x (`btrfs-progs 7.1`).
>
> Related: [[Storage Stack]] · [[Disk]] · [[Bitlocker]] · [[Fixing Un Mountable NTFS drive]]

> [!info] This machine (verified August 2026)
> - Single-device Btrfs `DUSKY_ROOT` (120G partition) with a **flat topology**: every mount point is its own top-level subvolume — `@`, `@home`, `@snapshots`, `@home_snapshots`, `@var_log`, `@var_cache`, `@var_tmp`, `@var_lib_machines`, `@var_lib_portables`, `@var_lib_libvirt`, `@var_lib_mysql`, `@var_lib_postgres`, `@swap`
> - Snapper configs `root` and `home`; snapshot stores are the top-level `@snapshots`/`@home_snapshots` mounted at `/.snapshots` and `/home/.snapshots` (never nested inside `@`)
> - Daily paired snapshots at 20:00 via `dusky_snapshot.timer`; retention 6; timeline snapshots disabled; quotas disabled; rollback via `~/user_scripts/btrfs_snapshots/cc/dusky_snapshot_manager.py`
> - Swapfile lives at `/swap/swapfile` on the `@swap` subvolume
> - fstab still carries legacy `ssd,space_cache=v2` options — harmless, but redundant and safe to drop next time it is edited

---

## Policy

> [!summary]
> - Keep **CoW enabled** filesystem-wide.
> - Use targeted **NOCOW** (`chattr +C`) only for overwrite-heavy paths: VM images, database dirs, scratch/cache.
> - Put those in a **dedicated subvolume excluded from snapshots**.
> - Skip obsolete mount options (`ssd`, `space_cache=v2`).
> - Prefer `x-gvfs-show` over `comment=x-gvfs-show`.
> - NTFS volumes: use the in-kernel **`ntfs3`** driver unless `ntfs-3g` is specifically needed.

---

## CoW and NOCOW

CoW is what gives Btrfs checksumming, compression, reflinks, snapshots, and crash consistency. It hurts exactly one class of workload: large files with frequent random overwrites — VM disks, database files, high-churn scratch.

### Effects of NOCOW

| Behavior | CoW file | NOCOW file |
|---|---|---|
| Data checksums | Yes | **No** |
| Compression | Yes | **No** |
| Reflink/dedupe compatible | Yes | **No/incompatible** |
| Included in snapshots | Yes | **Yes** |
| Random overwrite performance | Poor for large overwrite-heavy files | Better while extents are unshared |

> [!warning]
> NOCOW files are **still snapshotted**. `chattr +C` does not make anything "invisible to snapshots" — it drops checksumming/compression for that inode's data. And once a snapshot or reflink shares an extent, later writes must CoW again anyway. This is why NOCOW workloads belong in subvolumes excluded from snapshot schedules.

### Rules of `chattr +C`

- Set `+C` on an **empty** directory before data lands; it applies to **newly created files only**.
- Existing files are never converted in place.
- `mv` **within the same filesystem** renames the inode and preserves its current state — moving a plain file into a `+C` dir does **not** convert it.
- Exception learned the hard way: if the move crosses a **subvolume boundary**, rename fails (`EXDEV`) and GNU `mv` silently falls back to copy+delete → fresh inode → **it does inherit `+C`**. Same-looking command, different result.

```bash
sudo btrfs subvolume create /mnt/vms          # dedicated subvolume first
sudo chattr +C /mnt/vms
lsattr -d /mnt/vms                            # expect ---------------C------
```

### Converting Existing Data to NOCOW

Rewrite it into a directory that already has `+C`:

```bash
sudo install -d -m 0755 /srv/vms.nocow
sudo chattr +C /srv/vms.nocow
sudo cp -a --reflink=never --sparse=always /srv/vms/. /srv/vms.nocow/

# swap
sudo mv /srv/vms /srv/vms.old && sudo mv /srv/vms.nocow /srv/vms
```

Verify: `find /srv/vms -maxdepth 1 -exec lsattr {} +`

> [!warning]
> `--reflink=never` is mandatory — reflinks share extents and defeat the whole conversion. A plain same-fs `mv` never converts either (see rules above).

Re-enable CoW for future files with `sudo chattr -C /dir`; existing NOCOW files stay NOCOW until rewritten.

### Compression Property (per-inode)

To disable *only* compression while keeping checksums and CoW, use the Btrfs inode property — equivalent to `chattr +m`:

```bash
sudo btrfs property set /path/to/dir compression none   # sets NOCOMPRESS ('m' flag)
sudo btrfs property get /path/to/dir compression
sudo btrfs property set /path/to/dir compression ""     # reset to default
```

Valid values: `zstd`, `zlib`, `lzo`, `none`/`no` (= NOCOMPRESS), empty string (= default). Requires btrfs-progs ≥ 5.18 semantics (current Arch is far past this). Fails with `Invalid argument` on any inode that has `+C`.

### Mount-wide `nodatacow`

Rarely right: kills checksums + compression for all data. Many "per-subvolume" options (`compress=`, `nodatacow`) are really **whole-filesystem** policy — the first mount wins. If you truly want a bulk-NOCOW store, use a dedicated filesystem or ext4/XFS instead.

```fstab
UUID=<btrfs-uuid>  /mnt/vmstore  btrfs  rw,noatime,nodatacow,nofail,x-systemd.automount  0  0
```

---

## Modern `fstab`

General-purpose data volume baseline:

```fstab
UUID=<btrfs-uuid>  /mnt/data  btrfs  rw,noatime,compress=zstd:3,discard=async,nofail,x-systemd.automount,x-gvfs-show  0  0
```

| Option | Meaning | Recommendation |
|---|---|---|
| `compress=zstd:3` | transparent zstd level 3 (levels 1–15) | best general default |
| `compress-force=zstd:3` | skip the "first blocks look incompressible" heuristic | only for known-compressible data (logs, text); wasted CPU elsewhere |
| `noatime` | no access-time writes | good default; `relatime` also fine |
| `discard=async` | online TRIM | **default since kernel 6.2** on discard-capable devices — listing it is optional |
| `nofail` | don't block boot when absent | secondary/removable volumes |
| `x-systemd.automount` | mount on first access | non-root data disks |
| `subvol=@data` | select subvolume | always, with flat layouts |
| `x-gvfs-name=Data` · `x-systemd.idle-timeout=10min` | cosmetic / idle unmount | optional |

Options to omit on modern kernels:

| Option | Why omit |
|---|---|
| `ssd` | auto-detected from the rotational flag |
| `space_cache=v2` | free-space-tree has been the default implementation since kernel 6.2 |
| `nodatacow` on shared fs | too blunt; use `chattr +C` per path |
| `autodefrag` on SSDs | burns write cycles for zero physical benefit; HDD-only option (and it breaks reflinks/snapshots of the files it touches) |

> [!note]
> Because `discard=async` is automatic on Btrfs, this machine's `fstrim.timer` only really serves the **ext4** volumes (`/mnt/media`, the mozilla partition). Keeping both is harmless — redundant on Btrfs, useful everywhere else.

After editing:

```bash
sudo findmnt --verify --verbose
sudo systemctl daemon-reload
sudo systemctl start "$(systemd-escape --path --suffix=automount /mnt/data)"
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/data
```

> [!note]
> LUKS underneath? The mapper must be unlocked first (`/etc/crypttab` or systemd-cryptsetup) before the Btrfs mount can succeed.

---

## Subvolume Operations

```bash
sudo btrfs subvolume create /path/@name        # create
sudo btrfs subvolume list /                    # all subvolumes (-a adds ones queued for deletion)
sudo btrfs subvolume show /path                # id, uuid, flags
sudo btrfs subvolume delete /path/@name
sudo btrfs subvolume snapshot /path/@ /path/@new            # writable clone
sudo btrfs subvolume snapshot -r /path/@ /path/@ro          # read-only (sendable)
sudo btrfs property set /path/@ ro true                     # flip after the fact
sudo btrfs subvolume set-default <ID> /mountpoint           # what rootflags/boot pick up
btrfs subvolume get-default /
```

Snapshots are **not recursive**: nested subvolumes appear as empty directories inside a snapshot. That is precisely why the flat layout matters — restoring `@` would strand any nested subvolume inside the retired copy and make it undeletable (`ENOTEMPTY`). Keep everything top-level like this machine.

Rollback workflow (paired root/home snapshots, atomic swap): see `~/user_scripts/btrfs_snapshots/cc/dusky_snapshot_manager.py`.

### Send / Receive (incremental backups)

```bash
sudo btrfs send -p /.snapshots/42/snapshot /.snapshots/43/snapshot | \
    sudo btrfs receive /mnt/backup
```

Only the delta since snapshot 42 travels. Requires read-only snapshots; pipe over ssh for off-machine backups.

---

## Integrity & Space

```bash
sudo btrfs device stats /          # I/O, corruption, generation errors — should be all zero
sudo btrfs scrub start -B /        # verify every byte against checksums (-B = foreground)
sudo btrfs scrub status /
sudo btrfs balance status /
sudo btrfs filesystem usage /      # allocator truth (df lies here)
sudo btrfs filesystem du /path     # real per-file usage incl. shared/reflink'd extents
sudo compsize -x /                 # compression savings breakdown (pacman: compsize)
```

> [!tip]
> Run `device stats` after any suspected issue; run a scrub monthly or before trusting old backups. Enable `btrfs-scrub@-.timer` if you want it automatic.

Defragmentation is rarely wanted on SSDs and **breaks reflinks/snapshots** for the files it rewrites:

```bash
sudo btrfs filesystem defragment -r -czstd /path   # recursive, recompress with zstd
```

---

## Resizing

The order matters — always keep the filesystem smaller than its partition:

```bash
# GROW: partition first, then filesystem
sudo sfdisk --no-reread -N 3 /dev/nvme0n1     # feed new "start= …, size= …" on stdin
sudo partx -u /dev/nvme0n1 && sudo udevadm settle
sudo btrfs filesystem resize max /

# SHRINK: filesystem first (with headroom!), then partition
sudo btrfs filesystem resize -20G /
sudo sfdisk --no-reread -N 3 /dev/nvme0n1     # shrink partition to match
sudo partx -u /dev/nvme0n1 && sudo udevadm settle
sudo btrfs filesystem resize max /            # re-fit exactly
```

Automated, validated version: `~/user_scripts/drives/format/resize_btrfs.py` (checks free space, +500M safety margin, byte-exact sector math).

---

## ENOSPC: When df Says Free But Writes Fail

Btrfs allocates space in fixed chunks, separately for data and metadata. A filesystem can report gigabytes free while refusing writes because **every allocated chunk is already used up and there is no unallocated device space left to allocate new chunks from** — classic on small volumes stuffed with snapshots.

Diagnose:

```bash
sudo btrfs filesystem usage /    # look at "Device unallocated" → near 0 = trouble
```

Escape ladder (least invasive first):

1. Delete old snapshots: `sudo snapper -c root list` then `sudo snapper -c root delete 5-10`
2. Reclaim pinned chunks — iterative data balance from low usage upward:

```bash
for u in 5 10 20 30 50; do sudo btrfs balance start -dusage=$u / && sync; done
sudo btrfs balance start -musage=50 /
```

3. Still stuck: temporarily inject a loop-backed device so the allocator gets raw room, rebalance, then evacuate and remove it:

```bash
fallocate -l 2G /mnt/other-disk/rescue.img
LOOP=$(sudo losetup -f --show /mnt/other-disk/rescue.img)   # needs CONFIG_BLK_DEV_LOOP
sudo btrfs device add -f "$LOOP" /
sudo btrfs balance start -dusage=0 /
sudo btrfs device remove "$LOOP" /    # do NOT reboot between add and remove
```

4. Automated end-to-end rescue: `~/user_scripts/drives/btrfs_fix_enospc_metadata_exaustion.sh`

> [!warning]
> On this machine's custom kernels `CONFIG_BLK_DEV_LOOP=n`, so step 3 must run from a stock kernel (or use a USB stick as the temporary device). Never reboot while the pool spans a loop device you intend to delete.

---

## Swapfile on Btrfs

Requirements: NOCOW, uncompressed, on a subvolume outside snapshot schedules — exactly what `@swap` is here. The helper sets all of that up (nocow + no-compress) and formats in one step:

```bash
sudo btrfs filesystem mkswapfile --size 8g /swap/swapfile
sudo swapon /swap/swapfile
```

fstab: `/swap/swapfile  none  swap  defaults  0  0`
Resume support needs `resume=/dev/...` + `resume_offset=` (`btrfs inspect-internal map-swapfile -r /swap/swapfile`).

---

## NTFS Volumes

Prefer the in-kernel **`ntfs3`** driver (module present here) over FUSE `ntfs-3g`:

```fstab
UUID=<ntfs-uuid>  /mnt/media  ntfs3  uid=1000,gid=1000,dmask=0022,fmask=0133,windows_names,noatime,nofail,x-systemd.automount,x-gvfs-show  0  0
```

- `dmask=0022` → dirs `0755`; `fmask=0133` → files `0644`. Plain `umask=0022` makes every file look executable.
- `windows_names` rejects filenames Windows can't handle.
- Substitute the real owner: `id -u user && id -g user`.

> [!warning]
> Never write-mount an NTFS volume that Windows hibernated or left in Fast Startup state. Disable Fast Startup on dual-boot systems. Repair path: [[Fixing Un Mountable NTFS drive]] · encrypted variant: [[Bitlocker]].

Discover UUIDs first: `lsblk -f` or `sudo blkid`.
