# Kernel mitigation boot flags on Arch Linux
## Writing, applying, and verifying x86 CPU-mitigation parameters with **Limine** or **systemd-boot**
*Updated for current Arch/Linux practice, March 2026*

> [!summary]
> Disabling CPU-side-channel mitigations is **not** a uniform `vulnerability_name=off` exercise.  
> Mainline Linux uses a mix of parameter names such as `mitigations=off`, `pti=off`, `spectre_v2=off`, `nospectre_v1`, `mds=off`, `tsx_async_abort=off`, `mmio_stale_data=off`, `reg_file_data_sampling=off`, and others.  
> On Arch, the correct workflow is:
> 1. Identify the exact vulnerability status in `/sys/devices/system/cpu/vulnerabilities/`
> 2. Look up the documented kernel parameter
> 3. **Append** the flag to the existing boot command line
> 4. Rebuild the entry or UKI if required
> 5. Reboot and verify via `/proc/cmdline`, `/sys/.../vulnerabilities/*`, and the kernel log

---

## Scope

This note is a permanent reference for **Arch Linux on x86_64**, specifically when booting with:

- **systemd-boot**
- **Limine**

It assumes a typical modern Arch setup with:

- **LUKS2**-encrypted root
- **Btrfs** root filesystem
- optional **Btrfs snapshot booting**
- optional **UKI** workflow under systemd-boot

This note is **not** about GRUB.

> [!note]
> Most mitigation parameters discussed here are **x86-specific**. If the system is ARM64, RISC-V, or PowerPC, the available files and parameters differ.

---

## Threat model and safety

> [!warning]
> Disabling CPU vulnerability mitigations materially reduces protection against:
> - local privilege escalation
> - cross-process data leakage
> - cross-VM leakage on virtualized hosts
> - some speculative-execution side channels
>
> Only disable mitigations on systems you control, for trusted-code benchmarking, controlled lab work, or tightly bounded performance testing.

Do **not** disable mitigations on:

- multi-user machines
- virtualization hosts running untrusted guests
- developer workstations that routinely execute untrusted binaries
- systems handling sensitive secrets or production workloads

---

## Core rules

### 1. Sysfs vulnerability names are **reporting names**, not guaranteed command-line names

This shows the kernel’s current vulnerability status:

```bash
grep . /sys/devices/system/cpu/vulnerabilities/*
```

Example filenames may include:

- `spectre_v1`
- `spectre_v2`
- `spec_store_bypass`
- `meltdown`
- `mds`
- `tsx_async_abort`
- `mmio_stale_data`
- `reg_file_data_sampling`
- `gather_data_sampling`
- `retbleed`
- `spec_rstack_overflow`
- `srbds`

The **filename** is useful, but the boot parameter may be different. For example:

- `meltdown` status is typically controlled by `pti=off` or `nopti`
- `spectre_v1` does **not** use `spectre_v1=off`; the disable form is `nospectre_v1`

---

### 2. Kernel mitigation parameters are **not** a uniform namespace

Examples of real patterns in mainline Linux:

- `mitigations=off`
- `pti=off`
- `nospectre_v1`
- `spectre_v2=off`
- `spec_store_bypass_disable=off`
- `mds=off`
- `tsx_async_abort=off`
- `mmio_stale_data=off`
- `reg_file_data_sampling=off`

Do **not** assume `foo=off` exists just because `/sys/.../foo` exists.

---

### 3. Prefer canonical parameter forms over aliases

Aliases exist, but explicit forms are clearer and more reproducible.

Examples:

- Prefer `pti=off` over `nopti`
- Prefer `spectre_v2=off` over `nospectre_v2`
- Prefer `spec_store_bypass_disable=off` over `nospec_store_bypass_disable`

Exception:

- `nospectre_v1` is the normal public disable form for Spectre v1 on x86

---

### 4. Use the documented value token

Kernel parameters are usually string-valued, not boolean in the shell sense.

Use:

```text
spectre_v2=off
```

Do **not** guess:

```text
spectre_v2=0
```

Common value tokens seen in mitigation controls include:

- `off`
- `auto`
- `on`
- `force`
- `prctl`
- `full`
- `full,nosmt`
- `auto,nosmt`

