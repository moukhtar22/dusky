# Repairing a BitLocker-Unlocked NTFS Volume That Will Not Mount

> [!summary]
> BitLocker unlock succeeded but mounting `/dev/mapper/bitlk-*` fails with `$MFTMirr does not match $MFT`, `volume is dirty`, `Failed to load $MFT`, or `Input/output error`?
> Then **decryption worked** — the fault is in the **NTFS filesystem or the hardware path**. Linux `ntfsfix` is triage only; authoritative repair is Windows `chkdsk /f`.
>
> Related: [[Bitlocker]] · [[Storage Stack]] · [[BTRFS]] · [[Disk]]

---

## Representative Errors

```text
Error mounting /dev/dm-0: GDBus.Error:org.freedesktop.UDisks2.Error.Failed:
wrong fs type, bad option, bad superblock on /dev/mapper/bitlk-..., ...
```

```text
$MFTMirr does not match $MFT (record 3).
Failed to mount '/dev/mapper/bitlk-...': Input/output error
```

```text
ntfs3(dm-0): It is recommened to use chkdsk.
ntfs3(dm-0): volume is dirty and "force" flag is not set!
```

## What They Mean

- **Mapper exists → BitLocker layer is fine.** Failure happens at the NTFS layer.
- **`$MFTMirr does not match $MFT`** — MFT and its mirror disagree. Causes: unsafe unplug, unflushed write cache, Fast Startup/hibernation, flaky USB bridge/cable, interrupted operations, real media damage.
- **`volume is dirty`** — dirty bit set / journal unclean. Combined with MFT errors, treat as *real corruption until proven otherwise*.
- **`wrong fs type, bad option, bad superblock`** — generic `mount(8)` boilerplate, not a diagnosis.
- **The `SoftRAID/FakeRAID` hint** in NTFS errors is boilerplate too — ignore unless true.
- First `ntfs3:` lines about ACLs/compression are informational.

## Corrections to Common Advice

| Myth | Reality |
|---|---|
| "`dm-0` is the device" | Transient name; use `/dev/mapper/bitlk-*` or your own mapper name |
| "Run `ntfsfix`" | Fixes a tiny subset, resets some state, flags volume for Windows. Never full repair |
| "`ntfsfix --clear-dirty` clears bad blocks" | Clears the dirty flag only; repairs nothing |
| "Reboot Windows twice" | Legacy folklore; one successful `chkdsk X: /f` on a data volume is enough. Do ensure a **full shutdown** if hibernation/Fast Startup is involved |
| "TestDisk: select Intel, …" on `bitlk-*` | A decrypted mapper is a single volume, no partition table — generic TestDisk guides mislead. Forensics belong on a clone |

## Differential Diagnosis

| Symptom after unlock | Likely cause | Response |
|---|---|---|
| "Windows is hibernated" refusal | Hibernation / Fast Startup | Boot Windows, disable it, full shutdown, retry |
| `$MFTMirr`/`Failed to load $MFT`/I/O errors | NTFS metadata corruption | Copy data read-only first; Windows `chkdsk /f` |
| Only `volume is dirty` | Unclean journal, possibly shallow | Prefer `chkdsk /f`; read-only mount may rescue data |
| `Buffer I/O error`, USB resets, SMART failures | Hardware path | Stop repairing; image first |
| `unknown filesystem` | Wrong device targeted | Verify you are on the unlocked mapper |

---

## Safe Triage on Arch

### 1. Target the Right Device

```bash
lsblk -o NAME,PATH,TYPE,FSTYPE,FSVER,SIZE,LABEL,UUID,MOUNTPOINTS
```

`/dev/sdXN` = locked BitLocker partition · `/dev/mapper/<name>` = decrypted NTFS.

> [!warning]
> Never point repair tools at the locked outer partition — only at the unlocked mapper.

### 2. Unlock Read-Only, Deterministically

```bash
sudo cryptsetup open --type bitlk --readonly /dev/sdXN bitlk_slow   # → /dev/mapper/bitlk_slow
```

Desktop alternative: `udisksctl unlock -b /dev/sdXN` (creates `/dev/mapper/bitlk-<uuid>`).

### 3. Confirm + Check Logs Before Forcing Anything

```bash
lsblk -f /dev/mapper/bitlk_slow          # expect TYPE ntfs
journalctl -k -b -g 'ntfs|bitlk|dm-|udisks'
dmesg --level=warn,err                   # dirty flag, $MFT, I/O errors, USB resets
```

### 4. Try a Read-Only Mount

```bash
sudo install -d -m 0755 /mnt/slow
sudo mount -t ntfs3 -o ro /dev/mapper/bitlk_slow /mnt/slow
```

Mounts? Copy data off immediately, then unmount. Still fails with MFT/I/O errors → stop write-capable attempts on Linux, go to Windows.

> [!warning]
> Avoid `-o force` on the original volume; force-writing damaged NTFS can deepen corruption.

### Script Form

