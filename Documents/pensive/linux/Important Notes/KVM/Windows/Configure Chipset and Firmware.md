---
title: "Chipset & Firmware (Q35 + UEFI) — Windows Delta"
tags:
  - kvm
  - windows
  - q35
  - uefi
  - ovmf
aliases:
  - Windows Chipset Stub
---

# Chipset & Firmware — Q35 + UEFI (Windows)

> [!tip] Merged — canonical source
> **Shared Q35/OVMF steps now live in [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]].** Follow that note for the full wizard (Overview → Q35 → UEFI `OVMF_CODE.secboot.4m.fd`, `smm`, `loader`/`nvram` XML). This stub keeps **only Windows-specific delta** so `[[wikilink]]` history stays stable.

## Windows delta vs canonical

| Canonical | Windows requirement (Aug 2026) |
|---|---|
| `Q35` chipset | **required** (same as Linux) — PCIe, BAR resizing; `pc-q35-11.1` on QEMU 11.1 |
| `UEFI x86_64` `OVMF_CODE.secboot.4m.fd` | **required** — Win11 osinfo enforces `firmware="efi"` |
| `smm state="on"` | **must be `on`** — TPM + Secure Boot need SMM (`30_*:build_command` adds it) |
| Secure Boot `secure="yes"` | **on** — no need to disable; OVMF signed blob works |
| TPM | **mandatory for `win11`** → [[Enable Trusted Platform Module (TPM)]] (`swtpm` `tpm-crb` 2.0 + `smm`) else installer blocks “This PC can't run Windows 11” |

```xml
<!-- Windows 11 baseline (canonical + delta) -->
<os firmware="efi">
  <type arch="x86_64" machine="q35">hvm</type>
  <firmware><feature enabled="no" name="enrolled-keys"/><feature enabled="yes" name="secure-boot"/></firmware>
  <loader readonly="yes" secure="yes" type="pflash" format="raw">/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd</loader>
  <nvram template="/usr/share/edk2/x64/OVMF_VARS.4m.fd" templateFormat="raw" format="raw">/var/lib/libvirt/qemu/nvram/win11_VARS.fd</nvram>
</os>
<features><smm state="on"/></features>
<tpm model="tpm-crb"><backend type="emulator" version="2.0"/></tpm>
```

> [!tip] Windows 11 gate
> Without **UEFI + `smm=on` + `tpm-crb 2.0`** the installer blocks. [[Enable Trusted Platform Module (TPM)]] shows virt-manager **Add Hardware → TPM → Emulated / CRB / 2.0** or `virt-install --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb`. Patched LTSC images may skip TPM (use `win10` osinfo) but keep `swtpm` installed.

> [!warning] Snapshot limitation (shared)
> With `firmware="efi"` (pflash NVRAM) **internal** snapshots while running are not supported (`snapshot-create` fails). **Shut off** first, then `virsh snapshot-create-as`. Same for Linux.

## Next

- Canonical: [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]]
- Windows: [[Enable Trusted Platform Module (TPM)]] → [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]] or [[Configure the Storage]] (stub → canonical)
- Linux: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] (no TPM)

See: [[KVM Setup/VM Creation/01 Wizard — Create VM]], [[+ MOC Windows Installation Through Virt Manager]].
