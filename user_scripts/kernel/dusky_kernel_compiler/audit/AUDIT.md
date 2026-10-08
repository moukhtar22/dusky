# Audit — 2026-09-23

Scope: Arch Linux x86-64, Linux 7.2+ with direct configuration validation against upstream **7.3-rc4**. The initial audit used configuration-only checks; the final pass below also compiled a minimal test kernel. No package installation, reboot or live runtime tuning was performed.

## Changes

- **Profile separation:** removed embedded named presets; schema/defaults and wizard metadata live in `kernel_profiles/schema.py`. TOML profiles contain tuning selections. Unknown keys and incorrect types fail early. Explicit Kconfig overrides take precedence over automatic keep rules.
- **Optional patches:** extracted existing patches into `patches/`. Enhancement patches default off for new profiles; battery choices remain explicit. Dry-run/application require zero fuzz. Removed the unnecessary O3 source patch; compiler flags select O3. Scheduler fallback remains separately configurable.
- **Target accuracy:** versioned complete hardware bundles fail closed on missing target facts or census. Imported profiles inherit the chosen TOML tuning, use target CPU capabilities and cannot accidentally install on the build host. Build parallelism uses build-host memory/CPU affinity. Removed fixed CPU-name aliases and allowlists that silently weakened future CPU targeting.
- **Configuration reliability:** explicit required symbols and exact built-in/module states are verified after dependency resolution. Corrected native CPU selection, NTFS mapping, hibernation compression, virtualized filesystem dependencies, sched_ext tracing requirements, distributed ThinLTO selection, CAS/RSEQ interfaces and final localyesconfig conversion.
- **Build reliability:** sanitized inherited build variables; locked build directories; identified source trees by profile, target and implementation inputs; made source timestamps stable. Wired the persistent ThinLTO cache into actual linker flags. Corrected compiler/CPU flags in external-module header builds and their package dependencies. FDO requires the matching configured/running kernel.
- **Dependencies and packaging:** expanded package dependency resolution with installation prompts. Package metadata always preserves the resolved profile. Fixed integration with upstream generated packaging functions. Runtime tuning and scx units are optional, package-owned and specific to the running kernel. Existing ZRAM swap is left to its manager.
- **Installation:** removed blanket pacman overwrite, checked DKMS/bootloader failures, separated boot-entry creation from default selection, validated package identity and exact uninstall names, and separated install-space checks from build-space checks.

## Battery profile

Preserved its CPU/power, security, memory, scheduler, driver and enhancement-patch choices. Changes are: mainline/RC tracking for the requested 7.3 workflow; current CAS `llc_overaggr_pct` field with the same unset value; removal of obsolete `THUNDERBOLT` keep symbol while retaining `USB4`; explicit runtime/ZRAM management controls. The new NTFS driver remains `ntfs` / `CONFIG_NTFS_FS`.

## Verification

- 31 isolated Python regression tests: profile validation/roundtrip, CPU targeting, complete remote manifests, profile inheritance, pruning, config precedence, NTFS, optional patches, strict contracts, packaging hooks, external-module flags, runtime opt-out and fake-filesystem runtime behavior.
- Real 7.3-rc4 source: target census pruning, profile matrix, `olddefconfig`, `syncconfig` and strict contract verification. Battery: **589 operations, no hard mismatch**. The resolved configuration has native CPU tuning, ThinLTO, 300 Hz, lazy/dynamic preemption, sched_ext and CAS.
- Synthetic target configurations on that source: older Intel Core 2, AMD Zen 5 with distributed ThinLTO, and KVM x86-64-v2; all finish without hard contract mismatches. These are configuration/compiler checks, not hardware execution tests.
- Shell syntax checks for generated upstream PKGBUILD; CLI help and profile/schema rendering; Python syntax checks.
- On 7.3-rc4, selected evdev RCU and refreshed PCI PME patches apply exactly. The selected scheduler-inlining patch does not apply and is reported/skipped; it is optional and has not been rewritten speculatively.

See [battery configuration output](config-7.3-rc4.txt) and [target summaries](target-configs.txt).

## Limits and operational considerations

No performance, battery-life, boot, complete package-build or DKMS execution claims can be established by configuration checks. Test the produced kernel on each target and retain a working boot entry. The battery profile retains machine-specific choices such as forced PCIe ASPM; review those choices when inheriting it for different hardware.

Strict census pruning only knows devices/features represented in the target census or explicit keep/override settings. Attach relevant peripherals during census collection. CPU-specific packages must run on their intended target.

Four battery preferences remain dependency-gated by upstream Kconfig: THP_SWAP, PAGE_REPORTING, MODULE_SIG and CPU_THERMAL. Their differences are reported; requested explicit overrides remain strict. Runtime interfaces vary by hardware; unavailable interfaces are logged. Other power managers can override one-shot tuning. ZRAM algorithm selection does not schedule periodic recompression.

Future releases can change Kconfig, compiler or packaging interfaces. Validation detects incompatible requirements; it cannot guarantee compatibility with unreleased kernels. Optional third-party scheduler/Polly/ACS patches, full package production and every bootloader path were not exercised end to end.

## Primary references

