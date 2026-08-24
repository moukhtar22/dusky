---
title: "Enable TPM 2.0 (Windows 11)"
tags:
  - kvm
  - windows
  - tpm
  - swtpm
---

# Enable TPM 2.0 — Windows 11 Only

> [!info] Why
> Win11 osinfo (`win11`) hard-requires TPM 2.0 + SMM. `30_kvm_vm_deploy.py:build_command` adds `--tpm backend.type=emulator,backend.version=2.0,model=tpm-crb` + `smm.state=on` automatically; this note is manual virt-manager equivalent.

## Prereq (already via `05_virtio_iso.py`)

```bash
sudo pacman -S --needed swtpm
pacman -Q swtpm
```

## Manual steps (virt-manager)

1. VM Details → `TPM` (or **Add Hardware** → **TPM** if absent)
2. **Advanced options:**
   - **Type:** `Emulated`
   - **Version:** `2.0`
   - **Model:** `CRB` (or `TIS` — CRB is modern)
3. **Apply**

XML (`virsh dumpxml win11 | grep -A4 '<tpm'`):

```xml
<tpm model="tpm-crb">
  <backend type="emulator" version="2.0"/>
</tpm>
<features><smm state="on"/></features>
```

> [!success] Result
> VM now satisfies `win11` osinfo → installer no longer blocks on “This PC doesn't meet…”.

> [!note] Skip condition
> If your ISO has TPM patched out (custom de-bloated/LTSC), you can omit TPM and set osinfo to `win10` instead — but keep `swtpm` installed for future `win11` guests.

See: [[Configure Chipset and Firmware]] (UEFI/SMM), [[Enable Hyper-V Enlightenments]].
