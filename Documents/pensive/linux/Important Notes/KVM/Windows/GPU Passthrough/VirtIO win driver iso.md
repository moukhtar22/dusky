---
title: "VirtIO Drivers — virtio-win ISO"
tags:
  - kvm
  - windows
  - virtio
  - arch
---

# VirtIO Drivers — `virtio-win.iso`

> [!abstract] Why
> Windows has no virtio drivers → without this, guest sees no `virtio` disk/NIC. We attach the ISO as CD-ROM at install and load `viostor`/`NetKVM`.

## 1. Get the ISO — pick one

### A — AUR (managed updates, recommended)

```bash
paru -S --needed virtio-win
ls -l /var/lib/libvirt/images/virtio-win.iso   # symlink (05_virtio_iso.py)
pacman -Qlq virtio-win | grep '\.iso$'         # canonical location
# alt: /usr/share/virtio/virtio-win.iso (older revisions)
```

### B — Manual (Fedora upstream)

```bash
sudo curl -L https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso \
  -o /var/lib/libvirt/images/virtio-win.iso
# volatile pool like /mnt/zram1 is OK for ephemeral guests, but prefer persistent pool for Windows
ls -lh /var/lib/libvirt/images/virtio-win.iso   # ≥80 MiB (pipeline floor)
```

Mechanism (`05_virtio_iso.py:stage_virtio`): tries AUR → symlink; else symlink-missing path → `paru` + `pacman -Qlq`; else streamed download with `Range` resume, `.part` atomic `os.replace`, size≥80M + sha256, TAB completion for local path.

## 2. How we use it (preview)

1. VM Details → **Add Hardware → Storage → CDROM** → pick `virtio-win.iso`
2. Attach as `SATA CDROM` (see [[Mount the VirtIO-Win ISO Image]])
3. During Windows Setup → **Load driver → Browse → `E:\viostor\w11\amd64` → *Red Hat VirtIO SCSI controller***
4. Same for `NetKVM` if you need LAN during OOBE

> [!tip] Next
> [[+ MOC Windows Installation Through Virt Manager]] (wizard) or `30_kvm_vm_deploy.py` (`--disk …device=cdrom,bus=sata,readonly=on`).

Verify after install: `Device Manager → Storage controllers` shows **Red Hat VirtIO SCSI pass-through controller** (not “Standard SATA AHCI”).
