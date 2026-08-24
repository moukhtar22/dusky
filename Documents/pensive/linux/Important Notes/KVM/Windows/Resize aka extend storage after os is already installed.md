---
title: "Resize Windows Disk (qcow2 Expand)"
tags:
  - kvm
  - storage
  - qemu-img
  - windows
---

# Resize Windows Disk (qcow2 Expand)

> [!warning] VM must be **Shutoff**
> Modifying a live `qcow2` → **corruption**. In `virt-manager` ensure **Shutoff**.
> ```bash
> virsh -c qemu:///system list --all | grep win11   # → shut off
> virsh -c qemu:///system domstate win11
> ```

## Part 1 — Host: grow image

```bash
# locate image idempotently
virsh -c qemu:///system domblklist win11
# e.g. /var/lib/libvirt/images/win11.qcow2  (from 07_storage_setup.py pool)

# add 20 GiB (sparse — only metadata grows)
sudo qemu-img resize /var/lib/libvirt/images/win11.qcow2 +20G

# verify
qemu-img info /var/lib/libvirt/images/win11.qcow2 | grep -E 'virtual size|disk size'
```
> Adjust pool path per your `storage_dir` (`/var/lib/arsonix/state.json`).

## Part 2 — Guest: extend C:

1. **Start** win11
2. `Win + X` → **Disk Management**
3. Find `C:` → to its right you now have **Unallocated** (black bar). Right-click `C:` → **Extend Volume…** → Next → Finish. Immediate, no reboot.

> [!bug] Extend Volume grayed — recovery partition blocking
> If a ~500 MiB Recovery partition sits between `C:` and Unallocated, Windows cannot skip it.

### Option A — GParted Live (safer, keeps WinRE)

1. Download **GParted Live ISO** → attach as CDROM in virt-manager → boot guest from CD.
2. Drag **Recovery** to end → drag `C:` to fill → Apply → reboot to Windows → Extend now works.

### Option B — `diskpart` (faster, removes WinRE)

> [!danger] Loses “Reset this PC” until WinRE rebuilt.

Inside **Admin** PowerShell/CMD in Windows VM:

```cmd
diskpart
list disk
select disk 0        &:: your OS disk (usually 0)
list partition       &:: note Recovery (~500 MB) number X
select partition X
delete partition override
exit
```

Back in Disk Management → `C:` → **Extend Volume** now available.

Verify in guest: `Get-PSDrive C | Format-List Used,Free`

See: [[Configure the Storage]] (format/cache note), `30_kvm_vm_deploy.py:provision_disk` (create flags).
