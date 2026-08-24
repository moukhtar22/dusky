# Mastering CPU Management on Arch Linux

> [!note] Scope
> Permanent reference for **CPU inspection, frequency/power policy, thermal verification, and low-level profiling** on Arch Linux (x86_64). Updated **August 2026** against kernel 7.x.
>
> Covers: `lscpu`, `cpupower`, `turbostat`, `perf`, `lm_sensors`, `sysstat`, `stress-ng`, plus Intel iGPU media offload (because successful offload materially reduces CPU load).
>
> Related: [[Power Management]] · [[CPU Vulnerabilities]] · [[CPU mitigations flag for bootloader]]

> [!info] This machine (verified August 2026)
> ASUS TUF F15 FX507ZE · i7-12700H (6 P-cores + 8 E-cores, 20 threads) · scaling driver `intel_pstate` (HWP/active) · EPP `performance` set by **TLP** (`tlp-pd.service`) · cpuidle governor `teo` · ESP mounted at `/boot`.

---

## Quick Identification

```bash
lscpu
```

Fastest high-level summary: model, topology, caches, SMT, current/max MHz, microcode version, and one `Vulnerability ...` line per CPU flaw.

```bash
sudo lshw -C cpu
```

Hardware-level view: bus width, slot, capacity vs. current clock, enabled cores, microcode version.

```bash
/lib/ld-linux-x86-64.so.2 --help
```

glibc's view of the CPU: shows supported **x86-64 microarchitecture levels** (`x86-64-v2`, `v3`, `v4`) under *Subdirectories of glibc-hwcaps directories*. This i7-12700H reports `x86-64-v3` as supported — useful for choosing optimized package repos.

---

## Package Reference

### Core diagnostics

```bash
sudo pacman -S --needed perf cpupower turbostat lm_sensors sysstat stress-ng
```

### Intel iGPU / VA-API offload

```bash
sudo pacman -S --needed intel-gpu-tools libva-utils intel-media-driver
```

### Legacy Intel VA-API driver — only when needed

```bash
sudo pacman -S --needed libva-intel-driver
```

### What each package provides

| Package | Key commands | Purpose |
|---|---|---|
| `perf` | `perf` | PMU-based profiling, event counting, tracing |
| `turbostat` | `turbostat` | x86 frequency / power / C-state truth |
| `x86_energy_perf_policy` | `x86_energy_perf_policy` | Set MSR energy-perf-bias per core |
| `cpupower` | `cpupower` | Inspect/set frequency policy, idle info |
| `lm_sensors` | `sensors`, `sensors-detect` | Temperature, voltage, fan telemetry |
| `sysstat` | `mpstat`, `pidstat`, `sar` | Per-CPU and per-process utilization |
| `stress-ng` | `stress-ng` | Controlled synthetic load generation |
| `intel-gpu-tools` | `intel_gpu_top` | Verify iGPU/video engine activity |
| `libva-utils` | `vainfo` | Confirm VA-API driver and codec support |
| `intel-media-driver` | VA-API driver `iHD` | Broadwell (Gen8) and newer — the default choice |
| `libva-intel-driver` | VA-API driver `i965` | GMA 4500 up to Coffee Lake; legacy/compatibility |

> [!tip] Kernel tools are individual packages now
> On current Arch, `linux-tools` is a **package group** (and `linux-tools-meta` a meta-package), not a single installable package. If a command is missing, find its owner:
>
> ```bash
> sudo pacman -Fy
> pacman -F turbostat
> ```

---

## Quick Inspection Checklist

Run this before changing anything:

| Goal | Command | Notes |
|---|---|---|
| Identify CPU, topology, caches, SMT | `lscpu` | Fastest summary |
| Detailed per-core topology | `lscpu -e=cpu,node,socket,core,maxmhz,minmhz` | Shows P/E-core max-MHz differences on hybrid CPUs |
| Active scaling driver + governors | `cpupower frequency-info` | Detects `intel_pstate`, `amd_pstate`, `acpi-cpufreq` |
| Idle states | `cpupower idle-info` | C-states; here: POLL/C1E/C6/C8/C10, governor `teo` |
| Temperatures/fans | `sensors` | Requires `lm_sensors` |
| Per-CPU usage live | `mpstat -P ALL 1` | From `sysstat` |
| Per-process usage live | `pidstat -u 1` | From `sysstat` |
| Turbo, C-state residency, power | `sudo turbostat --Summary --quiet sleep 5` | Best x86 power/frequency snapshot |
| Microcode loaded at boot | `journalctl -k -b \| grep -i microcode` | Confirms early load |
| Video decode offloading from CPU | `vainfo` / `intel_gpu_top` | Intel graphics only |

---

## CPU Topology and Baseline Identification

### Extended topology view

