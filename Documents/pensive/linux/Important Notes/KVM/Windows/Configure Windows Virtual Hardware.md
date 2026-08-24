---
title: "Virt-Manager — Enable XML Editing & New VM (Redirect)"
tags:
  - kvm
  - virt-manager
  - windows
aliases:
  - Virt-Manager XML Stub
---

# Virt-Manager — Enable XML Editing & New VM

> [!tip] Merged — canonical source
> **Shared prereq lives in [[KVM Setup/VM Creation/01 Wizard — Create VM#Prerequisites]].** Follow that note + [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]]. This stub keeps filename for `[[wikilink]]` stability.

## Steps retained (both OSes)

1. Launch: `virt-manager --connect qemu:///system` (ensure **system**, not `qemu:///session`)
2. **Edit → Preferences → General → ✅ Enable XML editing** ← mandatory for Hyper-V/TPM/pinning/`<shmem>` edits later
3. Toolbar → **Create a new virtual machine** (computer icon) → proceed to [[KVM Setup/VM Creation/01 Wizard — Create VM]]

> [!important] Skip = blocked
> Without XML editing you cannot apply [[Enable Hyper-V Enlightenments]], [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology|05 CPU]] topology, or [[Looking Glass]] `<shmem>`.

See: [[KVM Setup/VM Creation/01 Wizard — Create VM]], [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[+ MOC Windows Installation Through Virt Manager]].
