# Engine: `fstab`

- **Class:** `FstabEngine` — `engines/fstab.py`
- **Engine types:** `fstab`
- **Default target:** `/etc/fstab`

## Target format

A standard fstab file. The engine targets **one device's entry**: the device must first be selected via the `mount_info/uuid` key, then the engine surgically replaces that entry's line (preserving comments and surrounding lines) with a line compiled from the full state model. Cross-tag device matching treats `UUID=…`, `PARTUUID=…`, `LABEL=…`, `PARTLABEL=…` and `/dev/…` paths as the same device (including `/dev/disk/by-*` symlink resolution).

```
UUID=abcd-1234  /home  btrfs  defaults,noatime,compress=zstd:3,subvol=@home  0 0
```

## Scope / key mapping

Fixed state model — scopes and keys are exactly these (8 keys, no others are read):

| scope | key | type_ | notes |
|---|---|---|---|
| `mount_info` | `uuid` | string | **device selector**: `UUID=…`, `PARTUUID=…`, `LABEL=…`, `PARTLABEL=…`, `/dev/…`. Must normalize to a valid identifier or the write is rejected. |
| `mount_info` | `mount_point` | string | must start with `/` (or `none`/`swap` when fs is `swap`); NUL/LF/CR rejected |
| `filesystem` | `fs_type` | cycle | `btrfs`, `vfat`, `exfat`, `ntfs`, `ext4`, `ext3`, `ext2`, `swap` (anything else fails the write) |
| `filesystem` | `drive_type` | cycle | `ssd`, `hdd` (auto-detected on load) |
| `btrfs_ops` | `subvol` | string | must be non-empty; `, \t \n` and NUL rejected |
| `btrfs_ops` | `cow_enabled` | bool | CoW on/off (`compress=zstd:3` vs `nodatacow`) |
| `system_flags` | `auto_mount` | bool | `false` → adds `noauto` |
| `system_flags` | `gvfs_show` | bool | adds `user,comment=x-gvfs-show` |

Defaults (used when no device is selected): `uuid=""`, `mount_point="/"`, `fs_type="btrfs"`, `drive_type="ssd"`, `subvol="@"`, `cow_enabled=True`, `auto_mount=True`, `gvfs_show=True`. State values for the bool keys are real Python booleans.

## Types & value handling

- Values are coerced to bool when `item_type == "bool"` **or** the key is `cow_enabled`/`auto_mount` (accepts `true/1/yes/on/y/t/enabled` and `false/0/no/off/n/f/disabled`, case-insensitive).
- `scope`/`key` must match the exact catalog above (`mount_info/uuid` etc.) — the engine stores state as `f"{scope}/{key}"`.
- Writing `mount_info/uuid` is a **selection switch**: it validates the identifier (rejects "RAW" forms), forces a re-parse of `/etc/fstab`, and does **not** modify the file.
- Any other write with `uuid=""` fails: "Cannot modify fstab: Select a valid target device first."
- The final line is compiled per filesystem: btrfs (`discard=async` on ssd, `compress=zstd:3` / `nodatacow`, `subvol=…`), vfat/exfat/ntfs (uid/gid/fmask/dmask/utf8 variants), ext4/ext3/ext2 (`lazytime`, pass 1 for `/`), swap (mount point forced to `none`).

## Quirks

- `nofail` is auto-added for non-critical mounts and **never** for `/`, `/usr`, `/var`, `/boot`, `/efi`, `/boot/efi`.
- `drive_type` for btrfs is detected from `/sys/block/…/queue/rotational`; for other filesystems it is inferred from `ssd`/`discard*` options.
- `load_state` caches until mtime changes; on load, `mount_point`/`subvol` are decoded from octal escapes (`\040` → space) and `subvol` is the first `subvol=` option.
- Concurrency/atomicity: dedicated lockfile with `flock` (SH 2 s / EX 5 s timeouts), mtime TOCTOU abort, atomic `mkstemp` + `os.replace` with fchown/fchmod and directory fsync.
- **No `AUTH_REQUIRED` / no sudo fallback** — the engine writes directly; schemas should set `REQUIRE_ROOT = True`.

## Example items

```python
ConfigItem(label="Device", key="uuid", scope="mount_info", type_="string",
           default="UUID=abcd-1234", group="Mount"),
ConfigItem(label="Filesystem", key="fs_type", scope="filesystem", type_="cycle",
           default="btrfs", options=["btrfs", "vfat", "exfat", "ntfs",
                                     "ext4", "ext3", "ext2", "swap"], group="Mount"),
ConfigItem(label="Subvolume", key="subvol", scope="btrfs_ops", type_="string",
           default="@", group="Btrfs"),
ConfigItem(label="Auto Mount", key="auto_mount", scope="system_flags",
           type_="bool", default=True, group="Flags"),
```