```bash
lscpu -e=cpu,node,socket,core,maxmhz,minmhz
```

Especially useful for hybrid CPUs (P/E cores), multi-socket, and NUMA systems.

> [!warning] Hybrid CPUs
> On this i7-12700H, `MAXMHZ` differs per core type (P-cores 4.6–4.7 GHz, E-cores lower). Never compare an all-core sustained frequency to a single-core turbo spec.

---

## CPU Frequency Scaling and Power Policy

Modern Linux does not manage clocks identically everywhere — the active driver matters.

| Driver | Typical systems | Notes |
|---|---|---|
| `intel_pstate` | Most modern Intel systems | Hardware-managed via HWP; governor names mean *policy preference*, not fixed clocks |
| `amd_pstate` | Modern AMD Zen with CPPC | Active/guided/passive modes depending on kernel + firmware |
| `acpi-cpufreq` | Older x86 / fallback | Classic cpufreq semantics (`ondemand`, `conservative`, ...) |

### Inspect driver and governor

```bash
cpupower frequency-info
```

Direct sysfs inspection:

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors
```

Energy Performance Preference (HWP systems):

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference
cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_available_preferences
```

On this machine: available EPPs are `default performance balance_performance balance_power power`; TLP currently selects `performance`.

Driver-specific status, if present:

```bash
[[ -r /sys/devices/system/cpu/intel_pstate/status ]] && cat /sys/devices/system/cpu/intel_pstate/status
[[ -r /sys/devices/system/cpu/amd_pstate/status   ]] && cat /sys/devices/system/cpu/amd_pstate/status
```

### Governor semantics on modern systems

- `intel_pstate`: `performance` ≠ "max frequency always"; `powersave` ≠ "min frequency only". With HWP these names express policy preference.
- `amd_pstate`: CPPC-aware; same abstraction idea.
- `acpi-cpufreq`: classic behavior (`schedutil`, `ondemand`, ...).

### Temporarily change governor

```bash
sudo cpupower frequency-set -g performance
sudo cpupower frequency-set -g powersave
```

> [!tip] Reasonable defaults
> Generic cpufreq stack → `schedutil` is usually best. On `intel_pstate`/`amd_pstate` active mode only `performance` and `powersave` exist (verified here) — do not expect `schedutil`.

### Idle-state information

```bash
cpupower idle-info
```

Reports C-state support/residency — useful when debugging high idle power, poor battery life, or systems that never reach deep package C-states.

---

## Avoid Conflicting Policy Managers

> [!warning] One CPU power authority at a time
> Do not let multiple tools fight over governors, EPP, or frequency limits — behavior becomes inconsistent and undiagnosable.

Common conflicting components: `cpupower.service`, `power-profiles-daemon`, **TLP**, `tuned`, `auto-cpufreq`. This machine runs **TLP** (`tlp-pd.service`).

Check what is active:

```bash
systemctl --type=service --state=running | grep -E 'cpupower|power-profiles|tlp|tuned|auto-cpufreq'
```

---

## Thermals, Power, and Idle-State Verification

### `lm_sensors`

```bash
sensors
watch -n 1 sensors
```

If expected telemetry is missing:

```bash
sudo sensors-detect --auto
```

> [!note] Usually optional
> Modern Arch detects CPU temperature sensors automatically. Run `sensors-detect` only if needed.

### `turbostat`

The most useful x86 telemetry tool: effective frequency, turbo behavior, package temperature and power, idle-state residency, per-core activity.

```bash
sudo turbostat --Summary --quiet sleep 5
```

If it complains about MSR access:

```bash
sudo modprobe msr
```

| Field | Meaning |
|---|---|
| `Avg_MHz` / `Bzy_MHz` | Effective operating frequency while busy |
| `Busy%` | Fraction of time busy |
| `CoreTmp` / `PkgTmp` | Core / package temperature |
| `PkgWatt` / `CorWatt` / `GFXWatt` / `RAMWatt` / `SysWatt` | Estimated power draw |
| `Pkg%pcN` / `CPU%cN` | Package / core C-state residency |

Typical uses: verify turbo engages, distinguish thermal vs. power limits, check deep C-state residency at idle.

> [!warning] x86-oriented
> Feature-rich on Intel; useful on many AMD systems, fields vary by CPU and firmware.

### `sysstat`: Real-Time Utilization

```bash
mpstat -P ALL 1   # per-CPU: bottlenecks, imbalance, IRQ-heavy loads
pidstat -u 1      # which process is consuming CPU
```

---

## Microcode and Mitigation State

Install the correct package — boot flow must load it early (modern `mkinitcpio` bundles it into the initramfs automatically):

```bash
sudo pacman -S --needed intel-ucode   # Intel
sudo pacman -S --needed amd-ucode     # AMD
```