Not every parameter supports every token.

---

### 5. Unknown parameters do **not** help, and the kernel often logs them

If a parameter is unrecognized by your kernel:

- it will not control the mitigation
- the kernel usually logs it during early boot
- some unknown tokens may be forwarded to userspace/init depending on syntax, but they still do not affect mitigation state

Always verify with the running kernel log after reboot.

---

## Reliable workflow

### 1. Inspect current mitigation state

```bash
grep . /sys/devices/system/cpu/vulnerabilities/*
```

### 2. Check the active kernel command line

```bash
cat /proc/cmdline
```

### 3. Look up the exact parameter in kernel documentation

Primary references:

- `Documentation/admin-guide/hw-vuln/`
- `Documentation/admin-guide/kernel-parameters.rst`
- online: `docs.kernel.org/admin-guide/hw-vuln/`
- online: `docs.kernel.org/admin-guide/kernel-parameters.html`

### 4. Append the desired parameter to the existing working command line

Do **not** replace the entire line.

### 5. Rebuild the boot artifact if your workflow requires it

- **systemd-boot Type #1 entry**: edit text entry only
- **systemd-boot UKI**: edit embedded cmdline source, then rebuild the UKI
- **Limine**: edit `CMDLINE=` in the active `limine.conf`; no bootloader reinstall is required just for cmdline changes

### 6. Reboot and verify

Use:

```bash
cat /proc/cmdline
grep . /sys/devices/system/cpu/vulnerabilities/*
sudo journalctl -b -k
```

---

## Common x86 mitigation parameters
### Non-exhaustive, but current and commonly relevant

> [!note]
> Availability depends on kernel version, CPU vendor/family, microcode level, and architecture.  
> If the CPU is not affected, the corresponding sysfs file usually reports `Not affected`, and the parameter will have no meaningful effect.

| Sysfs vulnerability file | Common boot parameter | Notes |
|---|---|---|
| `meltdown` | `pti=off` | Alias: `nopti` |
| `spectre_v1` | `nospectre_v1` | No generic `spectre_v1=off` interface |
| `spectre_v2` | `spectre_v2=off` | Alias commonly exists: `nospectre_v2` |
| `spectre_bhi` | `spectre_bhi=off` | Branch History Injection control on affected systems |
| `spec_store_bypass` | `spec_store_bypass_disable=off` | Alias commonly exists: `nospec_store_bypass_disable` |
| `l1tf` | `l1tf=off` | Relevant mainly on affected Intel systems |
| `mds` | `mds=off` | Often interacts with shared buffer-clearing mitigations |
| `tsx_async_abort` | `tsx_async_abort=off` | TAA; related to TSX and MDS behavior |
| `mmio_stale_data` | `mmio_stale_data=off` | Shares mitigation mechanisms with some Intel buffer issues |
| `reg_file_data_sampling` | `reg_file_data_sampling=off` | RFDS |
| `gather_data_sampling` | `gather_data_sampling=off` | GDS / Downfall |
| `retbleed` | `retbleed=off` | On kernels/CPUs exposing it |
| `spec_rstack_overflow` | `spec_rstack_overflow=off` | AMD SRSO control on affected systems |
| `srbds` | `srbds=off` | Special Register Buffer Data Sampling / CrossTalk |

### Global master switch

```text
mitigations=off
```

This is the broad, blunt option.

Use it when the goal is:

- quick lab testing
- broad “remove mitigation overhead” benchmarking
- validating the performance delta between hardened and minimally mitigated states

Use per-vulnerability flags instead when you need:

- finer control
- clearer auditability
- reproducibility across multiple test systems

> [!important]
> `mitigations=off` disables **most optional CPU vulnerability mitigations**, but it is **not** a promise to remove every hardening layer in the system. It does not undo:
> - compiler-generated hardening in already-built userspace binaries
> - all non-CPU security features
> - firmware behavior
> - microcode changes that expose or alter architectural behavior

---

## Parameter patterns and examples

### Minimal targeted example

```text
spectre_v2=off spec_store_bypass_disable=off reg_file_data_sampling=off
```

### Broader explicit example

