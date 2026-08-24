---
title: "Hyper-V Enlightenments — Upstream Reference"
tags:
  - kvm
  - hyperv
  - qemu
  - reference
---

# Hyper-V Enlightenments — Upstream Reference

> [!info] Purpose
> Paravirtual Hyper-V interfaces KVM exposes so proprietary Windows can cooperate with the hypervisor. Mirrors QEMU docs (KVM Hyper-V Enlightenments) as shipped with **QEMU 11.1**. Canonical pipeline value: `30_kvm_vm_deploy.py:HYPERV`.

Windows on KVM without enlightenments falls back to emulated timers/APIC — slower and busier at idle. When any set is enabled, QEMU changes CPUID `0x40000000..0x4000000A` to `Microsoft Hv` (KVM leaves remain at `0x40000100..101`).

## Enabling

No enlightenment is `on` by default. In `virt-install`:

```bash
--features hyperv.relaxed.state=on,hyperv.vapic.state=on,hyperv.spinlocks.state=on,hyperv.spinlocks.retries=8191,hyperv.vpindex.state=on,hyperv.runtime.state=on,hyperv.synic.state=on,hyperv.stimer.state=on,hyperv.frequencies.state=on,hyperv.reenlightenment.state=on,hyperv.tlbflush.state=on,hyperv.ipi.state=on
# + xml <kvm><hidden state='on'/></kvm> for NVIDIA Error 43, clock hypervclock
```

Or raw QEMU: `-cpu host,hv_relaxed,hv_vpindex,hv_time,…`.

## Enlightenments

| Flag | What it does | Requires |
|---|---|---|
| `hv-relaxed` | guest disables watchdog on hypervisor | — |
| `hv-vapic` | VP Assist page → exit-less EOI | — |
| `hv-spinlocks=xxx` | paravirtual spinlock (tries before hypercall; `0xffffffff` = never) | — |
| `hv-vpindex` | `HV_X64_MSR_VP_INDEX` (0x40000002) | — |
| `hv-runtime` | `HV_X64_MSR_VP_RUNTIME` (stolen time) | — |
| `hv-crash` | guest crash MSRs → QEMU log/QAPI (blocks crash dumps) | — |
| `hv-time`/`hv_time` | Hyper-V clock + Reference TSC page (exit-less timestamps) | — |
| `hv-synic` | Synthetic Interrupt Controller (messages/events, VMBus) | `vpindex` |
| `hv-stimer` | 4 Synthetic timers per vCPU (else HPET/RTC fallback → CPU burn) | `vpindex,synic,time` |
| `hv-tlbflush` | paravirtual TLB shootdown | `vpindex` |
| `hv-ipi` | `HvCallSendSyntheticClusterIpi` (>64 vCPU in one hypercall) | `vpindex` |
| `hv-vendor-id=xxx` | CPUID `0x40000000.EBX-EDX` string (≤12 chars) | not an enlightenment alone |
| `hv-reset`, `hv-frequencies`, `hv-reenlightenment`, `hv-evmcs` (Intel nested), `hv-stimer-direct`, `hv-avic`, `hv-no-nonarch-coresharing`, `hv-version-id-*`, `hv-syndbg` (debug), `hv-emsr-bitmap`, `hv-xmm-input`, `hv-tlbflush-ext/direct`, `hv-passthrough` (all), `hv-enforce-cpuid` | per upstream | see Hyper-V TLFS |

## Recommendations (this vault)

- **Enable:** `relaxed,vapic,spinlocks,vpindex,runtime,synic,stimer+direct,frequencies,reenlightenment,tlbflush,ipi,evmcs` (Intel) — `30_kvm_vm_deploy.py` default.
- **Avoid in prod:** `syndbg,passthrough,enforce-cpuid` (debug/dev). `reset` generally unnecessary. `no-nonarch-coresharing=on` only with correct pinning + topology. `spinlocks=0xfff` if host overcommitted, else `0xffffffff`.

See: [[Hypervisor Features]] (XML schema), [[Enable Hyper-V Enlightenments]] (pasted XML + cputune).