Verify early load (on this machine: `Updated early from: 0x00000429` → `0x0000043b`):

```bash
journalctl -k -b | grep -i microcode
```

Mitigation state exposed by the running kernel:

```bash
lscpu | grep -i '^Vulnerability'
```

Details and control: [[CPU Vulnerabilities]] · [[CPU mitigations flag for bootloader]]

> [!note] Performance impact
> Mitigations can measurably affect syscall-heavy, virtualization, and I/O-heavy workloads. Record mitigation state when benchmarking.

---

## Intel iGPU Media Offload and CPU Load Reduction

> [!note] Adjacent topic
> Decode/encode runs on GPU media engines, not the CPU — but correct offload dramatically lowers CPU use during playback/transcoding.

### Driver selection

| Driver | Package | VA-API name | Coverage |
|---|---|---|---|
| Modern | `intel-media-driver` | `iHD` | Broadwell (2014) and newer — default |
| Legacy | `libva-intel-driver` | `i965` | GMA 4500 up to Coffee Lake; compatibility cases |

Both can coexist; know which one you intend to use.

### Verify VA-API capability

```bash
vainfo
```

Headless or DRM-only:

```bash
vainfo --display drm --device /dev/dri/renderD128
```

Force a specific driver for testing:

```bash
LIBVA_DRIVER_NAME=iHD vainfo
LIBVA_DRIVER_NAME=i965 vainfo
```

### Monitor actual GPU/video engine activity

```bash
sudo intel_gpu_top
```

Watch the RCS/VCS/VECS engine columns during playback — activity there with low CPU means offload works.

> [!warning] `vainfo` is necessary but not sufficient
> A valid `vainfo` proves the driver stack works. The **application** must also be configured to use VA-API.

---

## Low-Level Profiling with `perf`

`perf` is the canonical interface to PMUs, software counters, tracepoints, sampling profilers, scheduler analysis, and syscall tracing.

### Permissions on this system

`kernel.perf_event_paranoid = 2` (Arch default). Verified behavior:

- Unprivileged: `perf stat`, `perf record`, `perf report`, `perf annotate`, `perf list`, and `perf top -p <own-PID>` all work for your own processes.
- Root required: system-wide `perf top`, `perf trace`, `perf sched record`.

Check current restrictions:

```bash
sysctl kernel.perf_event_paranoid
```

A reasonable workstation compromise:

```bash
printf '%s\n' 'kernel.perf_event_paranoid = 1' | sudo tee /etc/sysctl.d/90-perf.conf
sudo sysctl --system
```

> [!warning] Security tradeoff
> Lower values expose more information to local users. Keep restrictions tight on multi-user systems.

### Symbol resolution

```bash
export DEBUGINFOD_URLS="https://debuginfod.archlinux.org"
```

If symbols show `[unknown]`: debuginfod unset, binary stripped, wrong unwinding mode, or no permission on target process.

---

## `perf list`

```bash
perf list
```

Lists events actually available on this hardware. On hybrid CPUs you will see separate `cpu_core/...` and `cpu_atom/...` variants.

---

## `perf stat`

Compact statistical summary of how a command interacts with the CPU:

```bash
perf stat -- <your_command>
perf stat -r 5 -- <your_command>          # repeated runs, better baseline
perf stat -d -- <your_command>            # more derived metrics (L1/L2/TLB)
perf stat -e cycles,instructions,branches,branch-misses,cache-misses -- <your_command>
sudo perf stat -a sleep 10                # system-wide over an interval
```

Reading it:

- **IPC** = instructions / cycles — how much work per cycle
- **cache-misses** → memory hierarchy pressure
- **branch-misses** → bad branch prediction
- Hybrid note: counters split across `cpu_core` and `cpu_atom` PMUs

> [!note] Event multiplexing
> Too many requested counters get time-multiplexed; precision drops. `perf stat` prints scaling percentages when that happens.

---

## `perf top`

Real-time sampling profiler:

```bash
sudo perf top              # system-wide
sudo perf top -p <PID>     # one process
perf top -K                # hide kernel symbols
perf top -U                # hide user-space symbols
```

Use it to find hot functions fast and to see whether time goes to crypto, decompression, syscalls, page faults, etc.

---

## `perf record`, `perf report`, `perf annotate`

Offline analysis workflow:

```bash
perf record -g -- <your_command>                  # writes perf.data
perf record --call-graph dwarf,16384 -- <your_command>   # more robust unwinding, higher overhead
perf report                                       # interactive TUI
perf report --stdio                               # non-interactive
perf annotate                                     # assembly annotated with samples
```

Practical order: `perf stat` → `perf top` → `perf record`+`report` → `annotate` once you know the hot symbol.

---

## `perf trace`

Strace-like syscall tracing built on perf:

