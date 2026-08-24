---
title: "Mount virtio-win ISO"
tags:
  - kvm
  - windows
  - virtio
  - arch
---

# Mount virtio-win ISO

> [!abstract] Why
> Windows has no virtio drivers built-in. Without `virtio-win` the guest cannot see `virtio` disks/NICs at install. We attach the ISO as a second CD-ROM; Windows loads drivers from it.

## Prereq (already staged by `05_virtio_iso.py`)

```bash
pacman -Qlq virtio-win | grep '\.iso$'
ls -l /var/lib/libvirt/images/virtio-win.iso   # symlink → AUR file, or standalone
# fallback AUR helper:
paru -S --needed virtio-win
# manual:
# sudo curl -L https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso -o /var/lib/libvirt/images/virtio-win.iso
```

## Virt-manager steps

1. VM Details (lightbulb) → **Add Hardware** (bottom-left)
2. **Storage** → **Device type:** `CDROM device`
3. **Manage…** → select `virtio-win.iso` (under pool that covers target, e.g. `arsonix-…` or `default`) → **Choose Volume**
4. **Finish** → **Apply**

You now have:
- `SATA CDROM 1` → Windows ISO
- `SATA CDROM 2` → `virtio-win.iso`

> [!tip] Can't find ISO?
> - Check `virsh pool-list --all --details` — your pool path is `07_storage_setup.py`-chosen. `virt-manager` only lists volumes under declared pools (`pool-define-as`).
> - AUR alt location: `/usr/share/virtio/virtio-win.iso` (older `virtio-win` revisions).
> - CLI add: `virsh attach-disk win11 /var/lib/libvirt/images/virtio-win.iso sdc --type cdrom --mode readonly --config`

After install, leave the virtio CD attached or keep the file in pool; guest tools can be reinstalled without re-mount. Next: [[Install a Windows Virtual Machine on KVM]].

See: `05_virtio_iso.py:stage_virtio` (symlink vs download, 80 MiB floor, Range-resume, sha256).