```text
pti=off nospectre_v1 spectre_v2=off spec_store_bypass_disable=off mds=off tsx_async_abort=off mmio_stale_data=off l1tf=off reg_file_data_sampling=off gather_data_sampling=off retbleed=off spec_rstack_overflow=off srbds=off
```

### Broad master-switch example

```text
mitigations=off
```

> [!warning]
> Do **not** combine a large set of guessed flags copied from random forum posts. Use only parameters documented for your kernel and CPU family.

---

## Arch-specific bootloader application
## Preserve LUKS2 and Btrfs boot arguments

> [!warning]
> On an encrypted Btrfs root, the mitigation flags must be **appended** to the existing working command line.
>
> If you accidentally remove or damage:
> - `rd.luks.*` or `cryptdevice=...`
> - `root=...`
> - `rootfstype=btrfs`
> - `rootflags=subvol=...` or `rootflags=subvolid=...`
> - `resume=...` if you use hibernation
>
> the system may fail to unlock or mount the correct root subvolume.

Typical existing Arch command-line patterns include one of the following:

### systemd initramfs / `sd-encrypt` / dracut style

```text
rd.luks.name=<LUKS_UUID>=cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw
```

### mkinitcpio `encrypt` hook style

```text
cryptdevice=UUID=<LUKS_UUID>:cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw
```

Append mitigation flags to the **end**:

```text
rd.luks.name=<LUKS_UUID>=cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw spectre_v2=off spec_store_bypass_disable=off
```

---

## systemd-boot

### Type #1 entry files (`loader/entries/*.conf`)

These are text entries such as:

- `/boot/loader/entries/*.conf`
- `/efi/loader/entries/*.conf`

depending on how the ESP/XBOOTLDR is mounted on your system.

A typical entry:

```ini
title   Arch Linux
linux   /vmlinuz-linux
initrd  /intel-ucode.img
initrd  /initramfs-linux.img
options rd.luks.name=<LUKS_UUID>=cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw spectre_v2=off spec_store_bypass_disable=off
```

Notes:

- Keep the microcode image first if you use one.
- Use `amd-ucode.img` on AMD systems.
- Editing the text entry is enough; no `bootctl install` is required for a mere cmdline change.

### Safe workflow: create a separate benchmark entry

Instead of modifying your default entry in place, duplicate it:

- `arch-linux.conf` → `arch-linux-bench.conf`

Then add mitigation flags only to the benchmark entry.

This preserves a clean rollback path from the boot menu.

---

### Type #2 entries / UKIs (`EFI/Linux/*.efi`)

If you boot a **Unified Kernel Image**, the command line is typically **embedded** in the UKI.

In that case:

- editing `loader/entries/*.conf` is irrelevant or insufficient
- you must edit the cmdline source used to build the UKI
- then regenerate the UKI

On Arch, the common cmdline source is:

```text
/etc/kernel/cmdline
```

Example:

```text
rd.luks.name=<LUKS_UUID>=cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw spectre_v2=off spec_store_bypass_disable=off
```

Then rebuild using the method your system actually uses.

#### Common Arch UKI rebuild patterns

If your mkinitcpio presets generate UKIs:

```bash
sudoedit /etc/kernel/cmdline
sudo mkinitcpio -P
```

If your system uses `kernel-install`/`ukify` style UKI generation:

```bash
sudoedit /etc/kernel/cmdline
sudo kernel-install add "$(uname -r)" "/usr/lib/modules/$(uname -r)/vmlinuz"
```

> [!important]
> Use the workflow your machine already uses. Do **not** run random rebuild commands blindly on a production boot setup.

> [!note]
> If Secure Boot is enabled and you sign UKIs manually or via a tool such as `sbctl`, re-sign the rebuilt UKI before rebooting.

---

## Limine

Edit the active `limine.conf` and append the mitigation parameters to the existing `CMDLINE=` value.

Common installation layouts place `limine.conf` on the boot volume, often at one of:

- `/boot/limine.conf`
- `/efi/limine.conf`
- `/boot/EFI/limine/limine.conf`
- `/efi/EFI/limine/limine.conf`

The exact path depends on how Limine was installed.

A typical Linux entry:

