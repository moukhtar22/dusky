# Btrfs Filesystem Management

> [!summary]
> **Recommended policy on modern Arch Linux systems**
> - Keep **CoW enabled** on general-purpose Btrfs filesystems.
> - Use **targeted NOCOW** (`chattr +C`) only for paths that genuinely benefit from overwrite-heavy behavior, such as **VM images**, **database data directories**, and some **scratch/cache** workloads.
> - Put those workloads in a **dedicated directory or subvolume**, and usually **exclude them from snapshot schedules**.
> - Avoid legacy/obsolete mount options like `ssd` and `space_cache=v2`.
> - Prefer `x-gvfs-show` over `comment=x-gvfs-show`.
> - For NTFS on Arch, prefer the in-kernel **`ntfs3`** driver unless you specifically need `ntfs-3g`.

---

## CoW and NOCOW on Btrfs

Btrfs uses **Copy-on-Write (CoW)** for file data by default. This is one of the filesystem’s core features and enables:

- data checksumming
- transparent compression
- reflinks/clones
- snapshot-friendly behavior
- safer crash consistency semantics for many write patterns

For some workloads, especially large files with frequent random overwrites, CoW can add measurable overhead and fragmentation. Typical examples:

- virtual machine disk images
- database data files
- high-churn scratch/build/cache directories
- swapfiles on Btrfs, using the dedicated helper

---

## What disabling CoW actually changes

On Btrfs, disabling CoW is commonly called **NOCOW** and is usually applied with the `C` file attribute.

### Effects of NOCOW

| Behavior | CoW file | NOCOW file |
| --- | --- | --- |
| Data checksums | Yes | **No** |
| Compression | Yes | **No** |
| Reflink/dedupe compatibility | Yes | **No / incompatible** |
| Snapshot inclusion | Yes | **Yes** |
| Random overwrite performance | Often worse for large overwrite-heavy files | Often better when extents are exclusive |
| Metadata CoW/checksums | Yes | **Still yes** |

> [!warning]
> **NOCOW files are still included in snapshots.**  
> The common misconception is that `chattr +C` makes files “unsnapshotable.” That is incorrect.  
> What actually changes is that **file data** loses checksumming and compression, and later writes may lose the NOCOW fast path if snapshots or reflinks make the extents shared.

### Important snapshot nuance

NOCOW helps most when the file’s extents are **not shared**.

If you snapshot a subvolume containing NOCOW files, the files are still present in the snapshot. Because snapshots share extents, later writes to those files may need to allocate new extents to preserve snapshot semantics. In practice:

- NOCOW still works
- but the overwrite-in-place advantage is reduced after snapshots/reflinks
- therefore, VM/database directories are usually best kept in a **separate subvolume excluded from automated snapshots**

---

## When to use `chattr +C`

Use `chattr +C` only when you specifically want:

- reduced CoW overhead for overwrite-heavy files
- no compression on those files
- no data checksums on those files

### Good candidates

- raw VM images
- qcow2 images stored on Btrfs, when you accept the tradeoffs
- database data directories, if the application/vendor supports or recommends it
- large scratch data rewritten frequently

### Poor candidates

- normal documents and media libraries
- anything where Btrfs data checksums are a primary benefit
- paths you snapshot heavily and expect to retain maximum NOCOW benefit
- workloads where you only want to disable compression, not CoW

> [!note]
> If your real goal is only to disable compression for a path, use a **compression property** instead of NOCOW:
>
> ```bash
> sudo btrfs property set /path/to/dir compression none
> ```
>
> This preserves CoW and checksumming while disabling compression for new files created under that path.

---

## Applying NOCOW to a directory

### Core rule

Set `+C` **before** the data is written.

- On a **directory**, `+C` affects **newly created files** under that directory.
- Existing files are **not converted**.
- Moving files into the directory with `mv` **within the same filesystem** does **not** convert them, because the inode is unchanged.

### Recommended pattern

For VM or database storage, create a dedicated directory or subvolume first, then apply `+C`.

#### Example: dedicated VM directory

```bash
sudo install -d -m 0755 /mnt/browser/vms
sudo chattr +C /mnt/browser/vms
lsattr -d /mnt/browser/vms
```

Expected output includes a `C` attribute on the directory.

#### Example: dedicated subvolume for snapshot isolation

```bash
sudo btrfs subvolume create /mnt/browser/vms
sudo chattr +C /mnt/browser/vms
lsattr -d /mnt/browser/vms
```

This is often the better design if you use automated snapshots, because the subvolume can be excluded cleanly.

> [!tip]
> For VM or database data on Btrfs, the best practice is usually:
> 1. create a dedicated **subvolume**
> 2. apply `chattr +C`
> 3. exclude that subvolume from regular snapshots

---

## Converting existing data to NOCOW

Setting `+C` on a populated directory does **not** convert its existing files.

To convert existing data, you must **rewrite it into a directory that already has `+C`**.

### Safe migration procedure

