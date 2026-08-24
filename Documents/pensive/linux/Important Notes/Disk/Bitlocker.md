# Accessing a BitLocker Drive on Arch Linux

> [!note] Scope
> Unlocking and mounting Windows **BitLocker** (BITLK) volumes. Updated **August 2026**, verified against `cryptsetup 2.8` + `udisks2 2.11` (both ship full bitlk support).
>
> Related: [[Fixing Un Mountable NTFS drive]] · [[Storage Stack]] · [[Disk]] · [[BTRFS]]

---

## 1. Identify the Encrypted Partition

```bash
lsblk -f          # a locked volume may show FSTYPE "BitLocker"
sudo blkid        # TYPE="BitLocker"
```

Target the **partition** (`/dev/sdXn`), not the whole disk.

## 2. Unlock

```bash
sudo cryptsetup open --type bitlk /dev/sdXn bitlk_device
```

Prompts for the BitLocker password or recovery key. The decrypted device appears at `/dev/mapper/bitlk_device`. Inspect the header without unlocking via `sudo cryptsetup bitlkDump /dev/sdXn`.

Desktop alternative — UDisks handles it through Polkit:

```bash
udisksctl unlock -b /dev/sdXN      # creates /dev/mapper/bitlk-<uuid>
```

> [!note]
> Never rely on `/dev/dm-0`-style names — they are transient. With UDisks use the stable `/dev/mapper/bitlk-*` symlink; with cryptsetup you chose the name yourself.

## 3. Confirm the Inner Filesystem

BitLocker usually wraps NTFS but not always:

```bash
lsblk -f /dev/mapper/bitlk_device
```

## 4. Mount

```bash
sudo mkdir -p /mnt/bitlk
sudo mount /dev/mapper/bitlk_device /mnt/bitlk
```

If both NTFS drivers are installed, pin the kernel one explicitly with `-t ntfs3` ([[BTRFS#NTFS Volumes]]).

If Windows did not shut down cleanly, Linux refuses a writable mount. Either fully shut down Windows or go read-only:

```bash
sudo mount -o ro /dev/mapper/bitlk_device /mnt/bitlk
```

For diagnosis/rescue prefer opening read-only from the start:

```bash
sudo cryptsetup open --type bitlk --readonly /dev/sdXn bitlk_ro
```

## 5. Clean Up

```bash
sudo umount /mnt/bitlk
sudo cryptsetup close bitlk_device
```

UDisks variant: `udisksctl unmount -b /dev/mapper/bitlk-<uuid>` then `udisksctl lock -b /dev/sdXN`. For USB drives optionally `udisksctl power-off -b /dev/sdX` afterwards.

---

## Quick Reference

| Action | Command |
|---|---|
| Identify | `lsblk -f` |
| Unlock | `sudo cryptsetup open --type bitlk /dev/sdXn bitlk_device` |
| Header info | `sudo cryptsetup bitlkDump /dev/sdXn` |
| Read-only unlock | `sudo cryptsetup open --type bitlk --readonly /dev/sdXn name` |
| Mount | `sudo mount /dev/mapper/name /mnt/bitlk` |
| Unmount | `sudo umount /mnt/bitlk` |
| Lock | `sudo cryptsetup close name` |

Missing tools: `sudo pacman -S --needed cryptsetup udisks2 ntfs-3g`

---

## Caveats

> [!warning]
> cryptsetup can **read** BitLocker, never create or modify it — the BITLK header is untouched on-device. TPM-only or SmartCard-protected volumes cannot be unlocked from Linux; recovery key or password required.
>
> If mounting fails after a successful unlock, the problem is the inner NTFS, not BitLocker → [[Fixing Un Mountable NTFS drive]].
