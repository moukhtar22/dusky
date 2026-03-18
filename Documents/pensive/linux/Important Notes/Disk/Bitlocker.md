## Main corrections

### 1. Use `lsblk -f`, not plain `lsblk`
A locked BitLocker partition often does **not** appear as `ntfs` until after unlocking. It may show up as `BitLocker` or without a normal filesystem type.

Use:

```bash
lsblk -f
```

or:

```bash
sudo blkid
```

### 2. Prefer `cryptsetup open --type bitlk`
This is the more standard form and works even where the `bitlkOpen` alias may not.

```bash
sudo cryptsetup open --type bitlk /dev/sdXn bitlk_device
```

### 3. The unlocked filesystem may not always be NTFS
It often is, but BitLocker volumes can also contain other filesystems. After unlocking, you can check:

```bash
lsblk -f /dev/mapper/bitlk_device
```

### 4. Windows hibernation / Fast Startup can block read-write mounts
If the volume was not cleanly shut down in Windows, Linux may refuse a writable mount.

If that happens:
- fully shut down Windows, or
- mount read-only:

```bash
sudo mount -o ro /dev/mapper/bitlk_device /mnt/bitlk
```

---

## Suggested revised version

### Accessing a BitLocker Drive on Arch Linux

#### 1. Identify the encrypted partition

```bash
lsblk -f
```

Look for the partition by size and filesystem type. A locked BitLocker volume may appear as `BitLocker`.

Assume the target partition is:

```bash
/dev/sdXn
```

#### 2. Unlock the BitLocker volume

```bash
sudo cryptsetup open --type bitlk /dev/sdXn bitlk_device
```

You’ll be prompted for the BitLocker password or recovery key.

After unlocking, the decrypted device will appear as:

```bash
/dev/mapper/bitlk_device
```

#### 3. Create a mount point

```bash
sudo mkdir -p /mnt/bitlk
```

#### 4. Mount the unlocked filesystem

```bash
sudo mount /dev/mapper/bitlk_device /mnt/bitlk
```

If you want to confirm the inner filesystem first:

```bash
lsblk -f /dev/mapper/bitlk_device
```

#### 5. Access your files

Your files will now be available at:

```bash
/mnt/bitlk
```

#### 6. Clean up when done

Unmount the filesystem:

```bash
sudo umount /mnt/bitlk
```

Then close the BitLocker mapping:

```bash
sudo cryptsetup close bitlk_device
```

---

## Optional Arch-specific note

If `cryptsetup` is not installed:

```bash
sudo pacman -S cryptsetup
```

---

## Quick reference

| Action | Command |
|---|---|
| Identify partitions | `lsblk -f` |
| Unlock BitLocker | `sudo cryptsetup open --type bitlk /dev/sdXn bitlk_device` |
| Create mount point | `sudo mkdir -p /mnt/bitlk` |
| Mount unlocked volume | `sudo mount /dev/mapper/bitlk_device /mnt/bitlk` |
| Unmount | `sudo umount /mnt/bitlk` |
| Close mapping | `sudo cryptsetup close bitlk_device` |