1. Stop the application using the data.
2. Create a new empty destination directory.
3. Apply `+C` to the destination.
4. Copy the data with a **real copy**, not a reflink.
5. Replace the old directory.

#### Example

```bash
sudo install -d -m 0755 /mnt/browser/vms.nocow
sudo chattr +C /mnt/browser/vms.nocow

sudo cp -a --reflink=never --sparse=always /mnt/browser/vms/. /mnt/browser/vms.nocow/
```

Then swap directories:

```bash
sudo mv /mnt/browser/vms /mnt/browser/vms.old
sudo mv /mnt/browser/vms.nocow /mnt/browser/vms
```

Verify:

```bash
lsattr -d /mnt/browser/vms
find /mnt/browser/vms -maxdepth 1 -type f -exec lsattr {} +
```

> [!warning]
> `mv` only converts data if it becomes a **copy+delete** operation, which happens across filesystems.  
> A plain `mv` **within the same Btrfs filesystem** only renames the inode and preserves its current CoW/NOCOW state.

> [!warning]
> Do **not** use reflink cloning for conversion.  
> Use `cp --reflink=never` or another tool that performs a real data copy.

---

## Re-enabling CoW

To stop applying NOCOW to future files in a directory:

```bash
sudo chattr -C /path/to/directory
```

This only affects **future files** created there.

Existing NOCOW files are not “repaired” back into normal CoW files automatically. To fully restore normal Btrfs behavior for existing data, rewrite the files into a normal CoW directory.

---

## Mount-wide `nodatacow`

Btrfs also supports the `nodatacow` mount option.

### Use this only when you really mean it

`nodatacow` is rarely the right default for a general-purpose filesystem because it disables core Btrfs benefits for affected data:

- no data checksums
- no compression
- reduced reflink usefulness

> [!caution]
> On Btrfs, many mount options are effectively **filesystem-wide**, not cleanly per-subvolume.  
> In practice, options such as `compress=...`, `nodatacow`, and several others should be treated as **whole-filesystem policy**.  
> If the same Btrfs filesystem is mounted in multiple places, the **first mount generally determines the effective policy**.

### Practical guidance

If you truly want a mostly or entirely NOCOW datastore:

- use a **dedicated Btrfs filesystem**
- or use another filesystem better suited for that workload, such as **XFS** or **ext4**

### Dedicated NOCOW Btrfs filesystem example

```fstab
UUID=67a3dcc0-6186-4000-a96a-47f29ab0293e  /mnt/vmstore  btrfs  rw,noatime,nodatacow,discard=async,nofail,x-systemd.automount  0  0
```

> [!warning]
> Do **not** combine `nodatacow` with `compress=...` and expect compression to apply to that NOCOW data. It will not.

---

## Modern `fstab` reference

---

## Btrfs: recommended general-purpose data mount

For a modern SSD/NVMe-backed Btrfs data volume, a good baseline is:

```fstab
UUID=67a3dcc0-6186-4000-a96a-47f29ab0293e  /mnt/browser  btrfs  rw,compress=zstd:3,discard=async,nofail,x-systemd.automount,x-gvfs-show  0  0
```

### Why this is a better 2026 baseline

- keeps CoW enabled globally
- keeps compression enabled for normal data
- allows targeted NOCOW directories where needed
- avoids legacy options that are no longer useful

### Option reference

| Option | Meaning | Recommendation |
| --- | --- | --- |
| `rw` | Read-write mount | Normal default |
| `compress=zstd:3` | Transparent compression using Zstandard level 3 | Good general-purpose default |
| `discard=async` | Asynchronous online TRIM | Good for SSD/NVMe if the full stack supports discard |
| `nofail` | Do not block boot if the device is absent | Good for secondary/removable data volumes |
| `x-systemd.automount` | Create an automount unit and mount on first access | Good for non-root data disks |
| `x-gvfs-show` | Hint for desktop file managers | Optional but useful on desktop systems |

### Common optional additions

| Option | Use when | Notes |
| --- | --- | --- |
| `noatime` | You want to suppress access-time writes entirely | `relatime` is already the default and is fine for most systems |
| `subvol=@data` | You mount a specific subvolume | Recommended when using subvolume layouts |
| `x-systemd.idle-timeout=10min` | You want automounted volumes to unmount after inactivity | Optional desktop convenience |
| `x-gvfs-name=Browser` | You want a custom display name in file managers | Optional |

> [!note]
> If the device is encrypted with LUKS, ensure the mapper device is unlocked first via `/etc/crypttab`, a systemd-cryptsetup unit, or your chosen unlock mechanism.  
> The Btrfs mount cannot succeed until the decrypted block device exists.

---

## Btrfs mount options to avoid or omit on modern systems

| Option | Status | Why |
| --- | --- | --- |
| `ssd` | Usually omit | Btrfs auto-detects non-rotational devices |
| `space_cache=v2` | Omit | The free-space-tree implementation is standard; explicitly setting this is unnecessary |
| `comment=x-gvfs-show` | Replace | Use `x-gvfs-show` directly |
| `nodatacow` on a shared/general filesystem | Avoid | Too blunt; use `chattr +C` for targeted paths instead |