```bash
sudo perf trace -- <your_command>
sudo perf trace -p <PID>
```

Requires root (or `CAP_PERFMON`) — unprivileged attempts fail with tracefs/BPF permission errors even on your own processes.

---

## Optional Advanced Workflows

### Scheduler latency

```bash
sudo perf sched record -- sleep 10
sudo perf sched timehist
```

Useful when CPU is *not* fully utilized yet things feel laggy (run-queue contention).

> [!warning] Known quirk on this machine
> `perf.data` captured **under sudo** can fail to open afterwards (*incompatible file format*) on the custom `dusky-*` kernel builds, even read back by root. User-captured data reads back fine. Prefer unprivileged capture wherever possible.

### Pin benchmarks to specific CPUs

```bash
taskset -c 0-3 perf stat -r 5 -- <your_command>
```

Reduces noise between runs.

---

## Controlled Load Generation

Synthetic load validates cooling, boost behavior, and policy response — never a substitute for real profiling.

```bash
stress-ng --cpu 0 --timeout 60s --metrics-brief        # 0 = all logical CPUs
taskset -c 0-3 stress-ng --cpu 4 --timeout 60s --metrics-brief
```

In a second terminal:

```bash
watch -n 1 sensors
sudo turbostat --Summary --quiet sleep 60
```

> [!warning] Watch thermals
> If temperature climbs fast and frequency drops below expected sustained clocks, the limit is thermal/power — not scheduling.

---

## Reproducible Benchmarking Checklist

Before comparing results:

- Same power source, same governor/policy/EPP
- Record microcode version and mitigation state ([[CPU Vulnerabilities]])
- Stop background jobs and heavy browser tabs
- Pin CPUs with `taskset`; warm caches if comparing hot paths
- Never compare single-core turbo claims to all-core sustained frequencies

Capture set:

```bash
uname -r
lscpu
cpupower frequency-info
sensors
journalctl -k -b | grep -i microcode
grep . /sys/devices/system/cpu/vulnerabilities/*
sudo turbostat --Summary --quiet sleep 5
```

---

## Troubleshooting

### `perf_event_open ... Operation not permitted`

Restricted `kernel.perf_event_paranoid`, missing capability, or VM/container policy.

```bash
sysctl kernel.perf_event_paranoid
sudo perf stat -- <your_command>
```

### `turbostat` cannot access MSRs

```bash
sudo modprobe msr
```

### Limited governor choices

Expected on `intel_pstate`/`amd_pstate` — only `performance`/`powersave` exist. Check `scaling_driver` first; do not interpret `powersave` as "locked to minimum clock".

### CPU does not reach advertised boost

Causes: thermal limits, firmware power limits, battery mode/platform profile, all-core workloads, conflicting power managers, virtualization, turbo disabled in firmware.

```bash
cpupower frequency-info
sensors
sudo turbostat --Summary --quiet sleep 5
```

Also check firmware toggles: Intel Turbo Boost / Speed Shift, AMD Core Performance Boost, CPPC, SMT.

### Idle power too high

Causes: something keeps waking the CPU, deep C-states unreached, devices blocking package idle, a daemon forcing performance policy.

```bash
cpupower idle-info
sudo turbostat --Summary --quiet sleep 10
mpstat -P ALL 1
```

Look for poor `Pkg%pcN` residency and background activity.

### Video playback too CPU-heavy on Intel graphics

```bash
vainfo
sudo intel_gpu_top
LIBVA_DRIVER_NAME=iHD vainfo    # test drivers explicitly if needed
LIBVA_DRIVER_NAME=i965 vainfo
```

`vainfo` OK but CPU stays high → application is not actually using VA-API.

### Root-captured `perf.data` fails to open

See the known quirk above; re-run capture as your own user.

---

## Recommended Minimal Workflow

System feels slow:

```bash
lscpu
cpupower frequency-info
sensors
mpstat -P ALL 1
sudo turbostat --Summary --quiet sleep 5
```

One command is slow:

```bash
perf stat -r 5 -- <your_command>
sudo perf top
perf record -g -- <your_command>
perf report
```

Media playback unexpectedly CPU-heavy on Intel:

```bash
vainfo
sudo intel_gpu_top
```

---

## Bottom Line

Core tools for Arch CPU work:

- **`lscpu`** — topology and kernel-exposed state
- **`cpupower`** — frequency/idle policy
- **`sensors`** — thermals
- **`turbostat`** — x86 power/frequency/C-state truth
- **`perf`** — serious profiling
- **`vainfo` + `intel_gpu_top`** — verify media offload that should reduce CPU load

> [!tip] Diagnose in this order
> 1. Topology / driver / governor
> 2. Thermals / power / idle state
> 3. Per-process CPU usage
> 4. Low-level profiling with `perf`
> 5. Media offload verification when applicable
