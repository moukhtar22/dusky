# Dusky Kernel Compiler — Visual Map (v4.0.0)

> **One sentence:** You pick a *recipe* (TOML profile) → the compiler *tailors* a kernel to your hardware like a bespoke suit, borrowing only what you need.

---

## 1. The Big Analogy: Bespoke Tailor Shop

| Kernel World | Tailor Shop Analogy |
|---|---|
| **TOML profiles** `kernel_profiles/*.toml` | **Recipe cards** on the wall — "Gaming cut", "Battery saver cut", "Minimal strict (hardware-only)" |
| **Live system** `/proc/config.gz` + `modprobed.db` | **Your body measurements** taken while you wear the current suit |
| `localmodconfig strict` | **Slim-fit:** only fabric for *your* measurements, tiny closet, fastest to sew (~300 modules) |
| `localmodconfig expanded` + `LMC_KEEP` | **Regular-fit:** slim + extra pockets for common tools (USB, NVMe, GPU, Audio) — safe if you plug new gear |
| **CPU arch** `native / generic_v3 / v4 / znver4` | **Fabric cut:** `native` = cut to *your* exact CPU silicon & vendor, `generic_v3` = shareable across modern PCs |
| `hz / tickless / preempt` | **Heartbeat:** 1000Hz full-tickless = drummer hitting every millisecond (gaming latency), 250Hz = drummer rests (battery) |
| **Build dir** `~/.cache/dusky-kernel` or `/mnt/zram1` | **Workbench:** disk table (`~/.cache/...`) vs ultra-fast RAM table (ZRAM/tmpfs) |
| **pacman-pkg** | **Boxing the suit** with dual packages: `linux-dusky-gaming` + `linux-dusky-gaming-headers` for 100% DKMS support |

---

## 2. Where Everything Lives (Source of Truth)

```
~/user_scripts/kernel/
├── dusky_kernal_compile.py          # The tailor (engine)
├── kernel_profiles/                 # Recipe drawer — YOU edit these, code reads them
│   ├── 01_gaming.toml               #   BORE + 1000Hz + native + expanded + O3
│   ├── 02_snappiness.toml           #   BORE + 1000Hz + MGLRU + enhanced
│   ├── 03_maximum_performance.toml  #   Mitigations off + Full LTO + O3 + PREEMPT_LAZY
│   ├── 04_throughput_bmq.toml       #   BMQ scheduler + 250Hz + voluntary
│   ├── 05_battery.toml              #   TEO idle + lazy RCU + power workqueues
│   ├── 06_low_ram.toml              #   SLUB_TINY + Zswap + strict localmodconfig
│   ├── 07_minimal_strict.toml       #   strict localmodconfig (only live hardware)
│   ├── 08_generic_v3.toml           #   x86-64-v3 (AVX2/BMI2/FMA) - shareable
│   ├── 09_generic_v4.toml           #   x86-64-v4 (AVX-512)
│   ├── 10_znver4.toml               #   AMD Zen 4 tuned + amd-pstate active EPP
│   └── SCHEMA_GUIDE.md              #   Master template & setting documentation
└── kernel.config.<profile>          # Saved .config per profile (your perfect pattern)

~/.cache/dusky-kernel/
├── src/                             # Extracted kernel trees (e.g., linux-7.2)
├── tarballs/                        # Downloaded & sha256/PGP verified kernel tarballs
├── dusky_patch_cache/               # Cached scheduler patches (BORE, BMQ)
└── packages/                        # Built Arch .pkg.tar.zst packages isolated per profile

~/.local/state/dusky-kernel/logs/    # Timestamped full build logs
```

> **Rule:** TOML is king. Code never hard-codes a tuning knob. You add `11_my_experiment.toml`, it appears instantly in `--list-profiles` and the menu.

---

## 3. The Journey: From Menu to Boot (Visual Flow)

```mermaid
flowchart TD
    A[You run dusky_kernal_compile.py] --> B{Profiles found?}
    B -->|no| Z[Error: no TOML in kernel_profiles]
    B -->|yes| C[Menu / Table: gaming | snappiness | ... | znver4]
    C --> D[You pick #1 gaming]
    D --> E[Ephemeral overrides?]
    E -->|"CPU arch? keep/native/v3/v4/znver4"| F[ Effective arch = native ]
    E -->|"Modules? keep/strict/expanded"| G[ Effective mode = expanded ]
    F & G --> H[Effective profile = TOML + overrides\n(TOML on disk stays 100% untouched)]
    H --> I[Fetch kernel.org/releases.json live]
    I --> J[Pick version: 7.2 (#1) / 7.1.9 / 6.18.45]
    J --> K[Download linux-7.2.tar.xz\naria2 16x + sha256sums.asc PGP verify]
    K --> L[Unpack to ~/.cache/dusky-kernel/src/linux-7.2]
    L --> M[Patch stage: BORE / BMQ CachyOS patch\nif missing → fallback to vanilla EEVDF]
    M --> N[Base config:\n saved kernel.config.<profile> ? that : running /proc/config.gz]
    N --> O[Prune: make localmodconfig\nLSMOD=modprobed.db + LMC_KEEP\nstrict→LMC_KEEP='' | expanded→LMC_KEEP safety net]
    O --> P[make scripts]
    P --> Q[Matrix: scripts/config\n1000Hz + NO_HZ_FULL + PREEMPT + BORE + ThinLTO + BBR + sched_ext + BTF]
    Q --> R[Rust? probe in-tree rustavailable → -e RUST : -d RUST]
    R --> S[localversion = -dusky-gaming\nmake olddefconfig → make prepare → make olddefconfig]
    S --> T[[Dry-run make -n all to estimate total steps for Live ETA bar]]
    T --> U{Stale .o/vmlinux?}
    U -->|yes| V[clean_stale artifacts]
    U -->|no| W
    V --> W[Build: make -j$(nproc) PACMAN_PKGBASE=linux-dusky-gaming PACMAN_EXTRAPACKAGES=headers pacman-pkg]
    W --> X[Live progress bar + ETA + last 20 log lines]
    X --> Y{Success?}
    Y -->|no| Y1[Display last 15 error lines, preserve .config]
    Y -->|yes| Z1[Find kernel + headers packages in PKGDEST]
    Z1 --> Z2[sudo pacman -U linux-dusky-*.pkg.tar.zst]
    Z2 --> Z3[kernel-install add / bootctl update / grub-mkconfig]
    Z3 --> AA[Mission Accomplished! Reboot → select in bootloader]
```

