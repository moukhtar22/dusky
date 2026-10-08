# Stock kernel audit — 2026-10-04

## Scope and release

The compiler, directly used schema/profiles, storage helper, runtime helper,
regression tests and workflow documentation were reviewed. Changes preserve
x86-64 Arch Linux as the supported platform and use stock upstream kernel
features. No physical-machine installation was performed during this audit.

The [kernel.org release feed](https://www.kernel.org/releases.json) reported
**7.3-rc5** as the newest mainline release. Compilation tests use that release
exclusively. Installed older distribution headers supplied a configuration
seed; no older kernel was compiled.

## Phase 1: architecture and behavior

- Removed scheduler/enhancement/ACS/Polly patch machinery, custom HZ source
  injection, NVIDIA source modification and associated profile fields. Historical
  files remain unused; no third-party module source is added or altered.
- Retained native CPU tuning, ThinLTO, stock EEVDF/cache-aware scheduling,
  module stripping/compression and persistent caches. CachyOS's relevant
  PKGBUILD sections were compared; their useful stock build practices were
  already represented. Their patches and unconditional optimization assumptions
  were not imported. O2 remains the performance default.
- Replaced the ten-entry main menu with six entries and an ordered preparation,
  build, transfer and installation workflow. The full wizard is explicitly
  selected. Removed the redundant defaults questionnaire.
- Reduced the four profile files from 1,420 lines to 243 lines by inheriting
  schema defaults. Preserved meaningful tuning differences. Removed battery's
  forced vendor/VM/VFIO keep lists and firmware-forcing ASPM argument. Unused
  sched_ext/BTF and blanket controller support are disabled by default.
- RAM selection automatically prefers `/mnt/zram1/dusky_kernel`. Selection
  checks the mounted filesystem, available memory, selected tree, shared
  caches and memory reserve; insufficient capacity falls back to disk.
  Checkpointing and package persistence remain on disk.
- Stock-source cache identity retains the resolved profile and target facts,
  including toolchain versions, instead of hashing orchestration/schema/runtime
  source files. Make tracks changed configuration and command dependencies;
  runtime payloads/build-file additions are restaged. UI-only maintenance no
  longer creates another complete native build tree. Existing audited objects
  were migrated to the final identity for the incremental verification run.
- Target bundles retain the complete hardware/module information used by
  configuration. Import accepts guests without an L3 cache, validates the
  manifest before persistence, chooses the target CPU rather than `native`,
  and no longer injects a blanket list of VirtIO module names. Build parallelism
  remains a decision based on the build computer.

## Phase 2: detailed correctness and command verification

- A single `.config` update replaces hundreds of shell/sed operations.
  `olddefconfig` and `syncconfig` remain the dependency/choice authority.
  String escaping now follows Kconfig's quote/backslash syntax. Duplicate
  entries are removed; multiline string overrides are rejected as invalid
  configuration values.
- Fixed timeout and exception cleanup for directly owned child processes,
  subprocess pipes and signature-verification pipelines. Correctly recognize
  kernel signing subkeys through GnuPG's primary-key fingerprint field.
- Check cached/extracted source versions against the exact requested release.
  Enforce the 7.3 series floor while allowing explicitly selected 7.3 RCs.
- Restore census-listed Intel platform drivers declared through generated
  `intel-target-*` Makefile rules that upstream localmodconfig misses.
  User overrides still take precedence.
- Compile only requested ZRAM compression backends and omit NUMA on
  single-node targets unless a deliberately broad package is requested.
- Fixed the AutoFDO initial pass: retain profile-collection/debug information
  without passing a nonexistent feedback file. Support installed
  `llvm-profgen`; Propeller still needs its separate converter. Validate
  recording duration and branch-sampling hardware requirements.
- Saved-package installation retains its embedded profile. Packages without
  a recognized profile retain the base command line rather than adding
  unrelated default tuning. Build completion points users to this installer.

Installed manuals/help were read for the affected external command interfaces,
including make, makepkg, pacman, findmnt, rsync, systemd/run0/bootctl/kernel-install,
mkinitcpio, perf, GnuPG, compression and compiler/linker tools. The upstream
7.3-rc5 `scripts/config` implementation/help, Kconfig and packaging sources
were checked directly. Command references include
[LLVM kernel builds](https://docs.kernel.org/kbuild/llvm.html),
[Kbuild interfaces](https://docs.kernel.org/kbuild/kbuild.html) and
[AutoFDO](https://docs.kernel.org/dev-tools/autofdo.html).
Absent optional bootloader/converter commands were not presented as exercised.

The development machine matched the planned floors: Python 3.14.7,
Bash 5.3.20, systemd 262-1-arch and Hyprland 0.56.2. Relevant installed tools
included LLVM/Clang/LLD 23.1.1, GNU make 4.4.1, pacman 7.1.0,
mkinitcpio 42.2, Rust 1.99, bindgen 0.73.2, pahole 1.32,
ccache 4.14 and rsync 3.5.1. These are observed test versions, not version pins
or a claim that the eventual ISO's complete package set has been finalized.

## Phase 3: empirical verification

### Completed checks

- Baseline: 75 isolated tests passed. Final: **86 tests passed**, including
  real rsync fixtures, process timeout cleanup, bundle validation, generated
  Intel driver restoration, RAM capacity/reuse and configuration writing.
  Final run promoted ResourceWarnings to errors and completed in 5.576 seconds.
- Python syntax checks passed for the compiler, helpers and schema. CLI help
  executed, and a zero-second FDO recording request returned a profile error.
- A real CLI export/import/show round trip in isolated directories preserved
  the target census plus its exported live modules and selected `alderlake`
  instead of the builder's `native`. This used the local machine as the target;
  it does not substitute for installation on a separate computer.
- The generated package recipe passed `bash -n`; the packaged runtime unit
  passed `systemd-analyze verify` without diagnostics.
- Compared the previous `scripts/config` method with the new single update
  for **542 operations**: identical parsed configuration, **11.851 seconds
  versus 0.028 seconds** in one measured run. An earlier run under active
  compilation measured 21.140 versus 0.094 seconds. These measure preparation
  only; they are not kernel runtime or total compilation benchmarks.
- Four profiles were resolved against actual 7.3-rc5 Kconfig for local
  hardware and KVM target fixtures. All eight contracts passed. Before the
  huge-page policy correction, repeating all eight with the single update
  produced exactly the same resolved configurations as `scripts/config`.
  All eight passed again with the final corrected policy.
- Compared **95,943 regular upstream archive files** with the build tree:
  no missing files; only top-level `Makefile` and `scripts/package/PKGBUILD`
  differed. No kernel implementation or Kconfig feature sources were patched.

### Build failure found and corrected

The first full stock 7.3-rc5 ThinLTO build exposed an actual upstream dependency:
disabling both THP support and hugetlbfs disables `PGTABLE_HAS_HUGE_LEAVES`.
The in-tree DRM GPU SVM/pagemap helper uses `HPAGE_PMD_ORDER`, whose definition
then emits a compile-time `BUILD_BUG`. This failed during module linking.

The final configuration retains THP support and selects
`TRANSPARENT_HUGEPAGE_NEVER` for profiles requesting `thp="never"`.
Automatic allocation stays disabled; the necessary GPU definitions remain
available. THP support is therefore not advertised as compiled out. This
avoids a stock-source patch or a fragile per-release exception.

The failed build was interrupted deliberately and successfully checkpointed.
The corrected build reused those 7.3-rc5 objects on RAM.
An isolated ThinLTO compilation of `drm_gpusvm_helper.o` with the corrected
battery configuration succeeded, directly retesting the failed code path.

### Full workflow, packages and guest

- The corrected **7.3-rc5 battery ThinLTO build passed**, producing kernel
  and headers packages in **24m13s**, with 8,265 reported Kbuild steps.
  This was a complete package workflow using available objects/caches, not
  a clean-build performance benchmark.
- A final incremental run using the stable stock-source cache identity passed
  in **2m22s**, with 2,571 reported steps. Both runs automatically selected
  `/mnt/zram1`, completed persistent checkpoints, and left no `.unsaved` marker.
  These timings demonstrate working reuse, not a controlled speed comparison.
- Final kernel package: **22,152,992 bytes (21.1 MiB)**, including **353 stripped,
  ZSTD-compressed modules**. Headers: **13,326,585 bytes (12.7 MiB)**.
  Metadata identifies `7.3.0-rc5-dusky-battery` and the resolved battery profile.
  The packaged kernel image matches the built image byte-for-byte.
- Runtime dependencies are Python/systemd/util-linux/kmod; headers correctly
  declare clang/lld/llvm. No external NVIDIA modules or patch files were
  included. Stock NVIDIA-related platform drivers remain legitimate upstream
  components.
- The extracted headers successfully built an isolated test module through
  the documented modern `make -f .../Makefile M=... LLVM=1 modules` interface,
  invoked from the test directory. The module was neither loaded nor packaged.
- **The final packaged kernel booted in QEMU 11.1.1 with KVM acceleration,
  host CPU exposure, two virtual CPUs and 1 GiB RAM.** A temporary initramfs
  confirmed the exact kernel release and detected the isolated VirtIO disk:

  ```text
  DUSKY_BOOT_OK 7.3.0-rc5-dusky-battery
  DUSKY_VIRTIO_BLOCK_OK
  ```

The final source comparison again checked all 95,943 upstream files and found
only the two documented build/packaging adaptations. The release feed was
rechecked at completion and still listed 7.3-rc5 as newest mainline.

Verification journals are under `~/.local/state/dusky-kernel/logs/`:
`build-battery-20261004T134725Z.log` (corrected full workflow) and
`build-battery-20261004T142707Z.log` (final incremental workflow).
Final packages are under `~/.cache/dusky-kernel/packages/battery/` with package
revision `3`. No host-kernel installation or boot-entry changes were performed.
Compact machine-readable results are saved in
[the evidence file](STOCK_AUDIT_2026-10-04_evidence.json).

**Green Light for the audited stock-kernel build/package workflow and the
verified KVM smoke boot, subject to the target-specific limits below.**

## Limits

Configuration fixtures are not a complete desktop/graphics test in every
hypervisor. Physical reboot, DKMS installation, target initramfs/bootloader
promotion, remote-machine installation and FDO recording/conversion require
their respective target environments and have not been exercised here.
Existing external drivers remain the target user's responsibility.

Strict pruning depends on an accurate target module census and configuration
seed. It cannot discover peripherals or workload features never represented
there; users can supply an explicit seed and keep/override symbols.

RAM planning is an estimate, not an allocation cap. Concurrent workloads can
consume the reserve. Tmpfs can swap; power loss before checkpointing can lose
RAM-only changes. Packages and checkpoints are persistent.

No claims of optimal application speed, minimum achievable kernel size,
battery gains or universal hardware compatibility are justified by this audit.
Future upstream releases still need their own build and boot verification.

## Critical follow-up review: compression and intentional changes

Ran the user's `gitdelta` helper for each of the 15 changed files, without its
bare invocation's staging side effect. Reviewed the changed code and complete
resolved profile differences against the preserved pre-audit copies. Complete
file bytes were read and hashed, checked for UTF-8 round-trip integrity and
unexpected CR/NUL bytes; `git diff --check` passed. These byte checks complement
code review and do not constitute proof of semantic correctness.

All retained changes have an identified purpose. The large preset deletions
mostly remove comments and values equal to schema defaults. Native CPU tuning,
ThinLTO, O2 (size optimization for low_memory), MGLRU, profile tick/preemption,
CPU power preferences and existing security preferences remain. Battery's
previously enabled enhancement patches were deliberately removed to meet the
stock-source requirement. Its forced vendor/VFIO/filesystem list, unused
sched_ext class and forced ASPM were deliberately removed to meet the lean,
hardware-specific requirement. These choices have compatibility and workload
tradeoffs; they are not evidence of universally improved runtime performance.

The pre-audit performance preset explicitly used LZ4 for ZRAM, and battery had
an inactive LZ4 recompression override. Removed both overrides to follow the
user's clarified ZSTD preference. All four presets now resolve primary ZRAM,
recompression and optional ZSWAP compressor settings to ZSTD. ZSTD can cost more
CPU than LZ4 under swap pressure; no workload comparison was measured here.
Kernel-image and module ZSTD compression were preserved throughout. Package
compression follows makepkg settings; the verified packages use ZSTD.

Hardware exports now use Python 3.14's documented `tarfile` ZSTD writer and
`.tar.zst` default name. Existing gzip bundles remain readable. A real CLI
export/import/show round trip passed, confirming the ZSTD magic bytes,
327 target modules and explicit target CPU preservation. Python 3.14.7 on this
machine includes the required `compression.zstd` module.

Existing initramfs compression and an existing ZRAM manager are respected.
The tested mkinitcpio configuration defaults to ZSTD. Stock 7.3-rc5 hibernation
Kconfig offers only LZO/LZ4; its existing LZ4 choice remains rather than adding
an out-of-tree codec. Readers for other firmware/archive formats remain.

Follow-up verification passed **87 tests** with ResourceWarnings promoted to
errors, syntax checks, and **eight fresh 7.3-rc5 Kconfig resolutions** across
all four profiles and local/KVM fixtures. Every fixture verified native CPU,
ThinLTO, its original optimization level and ZSTD image/modules/ZRAM backend;
the unrequested LZ4 ZRAM backend is disabled. The resulting local battery
`.config` is **byte-for-byte identical** to the configuration used by the
previous successful complete build, package verification and KVM smoke boot.
No additional complete compilation or physical reboot was performed in this
follow-up. The earlier package sizes and boot results describe those previously
built packages; regenerated runtime metadata would carry the updated inactive
recompression preference. The kernel.org feed still lists 7.3-rc5 as mainline.

**Green Light for the verified scope.** No claim of equal or better performance
for every workload, peripheral or external driver follows from these checks.