```bash
#!/usr/bin/env bash
set -euo pipefail

src='/dev/disk/by-id/...'   # stable path, never /dev/sdXN in production
name='slow'
mnt='/mnt/slow'

sudo cryptsetup open --type bitlk --readonly -- "$src" "$name"
sudo install -d -m 0755 -- "$mnt"
sudo mount -t ntfs3 -o ro -- "/dev/mapper/$name" "$mnt"
# ... rescue data ...
sudo umount -- "$mnt"
sudo cryptsetup close -- "$name"
```

---

## Linux-Side Tools: Honest Limits

```bash
sudo pacman -S --needed ntfs-3g smartmontools
```

`ntfs3` mounts; `ntfs-3g` provides `ntfsfix`. Switching drivers repairs nothing.

`ntfsfix` flags:

```bash
sudo ntfsfix -n /dev/mapper/bitlk_slow   # -n = dry run: show what it would do first
sudo ntfsfix -d /dev/mapper/bitlk_slow   # clear the dirty flag (hibernation/Fast Startup leftovers)
sudo ntfsfix -b /dev/mapper/bitlk_slow   # clear the bad-sector list (forces re-check)
```

It can clear simple inconsistencies, reset journal state in some cases, and schedule a Windows check. It cannot rebuild MFT metadata, fix media, or replace `chkdsk`. Output like this means stop:

```text
Failed to load $MFT: Input/output error
Unrecoverable error
Volume is corrupt. You should run chkdsk.
```

> [!note]
> `fsck.ntfs` on Linux is just `ntfsfix`, not a checker.

## Authoritative Repair: Windows `chkdsk`

On a normal Windows install:

1. Attach drive, unlock the BitLocker volume
2. Admin Command Prompt: `chkdsk X: /f`
3. `/r` adds surface scanning — only if physical reads are suspect (much slower)

> [!warning]
> Real I/O errors or SMART failures? Image before any `chkdsk`. `chkdsk` is not data recovery.

Rebooting afterwards: scheduled offline repair → reboot once; hibernation involved → full shutdown before returning to Linux.

### From Windows Install Media / WinRE

```text
Repair your computer -> Troubleshoot -> Command Prompt
```

```text
diskpart
list volume
select volume <N>
assign letter=Z
exit
```

```bat
manage-bde -status
manage-bde -unlock Z: -RecoveryPassword 111111-222222-333333-444444-555555-666666-777777-888888
chkdsk Z: /f
```

(`manage-bde -unlock Z: -Password` prompts instead.)

---

## If Hardware Is Failing, Image First

Warning signs: repeated `Input/output error`, dmesg USB resets/disconnects, reallocated/pending sectors, drive disappearing intermittently.

Check SMART (`-d sat` for many USB bridges):

```bash
sudo smartctl -d sat -a /dev/sdX
```

Image the **outer encrypted partition** (preserves evidence, avoids repeat reads):

```bash
sudo pacman -S --needed gddrescue
sudo ddrescue -f -n /dev/sdXN bitlocker-partition.img bitlocker-partition.map
```

All further experiments happen on the image.

---

## After Repair: Mount Cleanly

```bash
sudo cryptsetup open --type bitlk /dev/sdXN bitlk_slow
sudo install -d -m 0755 /mnt/slow
sudo mount -t ntfs3 -o uid=1000,gid=1000,windows_names /dev/mapper/bitlk_slow /mnt/slow

sudo umount /mnt/slow
sudo cryptsetup close bitlk_slow
```

UDisks flow: `unlock` → `mount -b /dev/mapper/bitlk-<uuid>` → `unmount` → `lock -b /dev/sdXN` → optional `power-off`.

## Automation Guidance

> [!warning]
> Never put `/dev/dm-N` in `/etc/fstab`.

Stable design: `/etc/crypttab` entry for the BitLocker volume → `/etc/fstab` entry for the inner NTFS (by inner UUID once unlocked) → zero hard-coded `dm-*` names. The limitation is not "BitLocker integrity" — unlocked volumes are ordinary NTFS; the gap is that Linux has no `chkdsk` equivalent.

## Last-Resort Recovery

TestDisk / DMDE / R-Studio / professional services — when `chkdsk` cannot repair, MFT is shredded, or recovery outranks mountability. Run them against a **clone**, never the original.

## Prevention

- Always unmount before unplugging; close/lock the mapper too
- `udisksctl power-off` USB drives after locking
- No writable sharing with Windows installs using Fast Startup/hibernation
- Replace flaky cables/hubs/bridges; monitor SMART
- Pure shuttle disk without NTFS needs? Consider exFAT — but check kernel support first (`zgrep CONFIG_EXFAT_FS /proc/config.gz`; the custom `dusky-*` kernels here lack it). exFAT is still not immune to unsafe removal.

---

## Bottom Line

1. Unlock success ⇒ BitLocker is fine
2. Mount failure on `mapper` ⇒ NTFS or hardware
3. Read-only first when data matters
4. `ntfsfix` = triage only
5. Windows `chkdsk /f` = real repair
6. Unstable hardware ⇒ image first, repair the clone
