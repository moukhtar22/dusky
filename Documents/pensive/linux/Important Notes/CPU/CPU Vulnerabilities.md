# CPU Vulnerabilities

> [!note] Scope
> How to **read**, **understand**, and (where supported) **control** CPU vulnerability mitigation state on Arch Linux x86_64. Updated **August 2026** against kernel 7.x.
>
> Related: [[CPU]] · [[CPU mitigations flag for bootloader]]

---

## Check Mitigation Status

The kernel exposes one sysfs file per known vulnerability:

```bash
ls /sys/devices/system/cpu/vulnerabilities/
```

Status of every vulnerability, with filenames:

```bash
grep . /sys/devices/system/cpu/vulnerabilities/*
```

Same information via `lscpu`:

```bash
lscpu | grep -i '^Vulnerability'
```

### Reading the statuses

| Status | Meaning |
|---|---|
| `Not affected` | CPU is not susceptible — nothing to do, parameter would be meaningless |
| `Mitigation: ...` | Mitigation compiled in and active |
| `Vulnerable` / `Vulnerable; ...` | Susceptible and **not** mitigated (detail text explains which parts) |
| `Unknown` | Detection failed (missing microcode or CPUID data) |

---

## Third-Party Audit

`spectre-meltdown-checker` examines CPU, kernel, and microcode across ~30 CVEs. Verified working August 2026 (`meltdown.ovh` redirects to the upstream GitHub raw script):

```bash
curl -L https://meltdown.ovh -o spectre-meltdown-checker.sh
chmod +x spectre-meltdown-checker.sh
sudo ./spectre-meltdown-checker.sh          # interactive
sudo ./spectre-meltdown-checker.sh --batch  # machine-readable
```

---

## What Actually Controls Mitigation State

Three layers, in order of authority:

### 1. Kernel build configuration

Since kernel 6.9 there is a global compile-time gate:

```bash
zcat /proc/config.gz | grep -E 'CONFIG_CPU_MITIGATIONS|CONFIG_MITIGATION_'
```

Upstream documentation is explicit: the `mitigations=` boot parameter works **if and only if** the kernel was built with `CPU_MITIGATIONS=y`.

> [!important] This machine
> The custom `dusky-gaming` / `dusky-battery` kernels are built with **`CONFIG_CPU_MITIGATIONS=n`** (verified via `/proc/config.gz`; retpoline/unret entry thunks are compiled out entirely). Consequences:
>
> - Every `Vulnerable` line in sysfs is **fixed by the build** — it is not a runtime setting.
> - Boot parameters such as `spectre_v2=off`, `mitigations=off`, … are **no-ops on these kernels**.
> - To change mitigation state, boot a mitigated kernel (e.g. stock `linux`, whose default entry exists in the boot menu) or rebuild with a different config.

### 2. Microcode

Early microcode load determines which mitigations are even possible:

```bash
journalctl -k -b | grep -i microcode
```

Example from this machine:

```text
microcode: Current revision: 0x0000043b
microcode: Updated early from: 0x00000429
```

A microcode update can add/remove mitigation capability, change defaults, and alter performance. Record its revision whenever comparing benchmarks.

### 3. Boot parameters

Only effective on kernels built with mitigations enabled. Application details: [[CPU mitigations flag for bootloader]]. Reference table below.

The sysfs files above are always the **runtime truth** — after any change, verify there.

---

## Current Snapshot — This Machine (August 2026)

i7-12700H under `7.2.0-dusky-gaming`:

| Vulnerable | Why |
|---|---|
| `reg_file_data_sampling` (RFDS) | RFDS only affects Intel Atom-derived cores — i.e. the Gracemont E-cores |
| `spec_store_bypass` | SSBD not applied |
| `spectre_v1` | Partially mitigated (usercopy barriers only, no swapgs barriers) |
| `spectre_v2` | IBPB/STIBP disabled; BHI and PBRSB-eIBRS unmitigated |
| `vmscape` | Unmitigated |

Everything else reports `Not affected` (expected for Alder Lake: meltdown, MDS, L1TF, TAA, MMIO stale data, GDS, retbleed, SRBDS, SRSO…).

---

## Master Switch

```text
mitigations=off
```

Blunt instrument: disables all optional mitigations at once. Requires `CONFIG_CPU_MITIGATIONS=y`. Useful for lab/benchmark deltas, not for daily multi-user use.

Documented equivalents of `mitigations=off` (kernel 7.x):

