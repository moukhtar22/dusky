# Dusky Kernel Compiler (v6.0.0) — Architecture, Configuration & Profile Engineering Manual

*Targeting Linux 7.2+ and Linux 7.3-rc on Arch Linux (September 2026 Specification)*

---

## Table of Contents

1. [Executive Summary & Core Architecture](#1-executive-summary--core-architecture)  
2. [The Kernel Optimization Trilemma (Performance vs. Battery vs. RAM)](#2.-the-kernel-optimization-trilemma)  
3. [Comprehensive Section & Parameter Reference](#3-comprehensive-section--parameter-reference)  
   - [3.1. \[meta\] — Package Metadata & Hardware Portability](#31-meta--package-metadata--hardware-portability)  
   - [3.2. \[release\] — Source Channels & Version Enforcement](#32-release--source-channels--version-enforcement)  
   - [3.3. \[scheduler\] — CPU Scheduling Core (EEVDF, BORE, BMQ, sched\_ext)](#33-scheduler--cpu-scheduling-core-eevdf-bore-bmq-sched_ext)  
   - [3.4. \[cache\] — Cache-Aware Scheduling (CAS) & LLC Domains](#34-cache--cache-aware-scheduling-cas--llc-domains)  
   - [3.5. \[rseq\] — Restartable Sequences Time-Slice Extensions](#35-rseq--restartable-sequences-time-slice-extensions)  
   - [3.6. \[cpu\] — Microarchitecture, P-State Autonomy & Mitigations](#36-cpu--microarchitecture-p-state-autonomy--mitigations)  
   - [3.7. \[timing\] — Clock Cadence, PREEMPT\_LAZY & Tickless Modes](#37-timing--clock-cadence-preempt_lazy--tickless-modes)  
   - [3.8. \[memory\] — Paging, Multi-Gen LRU, Compressed Swap & Footprint Tiers](#38-memory--paging-multi-gen-lru-compressed-swap--footprint-tiers)  
   - [3.9. \[compiler\] — LLVM/Clang, ThinLTO Caching, kCFI & Rust-for-Linux](#39-compiler--llvmclang-thinlto-caching-kcfi--rust-for-linux)  
   - [3.10. \[security\] — Hardening Profiles, Allocator Defenses & Lockdown](#310-security--hardening-profiles-allocator-defenses--lockdown)  
   - [3.11. \[gaming\] — NTSync Driver, UCLAMP & Wine/Proton Optimizations](#311-gaming--ntsync-driver-uclamp--wineproton-optimizations)  
   - [3.12. \[storage\] — NVMe IOPOLL, Multi-Queue Schedulers & Writeback](#312-storage--nvme-iopoll-multi-queue-schedulers--writeback)  
   - [3.13. \[power\] — Energy Models, Idle Governors (TEO) & RCU Lazy](#313-power--energy-models-idle-governors-teo--rcu-lazy)  
   - [3.14. \[network\] — BBR Congestion, CAKE/FQ Qdiscs & Protocol Engines](#314-network--bbr-congestion-cakefq-qdiscs--protocol-engines)  
   - [3.15. \[modules\] — Streamlined localmodconfig & modprobed-db Safety Nets](#315-modules--streamlined-localmodconfig--modprobed-db-safety-nets)  
   - [3.16. \[boot\] — Command-Line Injection & Bootloader Synchronization](#316-boot--command-line-injection--bootloader-synchronization)  
   - [3.17. \[verify\] — Invariant Contract Enforcement](#317-verify--invariant-contract-enforcement)  
   - [3.18. \[dusky\] — Seed Configurations & Runtime Dispatch](#318-dusky--seed-configurations--runtime-dispatch)  
4. [Cross-Subsystem Incompatibilities & Conflict Rules](#4-cross-subsystem-incompatibilities--conflict-rules)  
5. [Production Configuration Templates (TOML Profiles)](#5.-production-configuration-templates-\(toml-profiles\))  
   - [Template 1: Sub-300MB Idle Minimalist (Low-RAM / Embedded)](#template-1-sub-300mb-idle-minimalist-low-ram--embedded)  
   - [Template 2: Unconstrained Maximum Performance (Gaming / Workstation)](#template-2-unconstrained-maximum-performance-gaming--workstation)  
   - [Template 3: Maximum Battery Endurance (Laptops / Handhelds)](#template-3-maximum-battery-endurance-laptops--handhelds)  
   - [Template 4: The Golden Ratio (Daily Driver Workstation & Laptop)](#template-4-the-golden-ratio-daily-driver-workstation--laptop)  
6. [Compilation & Operational Workflow](#6-compilation--operational-workflow)

---

## 1\. Executive Summary & Core Architecture

`dusky_kernal_compile.py` is a specialized kernel compilation and optimization engine designed for Arch Linux running on the cutting-edge Linux 7.2 and 7.3-rc kernel series. It eliminates generic distribution bloat by bridging compile-time hardware tailoring (`scripts/config`, LLVM ThinLTO, `modprobed-db`) with boot-time system tuning (`systemd`, `udev`, `sysctl`, `zram-generator`).

### The Pipeline Architecture

1. **Host Discovery & Hardware Profiling**: Evaluates CPU microarchitecture, psABI levels (x86-64-v1 through v4), Last-Level Cache (LLC) topology, ACPI CPPC capabilities, disk controller queues, and GPU drivers.  
2. **Deterministic Source Resolution**: Fetches signed releases from kernel.org or tracking branches, validates cryptographic PGP keys or SHA256 hashes, and extracts clean sources.  
3. **Out-of-Tree Scheduler & Timer Injections**: Conditionally applies monolithic patches (such as BORE or Project C BMQ) and patches `kernel/Kconfig.hz` to introduce non-standard desktop tick rates (500 Hz, 600 Hz, 750 Hz).  
4. **Seed Ingestion & localmodconfig Pruning**: Seeds from running configs, Arch Linux packaging defaults, or snapshots, and runs `make localmodconfig` using `modprobed.db` with path-based safety overrides (`LMC_KEEP`).  
5. **Declarative Kconfig Matrix Application**: Applies atomic configuration batches via `scripts/config`, resolves dependencies with `make olddefconfig`, and strictly validates invariant contracts before invoking the compiler.  
6. **LLVM/Clang Native Compilation**: Compiles the kernel and modules with ThinLTO or monolithic Full LTO, generating twin Arch Linux native packages (`linux-dusky-<flavor>` and `linux-dusky-<flavor>-headers`).  
7. **Runtime & Bootloader Provisioning**: Deploys per-flavor sysctls, udev rules, NTSync device nodes, ZRAM multi-compression recompression timers, and updates systemd-boot, GRUB, or Limine entries.

---

## 2\. The Kernel Optimization Trilemma

Every kernel customization decision navigates three competing architectural forces:

                  \[1\] Responsiveness & Low Latency

                   (Frametime Pacing, 1000Hz, Audio)

                                 /                                /                                 /                                  /                                   /   ⚙                                /                                     /              \[2\] Compute Throughput  /\_\_\_\_\_\_\_\_\_\_\_\_\_\_\\  \[3\] Resource & Power Footprint

  (Batch Processing, LTO,                  (Sub-300MB Idle RAM, C10 States,

   mTHP, Unrolled Loops)                    SLUB\_TINY, RCU Lazy)

1. **Responsiveness vs. Throughput**: Forcing an interrupt tick rate of 1000 Hz or running full preemption (`PREEMPT_FULL`) guarantees immediate response to incoming input events and eliminates audio buffer underruns. However, it constantly interrupts active CPU execution pipelines and evicts L1/L2 caches, causing a 3% to 8% penalty in raw multi-core batch processing (such as code compilation or video encoding).  
2. **Throughput vs. Memory Footprint**: Transparent Huge Pages (`THP=always`) and multi-size THP (mTHP) drastically reduce Translation Lookaside Buffer (TLB) misses, accelerating game render loops and JVM runtimes. However, allocating 2MB blocks for small data structures creates severe internal fragmentation, inflating memory footprint and destabilizing systems with 4GB–8GB of RAM.  
3. **Responsiveness vs. Battery Endurance**: Immediate frequency scaling (`performance` governor, `EPP=performance`) and high tick rates wake CPU cores hundreds of times per second. This prevents CPU packages from settling into deep low-power sleep states (C8/C10), increasing idle power draw by 1.5W to 4W on mobile platforms.

---

## 3\. Comprehensive Section & Parameter Reference

### 3.1. \[meta\] — Package Metadata & Hardware Portability

Governs packaging identity, bootloader naming, and hardware targeting scope.

* **`name`** *(String)*: Base package name passed to `PACMAN_PKGBASE` (e.g., `"dusky_personal"`, `"gaming"`). Defines the package filename and directory paths under `/usr/lib/modules/`.  
* **`description`** *(String)*: Informational metadata embedded into the package header, viewable via `pacman -Qi`.  
* **`suffix`** *(String)*: Unique identifier appended to `LOCALVERSION` and pacman packages (e.g., `"dusky-gaming"` creates `linux-dusky-gaming` and `/boot/vmlinuz-linux-dusky-gaming`). Isolates out-of-tree DKMS modules from stock distribution kernels.  
* **`priority`** *(Integer, 1–100)*: Determines sorting precedence when multiple profiles match host telemetry and orders bootloader entries.  
* **`tags`** *(List of Strings)*: Metadata labels (e.g., `["gaming", "laptop", "low-ram"]`) used for filtering and search.  
* **`bare_metal_only`** *(Boolean)*:  
  - `true`: Completely compiles out hypervisor and virtualization guest drivers (`CONFIG_HYPERVISOR_GUEST`, `CONFIG_PARAVIRT`, `CONFIG_KVM_GUEST`, `CONFIG_XEN`, `CONFIG_VIRTIO*`). Reduces static kernel size and eliminates indirect hypercall overhead.  
  - `false`: Retains paravirtualization guest awareness.  
  - *Conflict*: If `true`, the resulting kernel will panic with `VFS: Unable to mount root fs` if booted inside QEMU/KVM, VirtualBox, Proxmox, or cloud instances.  
* **`portable_package`** *(Boolean)*:  
  - `true`: Targets a generic microarchitecture baseline (`generic_v3`) for distribution to multiple machines.  
  - `false`: Compiles strictly for the host CPU using native instructions.  
  - *Conflict*: `portable_package = true` strictly forbids `cpu.arch = "native"`. Building with AVX-512 or AVX2 instructions on a modern host and booting on an older machine causes an immediate `#UD` (Invalid Opcode) kernel panic.

---

### 3.2. \[release\] — Source Channels & Version Enforcement

Specifies upstream source acquisition, branch policies, and cryptographic verification.

* **`channel`** *(Choice: `"mainline"`, `"stable"`, `"longterm"`)*:  
  - `"mainline"`: Tracks Linus Torvalds’ active development tree, enabling access to 7.3-rc snapshots.  
  - `"stable"`: Tracks official point releases (e.g., 7.2.y). Recommended for production daily drivers.  
  - `"longterm"`: Tracks active Long Term Support branches.  
* **`pin`** *(String)*: Forces compilation of an exact version string (e.g., `"7.2.4"` or `"7.3-rc2"`), overriding channel discovery.  
* **`allow_rc`** *(Boolean)*: Toggles permission to download and build pre-release Release Candidate (`-rcX`) tarballs. Automatically enabled if `channel = "mainline"`.  
* **`min_version`** *(String)*: Hard version floor (default `"7.2"`). Aborts the build if an older tree is selected, preventing compilation failures on features requiring 7.2+ APIs (such as in-tree NTSync or Cache-Aware Scheduling).  
* **`require_signature`** *(Boolean)*: Enforces cryptographic PGP signature verification of downloaded kernel tarballs using kernel.org release keys (Torvalds, Kroah-Hartman, Levin) via GnuPG Web Key Directory (WKD). If signature verification fails or keys are missing, the build halts.

---

### 3.3. \[scheduler\] — CPU Scheduling Core (EEVDF, BORE, BMQ, sched\_ext)

Manages process runqueues, priority assignments, and thread dispatching across CPU cores.

* **`type`** *(Choice: `"eevdf"`, `"bore"`, `"bmq"`)*:  
  - `"eevdf"`: Upstream Earliest Eligible Virtual Deadline First. Manages task scheduling via two scalar metrics: *Eligibility Time* ($V(t) \\ge v\_i$) and *Virtual Deadline* ($d\_i \= v\_i \+ q\_i/w\_i$). Delivers excellent multi-core scaling and balanced throughput.  
  - `"bore"` *(Burst-Oriented Response Enhancer)*: Out-of-tree patch over EEVDF by FireLzrd. Measures thread burstiness (ratio of execution time to sleep time). Interactive tasks (compositors, input handlers, audio threads) receive burst score 0 and deadline priority boosts, while background batch tasks are penalized, eliminating micro-stutter under heavy compilation or rendering.  
  - `"bmq"` *(BitMap Queue)*: Project C scheduler by Alfred Chen. Replaces red-black trees with static priority array bitmapped queues. Delivers deterministic $O(1)$ dispatching on low-core-count machines ($\\le 16$ threads), but lacks upstream cgroup scalability.  
* **`scx`** *(Choice: `"none"`, `"scx_lavd"`, `"scx_bpfland"`, `"scx_layered"`, `"scx_rusty"`, `"scx_flash"`, `"scx_p2dq"`, `"scx_cosmos"`)*:  
  - Dynamically loads a user-space/eBPF scheduler daemon on top of `sched_ext`:  
    - `"scx_lavd"`: Criticality-aware virtual deadline scheduler tailored for gaming and handhelds. Compacts tasks to conserve battery under low load and spreads across cores when frametimes drop.  
    - `"scx_bpfland"`: vruntime-based scheduler that evaluates voluntary context switches to prioritize interactive desktop tasks.  
    - `"scx_layered"`: Meta's production scheduler. Partitions tasks into distinct cgroup layers with custom execution policies.  
    - `"scx_p2dq"`: Pick-2 dispatch queue scheduler with strong Last-Level Cache (LLC) awareness.  
    - `"scx_cosmos"`: Minimalist scheduler focused on high cache affinity and reduced TLB shootdowns.  
* **`scx_flags`** *(String)*: Command-line parameters passed directly to the SCX daemon (e.g., `"--autopilot"`, `"-m performance"`).  
* **`scx_enable_class`** *(Boolean)*: Compiles `CONFIG_SCHED_CLASS_EXT=y`. Embeds the BPF scheduling class into the core scheduling hierarchy immediately above the fair class. Requires `CONFIG_DEBUG_INFO_BTF=y`.  
* **`require_patch`** *(Boolean)*: Halts the build immediately if an out-of-tree scheduler patch (BORE or BMQ) fails to apply cleanly.  
* **`allow_vanilla_fallback`** *(Boolean)*: If a patch fails due to source changes in rapid `-rc` kernels, cleanly discards the rejected patch and falls back to vanilla upstream EEVDF.  
* **`autogroup`** *(Boolean)*: `CONFIG_SCHED_AUTOGROUP=y`. Automatically organizes tasks by TTY terminal session into dedicated scheduling cgroups, preventing background compilation commands (`make -j64`) from starving desktop UI responsiveness.  
* **`rt_group`** *(Boolean)*: `CONFIG_RT_GROUP_SCHED`. Enforces bandwidth limits on real-time threads (typically throttling to 95%).  
  - *Recommendation*: Set to `false` on audio workstations and gaming systems. Throttling can cause PipeWire/JACK buffer underruns (Xruns) under heavy loads.  
* **`sched_core`** *(Boolean)*: `CONFIG_SCHED_CORE=y`. Enforces SMT core scheduling via security cookies to prevent side-channel data leaks across sibling threads. Introduces a 10% to 25% multi-threaded throughput penalty; should be disabled on single-user gaming systems.  
* **`patch_sources`** *(List of Strings)*: Ordered list of mirrors and repositories (e.g., CachyOS, upstream git) to fetch scheduler patches from.

---

### 3.4. \[cache\] — Cache-Aware Scheduling (CAS) & LLC Domains

Configures Linux 7.2+ Cache-Aware Scheduling (`CONFIG_SCHED_CACHE`) to optimize task placement based on Last-Level Cache (L3/LLC) topology.

* **`sched_cache`** *(Boolean)*: Activates `CONFIG_SCHED_CACHE`. Directs the scheduler to track task memory footprints (`mm_struct`) and bias load balancing toward co-locating cooperating threads within the same physical CCX/L3 cache cluster.  
* **`llc_aggr_tolerance`** *(Integer, 0–100)*:  
  - `0`: Disables task aggregation.  
  - `1` (Strict): Aggregates tasks only if their combined Resident Set Size (RSS) fits completely within the physical capacity of a single L3 cache (e.g., 32MB). Prevents L3 cache thrashing.  
  - `2`–`100` (Relaxed): Permits aggressive thread co-location even if memory footprints exceed single-cache limits. Ideal for synchronization-bound multithreaded software.  
* **`llc_aggr_cap`** *(Integer, \-1–100)*: Percentage cap of runqueue depth on an LLC domain before spilling tasks to neighboring CCXs. `-1` uses the kernel default.  
* **`persist`** *(Boolean)*: Installs a boot-time systemd service to write CAS parameters to `/sys/kernel/debug/sched/` across reboots.

---

### 3.5. \[rseq\] — Restartable Sequences Time-Slice Extensions

Optimizes user-space per-CPU lockless memory structures via the `rseq` ABI.

* **`slice_extension`** *(Boolean)*: Enables `CONFIG_RSEQ_SLICE_EXTENSION`. Allows a user-space thread executing inside an atomic per-CPU sequence (e.g., jemalloc or glibc heap allocators) to request a short preemption delay when its timeslice expires.  
* **`slice_ext_nsec`** *(Integer, 1000–100000)*: Duration in nanoseconds (default `10000` \= 10 µs) of the preemption extension. Prevents lock-holder preemption without letting threads monopolize CPU cores.

---

### 3.6. \[cpu\] — Microarchitecture, P-State Autonomy & Mitigations

Directs CPU compiler optimizations, frequency scaling drivers, and hardware security mitigations.

* **`arch`** *(Choice: `"native"`, `"generic_v1"` through `"generic_v4"`, or specific architectures like `"znver4"`, `"alderlake"`)*:  
  - `"native"`: Uses `-march=native`. Emits AVX2, AVX-512, BMI2, and host-specific instruction sets. Produces the fastest execution on physical hardware.  
  - `"generic_v3"`: Baseline for AVX2/BMI2. The recommended target for portable packages.  
* **`march`** *(String)*: Custom compiler flag override appended to `KCFLAGS`.  
* **`governor`** *(Choice: `"schedutil"`, `"performance"`, `"powersave"`, `"ondemand"`, `"conservative"`)*:  
  - `"schedutil"`: Integrates with scheduler PELT signals. Default recommendation for EEVDF and desktop setups.  
  - `"performance"`: Pins CPU frequencies to maximum non-boost/boost states.  
  - `"powersave"`: On `amd_pstate=active` and `intel_pstate`, delegates dynamic scaling to hardware autonomous EPP registers rather than running at minimum clock speeds.  
* **`amd_pstate`** *(Choice: `"active"`, `"guided"`, `"passive"`, `"disable"`, `"undefined"`)*:  
  - `"active"`: Enables autonomous CPPC hardware frequency management (`amd-pstate-epp`). Frequency adjustments occur in sub-millisecond hardware intervals rather than 10ms OS polling loops.  
  - `"guided"`: The kernel provides operating bounds; firmware chooses frequencies within that range.  
  - `"passive"`: The OS governor explicitly calculates target frequencies via traditional CPPC interfaces.  
* **`epp`** *(Choice: `"default"`, `"performance"`, `"balance_performance"`, `"balance_power"`, `"power"`)*:  
  - Energy-Performance Preference register hint.  
  - Desktop/Gaming recommendation: `"balance_performance"` or `"performance"`.  
  - Battery recommendation: `"power"` or `"balance_power"`.  
* **`mitigations`** *(Choice: `"on"`, `"off"`, `"nosmt"`)*:  
  - `"on"`: Full hardware vulnerability mitigations active (Spectre, Meltdown, Retbleed, Downfall, Zenbleed).  
  - `"off"`: Compiles out mitigations and passes `mitigations=off`. Bypasses Page Table Isolation (KPTI), IBPB, and return trampolines, recovering 5% to 15% compute and I/O throughput on trusted single-user systems. Requires `security.acknowledge_risk = true`.  
  - `"nosmt"`: Disables SMT/Hyper-Threading siblings to seal cross-thread speculative side channels.  
* **`nr_cpus`** *(Integer, 0–8192)*: Clamps `CONFIG_NR_CPUS`. Setting `0` auto-detects the host thread count rounded up to the nearest 8\. Reduces per-CPU static memory overhead and array sizes compared to distribution kernels built for 512+ cores.  
* **`smt`** *(Boolean)*: Keeps SMT/Hyper-Threading enabled (`CONFIG_SCHED_SMT=y`).  
* **`mce`** *(Boolean)*: Machine Check Exception handling (`CONFIG_X86_MCE`). Catches hardware memory parity and CPU bus errors.  
* **`prefcore`** *(Boolean)*: Enables AMD Preferred Core / Intel ITMT (`CONFIG_SCHED_MC_PRIO`). Instructs the scheduler to route high-priority single-threaded tasks to the highest-binned silicon cores.  
* **`compat32`** *(Boolean)*: `CONFIG_IA32_EMULATION`. Enables 32-bit application execution. Required for Steam runtime compatibility, older games, and Wine/Proton 32-bit wrappers.

---

### 3.7. \[timing\] — Clock Cadence, PREEMPT\_LAZY & Tickless Modes

Governs interrupt frequencies, preemption boundaries, and timer tick suppression.

* **`hz`** *(Choice: `100`, `250`, `300`, `500`, `600`, `750`, `1000`)*:  
  - `100`: Maximum compute throughput. Minimal timer interrupts; unsuitable for interactive desktops.  
  - `250`: Upstream enterprise default.  
  - `300`: Synchronizes with standard video framerates (24, 30, 60, 120 FPS).  
  - `500`: Balanced sweet spot for desktop responsiveness and energy efficiency.  
  - `600`: Aligns with high-refresh display panels (60, 120, 240, 300 Hz).  
  - `1000`: Minimum input latency and fastest thread wakeups. Increases power draw and context-switch frequency.  
* **`tickless`** *(Choice: `"periodic"`, `"idle"`, `"full"`)*:  
  - `"idle"` (`CONFIG_NO_HZ_IDLE`): Suppresses timer ticks on idle cores, allowing CPUs to enter deep package C-states. Active cores tick at the configured HZ rate. Recommended for desktops and laptops.  
  - `"full"` (`CONFIG_NO_HZ_FULL`): Suppresses ticks on cores running a single active task. Requires setting `nohz_full=<cpus>` on the kernel command line. Increases kernel-entry context overhead and is beneficial only for isolated real-time/HPC workloads.  
  - `"periodic"`: Ticks continuously; legacy debug setting.  
* **`preempt`** *(Choice: `"lazy"`, `"full"`, `"rt"`)*:  
  - `"lazy"` (`CONFIG_PREEMPT_LAZY`): The default preemption model in Linux 7.2+. Introduces dual preemption flags: $$	ext{Preemption Requests} ightarrow egin{cases} \\mathbf{TIF\_NEED\_RESCHED} & 	ext{(Urgent: RT / High-priority wakes preempt immediately)} \\ \\mathbf{TIF\_NEED\_RESCHED\_LAZY} & 	ext{(Normal: Fair tasks run to slice boundaries)} \\end{cases}$$ Delivers the raw throughput of `PREEMPT_VOLUNTARY` with the interactive responsiveness of `PREEMPT_FULL`.  
  - `"full"` (`CONFIG_PREEMPT`): Preempts any non-critical kernel execution path. Yields low latency, but costs 2% to 4% in raw throughput due to frequent cache evictions.  
  - `"rt"` (`CONFIG_PREEMPT_RT`): Deterministic hard real-time preemption. Converts spinlocks into sleeping mutexes. Used for industrial control and low-latency pro audio; reduces overall system throughput.  
* **`preempt_dynamic`** *(Boolean)*: `CONFIG_PREEMPT_DYNAMIC`. Allows changing preemption behavior at boot (`preempt=lazy|full|voluntary`) or at runtime via `/sys/kernel/debug/sched/preempt`.

---

### 3.8. \[memory\] — Paging, Multi-Gen LRU, Compressed Swap & Footprint Tiers

Configures virtual memory reclamation, allocation algorithms, page sizing, and compressed memory storage.

* **`footprint`** *(Choice: `"standard"`, `"lean"`, `"minimal"`, `"embedded"`)*:  
  - `"standard"`: Full distribution-grade features.  
  - `"lean"`: Optimized for $\\le 8	ext{ GB}$ systems. Strips debugfs, tracing, kexec, and legacy cgroup v1 interfaces.  
  - `"minimal"`: Optimized for $\\le 4	ext{ GB}$ targets. Enables `CONFIG_SLUB_TINY`, disables hugetlbfs, strips kallsyms, and configures DAMON proactive reclamation.  
  - `"embedded"`: Strips IA32 emulation, core dumps, and hibernation, using `CONFIG_BASE_SMALL=1` for a minimal memory footprint.  
* **`thp`** *(Choice: `"always"`, `"madvise"`, `"never"`)*:  
  - `"always"`: Backs all anonymous memory with 2MB huge pages. Reduces TLB misses in compute tasks, but increases memory usage via internal fragmentation.  
  - `"madvise"`: Allocates 2MB huge pages only when explicitly requested via `madvise(MADV_HUGEPAGE)`. Best balance for desktop and gaming.  
  - `"never"`: Enforces strict 4KB paging across all allocations. Essential for sub-300MB idle RAM profiles.  
* **`thp_defrag`** *(Choice: `"always"`, `"defer"`, `"defer+madvise"`, `"madvise"`, `"never"`)*:  
  - `"defer+madvise"`: Wakes `kcompactd` in the background for general memory while allowing direct compaction for `MADV_HUGEPAGE` regions. Avoids synchronous frame-time stalls during gaming.  
* **`thp_shmem`** *(Choice: `"always"`, `"within_size"`, `"advise"`, `"never"`)*: Configures huge page backing for shared memory (`tmpfs` and `/dev/shm`).  
* **`mglru`** *(Boolean)*: `CONFIG_LRU_GEN=y`. Replaces the legacy two-list (Active/Inactive) LRU page reclamation algorithm with Multi-Gen LRU. Uses generational aging and page-table walking to dramatically reduce CPU reclaim overhead and eliminate low-memory thrashing.  
* **`mglru_mask`** *(Integer, 0–7)*: Feature bitmask (`0x0001` \= anon, `0x0002` \= file, `0x0004` \= page-table walking). `7` enables all paths.  
* **`mglru_min_ttl_ms`** *(Integer, 0–60000)*: Minimum generational Time-To-Live in milliseconds. Protects active working sets from eviction during transient memory spikes.  
* **`swap_backend`** *(Choice: `"zram"`, `"zswap"`, `"none"`)*:  
  - `"zram"`: Creates a compressed RAM block device (`/dev/zram0`) formatted as swap space. Delivers microsecond access times and avoids disk wear.  
  - `"zswap"`: Compressed write-through cache sitting in front of a physical disk swap partition.  
  - `"none"`: Disables swap entirely.  
* **`zram_algo`** *(Choice: `"zstd"`, `"lz4"`, `"lz4hc"`, `"lzo-rle"`)*: Primary compression algorithm for ZRAM. `lz4` provides maximum throughput; `zstd` delivers a 2.7:1–3.2:1 compression ratio.  
* **`zram_recomp_algo`** *(Choice: `"zstd"`, `"lz4"`, `"lz4hc"`, `"lzo-rle"`)*: Secondary algorithm used for idle-page recompression (e.g., recompressing cold pages with `zstd` level 9–11).  
* **`zram_size_pct`** *(Integer, 10–400)*: Virtual swap device capacity as a percentage of physical RAM (typically `100` to `200`).  
* **`zram_multi_comp`** *(Boolean)*: `CONFIG_ZRAM_MULTI_COMP`. Enables tiered multi-compression streams in ZRAM.  
* **`swappiness`** *(Integer, 0–200)*: Controls kernel swap bias. Set to `150`–`180` for ZRAM to prioritize compressing cold anonymous memory over evicting filesystem caches.  
* **`vfs_cache_pressure`** *(Integer, 0–1000)*: Controls reclamation of dentry and inode caches. Lower values (`50`–`70`) retain filesystem metadata in RAM, speeding up repeated directory lookups.  
* **`watermark_scale_factor`** *(Integer, 10–3000)*: Sets the distance between `WMARK_MIN`, `WMARK_LOW`, and `WMARK_HIGH`. Raising to `125`–`200` wakes `kswapd` earlier under allocation pressure, preventing synchronous direct reclaim stalls.  
* **`watermark_boost_factor`** *(Integer, 0–30000)*: Set to `0` on gaming and ZRAM setups to prevent erratic reclaim bursts triggered by high-order allocation failures.  
* **`compaction_proactiveness`** *(Integer, 0–100)*: Determines how proactively `kcompactd` consolidates memory blocks in the background.  
* **`dirty_bytes_mb`** *(Integer)*: Sets hard byte limits for dirty memory writeback (e.g., `128` or `256` MB), preventing I/O stalls caused by percentage-based defaults on high-RAM machines.  
* **`slub_tiny`** *(Boolean)*: `CONFIG_SLUB_TINY`. Strips per-CPU partial slab lists and allocator debugging, saving 20MB–60MB of RAM.  
* **`slab_buckets`** *(Boolean)*: `CONFIG_SLAB_BUCKETS`. Isolates slab allocations into separate memory buckets to mitigate heap spraying. Incompatible with `slub_tiny`.  
* **`per_vma_lock`** *(Boolean)*: `CONFIG_PER_VMA_LOCK`. Enables per-VMA read/write locks for concurrent page-fault processing.  
* **`numa`** *(Boolean)*: Toggles NUMA support. Disabling on single-socket desktops eliminates per-node tracking arrays and saves 15MB–35MB of static memory.  
* **`ksm`** *(Boolean)*: `CONFIG_KSM`. Enables Kernel Samepage Merging memory deduplication.  
* **`damon`** *(Boolean)*: `CONFIG_DAMON`. Enables the Data Access Monitor framework for proactive memory management.  
* **`hugetlbfs`** *(Boolean)*: Controls HugeTLB filesystem support.  
* **`kallsyms_all`** *(Boolean)*: Setting to `false` removes non-exported symbols from the kernel binary, saving 4MB–10MB of unevictable `.rodata` memory.  
* **`memcg`** *(Boolean)*: Controls the cgroup memory resource controller. Disabling saves 30MB–70MB of base RAM, but disables `systemd-oomd`.  
* **`trim_unused_ksyms`** *(Boolean)*: `CONFIG_TRIM_UNUSED_KSYMS`. Drops unreferenced exported symbols from `.rodata`. Requires `compiler.headers = "never"`.

---

### 3.9. \[compiler\] — LLVM/Clang, ThinLTO Caching, kCFI & Rust-for-Linux

Governs the compilation toolchain, Link-Time Optimization, exploit mitigations, and cross-language modules.

* **`toolchain`** *(Choice: `"llvm"`, `"gcc"`)*:  
  - `"llvm"`: Uses Clang, LLD, and LLVM binary utilities (`make LLVM=1`). Required for ThinLTO, kCFI, AutoFDO, and Rust cross-language optimization.  
  - `"gcc"`: Uses GNU GCC and Binutils.  
* **`optimize`** *(Choice: `"o2"`, `"o3"`, `"size"`)*:  
  - `"o2"`: Upstream standard optimization level.  
  - `"o3"`: Injects aggressive loop unrolling and vectorization via `KCFLAGS`.  
  - `"size"` (`-Os`): Strips alignment padding and inlining to minimize binary size.  
* **`lto`** *(Choice: `"none"`, `"thin"`, `"full"`)*:  
  - `"none"`: Fast compilation without inter-module link-time optimization.  
  - `"thin"` (`CONFIG_LTO_CLANG_THIN`): Parallel Link-Time Optimization using module summaries. Delivers \~95–99% of Full LTO performance with faster link times and modest RAM usage (3GB–6GB).  
  - `"full"` (`CONFIG_LTO_CLANG_FULL`): Monolithic Whole-Program LTO. Merges intermediate representation across the entire kernel before code generation. Requires 16GB–32GB of RAM during linking.  
* **`thinlto_cache`** *(Boolean)*: Directs `ld.lld` to persist backend compilation artifacts across builds.  
* **`thinlto_cache_size_gb`** *(Integer)*: Maximum storage cap for cached ThinLTO compilation objects.  
* **`fdo`** *(Choice: `"none"`, `"autofdo"`, `"autofdo_propeller"`)*:  
  - `"autofdo"`: Profile-Guided Optimization using hardware PMU branch samples via `perf`.  
  - `"autofdo_propeller"`: Uses basic block sections and `ld.lld` to reorder basic blocks and optimize code layout across the binary.  
* **`kcfi`** *(Boolean)*: `CONFIG_CFI_CLANG`. Enforces forward-edge Control Flow Integrity by embedding 4-byte type hashes before indirect call targets.  
* **`debug_info`** *(Choice: `"none"`, `"reduced"`, `"full"`)*:  
  - `"none"`: Strips DWARF debug symbols.  
  - `"reduced"`: Minimal debug information for basic backtraces.  
  - `"full"`: DWARF5 debug symbols. Required for BTF metadata extraction via `pahole`.  
* **`module_compress`** *(Choice: `"zstd"`, `"xz"`, `"gzip"`, `"none"`)*: Post-build module compression format. `zstd` is recommended.  
* **`rust`** *(Boolean)*: `CONFIG_RUST=y`. Enables Rust support in the kernel. Automatically disabled if LTO and BTF are enabled simultaneously without a compatible `pahole` release.  
* **`headers`** *(Choice: `"auto"`, `"always"`, `"never"`)*: Policy for building the kernel headers package (`linux-headers`). `"auto"` builds headers only if active DKMS modules are detected on the host.  
* **`modversions`** *(Boolean)*: `CONFIG_MODVERSIONS`. Generates CRC checksums for exported symbols to enforce ABI compatibility.

---

### 3.10. \[security\] — Hardening Profiles, Allocator Defenses & Lockdown

Controls runtime kernel protection mechanisms, memory poisoning, and access control.

* **`profile`** *(Choice: `"balanced"`, `"extreme"`, `"hardened"`)*:  
  - `"balanced"`: Production desktop security with negligible performance overhead.  
  - `"extreme"`: Maximizes security mechanisms at the expense of throughput. Requires `acknowledge_risk = true`.  
  - `"hardened"`: Follows Kernel Self Protection Project (KSPP) baselines.  
* **`init_on_alloc`** *(Boolean)*: `CONFIG_INIT_ON_ALLOC_DEFAULT_ON`. Zeroes memory pages and slab allocations upon allocation. Prevents uninitialized memory disclosure vulnerabilities with a 1% to 3% performance cost.  
* **`init_on_free`** *(Boolean)*: `CONFIG_INIT_ON_FREE_DEFAULT_ON`. Clears memory blocks immediately when freed. Mitigates Use-After-Free (UAF) exploits, but carries a 5% to 12% performance cost and reduces cache warmth.  
* **`hardened_usercopy`** *(Boolean)*: `CONFIG_HARDENED_USERCOPY`. Validates memory bounds on `copy_to_user()` and `copy_from_user()` boundaries to prevent buffer overflows.  
* **`stackprotector`** *(Choice: `"strong"`, `"regular"`, `"none"`)*: Injects stack canaries to detect and prevent return address hijacking.  
* **`slab_freelist_hardened`** *(Boolean)*: `CONFIG_SLAB_FREELIST_HARDENED`. Obfuscates freelist pointers using XOR cookies.  
* **`slab_freelist_random`** *(Boolean)*: `CONFIG_SLAB_FREELIST_RANDOM`. Randomizes allocation order within newly allocated slab pages.  
* **`randomize_kstack`** *(Boolean)*: `CONFIG_RANDOMIZE_KSTACK_OFFSET_DEFAULT`. Adds a random offset to the kernel stack address on each system call.  
* **`ubsan_bounds`** *(Boolean)*: `CONFIG_UBSAN_BOUNDS`. Compiles in runtime bounds checking for array indexing.  
* **`apparmor`** *(Boolean)*: Enables the AppArmor pathname-based Mandatory Access Control LSM.  
* **`selinux`** *(Boolean)*: Enables the SELinux security module.  
* **`lockdown_early`** *(Boolean)*: `CONFIG_SECURITY_LOCKDOWN_LSM_EARLY`. Restricts root access to running kernel memory from early boot. Incompatible with unsigned proprietary modules and debuggers.  
* **`acknowledge_risk`** *(Boolean)*: Explicit safety confirmation required when using `profile = "extreme"` or `cpu.mitigations = "off"`.

---

### 3.11. \[gaming\] — NTSync Driver, UCLAMP & Wine/Proton Optimizations

Tuned specifically for low-latency gaming and running Windows titles via Wine and Proton.

* **`ntsync`** *(Boolean)*: Compiles the in-tree `ntsync` driver (`CONFIG_NTSYNC=y` or `=m`) and provisions `/dev/ntsync`. Implements native Windows NT synchronization primitives (mutexes, semaphores, events) directly in the kernel, eliminating `wineserver` IPC overhead and improving frame pacing in multithreaded DirectX 11/12 games.  
* **`uclamp`** *(Boolean)*: Enables Utilization Clamping (`CONFIG_UCLAMP_TASK=y`). Allows assigning utilization floors (`uclamp.min`) to game render loops, prompting CPU frequency governors to ramp up instantly without waiting for PELT load accumulation.  
* **`max_map_count`** *(Integer)*: Sets `vm.max_map_count` to `2147483642` (`MAX_INT - 5`). Prevents memory allocation crashes in memory-intensive Windows games, modern anti-cheat modules, and DirectX 12 translation layers.  
* **`split_lock_mitigate`** *(Boolean)*:  
  - `true`: Penalizes threads that issue unaligned atomic instructions across cache lines by forcing sleep delays.  
  - `false`: Disables the artificial sleep penalty, eliminating severe 10ms–20ms frametime spikes in games and emulation software.  
* **`controllers`** *(Boolean)*: Bundles in-tree drivers and force-feedback support for Xbox, PlayStation (DualShock/DualSense), Nintendo Switch, and Steam controllers.

---

### 3.12. \[storage\] — NVMe IOPOLL, Multi-Queue Schedulers & Writeback

Configures the block I/O layer and physical storage handling.

* **`nvme_poll_queues`** *(Integer, 0–128)*: Sets `nvme.poll_queues`. Allocates dedicated completion queues for interrupt-free polling via `io_uring` (`IORING_SETUP_IOPOLL`). Reduces I/O latency to sub-2 µs on high-end NVMe drives at the cost of 100% CPU thread utilization during active polling.  
* **`io_scheduler`** *(Choice: `"none"`, `"mq-deadline"`, `"bfq"`, `"kyber"`, `"keep"`)*:  
  - `"none"`: Bypasses software queuing. Recommended for high-speed NVMe drives.  
  - `"mq-deadline"`: Deadline-based queue sorting. Ideal for single-queue SATA SSDs.  
  - `"bfq"`: Budget Fair Queueing. Prioritizes interactive UI responsiveness on mechanical HDDs.  
  - `"kyber"`: Lightweight latency-bounded scheduler designed for fast flash storage.  
* **`blk_wbt`** *(Boolean)*: `CONFIG_BLK_WBT=y`. Throttles background write operations when read latencies exceed target thresholds to mitigate storage bufferbloat.  
* **`iocost`** *(Boolean)*: Proportional I/O control model for cgroup v2.  
* **`extra_filesystems`** *(List of Strings)*: Additional filesystems compiled into the kernel image (e.g., `["btrfs", "f2fs", "xfs"]`).

---

### 3.13. \[power\] — Energy Models, Idle Governors (TEO) & RCU Lazy

Optimizes power dissipation, battery longevity, and processor sleep states.

* **`wq_power_efficient`** *(Boolean)*: `CONFIG_WQ_POWER_EFFICIENT_DEFAULT=y`. Routes unbound workqueues to active CPU cores, allowing idle cores to stay in deep low-power C-states.  
* **`cpu_idle_governor`** *(Choice: `"teo"`, `"menu"`, `"haltpoll"`)*:  
  - `"teo"` (Timer Events Oriented): Evaluates upcoming timer deadlines using integer heuristics. Designed for tickless client desktop and laptop workloads.  
  - `"menu"`: Predictive model tailored for enterprise servers with sustained, predictable workloads.  
  - `"haltpoll"`: Used strictly inside virtual machines; consumes full CPU power on physical hardware.  
* **`rcu_lazy`** *(Boolean)*: `CONFIG_RCU_LAZY=y`. Batches non-urgent memory-freeing RCU callbacks for up to 10 seconds. Prevents timer interrupts on idle cores and reduces idle power draw on mobile systems by 5% to 15%.  
* **`energy_model`** *(Boolean)*: `CONFIG_ENERGY_MODEL=y`. Exposes hardware power and efficiency tables to the scheduler on heterogeneous hybrid CPUs (such as Intel P/E-cores or ARM big.LITTLE).  
* **`suspend`** *(Boolean)*: Enables suspend-to-RAM (`s2idle` / `deep`).  
* **`hibernation`** *(Boolean)*: Enables suspend-to-disk (`CONFIG_HIBERNATION=y`).  
* **`pcie_aspm`** *(Choice: `"default"`, `"powersave"`, `"powersupersave"`, `"performance"`)*: Active State Power Management policy for PCIe links. Setting `"powersave"` or `"powersupersave"` activates PCIe L1.1/L1.2 sub-states, reducing idle power draw for NVMe drives and network cards.  
* **`hda_power_save`** *(Integer)*: Inactivity timeout in seconds before powering down the audio controller. Setting to `1` or `2` saves 0.5W–1.5W of idle battery draw on laptops.

---

### 3.14. \[network\] — BBR Congestion, CAKE/FQ Qdiscs & Protocol Engines

Configures packet scheduling, transport protocols, and network fast paths.

* **`congestion`** *(Choice: `"bbr"`, `"cubic"`, `"reno"`)*:  
  - `"bbr"`: Bottleneck Bandwidth and Round-trip propagation time model. Maximizes throughput over lossy links and prevents bufferbloat by pacing packet transmission.  
  - `"cubic"`: Standard loss-based TCP congestion control algorithm.  
* **`qdisc`** *(Choice: `"fq"`, `"cake"`, `"fq_codel"`, `"fq_pie"`, `"pfifo_fast"`)*:  
  - `"fq"`: Socket-level pacing discipline. Essential pairing for BBR.  
  - `"cake"`: Advanced Active Queue Management (AQM) featuring per-host fairness and framing overhead compensation.  
  - `"fq_codel"`: Upstream standard fair-queuing discipline.  
* **`mptcp`** *(Boolean)*: Enables Multipath TCP (`CONFIG_MPTCP=y`), allowing connections to bond across multiple network interfaces.  
* **`xdp`** *(Boolean)*: Enables eXpress Data Path (`CONFIG_XDP_SOCKETS=y`) for high-throughput packet processing via eBPF.  
* **`nf_conntrack_procfs`** *(Boolean)*: Disabling removes the legacy `/proc/net/nf_conntrack` interface, eliminating lock contention and saving memory on high-throughput systems.  
* **`tcp_fastopen`** *(Boolean)*: Enables TCP Fast Open (`net.ipv4.tcp_fastopen=3`) for clients and servers to exchange data during initial handshakes.

---

### 3.15. \[modules\] — Streamlined localmodconfig & modprobed-db Safety Nets

Governs kernel module pruning to minimize compile times and binary footprint.

* **`mode`** *(Choice: `"strict"`, `"expanded"`)*:  
  - `"strict"`: Retains only hardware drivers present in the active `modprobed.db` profile or currently loaded in memory.  
  - `"expanded"`: Retains essential subsystem trees (`drivers/usb`, `drivers/gpu`, `sound`, `net/wireless`, `fs`) to ensure hotplugged devices work reliably.  
* **`modprobed_db`** *(Boolean)*: Passes historical hardware driver profiles to `localmodconfig` via the `LSMOD` environment variable.  
* **`modprobed_db_path`** *(String)*: Path to the `modprobed.db` file (defaulting to `~/.config/modprobed.db`).  
* **`allow_lsmod_fallback`** *(Boolean)*: Falls back to live `lsmod` if `modprobed.db` is missing or unpopulated.  
* **`lmc_keep_extra`** *(List of Strings)*: Directory paths preserved during `localmodconfig` pruning via the `LMC_KEEP` environment variable.  
* **`keep_symbols`** *(List of Strings)*: Explicit `CONFIG_*` driver symbols forced to `=m` after pruning.  
* **`localyesconfig`** *(Boolean)*: Compiles all active modules directly into the monolithic kernel image (`vmlinuz`) instead of producing modular `.ko` files.  
* **`manage_service`** *(Boolean)*: Automatically enables the `modprobed-db.service` user timer to ensure continuous background hardware logging.  
* **`sig_force`** *(Boolean)*: Enforces strict module signature verification (`CONFIG_MODULE_SIG_FORCE=y`), blocking unsigned out-of-tree modules.

---

### 3.16. \[boot\] — Command-Line Injection & Bootloader Synchronization

Handles command-line parameter baking and bootloader entry generation.

* **`cmdline`** *(Choice: `"bake"`, `"entry"`, `"print"`)*:  
  - `"bake"`: Compiles kernel parameters directly into the binary via `CONFIG_CMDLINE`. Tamper-resistant; recommended for Unified Kernel Images (UKIs).  
  - `"entry"`: Writes parameters to external bootloader entries (e.g., systemd-boot configuration files).  
  - `"print"`: Prints the recommended command-line parameters to the console without writing files.  
* **`cmdline_extra`** *(String)*: Custom command-line flags appended to the generated parameter string.  
* **`write_entries`** *(Boolean)*: Automatically installs or updates Boot Loader Specification (BLS) entries under `/boot/loader/entries/`.  
* **`nowatchdog`** *(Boolean)*: Disables software and hardware NMI watchdogs (`nowatchdog nmi_watchdog=0`), eliminating periodic timer interrupts to minimize system jitter.

---

### 3.17. \[verify\] — Invariant Contract Enforcement

Automated validation checks executed against the resolved `.config` before compilation begins.

* **`strict`** *(Boolean)*: If `true`, fails and halts the build if any non-optional Kconfig contract requirement is unsatisfied.  
* **`optional_symbols`** *(List of Strings)*: Symbols that emit non-fatal warnings rather than build failures if missing.  
* **`require_ntsync`** *(Boolean)*: Verifies that `CONFIG_NTSYNC` is compiled when `gaming.ntsync = true`.  
* **`require_btf`** *(Boolean)*: Asserts that `CONFIG_DEBUG_INFO_BTF=y` is present whenever `sched_ext` is enabled.  
* **`require_sched_ext`** *(Boolean)*: Asserts that `CONFIG_SCHED_CLASS_EXT=y` is enabled when an SCX scheduler is configured.

---

### 3.18. \[dusky\] — Seed Configurations & Runtime Dispatch

Internal orchestration settings managing baseline configuration sources and script behavior.

* **`enhanced`** *(Boolean)*: Enables desktop-focused optimizations (disabling deferred framebuffer takeover and turning off boot watchdogs).  
* **`seed`** *(Choice: `"auto"`, `"snapshot"`, `"arch"`, `"running"`, `"headers"`, `"defconfig"`)*:  
  - `"auto"`: Evaluates configuration sources in order: saved snapshot $ ightarrow$ Arch upstream packaging config $ ightarrow$ `/proc/config.gz` $ ightarrow$ installed headers $ ightarrow$ `defconfig`.  
* **`extra_config`** *(Table)*: Arbitrary key-value mappings directly injected into `.config` (e.g., `CONFIG_FOO = true`).  
* **`reproducible`** *(Boolean)*: Pins `KBUILD_BUILD_TIMESTAMP` and `SOURCE_DATE_EPOCH` to ensure byte-for-byte reproducible kernel builds.

---

## 4\. Cross-Subsystem Incompatibilities & Conflict Rules

When designing custom profiles, avoid these known subsystem conflicts:

┌───────────────────────────────────┬───────────────────────────────────┬────────────────────────────────────────────────────────┐

│ Setting A                         │ Setting B                         │ Conflict Mechanism & Resolution                        │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ compiler.toolchain \= "gcc"        │ compiler.lto \= "thin" / "full"    │ Clang ThinLTO requires LLVM. The script forces         │

│                                   │                                   │ compiler.lto \= "none" under GCC.                       │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ compiler.toolchain \= "gcc"        │ compiler.kcfi \= true              │ kCFI depends on Clang runtime instrumentation.          │

│                                   │                                   │ The script forces compiler.kcfi \= false.               │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ scheduler.type \= "bmq"            │ scheduler.scx \!= "none"           │ Project C BMQ replaces EEVDF completely. sched\_ext     │

│                                   │                                   │ requires EEVDF; the script forces scx \= "none".        │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ timing.preempt \= "rt"             │ timing.preempt\_dynamic \= true     │ PREEMPT\_RT replaces core spinlocks and does not        │

│                                   │                                   │ support dynamic preemption switching.                  │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ memory.slub\_tiny \= true           │ memory.slab\_buckets \= true        │ Hardened SLAB buckets depend on full SLUB allocator    │

│                                   │                                   │ multi-tier caches; incompatible with SLUB\_TINY.        │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ memory.trim\_unused\_ksyms \= true   │ compiler.headers \!= "never"       │ Trimming unreferenced symbols breaks out-of-tree       │

│                                   │                                   │ external DKMS module builds.                           │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ compiler.rust \= true              │ compiler.lto \!= "none" \+ BTF      │ LLVM bitcode mismatch between Clang and rustc can      │

│                                   │                                   │ break pahole BTF extraction during linking.            │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ memory.swap\_backend \= "zram"      │ zswap.enabled \= 1                 │ Causes an inefficient double-compression loop. ZSWAP   │

│                                   │                                   │ must be disabled when ZRAM swap is active.             │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ cpu.mitigations \= "off"           │ security.profile \= "hardened"     │ Direct security policy contradiction. The script       │

│                                   │                                   │ overrides cpu.mitigations to "on".                     │

├───────────────────────────────────┼───────────────────────────────────┼────────────────────────────────────────────────────────┤

│ meta.portable\_package \= true      │ cpu.arch \= "native"               │ Native compilation emits non-portable instructions.    │

│                                   │                                   │ The script rejects this configuration.                 │

└───────────────────────────────────┴───────────────────────────────────┴────────────────────────────────────────────────────────┘

---

## 5\. Production Configuration Templates (TOML Profiles)

### Template 1: Sub-300MB Idle Minimalist (Low-RAM / Embedded)

*Designed for 4GB–8GB RAM systems, lightweight netbooks, or headless workloads targeting minimal base memory usage.*

\[meta\]

name \= "minimal\_strict"

description \= "Sub-300MB idle footprint, SLUB\_TINY, stripped debugging, ZRAM"

suffix \= "dusky-minimal"

priority \= 31

bare\_metal\_only \= true

portable\_package \= false

\[release\]

channel \= "stable"

allow\_rc \= true

require\_signature \= true

\[scheduler\]

type \= "eevdf"

scx \= "none"

scx\_enable\_class \= false

autogroup \= true

rt\_group \= false

sched\_core \= false

\[cache\]

sched\_cache \= false

\[rseq\]

slice\_extension \= true

slice\_ext\_nsec \= 10000

\[cpu\]

arch \= "native"

governor \= "schedutil"

mitigations \= "on"

nr\_cpus \= 0

smt \= true

mce \= true

prefcore \= true

compat32 \= false

\[timing\]

hz \= 250

tickless \= "idle"

preempt \= "lazy"

preempt\_dynamic \= false

\[memory\]

footprint \= "minimal"

thp \= "never"

thp\_defrag \= "never"

mglru \= true

mglru\_mask \= 7

mglru\_min\_ttl\_ms \= 1000

swap\_backend \= "zram"

zram\_algo \= "zstd"

zram\_size\_pct \= 150

zram\_multi\_comp \= false

swappiness \= 180

vfs\_cache\_pressure \= 150

watermark\_scale\_factor \= 125

dirty\_bytes\_mb \= 64

slub\_tiny \= true

slab\_buckets \= false

per\_vma\_lock \= true

numa \= false

ksm \= false

damon \= true

hugetlbfs \= false

kallsyms\_all \= false

memcg \= true

base\_small \= true

log\_buf\_shift \= 15

tracing \= "minimal"

kexec \= false

ikconfig \= false

systemd\_oomd \= true

trim\_unused\_ksyms \= true

\[compiler\]

toolchain \= "llvm"

optimize \= "size"

lto \= "thin"

thinlto\_cache \= true

thinlto\_cache\_size\_gb \= 10

fdo \= "none"

kcfi \= false

debug\_info \= "none"

module\_compress \= "zstd"

rust \= false

headers \= "never"

modversions \= false

\[security\]

profile \= "balanced"

init\_on\_alloc \= true

init\_on\_free \= false

hardened\_usercopy \= true

stackprotector \= "strong"

slab\_freelist\_hardened \= false

slab\_freelist\_random \= false

randomize\_kstack \= true

ubsan\_bounds \= false

lockdown\_early \= false

acknowledge\_risk \= false

\[gaming\]

ntsync \= false

uclamp \= false

max\_map\_count \= 2147483642

split\_lock\_mitigate \= true

controllers \= false

\[storage\]

nvme\_poll\_queues \= 0

io\_scheduler \= "mq-deadline"

blk\_wbt \= true

iocost \= false

\[power\]

wq\_power\_efficient \= true

cpu\_idle\_governor \= "teo"

rcu\_lazy \= true

energy\_model \= false

suspend \= true

hibernation \= false

pcie\_aspm \= "powersave"

hda\_power\_save \= 1

\[network\]

congestion \= "bbr"

qdisc \= "fq\_codel"

mptcp \= false

xdp \= false

nf\_conntrack\_procfs \= false

tcp\_fastopen \= true

\[modules\]

mode \= "strict"

modprobed\_db \= true

allow\_lsmod\_fallback \= true

localyesconfig \= false

manage\_service \= true

sig\_force \= false

\[boot\]

cmdline \= "bake"

nowatchdog \= true

write\_entries \= true

\[verify\]

strict \= true

require\_ntsync \= false

require\_btf \= false

require\_sched\_ext \= false

---

### Template 2: Unconstrained Maximum Performance (Gaming / Workstation)

*Tuned for maximum throughput, low dispatch latency, and frame-time consistency in gaming and heavy multithreaded workloads.*

\[meta\]

name \= "gaming\_max"

description \= "Unconstrained gaming profile: BORE, scx\_bpfland, 1000Hz, NTSync"

suffix \= "dusky-gaming"

priority \= 10

bare\_metal\_only \= true

portable\_package \= false

\[release\]

channel \= "stable"

allow\_rc \= true

require\_signature \= true

\[scheduler\]

type \= "bore"

scx \= "scx\_bpfland"

scx\_flags \= "-m performance"

scx\_enable\_class \= true

allow\_vanilla\_fallback \= true

autogroup \= true

rt\_group \= false

sched\_core \= false

\[cache\]

sched\_cache \= true

llc\_aggr\_tolerance \= 0

persist \= true

\[rseq\]

slice\_extension \= true

slice\_ext\_nsec \= 10000

\[cpu\]

arch \= "native"

governor \= "performance"

amd\_pstate \= "active"

epp \= "performance"

mitigations \= "off"

smt \= true

mce \= true

prefcore \= true

compat32 \= true

\[timing\]

hz \= 1000

tickless \= "idle"

preempt \= "full"

preempt\_dynamic \= true

\[memory\]

footprint \= "standard"

thp \= "always"

thp\_defrag \= "defer+madvise"

mglru \= true

swap\_backend \= "zram"

zram\_algo \= "zstd"

zram\_size\_pct \= 50

zram\_multi\_comp \= false

swappiness \= 150

vfs\_cache\_pressure \= 50

watermark\_scale\_factor \= 150

watermark\_boost\_factor \= 0

dirty\_bytes\_mb \= 256

slub\_tiny \= false

slab\_buckets \= false

per\_vma\_lock \= true

numa \= true

ksm \= false

hugetlbfs \= true

kallsyms\_all \= true

tracing \= "full"

systemd\_oomd \= false

\[compiler\]

toolchain \= "llvm"

optimize \= "o2"

lto \= "thin"

thinlto\_cache \= true

thinlto\_cache\_size\_gb \= 20

fdo \= "none"

kcfi \= false

debug\_info \= "reduced"

module\_compress \= "zstd"

rust \= false

headers \= "auto"

\[security\]

profile \= "extreme"

init\_on\_alloc \= false

init\_on\_free \= false

hardened\_usercopy \= false

stackprotector \= "regular"

slab\_freelist\_hardened \= false

slab\_freelist\_random \= false

randomize\_kstack \= false

ubsan\_bounds \= false

acknowledge\_risk \= true

\[gaming\]

ntsync \= true

uclamp \= true

max\_map\_count \= 2147483642

split\_lock\_mitigate \= false

controllers \= true

\[storage\]

nvme\_poll\_queues \= 0

io\_scheduler \= "none"

blk\_wbt \= true

iocost \= false

\[power\]

wq\_power\_efficient \= false

cpu\_idle\_governor \= "teo"

rcu\_lazy \= false

energy\_model \= true

suspend \= true

hibernation \= false

pcie\_aspm \= "performance"

hda\_power\_save \= 0

\[network\]

congestion \= "bbr"

qdisc \= "cake"

mptcp \= true

xdp \= false

tcp\_fastopen \= true

\[modules\]

mode \= "strict"

modprobed\_db \= true

allow\_lsmod\_fallback \= true

localyesconfig \= false

manage\_service \= true

\[boot\]

cmdline \= "bake"

nowatchdog \= true

write\_entries \= true

\[verify\]

strict \= true

require\_ntsync \= true

require\_btf \= true

require\_sched\_ext \= true

---

### Template 3: Maximum Battery Endurance (Laptops / Handhelds)

*Engineered to keep mobile processors in deep package C-states and minimize background wakeups.*

\[meta\]

name \= "battery\_efficiency"

description \= "Maximum battery profile: TEO, RCU lazy, powersupersave ASPM, 300Hz"

suffix \= "dusky-battery"

priority \= 20

bare\_metal\_only \= true

portable\_package \= false

\[release\]

channel \= "stable"

allow\_rc \= true

require\_signature \= true

\[scheduler\]

type \= "eevdf"

scx \= "scx\_lavd"

scx\_flags \= "--autopower"

scx\_enable\_class \= true

autogroup \= true

rt\_group \= false

sched\_core \= false

\[cache\]

sched\_cache \= true

llc\_aggr\_tolerance \= 1

persist \= true

\[rseq\]

slice\_extension \= true

slice\_ext\_nsec \= 10000

\[cpu\]

arch \= "native"

governor \= "powersave"

amd\_pstate \= "active"

epp \= "power"

mitigations \= "on"

smt \= true

mce \= true

prefcore \= true

compat32 \= true

\[timing\]

hz \= 300

tickless \= "idle"

preempt \= "lazy"

preempt\_dynamic \= true

\[memory\]

footprint \= "lean"

thp \= "madvise"

thp\_defrag \= "defer"

mglru \= true

swap\_backend \= "zram"

zram\_algo \= "zstd"

zram\_recomp\_algo \= "zstd"

zram\_size\_pct \= 50

zram\_multi\_comp \= true

swappiness \= 180

vfs\_cache\_pressure \= 100

watermark\_scale\_factor \= 125

dirty\_bytes\_mb \= 128

slub\_tiny \= false

per\_vma\_lock \= true

numa \= false

tracing \= "minimal"

\[compiler\]

toolchain \= "llvm"

optimize \= "o2"

lto \= "thin"

thinlto\_cache \= true

thinlto\_cache\_size\_gb \= 15

debug\_info \= "none"

module\_compress \= "zstd"

rust \= false

headers \= "auto"

\[security\]

profile \= "balanced"

init\_on\_alloc \= true

init\_on\_free \= false

hardened\_usercopy \= true

stackprotector \= "strong"

slab\_freelist\_hardened \= true

slab\_freelist\_random \= true

randomize\_kstack \= true

ubsan\_bounds \= false

\[gaming\]

ntsync \= true

uclamp \= false

max\_map\_count \= 2147483642

split\_lock\_mitigate \= true

controllers \= true

\[storage\]

nvme\_poll\_queues \= 0

io\_scheduler \= "none"

blk\_wbt \= true

iocost \= false

\[power\]

wq\_power\_efficient \= true

cpu\_idle\_governor \= "teo"

rcu\_lazy \= true

energy\_model \= true

suspend \= true

hibernation \= true

pcie\_aspm \= "powersupersave"

hda\_power\_save \= 2

\[network\]

congestion \= "bbr"

qdisc \= "fq\_codel"

mptcp \= false

xdp \= false

tcp\_fastopen \= true

\[modules\]

mode \= "strict"

modprobed\_db \= true

allow\_lsmod\_fallback \= true

\[boot\]

cmdline \= "bake"

nowatchdog \= true

write\_entries \= true

\[verify\]

strict \= true

require\_ntsync \= false

require\_btf \= true

require\_sched\_ext \= true

---

### Template 4: The Golden Ratio (Daily Driver Workstation & Laptop)

*Balanced general-purpose profile providing low desktop latency, safe memory margins, full security mitigations, and solid battery life.*

\[meta\]

name \= "dusky\_balanced"

description \= "Balanced profile: EEVDF, PREEMPT\_LAZY, 500Hz, ThinLTO, ZRAM"

suffix \= "dusky-balanced"

priority \= 1

bare\_metal\_only \= true

portable\_package \= false

\[release\]

channel \= "stable"

allow\_rc \= true

require\_signature \= true

\[scheduler\]

type \= "eevdf"

scx \= "scx\_lavd"

scx\_flags \= "--autopilot"

scx\_enable\_class \= true

autogroup \= true

rt\_group \= false

sched\_core \= false

\[cache\]

sched\_cache \= true

llc\_aggr\_tolerance \= 1

persist \= true

\[rseq\]

slice\_extension \= true

slice\_ext\_nsec \= 10000

\[cpu\]

arch \= "native"

governor \= "schedutil"

amd\_pstate \= "active"

epp \= "balance\_performance"

mitigations \= "on"

smt \= true

mce \= true

prefcore \= true

compat32 \= true

\[timing\]

hz \= 500

tickless \= "idle"

preempt \= "lazy"

preempt\_dynamic \= true

\[memory\]

footprint \= "standard"

thp \= "madvise"

thp\_defrag \= "defer+madvise"

mglru \= true

swap\_backend \= "zram"

zram\_algo \= "zstd"

zram\_recomp\_algo \= "zstd"

zram\_size\_pct \= 75

zram\_multi\_comp \= true

swappiness \= 160

vfs\_cache\_pressure \= 100

watermark\_scale\_factor \= 125

dirty\_bytes\_mb \= 128

slub\_tiny \= false

per\_vma\_lock \= true

numa \= true

tracing \= "auto"

systemd\_oomd \= true

\[compiler\]

toolchain \= "llvm"

optimize \= "o2"

lto \= "thin"

thinlto\_cache \= true

thinlto\_cache\_size\_gb \= 20

debug\_info \= "reduced"

module\_compress \= "zstd"

rust \= false

headers \= "auto"

\[security\]

profile \= "balanced"

init\_on\_alloc \= true

init\_on\_free \= false

hardened\_usercopy \= true

stackprotector \= "strong"

slab\_freelist\_hardened \= true

slab\_freelist\_random \= true

randomize\_kstack \= true

ubsan\_bounds \= false

\[gaming\]

ntsync \= true

uclamp \= true

max\_map\_count \= 2147483642

split\_lock\_mitigate \= false

controllers \= true

\[storage\]

nvme\_poll\_queues \= 0

io\_scheduler \= "none"

blk\_wbt \= true

iocost \= false

\[power\]

wq\_power\_efficient \= true

cpu\_idle\_governor \= "teo"

rcu\_lazy \= true

energy\_model \= true

suspend \= true

hibernation \= true

pcie\_aspm \= "powersave"

hda\_power\_save \= 1

\[network\]

congestion \= "bbr"

qdisc \= "fq"

mptcp \= true

xdp \= false

tcp\_fastopen \= true

\[modules\]

mode \= "strict"

modprobed\_db \= true

allow\_lsmod\_fallback \= true

\[boot\]

cmdline \= "bake"

nowatchdog \= true

write\_entries \= true

\[verify\]

strict \= true

require\_ntsync \= true

require\_btf \= true

require\_sched\_ext \= true

---

## 6\. Compilation & Operational Workflow

### Step 1: Initialize System Tools & Module Profiling

\# Install core build dependencies

sudo pacman \-S \--needed base-devel clang lld llvm bc cpio kmod pahole zram-generator scx-scheds curl gnupg

\# Install and populate modprobed-db (AUR)

paru \-S modprobed-db

modprobed-db store

systemctl \--user enable \--now modprobed-db.timer

### Step 2: Write Default Profiles

./dusky\_kernal\_compile.py \--write-default-profiles

### Step 3: Run Diagnostics

./dusky\_kernal\_compile.py \--doctor

### Step 4: Preview Kconfig Configuration Matrix

./dusky\_kernal\_compile.py \--profile dusky\_balanced \--configure-only \--print-matrix

### Step 5: Execute the Kernel Build

\# Build and install using the balanced template

./dusky\_kernal\_compile.py \--profile dusky\_balanced  