- [Linux 7.3-rc4 source](https://github.com/torvalds/linux/tree/v7.3-rc4): Kconfig, Makefiles, scheduler/RSEQ interfaces and packaging used for direct checks.
- [New NTFS Kconfig](https://github.com/torvalds/linux/blob/v7.3-rc4/fs/ntfs/Kconfig): `NTFS_FS` and module `ntfs`.
- [Upstream Arch packaging](https://github.com/torvalds/linux/blob/v7.3-rc4/scripts/package/PKGBUILD): generated package wrappers and headers.
- [LLVM kernel builds](https://docs.kernel.org/kbuild/llvm.html): compiler/tool selection.
- [ZRAM interfaces](https://docs.kernel.org/admin-guide/blockdev/zram.html): device setup and recompression controls.
- [Arch scx-scheds](https://archlinux.org/packages/extra/x86_64/scx-scheds/): current scheduler package.

## Final pass: optional RAM storage and CPU/export checks

Added `kernel_settings.toml` and `kernel_storage.py`. Storage policy stays separate from kernel profiles. Interactive RAM builds always ask with a No default; unattended builds require `--ram-build`. A real tmpfs/ZRAM mount is required. Persistent source/object state and ccache/ThinLTO caches are restored with timestamps and checkpointed on success, failure and handled interruption. Packages stay on disk. Failed saves block stale restoration. RAM job sizing accounts for available memory and a configurable growth allowance. The actual mount at `/mnt/zram1` is tmpfs; SSD writes depend partly on swap configuration.

Closed a remote-target validation bypass: a manifest now prohibits native CPU flags regardless of the portable-package flag, including explicit X86_NATIVE_CPU overrides. Exporting with `-p NAME` now bundles that profile; importing without `-p` uses it. If a compiler cannot identify the target CPU, export offers to install Clang and explicitly reports any ISA-baseline fallback. Named CPU flags now mirror upstream's APX EGPR exclusion when supported. Enabled ccache is included in dependency resolution.

Reviewed `/home/dusk/.config/pacman/makepkg.conf`. Its userspace CFLAGS/RUSTFLAGS are not appropriate replacements for Kbuild flags. Upstream PKGBUILD uses !buildflags/!makeflags and restores Kbuild parallelism; current makepkg preserves the supplied PKGDEST over its configuration file. Native Kconfig selection supplies -march=native; remote CPU selection supplies the explicit target instead. Ordinary kernel code retains upstream FPU/SIMD/APX restrictions. CPU-specific optimization does not mean every instruction extension is emitted everywhere.

Validation now includes 42 regression tests, including actual small-file rsync restore/save/reboot simulation, save-failure protection, interruption handling, per-run RAM choice, memory budgeting, and bundled-profile export/import. A minimal upstream 7.3-rc4 native x86-64 bzImage compiled and linked successfully in about 85 seconds, entirely under `/mnt/zram1/dusky_kernel/audit-smoke/`. An additional init/main.o compile with -march=core2/-mtune=core2 succeeded; total smoke-test elapsed time was about 100 seconds. No full battery build, package installation or boot test was performed. These checks establish build-path viability, not benchmark gains or exhaustive future compatibility.

See [smoke-test summary](final-pass-smoke.txt). Checkpoint completion is required before reboot; forced termination/power loss can lose new RAM-only work. Reusing the same build path maximizes incremental reuse; switching disk/RAM paths can trigger recompilation. No claim is made that all possible optimizations have been exhausted.

## Profile and pruning audit

Added three separate TOML profiles: `performance`, `extreme_power`, and `low_memory`. Kept `battery` and its selection priority; corrected inaccurate comments without changing its workload/power choices. All profiles use strict target-census pruning by default. New profiles have no optional enhancement patches or machine-specific driver/boot overrides. They use native CPU targeting, ThinLTO, scalable SLUB and explicit workload tradeoffs described in README.

Additional fixes:

- Automatic NR_CPUS follows target possible-CPU slots (including offline and sparse IDs), rather than always allocating a 64-CPU limit. Automatic NODES_SHIFT follows target NUMA topology; insufficient explicit limits fail early.
- Strict pruning retains the target's Intel i915/xe choice instead of forcing both back on. Missing Intel driver census is reported. VM GPU choices follow target GPU types. Unselected TCP congestion/qdisc implementations are no longer forced on.
- Custom -march/-mtune overrides now resolve consistently for C and Rust, including argument ordering. Rust's supported CPU list is checked when Rust is enabled, preventing silent fallback. Ambient userspace/host compiler flags are excluded from the controlled build environment.
- Exported hardware values receive type/range validation. Imported profile/package names distinguish the source tuning profile and target; long suffixes preserve uniqueness with a hash. Remote GPU checks no longer consult a build-host-only GPU-disable state.
- Runtime CPU scaling handles active P-state drivers' dynamic powersave policy when a generic dynamic governor is requested. Performance EPP is used with their performance policy. See [upstream Intel P-state documentation](https://docs.kernel.org/admin-guide/pm/intel_pstate.html).
- --build-dir now participates in settings resolution before cache paths and overlap checks are derived.
- DKMS verification covers missing target entries with autoinstall and treats status failures as failures. Initramfs is regenerated after DKMS verification (to include repairs) or if the normal image was not refreshed during installation. This can add an initramfs generation on DKMS systems; no installation was performed during this audit.

Validation: **53 regression tests pass**. All four profiles independently passed real upstream Linux 7.3-rc4 seed/prune/olddefconfig/syncconfig/strict-contract checks using the supplied module census. Operations verified: battery 589, performance 542, extreme_power 546, low_memory 539; no hard mismatches. The census pruned 6,294 seeded modules to 347 before each profile's explicit requirements. Module counts are specific to that census and are not RAM or performance benchmarks. The latest pass required no additional full kernel compilation.

See [profile configuration checks](profiles-7.3-rc4.txt). The earlier minimal native build and remote Core 2 object smoke test remain documented above. No claim of perfect behavior on every CPU, peripheral, external module or unreleased kernel is made.
