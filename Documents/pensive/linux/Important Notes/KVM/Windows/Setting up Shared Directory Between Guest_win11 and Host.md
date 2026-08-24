---
title: "Shared Folder — Virtiofs (Host ↔ Win11)"
tags:
  - kvm
  - windows
  - virtiofs
  - sharing
---

# Shared Folder — Virtiofs (Host ↔ Win11)

> [!abstract] What is virtiofs?
> Host directory exposed as a guest filesystem via shared memory — near-local FS speed (vs SMB). Needs **Enable shared memory** + `virtiofs` device + **WinFsp** + **VirtIO-FS Service**.

## Prereqs

- **VirtIO Guest Tools** installed (see [[Install a Windows Virtual Machine on KVM]]).
- VM **Shutoff** before hardware edits (virt-manager enforces).

## Part 1 — Host (virt-manager)

### 1. Enable shared memory

1. VM → Open → **Show virtual hardware details** (lightbulb)
2. Left → **Memory** (RAM) → ✅ **Enable shared memory** → **Apply**

XML (`virsh dumpxml win11 | grep -A2 memoryBacking`):

```xml
<memoryBacking><source type="memfd"/><access mode="shared"/></memoryBacking>
```
Required for `memfd` + virtiofs DAX.

### 2. Add filesystem device

1. **Add Hardware** → **Filesystem**
2. **Driver:** `virtiofs`
3. **Source path:** host directory to share — e.g. `/mnt/zram1` (if that’s your image pool, share a subdir like `/mnt/zram1/share` to avoid exposing images)
4. **Target path:** *tag* (not a drive letter) — e.g. `host_zram1` (simple, no spaces)

→ **Finish** → **Apply** → Start VM.

> [!tip] Naming
> `Target path` is just a label string. Windows maps whatever string you pick.

## Part 2 — Guest (Windows)

### 1. Install WinFsp (File System Proxy)

FUSE for Windows — lets Win access virtiofs.

- Download **winfsp-*.msi** from <https://github.com/winfsp/winfsp/releases> → install defaults (Next→Install). Or in guest PowerShell:
  ```powershell
  Invoke-WebRequest -Uri "https://github.com/winfsp/winfsp/releases/download/v2.1/winfsp-2.1.25156.msi" -OutFile "$env:TEMP\winfsp.msi"; Start-Process msiexec -Wait -ArgumentList "/i $env:TEMP\winfsp.msi /qn"
  ```

### 2. Start VirtIO-FS Service

1. `Win + R` → `services.msc`
2. Locate **VirtIO-FS Service** → Properties → **Startup type: Automatic** → **Start** → OK

> [!info] Service missing?
> Means `virtio-win-guest-tools` didn’t install — reinstall from `virtio-win.iso` CD (keep ISO attached). Tools include the `viofs` driver.

## Part 3 — Use it

- **File Explorer → This PC** → new drive (often `Z:`) labeled with your `Target path` tag (`host_zram1`).

> [!success] Done — low-latency share. No Samba required.

## Permission gotcha

If `Z:` is invisible: host directory perms owned wrong (see `07_storage_setup.py` ACL). Quick fix for a `zram` share:

```bash
sudo chown -R "$(id -un)":"$(id -un)" /mnt/zram1/share
sudo chmod -R 775 /mnt/zram1/share
# or ACL for unprivileged QEMU: sudo setfacl -m u:libvirt-qemu:rwx /mnt/zram1/share
```

Verify host: `virtiofsd` should be running (`ps -ef | grep virtiofsd`).

See: [[Install a Windows Virtual Machine on KVM]], `07_storage_setup.py` (pool ACL deep dive).
