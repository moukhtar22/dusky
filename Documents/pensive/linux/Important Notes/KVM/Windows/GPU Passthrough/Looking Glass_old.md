---
title: "Looking Glass — Legacy (Muxless, qemu:commandline)"
tags:
  - kvm
  - vfio
  - looking-glass
  - legacy
  - archive
---

# Looking Glass — Legacy (Muxless, `qemu:commandline`) — Archived

> [!warning] Archived — retained as historical reference only
> This is the **pre-2026** muxless guide that used raw `<qemu:commandline>` `ivshmem-plain` + `kvmfr`. It works but is superseded by the native `<shmem>` path in [[Looking Glass]] (`25_looking_glass.py`: native `ivshmem-plain` emits identical `-device`/`-object` pair, validates, handles `/dev/shm` labeling, and tracks `<memballoon>`/NUMA). Keep this file diff-visible; **do not base new VMs on it**.

## Phase 1 — Host (legacy method, kept verbatim)

```bash
paru -S --needed looking-glass
sudo pacman -S --needed freerdp
sudo rm -f /dev/shm/looking-glass
echo "f /dev/shm/looking-glass 0660 new kvm -" | sudo tee /etc/tmpfiles.d/10-looking-glass.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
sudo fallocate -l 64M /dev/shm/looking-glass
sudo chown new:kvm /dev/shm/looking-glass
sudo chmod 0660 /dev/shm/looking-glass
ls -lh /dev/shm/looking-glass
```

> [!info] What changed in current guide vs here
> Current guide enforces `O_EXCL|O_NOFOLLOW`, `posix_fallocate` unconditional, `f` (not truncating live file), size = `pow2(width×height×4×2+10M)`, and documents why `fallocate` vs `truncate`.

## Phase 2 — XML Bridge (legacy)

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <devices>
    <memballoon model='none'/>
  </devices>
  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
  </qemu:commandline>
</domain>
```

> [!danger] Why this is now removed
> - Bypasses libvirt device model → no XML validation, breaks migration checks.
> - Requires `xmlns:qemu` escape hatch; desyncs from `memballoon`/`NUMA` accounting.
> - Leaves `/dev/shm` management to external scripts; `<shmem>` lets libvirt create/label/unlabel atomically.
> Pipeline `25_*.py:strip_legacy_qom` deletes this block before injecting `<shmem>`.

### Legacy hard-reset dance (kept)

```bash
sudo virsh destroy win_10_dusky
sudo rm -f /dev/shm/looking-glass
sudo virsh start win_10_dusky
ls -lh /dev/shm/looking-glass
# 67,108,864 = OK (64M); 4,194,304 = XML size tag wrong/ignored; 0 = invalid XML or VM not running
```

## Legacy: disable adapter

- Via RDP or virt-manager: **Device Manager → Display adapters → Disable Microsoft Basic / Red Hat QXL** → RDP remains, NVIDIA wakes.

## Legacy launch

```bash
sudo chown new:kvm /dev/shm/looking-glass; sudo chmod 0660 /dev/shm/looking-glass
looking-glass-client -f /dev/shm/looking-glass -m KEY_F6
```
> [!note] Race note on `chown`
> Under `fs.protected_regular=1`, `chown` on existing `libvirt-qemu`-owned file fails → must `rm` + `systemd-tmpfiles --create` (current guide documents this).

## What did that do? (legacy table kept)

| Component | Role | Failure |
|---|---|---|
| `/dev/shm` | RAM disk, zero-copy | 0 bytes → XML size missing |
| `IVSHMEM` | Virtual PCI shared-mem | needs `ivshmem-plain` |
| IDD (VDD) | Ghost monitor | no IDD → GPU sleep / Code 43 |
| RDP | Rescue bridge | display disabled → RDP needed |
| Basic Adapter | Emulated GPU | must be disabled for NVIDIA |

> Current alternative: see [[Looking Glass]] Phase 2 (`<shmem>`). If you still have a `qemu:commandline` VM, run `25_looking_glass.py --domain <name>` to migrate it.