> [!tip]
> If you do not want continuous online discard, omit `discard=async` and enable periodic TRIM instead:
>
> ```bash
> sudo systemctl enable --now fstrim.timer
> ```

---

## NTFS: modern Arch Linux mount example

On current Arch systems, prefer the in-kernel **`ntfs3`** driver for local NTFS volumes unless you specifically need `ntfs-3g`.

### Recommended `fstab` entry

```fstab
UUID=9C38076638073F30  /mnt/media  ntfs3  uid=1000,gid=1000,dmask=0022,fmask=0133,windows_names,noatime,nofail,x-systemd.automount,x-gvfs-show  0  0
```

### Why this is better than a plain `umask=0022`

Using `umask=0022` on NTFS-style permission emulation often makes **all files appear executable**.  
For most media/data volumes, this is undesirable.

A better split is:

- `dmask=0022` → directories behave like `0755`
- `fmask=0133` → files behave like `0644`

### NTFS option reference

| Option | Meaning |
| --- | --- |
| `uid=1000` | Synthetic owner UID for files on the mount |
| `gid=1000` | Synthetic owner GID for files on the mount |
| `dmask=0022` | Directory permissions emulate `0755` |
| `fmask=0133` | File permissions emulate `0644` |
| `windows_names` | Reject names invalid on Windows |
| `noatime` | Disable access-time updates |
| `nofail` | Do not block boot if the device is missing |
| `x-systemd.automount` | Mount on first access |
| `x-gvfs-show` | Show the volume in file managers |

> [!warning]
> Do **not** mount an NTFS volume read-write from Linux if Windows left it in a **hibernated** or **Fast Startup** state.  
> Disable **Fast Startup** in Windows on dual-boot systems if you want safe routine read-write access from Linux.

> [!note]
> Replace `1000` with the actual UID/GID of the user who should own the mount:
>
> ```bash
> id -u username
> id -g username
> ```

---

## Discovering UUIDs and filesystem types

Use these commands before writing `fstab` entries:

```bash
lsblk -f
sudo blkid
```

Example targeted output:

```bash
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/browser
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/media
```

---

## Validating `fstab` changes

After editing `/etc/fstab`:

```bash
sudo findmnt --verify --verbose
sudo systemctl daemon-reload
```

If the entry uses `x-systemd.automount`, start the generated automount unit:

```bash
sudo systemctl start "$(systemd-escape --path --suffix=automount /mnt/browser)"
sudo systemctl start "$(systemd-escape --path --suffix=automount /mnt/media)"
```

Then trigger the mount by accessing the path:

```bash
ls /mnt/browser
ls /mnt/media
```

To inspect the resulting effective mount options:

```bash
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/browser
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/media
```

---

## Common mistakes

> [!warning]
> Avoid these recurring errors:
>
> - Setting `chattr +C` on a directory and assuming **existing files** were converted
> - Moving files into a `+C` directory with `mv` and assuming they became NOCOW
> - Thinking NOCOW files are **excluded from snapshots**
> - Using `nodatacow` on one subvolume of a shared Btrfs filesystem and expecting it to stay isolated
> - Keeping legacy options like `ssd` or `space_cache=v2` in modern `fstab`
> - Using `umask=0022` on NTFS when you really want **non-executable regular files**

---

## Quick reference

### Create a NOCOW directory

```bash
sudo install -d -m 0755 /mnt/browser/vms
sudo chattr +C /mnt/browser/vms
lsattr -d /mnt/browser/vms
```

### Convert existing directory contents to NOCOW

```bash
sudo install -d -m 0755 /mnt/browser/vms.nocow
sudo chattr +C /mnt/browser/vms.nocow
sudo cp -a --reflink=never --sparse=always /mnt/browser/vms/. /mnt/browser/vms.nocow/
```

### Recommended Btrfs `fstab` baseline

```fstab
UUID=<btrfs-uuid>  /mnt/browser  btrfs  rw,compress=zstd:3,discard=async,nofail,x-systemd.automount,x-gvfs-show  0  0
```

### Dedicated NOCOW Btrfs filesystem

```fstab
UUID=<btrfs-uuid>  /mnt/vmstore  btrfs  rw,noatime,nodatacow,discard=async,nofail,x-systemd.automount  0  0
```

### Recommended NTFS3 `fstab` baseline

```fstab
UUID=<ntfs-uuid>  /mnt/media  ntfs3  uid=<uid>,gid=<gid>,dmask=0022,fmask=0133,windows_names,noatime,nofail,x-systemd.automount,x-gvfs-show  0  0
```

---

## Bottom line

For modern Btrfs systems:

- **Do not disable CoW filesystem-wide by default**
- **Keep compression enabled**
- Use **`chattr +C` selectively**
- Put NOCOW workloads in **dedicated directories/subvolumes**
- Exclude those paths from **snapshot schedules** when possible
- Use current `fstab` syntax and avoid legacy mount options
