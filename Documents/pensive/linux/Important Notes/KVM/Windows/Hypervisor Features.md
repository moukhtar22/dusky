---
title: "Hypervisor Features — XML Reference"
tags:
  - kvm
  - libvirt
  - xml
  - reference
---

# Hypervisor Features — XML Reference

> [!info] Source
> Extracted from `libvirt` domain XML schema + `virsh capabilities`. Feature set as exposed on **QEMU 11.1 / libvirt 12.6** (Aug 2026). Toggling is via `<features>`; omitting a tag = `off`. Use `virsh domcapabilities` to see host-supported feature list.

```xml
<features>
  <pae/><acpi/><apic/><hap/><privnet/>
  <hyperv mode='custom'>
    <relaxed state='on'/><vapic state='on'/><spinlocks state='on' retries='4096'/>
    <vpindex state='on'/><runtime state='on'/><synic state='on'/>
    <stimer state='on'><direct state='on'/></stimer><reset state='on'/>
    <vendor_id state='on' value='KVM Hv'/>
    <frequencies state='on'/><reenlightenment state='on'/><tlbflush state='on'><direct state='on'/><extended state='on'/></tlbflush>
    <ipi state='on'/><evmcs state='on'/><emsr_bitmap state='on'/><xmm_input state='on'/>
  </hyperv>
  <kvm><hidden state='on'/><hint-dedicated state='on'/><poll-control state='on'/><pv-ipi state='off'/><dirty-ring state='on' size='4096'/></kvm>
  <xen><e820_host state='on'/><passthrough state='on' mode='share_pt'/></xen>
  <pvspinlock state='on'/><gic version='2'/><ioapic driver='kvm'/>
  <hpt resizing='required'><maxpagesize unit='MiB'>16</maxpagesize></hpt>
  <vmcoreinfo state='on'/><smm state='on'><tseg unit='MiB'>48</tseg></smm>
  <htm state='on'/><ccf-assist state='on'/><msrs unknown='ignore'/>
  <cfpc value='workaround'/><sbbc value='workaround'/><ibs value='fixed-na'/>
  <tcg><tb-cache unit='MiB'>128</tb-cache></tcg>
  <async-teardown enabled='yes'/><ras state='on'/><ps2 state='on'/><aia value='aplic-imsic'/>
</features>
```

## Key groups

| Feature | Meaning |
|---|---|
| `pae` / `acpi` / `apic` / `hap` | PAE, power mgmt, IRQ (EIO opt. `eoi="on"` since 0.10.2), hardware-assisted paging |
| `viridian` | legacy alias for `hyperv` |
| `hyperv mode='custom'|'passthrough'` | Windows enlightenments (`passthrough` = expose every host-supported enlightenment; blocks migration) |
| `kvm.hidden / hint-dedicated / poll-control / pv-ipi / dirty-ring` | KVM specifics (hide hypervisor, dedicated vCPU hint, I/O poll, dirty ring `size` power-of-2 1024–65536) |
| `smm` (`tseg`) | SMM TSEG size (default 16 MiB pc-q35-2.10+ → ~272 vCPU / 5 GiB; 48 MiB → 240 vCPU/4 TiB) |
| `ioapic driver='kvm'|'qemu'` | split I/O APIC |
| `vmport` | VMware port (`off` for Hyper-V) |
| `htb` / `htm` / `ccf-assist` | pSeries etc. |

## Hyper-V sub-features (quick ref)

`relaxed` (timer slack), `vapic` (VP assist), `spinlocks` (retries ≥4095), `vpindex`, `runtime` (stolen), `synic`/`stimer`/`direct`, `reset`, `vendor_id` (≤12 chars), `frequencies`, `reenlightenment` (needs `frequencies`, TSC scaling), `tlbflush`/`direct`/`extended`, `ipi`, `evmcs` (Intel nested), `avic` (APICv/AVIC), `no-nonarch-coresharing`, `version-id-*`, `syndbg`, `emsr_bitmap`, `xmm_input`.

> [!tip] Recommended baseline for Windows (this vault)
> `relaxed,vapic,spinlocks=8191,vpindex,runtime,synic,stimer+direct,reset,vendor_id=Microsoft Hv,frequencies,reenlightenment,tlbflush,ipi,evmcs` plus `kvm.hidden=on`, `vmport=off`, `ioapic=kvm`, `smm=on`. See [[Enable Hyper-V Enlightenments]] and [[Hyper-V Enlightenments]] for rationale and upstream “do not enable” list (`syndbg`, `passthrough`, `enforce-cpuid`).

Use `virsh domcapabilities | grep -A2 '<feature'` to verify host exposure before enabling.
