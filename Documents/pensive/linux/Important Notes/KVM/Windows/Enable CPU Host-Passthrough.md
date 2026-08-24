---
title: "CPU Mode — Host-Passthrough (Windows Delta)"
tags:
  - kvm
  - cpu
  - windows
  - hyperv
aliases:
  - Windows CPU Stub
---

# CPU Mode — Host-Passthrough (Windows)

> [!tip] Merged — canonical source
> **Shared `host-passthrough` + `cache.mode=passthrough` + topology math lives in [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]].** Follow that note for **VM → CPUs → Copy host CPU configuration**, XML `mode="host-passthrough" check="none" migratable="off"`, `topology sockets×dies×cores×threads = vcpu`, and why not `host-model`/`qemu64`. This stub keeps **only Windows Hyper-V + clock** delta.

## Windows delta (on top of canonical)

After canonical `host-passthrough` + topology, add:

```xml
<features>
  <hyperv mode="custom">
    <relaxed state="on"/><vapic state="on"/><spinlocks state="on" retries="8191"/>
    <vpindex state="on"/><runtime state="on"/><synic state="on"/>
    <stimer state="on"><direct state="on"/></stimer><reset state="on"/>
    <vendor_id state="on" value="Microsoft Hv"/>
    <frequencies state="on"/><reenlightenment state="on"/><tlbflush state="on"/><ipi state="on"/><evmcs state="on"/>
  </hyperv>
  <kvm><hidden state="on"/></kvm>
  <vmport state="off"/><ioapic driver="kvm"/>
</features>
<cpu mode="host-passthrough" check="none" migratable="off">
  <topology sockets="1" dies="1" cores="6" threads="2"/><cache mode="passthrough"/>
</cpu>
<clock offset="localtime">
  <timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/>
  <timer name="hpet" present="no"/><timer name="hypervclock" present="yes"/>
</clock>
```

- List matches `30_kvm_vm_deploy.py:HYPERV` (`relaxed,vapic,spinlocks,vpindex,synic,stimer,frequencies,reenlightenment,tlbflush,ipi,evmcs`) + `vendor_id=Microsoft Hv` + `kvm.hidden=on` (NVIDIA Error 43) + `vmport off`.
- Clock `hypervclock=yes` + `hpet=no` pairs with **inside Windows** `bcdedit /set useplatformclock No` (see [[Disable useplatformclock]]) — else stutter.
- Use `35_cpu_pinning_generator.py` for `cputune` on hybrid CPUs (do not paste i7-12700H example blindly).

> [!info] Linux route
> Plain `host-passthrough` only — **no** Hyper-V, **no** `vendor_id`, clock `offset="utc"` (not `localtime`), `hpet present=no`, `hypervclock` off. See canonical.

See: canonical [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]], [[Enable Hyper-V Enlightenments]] (fast paste + cputune), [[Hyper-V Enlightenments]] (upstream ref), [[Hypervisor Features]] (schema), [[Disable useplatformclock]].