---

## 4. Decision Points & What Changes What

### 4.1 CPU Arch (the fabric)
```
native      → Auto-detects AMD vs Intel from /proc/cpuinfo
              AMD:   -e MNATIVE_AMD -e X86_NATIVE_CPU
              Intel: -e MNATIVE_INTEL -e X86_NATIVE_CPU
              Your CPU's exact execution pipelines & instruction set. Fastest.

generic_v3  → -e GENERIC_CPU3 --set-val X86_64_VERSION 3
              AVX2, BMI2, FMA (Haswell 2013+). Shareable sweet spot.

generic_v4  → -e GENERIC_CPU4 --set-val X86_64_VERSION 4 (AVX-512)
znver4      → -e MZEN4 (AMD Zen 4 tuned)
generic     → -e GENERIC_CPU --set-val X86_64_VERSION 1 (2003+ PCs)
```
*Override at build time without touching TOML:* `--cpu-arch generic_v3` or interactively in prompt.

### 4.2 Module Mode (the closet)
```
expanded (safe)   LMC_KEEP = drivers/usb:drivers/gpu:drivers/net:...:fs/btrfs:crypto
                  Keeps plug-and-play subsystems alive even if unplugged during DB capture.
                  Safe for USB docks, new gamepads, external audio interfaces.

strict (tiny)     LMC_KEEP = ""
                  Only compiles modules recorded in ~/.config/modprobed.db.
                  Kernel is 70%+ smaller, compiles in minutes, but needs recompile for new hardware.
                  Run `modprobed-db store` after plugging in new gear, then rebuild.
```

---

## 5. Under the Hood: 3 Layers

1. **Discovery & Trust**:
   * `releases.json` → `sha256sums.asc` aggregate manifest → PGP verify → `aria2c -x16 -c` resume → cache verified tarball.
2. **Configuration Surgery**:
   * Seed `.config` → `localmodconfig` with `modprobed.db` + `LMC_KEEP` → `build_config_matrix(profile)` (30–50 knobs: `sched_ext`, BTF, BBRv3, mitigations, ThinLTO) → `olddefconfig` cycle.
3. **Build & Ship**:
   * Estimate steps via `make -n all` → ANSI `Live` progress panel → `make pacman-pkg` with `PACMAN_PKGBASE` & `PACMAN_EXTRAPACKAGES=headers` → `pacman -U` → `kernel-install` auto-registration.

---

## 6. How to Invent Your Own Profile (30 seconds)

1. Copy: `cp kernel_profiles/01_gaming.toml kernel_profiles/11_my_lab.toml`
2. Edit:
   ```toml
   [meta]
   name = "my_lab"
   suffix = "dusky-lab"
   [cpu]
   arch = "generic_v3"   # shareable with lab machines
   governor = "schedutil"
   [modules]
   mode = "strict"      # tiny & fast compile
   [dusky]
   enhanced = true
   ```
3. Run: `python dusky_kernal_compile.py --list-profiles` → see `my_lab`
4. Pick in menu or run `python dusky_kernal_compile.py -p my_lab`.

---

## 7. Troubleshooting & Verification

| Symptom / Goal | Solution |
|---|---|
| **Run System Audit** | Run `python dusky_kernal_compile.py --doctor` (or Menu Option 5) |
| **Inspect TOML Schema** | Run `python dusky_kernal_compile.py --spec` (or view `SCHEMA_GUIDE.md`) |
| **Inspect Config Matrix** | Run `python dusky_kernal_compile.py --print-matrix -p gaming` |
| **Database has <100 drivers** | Menu Option 1 (`Install & Init`) → Option 2 (`Live Telemetry`) → plug in gear |
| **Need DKMS Headers** | Generated automatically as `linux-dusky-<flavor>-headers-*.pkg.tar.zst` |
| **Want to share kernel** | Build with `arch = "generic_v3"` (AVX2 baseline) |