```ini
:Arch Linux (bench)
    PROTOCOL=linux
    KERNEL_PATH=boot():/vmlinuz-linux
    MODULE_PATH=boot():/initramfs-linux.img
    CMDLINE=rd.luks.name=<LUKS_UUID>=cryptroot root=/dev/mapper/cryptroot rootfstype=btrfs rootflags=subvol=@ rw spectre_v2=off spec_store_bypass_disable=off
```

Notes:

- Preserve the existing, working `CMDLINE=` arguments.
- If your working entry already loads microcode or multiple initrds/modules, keep that ordering unchanged.
- Changing only `CMDLINE=` does **not** require reinstalling Limine.

### Recommended Limine practice

Create a second menu entry, for example:

- `:Arch Linux`
- `:Arch Linux (bench / mitigations off)`

This gives you a clean fallback without touching the default boot path.

---

## Btrfs snapshot considerations

> [!warning]
> Btrfs snapshots do **not** automatically protect your bootloader configuration if `/boot` or `/efi` is on a separate ESP/XBOOTLDR VFAT partition, which is the common Arch setup.

Important consequences:

1. Rolling back the root subvolume does **not** necessarily roll back:
   - `loader/entries/*.conf`
   - `limine.conf`
   - UKIs stored under `EFI/Linux/`
   - microcode images and initramfs images on the ESP/XBOOTLDR

2. Snapshot boot entries may use different:
   - `rootflags=subvol=...`
   - `rootflags=subvolid=...`

3. If you boot snapshots directly, each snapshot-specific entry may need its own cmdline maintenance.

### Recommended snapshot-safe practice

- Keep a **known-good fallback entry** with no mitigation-disabling flags.
- Back up the active bootloader config before editing it.
- If using UKIs, keep at least one older known-good UKI available.
- Verify `/boot` and `/efi` are actually mounted before editing files.

Check mountpoints first:

```bash
findmnt / /boot /efi
```

> [!important]
> A very common failure mode on Arch is editing `/boot/...` while `/boot` is **not mounted**, which writes files into the root filesystem mountpoint directory instead of the actual ESP/XBOOTLDR.

---

## Verification after reboot

### 1. Confirm the running kernel got the flags

```bash
cat /proc/cmdline
```

### 2. Check the vulnerability status files

```bash
grep . /sys/devices/system/cpu/vulnerabilities/*
```

### 3. Check the kernel log for parameter parsing and mitigation messages

Preferred:

```bash
sudo journalctl -b -k | grep -Ei 'unknown kernel command line|mitigat|microcode|IBRS|IBPB|VERW|RFDS|GDS|BHI|SRBDS'
```

Alternative:

```bash
dmesg | grep -Ei 'unknown kernel command line|mitigat|microcode|IBRS|IBPB|VERW|RFDS|GDS|BHI|SRBDS'
```

### 4. Record benchmark metadata

For reproducible performance work, record at minimum:

```bash
uname -r
cat /proc/cmdline
grep . /sys/devices/system/cpu/vulnerabilities/*
sudo journalctl -b -k | grep -Ei 'microcode|Command line'
```

> [!note]
> Early microcode updates materially affect mitigation availability and performance. Treat the microcode version as part of the benchmark environment.

---

## Minimal audit script

```bash
#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

printf 'kernel   : %s\n' "$(uname -r)"
printf 'cmdline  : %s\n' "$(</proc/cmdline)"
printf '\nvulnerabilities:\n'

for f in /sys/devices/system/cpu/vulnerabilities/*; do
  printf '  %-28s %s\n' "${f##*/}" "$(<"$f")"
done
```

Run it after reboot to capture the effective state.

---

## Troubleshooting

### The mitigation still shows as active

Common reasons:

#### 1. Wrong parameter name
You guessed the parameter from the sysfs filename instead of using documented kernel syntax.

Example:

- wrong: `spectre_v1=off`
- correct: `nospectre_v1`

---

#### 2. You edited the wrong boot source

Typical examples:

- edited a systemd-boot text entry, but the machine actually boots a **UKI**
- edited `/boot/...` while `/boot` was not mounted
- edited the wrong `limine.conf`
- modified a non-default menu entry, but booted a different one

Use:

```bash
bootctl status
cat /proc/cmdline
findmnt /boot /efi
```

---

