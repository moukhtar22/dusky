---
title: "Core Isolation / Memory Integrity (Optional)"
tags:
  - kvm
  - windows
  - security
  - vbs
---

# Core Isolation / Memory Integrity — Optional

> [!warning] Optional — perf cost
> Enabling VBS + **Memory integrity** adds virtualization overhead (nested EPT). For gaming passthrough it can cost FPS/latency. Keep it **off** unless compliance demands it. Standard Q35+UEFI+TPM 2.0+virtio already covers normal Win11 security.

## Prereqs

- CPU meets [Microsoft CPU requirements](https://learn.microsoft.com/en-us/windows-hardware/design/minimum/windows-processor-requirements)
- Host already on `host-passthrough` + correct topology

## Enable nested feature (XML edit — VM must be shut off)

In `virsh edit win11`, find single-line:

```xml
<cpu mode="host-passthrough" check="none" migratable="on"/>
```

Replace per vendor:

**Intel (`vmx`):**

```xml
<cpu mode="host-passthrough" check="none" migratable="on">
  <feature policy="require" name="vmx"/>
</cpu>
```

**AMD (`svm`):**

```xml
<cpu mode="host-passthrough" check="none" migratable="on">
  <feature policy="require" name="svm"/>
</cpu>
```

→ **Apply**.

> [!tip] Alternative via `virt-xml`
> ```bash
> virsh shutdown win11
> virt-xml win11 --edit --cpu host-passthrough,vmx.require=on  # or svm
> ```

## Inside Windows

1. **Settings → Privacy & security → Windows Security → Device security → Core isolation details**
2. **Memory integrity → On** → Reboot when prompted → verify stays **On**.

> [!note] If toggle stays gray
> Re-check `msinfo32` → **Virtualization-based security** should be `Running` and `Kernel DMA Protection`. If `Off`, re-edit `<cpu>` above and ensure no duplicate `<cpu>` entries (duplicate kills the second `feature`; `10_virt_modular_daemon:enforce_kv_config` collapses duplicates — `virsh define` does similarly).

See: [[+ MOC Windows Installation Through Virt Manager]], `30_kvm_vm_deploy.py` (no VBS by default).
