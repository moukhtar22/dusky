---
title: "ACLs on the Image Directory (POSIX.1e)"
tags:
  - kvm
  - storage
  - acl
  - arch
  - libvirt
---

# ACLs on the Image Directory — POSIX.1e

> [!abstract] Goal
> Give the human operator `rwx` on `/var/lib/libvirt/images` (or your custom/ephemeral pool) **without** changing `root:root` ownership, and inherit it for every new qcow2. Canonical: `07_storage_setup.py:provision_acls`.

## Why not `chmod 777` / `chown`?

- `chmod 777` is world-writable.
- `chown $USER` fights libvirt defaults and breaks idempotent pipelines that assume `root:root` owner.
- POSIX ACLs are per-user/per-default entries that coexist with the traditional owner.

## What the pipeline does (fact, not guess)

1. **Resolves QEMU identity** — parses **only uncommented** `user=`/`group=` in `/etc/libvirt/qemu.conf` (`07_*:resolve_qemu_identity`). Arch ships them **commented → root:root** (privileged). ACLs then needed **only for operator**; when de-privileged, adds `u:qemu:rwx` too.
2. **Traversal `--x` on every parent** above pool (e.g. `/`, skipped; `/var`, `/var/lib`, `/var/lib/libvirt`). Each parent gets `setfacl -m u:operator:x` if not already satisfied (checked via `getfacl -cEp` + `perm_value`).
3. **Full `rwx` + `default:rwx` on pool itself** (`setfacl -m u:operator:rwx` + `setfacl -d -m u:operator:rwx`).
4. **Validates FS supports ACLs** — if `setfacl` returns `Operation not supported` (e.g. FAT USB), bails with `mount_facts(fstype)` hint: choose `ext4/xfs/btrfs/tmpfs`.

## Manual reproduction

```bash
TARGET=/var/lib/libvirt/images   # or your 07_*-chosen path

# 1. Verify restriction
ls -ld "$TARGET"
ls -l "$TARGET" 2>&1 | head

# 2. Check current ACL graph
getfacl -cEp -- "$TARGET"
getfacl -cEp -- /var/lib /var/lib/libvirt  # parents

# 3. Clean only if you intentionally want to reset (pipeline never strips blindly)
# sudo setfacl -R -b "$TARGET"   # ← removes all extended ACLs; avoid unless resetting

# 4. Traversal on parents (repeat for each parent dir)
sudo setfacl -m u:"$(id -un)":x /var /var/lib /var/lib/libvirt

# 5. Full + inheritable on pool
sudo setfacl -m u:"$(id -un)":rwx "$TARGET"
sudo setfacl -d -m u:"$(id -un)":rwx "$TARGET"

# 6. If QEMU is de-privileged (user="qemu" active), also:
# sudo setfacl -m u:qemu:rwx "$TARGET" && sudo setfacl -d -m u:qemu:rwx "$TARGET"
# sudo setfacl -m u:qemu:x /var /var/lib /var/lib/libvirt

# 7. Verify
getfacl -- "$TARGET"
# expect:
# user:<you>:rwx
# default:user:<you>:rwx
# (and if unprivileged: user:qemu:rwx / default:user:qemu:rwx)

# 8. Test without sudo
touch "$TARGET"/test_file && ls -l "$TARGET"/test_file && rm "$TARGET"/test_file
```

> [!tip] `rwX` vs `rwx`
> `X` (capital) = execute only on directories / already-executable files. Pipeline uses explicit `rwx` on the pool directory (always `x` needed for `cd`) and `--x` on parents — no ambiguity.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `setfacl: Operation not supported` | pool on FAT/NTFS/no-ACL fs | move pool to `ext4/xfs/btrfs/tmpfs` |
| `Permission denied` even after ACL | missing `--x` on an ancestor | audit `getfacl /var /var/lib…`, add `x` |
| New qcow2 owned `root:root` with no ACL | `default` entry missing | re-add `setfacl -d …` and check `d:u:…` in `getfacl` |
| `ls -l` shows `+` but no effective perms | `mask::` limited | pipeline re-applies `rwx`; ensure mask `rwx` |

State record: `qemu_user`/`qemu_group` + `storage_dir` in `/var/lib/arsonix/state.json`.

See: [[Symbolic link to zram for image file]], `07_storage_setup.py`.