#### 3. UKI not rebuilt

If the cmdline is embedded in the UKI, editing `/etc/kernel/cmdline` changes nothing until the UKI is regenerated.

---

#### 4. Shared mitigation mechanisms

Some Intel vulnerabilities share mitigation plumbing, especially buffer-clearing paths.

Examples:

- `mds`
- `tsx_async_abort`
- `mmio_stale_data`
- `reg_file_data_sampling`

Disabling only one knob may not produce the status string you expected if related mitigations remain enabled.

---

#### 5. Microcode changed the mitigation landscape

A microcode update may:

- expose new mitigation capabilities
- alter default mitigation mode
- change performance characteristics
- add or remove vulnerability files or status wording

Always record microcode state when comparing results.

---

#### 6. The CPU is `Not affected`

If the kernel reports `Not affected`, toggling the corresponding parameter is usually meaningless.

---

#### 7. Some hardening is not globally toggleable

Not every mitigation or hardening measure is a single boot switch. Some protections are:

- compiled into kernel code paths
- dependent on CPU feature state
- only partially controllable
- scoped differently from the sysfs reporting entry

---

## Rollback procedure

### Preferred rollback strategy

1. Keep a separate **safe** boot entry:
   - systemd-boot: duplicate the entry without mitigation-disabling flags
   - Limine: duplicate the stanza without mitigation-disabling flags

2. If the benchmark entry misbehaves:
   - reboot
   - choose the safe entry
   - remove or correct the flags
   - rebuild the UKI if applicable

### Back up boot configuration before changes

#### systemd-boot text entry example

```bash
ts=$(printf '%(%F-%H%M%S)T' -1)
src=/boot/loader/entries/arch-linux.conf
sudo install -Dm0644 -- "$src" "${src}.bak.${ts}"
```

#### Limine config example

```bash
ts=$(printf '%(%F-%H%M%S)T' -1)
src=/boot/limine.conf
sudo install -Dm0644 -- "$src" "${src}.bak.${ts}"
```

Adjust paths for your actual layout.

> [!important]
> If your boot assets live on the ESP/XBOOTLDR, a Btrfs rollback of `/` will not restore them. Back up the boot partition configuration explicitly.

---

## Practical recommendations

### For quick “all mitigations off” lab testing

Use:

```text
mitigations=off
```

Then verify with:

```bash
cat /proc/cmdline
grep . /sys/devices/system/cpu/vulnerabilities/*
```

---

### For repeatable, explicit benchmarking

Prefer explicit per-mitigation flags such as:

```text
pti=off nospectre_v1 spectre_v2=off spec_store_bypass_disable=off mds=off tsx_async_abort=off mmio_stale_data=off reg_file_data_sampling=off gather_data_sampling=off retbleed=off spec_rstack_overflow=off srbds=off
```

This makes the test state easier to audit later.

---

### For multi-machine administration

Record:

- kernel version
- microcode version
- exact kernel command line
- vulnerability status files
- bootloader type
- whether the system uses text entries or UKIs
- root unlock method (`rd.luks.*` vs `cryptdevice=`)

---

## Reference locations

### Kernel documentation

- `Documentation/admin-guide/hw-vuln/`
- `Documentation/admin-guide/kernel-parameters.rst`

### Runtime state

- `/proc/cmdline`
- `/sys/devices/system/cpu/vulnerabilities/*`

### Bootloader-related paths

#### systemd-boot
- `/boot/loader/entries/*.conf`
- `/efi/loader/entries/*.conf`
- `/etc/kernel/cmdline`
- `/EFI/Linux/*.efi`

#### Limine
- active `limine.conf` on the boot volume

---

## Bottom line

- Do **not** assume every vulnerability maps to `name=off`
- Use the **documented** parameter for the running kernel
- On Arch with **LUKS2 + Btrfs**, **append** flags to the existing root-unlock and root-mount arguments
- With **systemd-boot UKIs**, change `/etc/kernel/cmdline` and rebuild the UKI
- With **Limine**, edit the active `CMDLINE=` and keep a fallback entry
- Verify with `/proc/cmdline`, `/sys/devices/system/cpu/vulnerabilities/*`, and the kernel log before trusting benchmark results
