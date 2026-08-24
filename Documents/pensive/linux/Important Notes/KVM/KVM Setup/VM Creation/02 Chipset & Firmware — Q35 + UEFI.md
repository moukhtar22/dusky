---
title: "Chipset & Firmware — Q35 + UEFI (Unified)"
tags:
  - kvm
  - q35
  - uefi
  - ovmf
  - arch
  - libvirt
aliases:
  - Q35 Unified
---

# Chipset & Firmware — Q35 + UEFI (Unified)

> [!abstract] Goal
> Modern PCIe topology + UEFI for **both** Windows and Linux guests. Mirrors `30_kvm_vm_deploy.py` (`--machine q35 --boot uefi` → firmware JSON via `edk2-ovmf`). One note, two deltas.

## Prereq

Wizard landed you in **Show virtual hardware details** (lightbulb) via [[KVM Setup/VM Creation/01 Wizard — Create VM]] ( **Customize configuration before install** ticked).

## 1. Overview tab (virt-manager)

1. Open VM → **Show virtual hardware details** (lightbulb)
2. Left panel → **Overview**

## 2. Chipset → `Q35`

- **Action:** dropdown → **Q35**
- **Why:** Native PCIe (vs `i440FX`'s PCI), required for PCIe passthrough, BAR resizing, `pcie-root-port` topology. Reference XML [[arch GPU ACCELERATION reference xml]] uses `pc-q35-10.1`; QEMU 11.1 exposes `pc-q35-11.1` as well.
- **Applies to:** Windows **and** Linux (identical).

## 3. Firmware → `UEFI x86_64`

- **Action:** dropdown → **UEFI x86_64: /usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd** (libvirt picks via `/usr/share/qemu/firmware/*.json`, no hard-coded `fd`).

```xml
<os firmware="efi">
  <type arch="x86_64" machine="q35">hvm</type>
  <firmware>
    <feature enabled="no" name="enrolled-keys"/>
    <feature enabled="yes" name="secure-boot"/>
  </firmware>
  <loader readonly="yes" secure="yes" type="pflash" format="raw">/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd</loader>
  <nvram template="/usr/share/edk2/x64/OVMF_VARS.4m.fd" templateFormat="raw" format="raw">/var/lib/libvirt/qemu/nvram/win11_VARS.fd</nvram>
</os>
<features><smm state="on"/></features>
```

> [!info] Windows route — `smm` + Secure Boot + TPM
> - `smm` **must be `on`** — required by `tpm-crb` + Secure Boot (Win11 `win11` osinfo enforces it; `30_*:build_command` adds it).
> - Without UEFI + `smm` + `tpm-crb 2.0` the Windows 11 installer blocks (“This PC can't run Windows 11”). No need to “disable Secure Boot” — shipped OVMF supports it.
> - Next mandatory: [[Enable Trusted Platform Module (TPM)]] ( `swtpm` `tpm-crb` `2.0` ) — create via **Add Hardware → TPM → Emulated / CRB / 2.0** or `virt-install --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb`.
> - For patched/LTSC images with TPM check removed, you may omit TPM and set osinfo to `win10` instead — but keep `swtpm` installed.

> [!info] Linux route
> - `smm` optional (no TPM dependency). Secure Boot `secure="yes"` + `enrolled-keys=no` is fine for Arch (no key enrollment needed); you may also set `<loader secure="no">` / `secure-boot=no` if you ship custom UKI / `sbctl` keys — both boot.
> - No TPM needed. Enable `smm` only if you later add `tpm-crb` for measured boot experiments.

> [!tip] Firmware files (Aug 2026)
> `edk2-ovmf` provides `/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd` (code, read-only) + `OVMF_VARS.4m.fd` (template copied per-VM to `/var/lib/libvirt/qemu/nvram/<name>_VARS.fd`). Libvirt 12.6 selects via JSON descriptors in `/usr/share/qemu/firmware/` — do not hardcode legacy `OVMF_CODE.fd` / `OVMF_VARS.fd` without `.4m.` suffix.

> [!warning] Snapshot limitation
> With `firmware="efi"` (pflash NVRAM), **internal** snapshots while running are **not** supported (`snapshot-create` fails). **Shut off** guest first, then `virsh snapshot-create-as`. Applies to both OSes.

Click **Apply**.

## Verify

```bash
virsh -c qemu:///system dumpxml win11 | grep -A4 '<os firmware'
virsh -c qemu:///system dumpxml archlinux | grep -A3 '<loader'
# expect: OVMF_CODE.secboot.4m.fd + per-VM _VARS.fd + smm on (Windows)
```

## Next

- [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]] (bus/cache)
- Windows only: [[Enable Trusted Platform Module (TPM)]] → [[Enable Hyper-V Enlightenments]]
- Linux: directly to storage/network — no extra firmware steps.

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], `30_kvm_vm_deploy.py`, [[Grub Kernal Parameters]] (host boot cmdline, separate plane).
