---
title: "CPU Mode — Host-Passthrough & Topology (Unified)"
tags:
  - kvm
  - cpu
  - arch
  - hyperv
aliases:
  - CPU Unified
---

# CPU Mode — Host-Passthrough & Topology (Unified)

> [!abstract] Goal
> Expose host CPU model + flags directly to guest for maximum speed and correct enlightenment support. Mirrors `30_kvm_vm_deploy.py:build_command --cpu host-passthrough,migratable=off,cache.mode=passthrough`. **Shared for Windows and Linux** — Windows adds Hyper-V enlightenments on top.

## Why `host-passthrough`

| Mode | Flags | Speed | Hyper-V | Live migration |
|---|---|---|---|---|
| `host-passthrough` ✅ | host exact (`AVX`, `AES-NI`, etc.) | native | **yes** (needs it) | **no** (`migratable=off`) |
| `host-model` | closest named model (`Skylake-Client` etc.) | 10–30% loss | partial | yes |
| `qemu64` / generic | least-common denominator | slowest | no | yes |

For pinned VFIO/lab VMs on one host, `host-passthrough` is correct — migration not intended.

## Steps (virt-manager, both OSes)

1. VM Details (lightbulb) → **CPUs**
2. **Configuration:** `Copy host CPU configuration (host-passthrough)` (label varies by virt-manager version; ensure XML shows `mode='host-passthrough'`)

XML (`virsh edit win11` / `virsh edit archlinux`):

```xml
<cpu mode="host-passthrough" check="none" migratable="off">
  <topology sockets="1" dies="1" cores="6" threads="2"/>
  <cache mode="passthrough"/>
</cpu>
```

- `check="none"` — don't fail on missing flags across hosts (migratable off)
- `migratable="off"` — allows passthrough that blocks live migration (desired for pinned VFIO)
- `cache.mode="passthrough"` — expose host cache topology directly (not emulated)
- Topology **must** satisfy `sockets × dies × cores × threads = vcpu` — mismatched triggers Windows “performance warning” prompt; `25_looking_glass.py:apply_cpu_topology` computes `cores = vcpus / threads` but virt-manager lets you type it.

> [!tip] Generate pinning + topology
> Do not hand-type `cputune` for hybrid CPUs (P/E cores). Use `35_cpu_pinning_generator.py` (auto-detects host `lscpu`, suggests `vcpupin` + `emulatorpin` + `iothreadpin` away from vCPUs). Example `i7-12700H` preset lives in [[Enable Hyper-V Enlightenments]] full XML skeleton but is **machine-specific** — regenerate for your box.

→ **Apply**.

## OS deltas

> [!info] Windows route — add Hyper-V enlightenments + clock
> After setting `host-passthrough` + topology, apply Hyper-V block + clock (mandatory for Windows perf). This is **Windows-only** — do not add for Linux.
>
> **Hyper-V enlightenments** ([[Enable Hyper-V Enlightenments]] fast path + [[Hyper-V Enlightenments]] upstream ref + [[Hypervisor Features]] XML schema):
> ```xml
> <features>
>   <hyperv mode="custom">
>     <relaxed state="on"/><vapic state="on"/><spinlocks state="on" retries="8191"/>
>     <vpindex state="on"/><runtime state="on"/><synic state="on"/>
>     <stimer state="on"><direct state="on"/></stimer><reset state="on"/>
>     <vendor_id state="on" value="Microsoft Hv"/>
>     <frequencies state="on"/><reenlightenment state="on"/><tlbflush state="on"/><ipi state="on"/><evmcs state="on"/>
>   </hyperv>
>   <kvm><hidden state="on"/></kvm>
>   <vmport state="off"/><ioapic driver="kvm"/>
> </features>
> ```
> Canonical `30_*:HYPERV` list: `relaxed,vapic,spinlocks,vpindex,synic,stimer,frequencies,reenlightenment,tlbflush,ipi,evmcs` (+ `vendor_id`, `kvm.hidden`, `vmport off`).
>
> **Clock** (pairs with `useplatformclock=No` inside Windows):
> ```xml
> <clock offset="localtime">
>   <timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/>
>   <timer name="hpet" present="no"/><timer name="hypervclock" present="yes"/>
> </clock>
> ```
> Verify inside Windows: `bcdedit /set useplatformclock No` (see [[Disable useplatformclock]]) — else stutter with `hpet=no`.
>
> Topology wrapper: `host-passthrough` + `dies=1` + computed `cores/threads`. Scripted: `30_*:build_command --features hyperv.* --clock hypervclock --cpu host-passthrough`.

> [!info] Linux route — no Hyper-V, UTC clock
> - Keep `<features><acpi/><apic/><vmport state="off"/></features>` — **no** `<hyperv>`, no `kvm.hidden`, keep `ioapic driver='kvm'` optional.
> - Clock: `<clock offset="utc">` (Linux expects UTC hardware clock, not `localtime`). `hpet` may stay default; `hypervclock` **off**.
> ```xml
> <clock offset="utc">
>   <timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/>
>   <timer name="hpet" present="no"/>
> </clock>
> ```
> - Optional: [[Optimize the Host with TuneD]] `virtual-host` vs [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard|03 Storage]] tuning is host-level, not per-guest.

## Topology math helper

```bash
# sockets × dies × cores × threads = vcpu
# example: 6 vCPU, host SMT=2 → 1×1×3×2 = 6
virsh -c qemu:///system dumpxml win11 | grep -A1 '<topology'
python3 -c "vcpu=6; threads=2; cores=vcpu//threads; print(f'sockets=1 dies=1 cores={cores} threads={threads} → {1*1*cores*threads} vCPU')"
```

> [!warning] Windows performance warning
> If you change vCPU count but not topology product, Windows may boot with a non-optimal scheduler (big.LITTLE unaware). Always keep product = vCPU. `35_cpu_pinning_generator.py` enforces this.

## Verify

```bash
virsh -c qemu:///system dumpxml win11 | grep -E "cpu mode=.host-passthrough|topology|hyperv|hypervclock"
virsh -c qemu:///system dumpxml archlinux | grep -E "cpu mode=.host-passthrough|topology|clock offset"
lscpu | grep -i virtualization   # host still VT-x / AMD-V
```

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[Enable Hyper-V Enlightenments]] (full skeleton + cputune), [[Hyper-V Enlightenments]] (upstream), [[Hypervisor Features]] (XML schema), `35_cpu_pinning_generator.py`.