```text
if nokaslr then kpti=0            [ARM64]
gather_data_sampling=off          [X86]
indirect_target_selection=off     [X86]
kvm.nx_huge_pages=off             [X86]
l1tf=off                          [X86]
mds=off                           [X86]
mmio_stale_data=off               [X86]
no_entry_flush                    [PPC]
no_uaccess_flush                  [PPC]
nobp=0                            [S390]
nopti                             [X86,PPC]
nospectre_bhb                     [ARM64]
nospectre_v1                      [X86,PPC]
nospectre_v2                      [X86,PPC,S390,ARM64]
reg_file_data_sampling=off        [X86]
retbleed=off                      [X86]
spec_rstack_overflow=off          [X86]
spec_store_bypass_disable=off     [X86,PPC]
spectre_bhi=off                   [X86]
spectre_v2_user=off               [X86]
srbds=off                         [X86,INTEL]
ssbd=force-off                    [ARM64]
tsa=off                           [X86,AMD]
tsx_async_abort=off               [X86]
vmscape=off                       [X86]
```

Exception: `kvm.nx_huge_pages=force` overrides `kvm.nx_huge_pages=off`.

Other values:

- `auto` *(default)* — mitigate everything, keep SMT on.
- `auto,nosmt` — mitigate everything, disable SMT if required.

On x86, `mitigations=` additionally supports attack-vector-based controls — see `Documentation/admin-guide/hw-vuln/attack_vector_controls.rst`.

---

## Per-Vulnerability Parameter Reference

Sysfs filenames are **reporting names**; boot parameters do not follow one uniform namespace. Never invent `foo=off` just because `/sys/.../foo` exists.

| Sysfs file | Boot parameter | Notes |
|---|---|---|
| `meltdown` | `pti=off` | Alias: `nopti` |
| `spectre_v1` | `nospectre_v1` | No `spectre_v1=off` form exists |
| `spectre_v2` | `spectre_v2=off` | Alias: `nospectre_v2`; user side: `spectre_v2_user=off` |
| `spec_store_bypass` | `spec_store_bypass_disable=off` | Note the `_disable` in the name |
| `l1tf` | `l1tf=off` | Affected Intel systems mainly |
| `mds` | `mds=off` | VERW-based buffer clearing family |
| `tsx_async_abort` | `tsx_async_abort=off` | Interacts with MDS |
| `mmio_stale_data` | `mmio_stale_data=off` | Shares plumbing with MDS/TAA |
| `reg_file_data_sampling` | `reg_file_data_sampling=off` / `=on` | RFDS — Intel Atom cores only; cannot be disabled while other VERW mitigations are on |
| `gather_data_sampling` | `gather_data_sampling=off` / `=force` | GDS/Downfall; mitigated by updated microcode by default |
| `retbleed` | `retbleed=off` | Values also include `auto`, `unret`, `ibpb` |
| `spec_rstack_overflow` | `spec_rstack_overflow=off` | AMD SRSO; other values `microcode`, `safe-ret`, `ibpb`, `ibpb-vmexit` |
| `srbds` | `srbds=off` | SRBDS; microcode-mitigated by default |
| `indirect_target_selection` | `indirect_target_selection=off` | ITS; other values `on`, `force`, `vmexit`, `stuff` |
| `spectre_bhi` | `spectre_bhi=off` | Branch History Injection |
| `tsa` | `tsa=off` | Transient Scheduler Attacks (AMD); other values `on`, `user`, `vm` |
| `vmscape` | `vmscape=off` | Default mitigation is `ibpb`; also accepts `force` |
| `old_microcode` | — | Informational only: flags microcode too old for proper mitigation coverage |

Non-x86 extras seen in the equivalents list: `ssbd=force-off`, `nospectre_bhb` (ARM64); `no_entry_flush`, `no_uaccess_flush` (PPC); `nobp=0` (S390).

---

## Value Tokens

Mitigation parameters take string tokens, not shell-style booleans. Use `spectre_v2=off`, **not** `spectre_v2=0`.

Common tokens: `off`, `on`, `auto`, `force`, `prctl`, `full`, `full,nosmt`, `auto,nosmt`. Not every parameter accepts every token — check the docs below.

Unknown parameters do nothing and are usually logged at boot:

```bash
sudo journalctl -b -k | grep -Ei 'unknown kernel command line|mitigat'
```

---

## References

- Kernel docs: <https://docs.kernel.org/admin-guide/hw-vuln/> — including `rfds.html`, `indirect-target-selection.html`, `vmscape.html`, `old-microcode.html`
- Full parameter reference: <https://docs.kernel.org/admin-guide/kernel-parameters.html>
- Runtime state: `/proc/cmdline`, `/sys/devices/system/cpu/vulnerabilities/*`

> [!tip] Performance note
> Disabling *all* mitigations is rarely optimal on modern CPUs — much mitigation is implemented efficiently in hardware/microcode, so blanket `mitigations=off` can even hurt. Measure per-vulnerability instead.
