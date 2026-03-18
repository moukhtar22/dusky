# Arch Linux NVIDIA Reference for Wayland, Hyprland, and UWSM
## Part 1 — Driver Selection, Installation, Verification, and Multi-GPU Session Configuration

> [!summary]
> This part is the permanent reference for:
> - selecting the correct NVIDIA package family on Arch Linux
> - installing the driver stack correctly
> - verifying DRM KMS and driver binding for Wayland
> - understanding hybrid graphics on laptops
> - configuring Hyprland and UWSM correctly in multi-GPU setups
>
> Baseline target date: **March 2026**.

> [!warning]
> Do **not** follow outdated NVIDIA/Linux advice such as:
> - using the upstream `.run` installer on Arch
> - assuming `nvidia-dkms` is always the best choice
> - globally exporting old wlroots-era variables for Hyprland
> - generating a global Xorg config on a Wayland-first system
> - blindly copying random `NVreg_*` parameters from forum posts

---

## 1. Current Best-Practice Baseline

### 1.1 Core principles

- Use **Arch packages**, not NVIDIA’s upstream `.run` installer.
- Use **prebuilt** `nvidia*` packages on stock Arch kernels unless you actually need DKMS.
- Use **DKMS** only when you run custom kernels or want one module package rebuilt for multiple kernels.
- On current Arch packaging, **DRM KMS is typically enabled already**; verify it instead of assuming you must add kernel parameters.
- For **Hyprland**, use **`AQ_DRM_DEVICES`**, not old `WLR_DRM_DEVICES`.
- For **UWSM** sessions, place compositor-critical environment variables in **UWSM env files**, not only in shell startup files.
- On hybrid laptops, good behavior depends on the entire stack:
  - firmware / MUX mode
  - NVIDIA package family
  - Wayland compositor GPU selection
  - application offload method
  - power-management configuration

### 1.2 What actually conflicts with the proprietary/open NVIDIA stack

> [!important]
> The real conflict is usually the **kernel module** `nouveau`, not Mesa, not `i915`, and not `amdgpu`.

That means:

- Do **not** remove your integrated GPU stack on a hybrid laptop.
- Do **not** treat Mesa as an NVIDIA conflict.
- Do **not** remove Intel or AMD kernel drivers on systems where the iGPU should remain active.
- The thing that must not bind first to the NVIDIA dGPU is typically **`nouveau`** (and on some newer kernels, possibly the emerging **`nova_*`** modules if present).

### 1.3 Do not generate a global Xorg config unless you specifically need Xorg

If your system is Wayland-first:

- do **not** run `nvidia-xconfig`
- do **not** create `/etc/X11/xorg.conf` unless you are solving a specific Xorg-only issue

Global Xorg configs often create unnecessary problems on Wayland systems.

---

## 2. Choose the Correct NVIDIA Package Family

## 2.1 Package family matrix

| Situation | Recommended package family | Why |
|---|---|---|
| Stock `linux` kernel only | `nvidia` or `nvidia-open` | Simplest path |
| Stock `linux` + `linux-lts` | `nvidia` + `nvidia-lts`, or open equivalents | Matching prebuilt packages for each kernel |
| Custom kernels (`linux-zen`, `linux-hardened`, self-built, mixed kernels) | `nvidia-dkms` or `nvidia-open-dkms` | Rebuilds module per installed kernel |
| Hybrid laptop prioritizing battery life / RTD3 | Usually start with proprietary `nvidia*` family | Conservative baseline for suspend/runtime-PM validation |
| Older unsupported GPUs | Legacy branch or `nouveau` | Current mainline branch may not support the hardware |

> [!note]
> `nvidia-open` is **not** `nouveau`.
>
> - **`nvidia-open`** = NVIDIA’s open-source kernel module family, using NVIDIA userspace
> - **`nouveau`** = community reverse-engineered driver stack

## 2.2 `nvidia` vs `nvidia-open`

| Family | Best fit | Caveat |
|---|---|---|
| `nvidia*` | Conservative default, especially on hybrid laptops | Closed kernel module |
| `nvidia-open*` | Supported newer GPUs, especially when you want the open kernel module path | Hybrid-laptop suspend/runtime-PM behavior can still be platform-specific |

> [!important]
> If your priority is **predictable hybrid-laptop behavior**, especially for later RTD3 tuning, start with **`nvidia*`** unless you have already validated `nvidia-open*` on the exact model.

## 2.3 Do not stack multiple kernel-module families for the same kernel

Avoid installing overlapping alternatives for the same kernel target.

Bad examples:

- `nvidia` + `nvidia-dkms` for the same kernel
- `nvidia` + `nvidia-open` for the same kernel

Valid examples:

- `nvidia` + `nvidia-lts`
- `nvidia-open` + `nvidia-open-lts`

Those are not duplicates; they are matching prebuilt packages for different kernels.

---

## 3. Companion Packages

Install only what you need.

| Package | Purpose |
|---|---|
| `nvidia-utils` | Required userspace libraries and tools; provides `nvidia-smi` |
| `nvidia-settings` | NVIDIA configuration GUI; limited under pure Wayland but still useful for diagnostics |
| `nvidia-prime` | Provides `prime-run` for render offload |
| `lib32-nvidia-utils` | Needed for many 32-bit Steam/Proton/Wine workloads |
| `vulkan-tools` | Provides `vulkaninfo` |
| `mesa-utils` | Provides `glxinfo` |
| `nvtop` | Terminal GPU monitor |
| `intel-gpu-tools` | Provides `intel_gpu_top` |
| `libva-utils` | Provides `vainfo` |

---

## 4. Prerequisites and Safety Checks

### 4.1 Full-system upgrades only

> [!warning]
> Arch Linux does **not** support partial upgrades.

A very common failure mode is:

- kernel module from one NVIDIA version
- userspace from another NVIDIA version

Always update the system as a whole:

```bash
sudo pacman -Syu
```

### 4.2 Secure Boot

If UEFI Secure Boot is enabled, unsigned NVIDIA modules may fail to load.

Common log symptoms include:

- `Required key not available`
- `module verification failed`
- kernel lockdown messages

If Secure Boot is enabled, either:

- sign the relevant kernel/modules with your Secure Boot workflow, or
- disable Secure Boot

This is especially relevant for **DKMS** installations.

### 4.3 Firmware / MUX / Advanced Optimus mode matters

Your firmware or vendor GPU-mode tool may expose one or more of these modes:

| Mode | Meaning |
|---|---|
| Integrated only | iGPU owns the display path; dGPU may be hidden or minimally exposed |
| Hybrid / Optimus | iGPU usually drives the desktop, dGPU is available on demand |
| Discrete only / dGPU only | NVIDIA owns the display path for the whole session |

> [!important]
> If the machine is set to **dGPU-only**, the NVIDIA GPU being active is expected behavior. That is not a Hyprland misconfiguration.

### 4.4 Audit currently installed kernels and NVIDIA packages

Before changing anything, inspect what is already installed:

```bash
pacman -Q | grep -E '^(linux(|-lts|-zen|-hardened)|nvidia(|-open|-dkms|-open-dkms|-lts|-open-lts|-utils|-settings|-prime)|dkms)\b' || true
```

If you are switching families, remove the old kernel-module package cleanly instead of piling new ones on top.

---

## 5. Installation

## 5.1 Stock kernel: single `linux`

### Proprietary kernel module

```bash
sudo pacman -S --needed nvidia nvidia-utils nvidia-settings
```

### Open kernel module

```bash
sudo pacman -S --needed nvidia-open nvidia-utils nvidia-settings
```

## 5.2 Stock kernels: `linux` + `linux-lts`

### Proprietary kernel module

```bash
sudo pacman -S --needed nvidia nvidia-lts nvidia-utils nvidia-settings
```

### Open kernel module

```bash
sudo pacman -S --needed nvidia-open nvidia-open-lts nvidia-utils nvidia-settings
```

## 5.3 DKMS installations

Use DKMS when:

- you run custom kernels
- you run mixed nonstandard kernels
- you specifically want one NVIDIA module source rebuilt against all installed kernels

### Install matching headers first

Example for `linux` + `linux-lts`:

```bash
sudo pacman -S --needed linux-headers linux-lts-headers
```

Example for `linux-zen`:

```bash
sudo pacman -S --needed linux-zen-headers
```

### Install the NVIDIA DKMS package

#### Proprietary kernel module

```bash
sudo pacman -S --needed nvidia-dkms nvidia-utils nvidia-settings
```

#### Open kernel module

```bash
sudo pacman -S --needed nvidia-open-dkms nvidia-utils nvidia-settings
```

> [!note]
> If you use only prebuilt packages such as `nvidia`, `nvidia-lts`, `nvidia-open`, or `nvidia-open-lts`, you do **not** need kernel headers just for NVIDIA.

## 5.4 Optional hybrid / gaming extras

```bash
sudo pacman -S --needed nvidia-prime lib32-nvidia-utils vulkan-tools nvtop intel-gpu-tools libva-utils
```

Add `mesa-utils` if you specifically want `glxinfo`:

```bash
sudo pacman -S --needed mesa-utils
```

## 5.5 Package role breakdown

| Package | What it does |
|---|---|
| `nvidia`, `nvidia-lts` | Prebuilt proprietary kernel modules for stock Arch kernels |
| `nvidia-open`, `nvidia-open-lts` | Prebuilt open NVIDIA kernel modules for supported newer GPUs |
| `nvidia-dkms`, `nvidia-open-dkms` | DKMS source package for rebuilding against installed kernels |
| `nvidia-utils` | Userspace stack, NVML, `nvidia-smi`, GL/Vulkan/EGL-related pieces required by current packaging |
| `nvidia-settings` | Optional GUI utility, still useful for inspection even if Wayland limits some functions |
| `nvidia-prime` | Supplies `prime-run` for per-application render offload |

---

## 6. Conflicting Driver Handling: `nouveau` and Related Modules

## 6.1 Verify packaged blacklists first

Current Arch NVIDIA packaging usually installs modprobe snippets that prevent conflicting modules from binding first.

Check both packaged and local modprobe configuration:

```bash
grep -R --line-number -E '\b(nouveau|nova_(core|drm))\b' /usr/lib/modprobe.d /etc/modprobe.d 2>/dev/null
```

You typically want to see `nouveau` blacklisted somewhere in packaged configuration or your local override.

> [!note]
> `nova_core` and `nova_drm` only matter on kernels where the emerging Nova driver stack is present. If those names do not appear on your system, they are irrelevant.

## 6.2 Manual fallback blacklist

If a conflicting module still binds first, add an explicit override:

```bash
sudo install -Dm644 /dev/stdin /etc/modprobe.d/blacklist-nvidia-conflicts.conf <<'EOF'
blacklist nouveau
blacklist nova_core
blacklist nova_drm
EOF
```

## 6.3 Rebuild initramfs after blacklist/modprobe changes

If you changed files under `/etc/modprobe.d/`, rebuild initramfs:

```bash
sudo mkinitcpio -P
```

> [!note]
> If you use `dracut` or `booster` instead of `mkinitcpio`, rebuild with the tool actually used on your system.

---

## 7. DRM KMS for Wayland

Wayland compositors require NVIDIA DRM KMS.

## 7.1 Verify current KMS state

On current Arch packaging, modesetting is typically already enabled. Verify it:

```bash
cat /sys/module/nvidia_drm/parameters/modeset
```

Expected output:

```text
Y
```

Interpretation:

- `Y` = DRM KMS enabled
- `N` = not enabled
- file missing = `nvidia_drm` not loaded yet, or driver stack not active

## 7.2 Only add a kernel parameter if verification shows it is needed

If KMS is **not** enabled, add:

```text
nvidia_drm.modeset=1
```

### GRUB

Edit:

```bash
sudoedit /etc/default/grub
```

Example:

```diff
-GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"
+GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet nvidia_drm.modeset=1"
```

Regenerate GRUB config:

```bash
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

### systemd-boot

Edit the relevant loader entry, commonly under one of:

- `/boot/loader/entries/*.conf`
- `/efi/loader/entries/*.conf`

Append the parameter to the `options` line:

```diff
-options root=UUID=... rw
+options root=UUID=... rw nvidia_drm.modeset=1
```

> [!note]
> `systemd-boot` does not require a GRUB-style regeneration step. The updated entry is used on the next boot.

## 7.3 `fbdev` is not a default recommendation

Some systems may benefit from:

```text
nvidia_drm.fbdev=1
```

but this should **not** be added blindly. Consider it only when diagnosing:

- black screens during VT handoff
- broken console output after boot
- specific handoff/resume issues attributable to framebuffer behavior

---

## 8. Reboot and Baseline Verification

After installation and any relevant boot/module changes:

```bash
sudo reboot
```

## 8.1 Confirm driver binding

```bash
lspci -Dnnk | grep -EA4 '\[03(00|02|80)\]'
```

Look for:

- `Kernel driver in use: nvidia` on the NVIDIA dGPU
- `Kernel driver in use: i915` or `amdgpu` on the integrated GPU
- **not** `nouveau`

## 8.2 Confirm NVIDIA modules are loaded

```bash
lsmod | grep -E '^(nvidia(_drm|_modeset|_uvm)?|nouveau|bbswitch)\b'
```

Healthy modern setups usually show some combination of:

- `nvidia`
- `nvidia_modeset`
- `nvidia_drm`

`nvidia_uvm` may appear only once CUDA, NVENC, NVDEC, or similar functionality is used.

> [!warning]
> `bbswitch` is legacy and generally not part of a modern Wayland + NVIDIA setup.

## 8.3 Check kernel logs

```bash
journalctl -b -k | grep -iE 'nvidia|nouveau|nova|firmware|module verification'
```

This is where you catch:

- module signature failures
- binding conflicts
- firmware issues
- modeset problems

## 8.4 Re-check KMS after boot

```bash
cat /sys/module/nvidia_drm/parameters/modeset
```

Expected:

```text
Y
```

## 8.5 Verify general userspace visibility

### NVIDIA management tool

```bash
nvidia-smi
```

### Vulkan device enumeration

```bash
vulkaninfo --summary
```

> [!note]
> `nvidia-smi`, `nvtop`, and some system monitors can wake the dGPU on hybrid laptops. That is normal. It only matters later when validating idle runtime suspend.

## 8.6 DKMS-specific verification

If you installed a DKMS package:

```bash
dkms status | grep -i nvidia
```

If the build failed, inspect the DKMS logs under `/var/lib/dkms/`.

---

## 9. Hybrid Graphics: Correct Mental Model

A hybrid laptop has multiple independent decision layers.

| Layer | Typical tool / setting | What it controls |
|---|---|---|
| Firmware / MUX | BIOS, OEM utility, Advanced Optimus | Which GPU owns outputs at the hardware level |
| Kernel driver | `nvidia*` / `nvidia-open*` | Which driver binds the dGPU and how it behaves |
| Compositor | `AQ_DRM_DEVICES` | Which DRM cards Hyprland is allowed to use |
| Application | `prime-run`, app-specific GPU selection | Which GPU renders a specific application |

> [!important]
> `AQ_DRM_DEVICES` and `prime-run` solve **different** problems.
>
> - **`AQ_DRM_DEVICES`** controls compositor-visible GPUs
> - **`prime-run`** controls per-application render offload

---

## 10. PRIME Render Offload

## 10.1 Install the wrapper

```bash
sudo pacman -S --needed nvidia-prime
```

## 10.2 Launch a specific app on the NVIDIA dGPU

```bash
prime-run <application>
```

Examples:

```bash
prime-run steam
prime-run gamescope
prime-run blender
```

Steam launch option example:

```text
prime-run %command%
```

> [!note]
> On Arch, `prime-run` is the standard user-facing wrapper for NVIDIA render offload. It sets the relevant environment for common OpenGL and Vulkan workloads.

> [!warning]
> Do **not** offload long-lived desktop daemons, bars, notification services, portals, or helper apps to the dGPU unless you intentionally want the dGPU kept active.

---

## 11. System-Wide GPU Mode Switching Tools

## 11.1 `envycontrol`

`envycontrol` is optional. It is useful on many Optimus laptops when you want whole-system switching between integrated, hybrid, and NVIDIA modes.

Install from the AUR using your preferred AUR workflow.

Typical usage:

```bash
sudo envycontrol -q
sudo envycontrol -s integrated
sudo envycontrol -s hybrid
sudo envycontrol -s nvidia
```

### Mode summary

| Mode | Meaning |
|---|---|
| `integrated` | iGPU only; best battery life |
| `hybrid` | iGPU primary, dGPU available on demand |
| `nvidia` | dGPU primary for the whole session |

> [!note]
> A reboot is typically required after changing modes with `envycontrol`.

> [!warning]
> If you use `envycontrol`, vendor MUX tools, and manual compositor GPU selection at the same time, document which layer is authoritative. Mixing them casually creates confusion.

## 11.2 Vendor-specific tools

Some vendors provide better platform integration than generic tools. Example:

- ASUS: `supergfxctl`

If your laptop vendor documents a supported tool, prefer that over low-level guesswork.

## 11.3 `acpi_call` is a last resort

`acpi_call` is **not** a first-line tool for current Arch + Wayland + NVIDIA setups.

Use it only if:

- your platform is poorly supported by normal mechanisms
- vendor tooling does not exist
- you are doing model-specific troubleshooting

It is platform-specific and easy to misuse.

---

## 12. Hyprland Multi-GPU Configuration

## 12.1 Use persistent GPU paths, not `card0` / `card1`

`/dev/dri/card0`, `card1`, etc. are dynamically assigned and are not the correct long-term identifiers for compositor configuration.

Use:

```text
/dev/dri/by-path/pci-....-card
```

These are stable across normal reboots.

> [!note]
> The PCI path is normally stable across ordinary reboots. It may change after significant topology changes such as firmware reconfiguration, eGPU/dock changes, or hardware replacement.

## 12.2 Identify GPUs and map them to persistent DRM paths

### Step 1: list display-class PCI devices

```bash
lspci -Dnn | grep -E '\[03(00|02|80)\]'
```

Example:

```text
0000:00:02.0 VGA compatible controller [0300]: Intel Corporation ...
0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation ...
```

### Step 2: map those PCI addresses to DRM nodes

```bash
ls -l /dev/dri/by-path/
```

Example:

```text
pci-0000:00:02.0-card   -> ../card0
pci-0000:00:02.0-render -> ../renderD128
pci-0000:01:00.0-card   -> ../card1
pci-0000:01:00.0-render -> ../renderD129
```

For Hyprland, use the `*-card` nodes, not the `renderD*` nodes.

## 12.3 Bash helper: map GPUs to persistent nodes

```bash
#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

mapfile -t gpu_pci_ids < <(
  lspci -Dnn |
    awk '/\[(0300|0302|0380)\]/{print $1}'
)

for pci in "${gpu_pci_ids[@]}"; do
  printf '\n== %s ==\n' "$pci"
  lspci -Dnn -s "$pci"
  for node in /dev/dri/by-path/pci-"$pci"-{card,render}; do
    [[ -e $node ]] && ls -l "$node"
  done
done
```

---

## 13. `AQ_DRM_DEVICES`: The Correct Hyprland GPU Variable

Hyprland uses **`AQ_DRM_DEVICES`** to decide which DRM devices it may use, in priority order.

### 13.1 Example: iGPU primary, dGPU secondary

```text
/dev/dri/by-path/pci-0000:00:02.0-card:/dev/dri/by-path/pci-0000:01:00.0-card
```

The first path is the preferred primary renderer.

### 13.2 Example: dGPU only

```text
/dev/dri/by-path/pci-0000:01:00.0-card
```

> [!important]
> If an external monitor is physically wired to the NVIDIA dGPU, the NVIDIA `*-card` path must be included in `AQ_DRM_DEVICES` or that output may not appear.

---

## 14. UWSM Configuration for Hyprland

If Hyprland is launched through **UWSM**, prefer a compositor-specific UWSM env file.

## 14.1 Create the env file

```bash
install -Dm644 /dev/stdin "$HOME/.config/uwsm/env-hyprland" <<'EOF'
export AQ_DRM_DEVICES=/dev/dri/by-path/pci-0000:00:02.0-card:/dev/dri/by-path/pci-0000:01:00.0-card
EOF
```

Adjust the paths for your actual hardware.

## 14.2 Why this is preferred

This ensures the variable exists:

- before the compositor starts
- in the systemd user environment used by the session
- without relying on shell startup files that may not govern a UWSM session

> [!warning]
> `~/.bash_profile`, `~/.zprofile`, and similar shell files are **not** the authoritative place for compositor-critical environment in UWSM-based sessions.

## 14.3 Apply the change

Log out fully and start a new Hyprland session through your normal UWSM flow.

To inspect the user-manager environment after relogin:

```bash
systemctl --user show-environment | grep '^AQ_DRM_DEVICES='
```

## 14.4 Launching offloaded apps under UWSM

You can combine UWSM app scopes with render offload:

```bash
uwsm app -- prime-run steam
```

Example Hyprland bind:

```ini
bind = SUPER, G, exec, uwsm app -- prime-run steam
```

---

## 15. Non-UWSM Hyprland Configuration

If you do **not** use UWSM, set the variable directly in `hyprland.conf`:

```ini
env = AQ_DRM_DEVICES,/dev/dri/by-path/pci-0000:00:02.0-card:/dev/dri/by-path/pci-0000:01:00.0-card
```

> [!note]
> Hyprland config syntax uses:
>
> `env = KEY,VALUE`
>
> Shell and UWSM env files use:
>
> `export KEY=VALUE`

> [!warning]
> Do not set conflicting values in both UWSM env files and `hyprland.conf` unless you are doing so deliberately and understand the precedence in your session flow.

---

## 16. Practical Multi-GPU Guidance

## 16.1 Common sane defaults

### Hybrid laptop, battery-oriented desktop, dGPU available on demand

- firmware/MUX: `hybrid`
- compositor primary GPU: iGPU
- dGPU included as secondary in `AQ_DRM_DEVICES` if needed for outputs
- applications launched on dGPU with `prime-run`

### dGPU-primary performance session

- firmware/MUX: `nvidia` / `discrete` / vendor equivalent
- or Hyprland limited to dGPU
- expect higher power usage and less opportunity for runtime suspend

## 16.2 External monitors

If a laptop output is electrically routed through the NVIDIA dGPU:

- the dGPU must be visible to the compositor
- the dGPU may need to stay active while that monitor is in use

That is hardware topology, not a Hyprland bug.

---

## 17. Obsolete Settings and Cargo-Cult Advice to Avoid

These are **not** modern defaults for Hyprland in 2026:

- `WLR_DRM_DEVICES=...` for Hyprland
- `WLR_NO_HARDWARE_CURSORS=1` as a default fix
- global `GBM_BACKEND=nvidia-drm`
- global `__GLX_VENDOR_LIBRARY_NAME=nvidia`
- random global `LIBVA_DRIVER_NAME=...`
- old EGLStreams-era workarounds copied from old forum posts

> [!warning]
> Only add graphics environment variables when they solve a specific observed problem.

### 17.1 Why global `__GLX_VENDOR_LIBRARY_NAME=nvidia` is usually wrong

That variable is useful in **offload contexts**, which `prime-run` handles for you.

Exporting it globally can cause the wrong apps to bind or prefer the wrong GPU.

### 17.2 Why `WLR_DRM_DEVICES` is wrong for Hyprland

Modern Hyprland uses **`AQ_DRM_DEVICES`**. Old wlroots guides are not the correct reference for current Hyprland GPU selection.

---

## 18. Baseline Command Reference for Part 1

## 18.1 Hardware and driver binding

```bash
lspci -Dnn | grep -E '\[03(00|02|80)\]'
ls -l /dev/dri/by-path/
lspci -Dnnk | grep -EA4 '\[03(00|02|80)\]'
```

## 18.2 Module and log inspection

```bash
lsmod | grep -E '^(nvidia(_drm|_modeset|_uvm)?|nouveau|bbswitch)\b'
journalctl -b -k | grep -iE 'nvidia|nouveau|nova|firmware|module verification'
dkms status | grep -i nvidia
```

## 18.3 Wayland readiness

```bash
cat /sys/module/nvidia_drm/parameters/modeset
nvidia-smi
vulkaninfo --summary
```

## 18.4 UWSM environment inspection

```bash
systemctl --user show-environment | grep '^AQ_DRM_DEVICES='
```

---

## 19. What Part 2 Covers

> [!summary]
> Part 2 covers:
> - laptop power management and RTD3
> - `nvidia-powerd`
> - `NVreg_DynamicPowerManagement`
> - udev runtime-PM rules
> - verifying `power/control` and `runtime_status`
> - identifying services and processes that keep the dGPU awake
> - suspend/resume helpers
> - low-level `/sys` and `/proc` inspection
> - deeper troubleshooting and monitoring workflows


# Arch Linux NVIDIA Reference for Wayland, Hyprland, and UWSM
## Part 2 — Laptop Power Management, RTD3, Process Attribution, and Suspend/Resume

> [!summary]
> This part is the permanent reference for:
> - NVIDIA laptop power management on Arch Linux
> - achieving and verifying RTD3 on hybrid systems
> - understanding `nvidia-powerd` vs runtime power management
> - applying module parameters and udev rules correctly
> - identifying what keeps the dGPU awake
> - using low-level `/sys` and `/proc` interfaces safely
> - handling suspend, hibernate, and resume issues
>
> Baseline target date: **March 2026**.

> [!important]
> On hybrid laptops, good idle battery life is not controlled by a single setting. RTD3 depends on the entire stack:
> - firmware / MUX mode
> - compositor GPU choice
> - active displays
> - PCI runtime PM policy
> - NVIDIA module behavior
> - whether any process or service is still using the dGPU

---

## 1. Core Power-Management Concepts

### 1.1 P-states are not RTD3

Do **not** confuse these two concepts:

| Term | Meaning |
|---|---|
| **P-states** | Active performance states while the GPU is powered on, such as high-performance vs idle clocks |
| **RTD3 / D3cold** | Runtime PCI suspend state where the dGPU is effectively powered down when idle |

A GPU sitting in a low P-state is still **on**.  
A GPU in RTD3 is effectively **runtime-suspended**.

> [!important]
> For laptop battery life, the real goal is usually **RTD3**, not merely “low utilization” or “P8”.

### 1.2 What RTD3 looks like when working

On a healthy hybrid laptop, when the dGPU is not needed:

- the iGPU owns the desktop workload
- the dGPU has no active clients
- all relevant NVIDIA PCI functions are set to runtime-manage automatically
- the dGPU’s runtime status becomes `suspended`
- power draw drops substantially

### 1.3 Situations where RTD3 is impossible or not expected

RTD3 typically will **not** engage while any of the following is true:

- the laptop is in **dGPU-only / discrete-only** firmware mode
- the compositor is rendering on the NVIDIA GPU
- an external monitor is physically wired to the NVIDIA GPU and is active
- a CUDA / Vulkan / OpenGL / NVENC / NVDEC workload is active
- persistence mode or `nvidia-persistenced` keeps the GPU initialized
- one child PCI function remains pinned `on`

---

## 2. Common RTD3 Blockers

| Blocker | Effect |
|---|---|
| External monitor connected to a dGPU-routed port | dGPU usually must remain active |
| Firmware set to dGPU-only | dGPU remains active by design |
| Hyprland primary renderer set to dGPU | compositor keeps dGPU awake |
| Long-lived GPU clients | browser, Electron app, AI service, streaming stack, etc. |
| Monitoring tools left open | `nvidia-smi`, `nvtop`, widgets, panels, custom scripts |
| Persistence mode / `nvidia-persistenced` | prevents deep idle power savings |
| NVIDIA audio/USB/UCSI child function stuck `on` | can block whole-device suspend |
| Incorrect or missing runtime PM policy | `power/control` not set to `auto` |

> [!warning]
> Testing RTD3 while repeatedly running `watch nvidia-smi`, `nvtop`, or GPU-monitor widgets is self-sabotage. Those tools can wake the dGPU or keep it active.

---

## 3. `nvidia-powerd` Is Not RTD3

### 3.1 What `nvidia-powerd` actually does

`nvidia-powerd` is the userspace daemon associated with **Dynamic Boost** on supported notebook platforms. It helps the system shift power budget between CPU and GPU under load.

That means:

- it is primarily a **performance / power-budgeting** feature
- it is **not** the same thing as runtime suspend
- it does **not** itself make an idle dGPU enter RTD3

### 3.2 When to enable it

Enable it only if:

- your laptop/platform supports it
- you actually want Dynamic Boost behavior

```bash
sudo systemctl enable --now nvidia-powerd.service
```

### 3.3 How to check whether it is supported

```bash
systemctl status nvidia-powerd.service
journalctl -b -u nvidia-powerd.service
```

If unsupported, logs commonly show initialization failures or platform mismatch symptoms. In that case, disable it:

```bash
sudo systemctl disable --now nvidia-powerd.service
```

> [!note]
> A failed `nvidia-powerd` service does **not** necessarily mean your driver installation is broken. It often just means your platform does not support Dynamic Boost.

---

## 4. Baseline Runtime-PM Module Configuration

## 4.1 The most important NVIDIA module parameter

For hybrid laptops using the proprietary NVIDIA kernel module family, the usual baseline is:

```ini
options nvidia NVreg_DynamicPowerManagement=0x02
```

### Meaning of `NVreg_DynamicPowerManagement`

| Value | Meaning |
|---|---|
| `0x00` | Disabled |
| `0x01` | Coarse-grained runtime PM |
| `0x02` | Fine-grained runtime PM; normal laptop tuning choice |
| `0x03` | Driver default behavior |

> [!important]
> If you are deliberately tuning a hybrid laptop for RTD3, explicitly setting `0x02` is still the clearest and most predictable baseline.

## 4.2 Create the modprobe configuration

```bash
sudo install -Dm644 /dev/stdin /etc/modprobe.d/nvidia-pm.conf <<'EOF'
options nvidia NVreg_DynamicPowerManagement=0x02
EOF
```

Rebuild initramfs and reboot:

```bash
sudo mkinitcpio -P
sudo reboot
```

> [!note]
> If you use `dracut` or `booster` instead of `mkinitcpio`, rebuild with the tool actually used by your system.

## 4.3 Verify the parameter was applied

After reboot:

```bash
grep -H . /sys/module/nvidia/parameters/NVreg_DynamicPowerManagement
```

Expected output typically resembles:

```text
/sys/module/nvidia/parameters/NVreg_DynamicPowerManagement:0x02
```

### Alternative verification

You can also inspect available parameters:

```bash
modinfo -p nvidia | grep NVreg_
```

> [!note]
> `modinfo -p nvidia` shows parameters supported by the installed module build; `/sys/module/nvidia/parameters/...` shows what was actually loaded.

---

## 5. Parameters You Should Not Blindly Copy from the Internet

> [!warning]
> These are **not** universal best practices and should not be enabled just because a forum post says so:
>
> - `NVreg_EnableGpuFirmware=0`
> - `NVreg_EnableS0ixPowerManagement=1`
> - `NVreg_PreserveVideoMemoryAllocations=1`

### 5.1 `NVreg_EnableGpuFirmware=0`

This is a troubleshooting knob for specific driver/platform combinations. It is **not** a general RTD3 recommendation, and it is not a universal fix for GSP-related issues.

### 5.2 `NVreg_EnableS0ixPowerManagement=1`

This is platform-specific and relevant only for certain suspend / modern-standby debugging cases. It is not a generic laptop tuning step.

### 5.3 `NVreg_PreserveVideoMemoryAllocations=1`

This can be relevant for specific suspend/hibernate or VRAM-preservation troubleshooting, but it should be introduced only when you are solving an actual resume issue and have validated the current Arch/NVIDIA guidance for your exact setup.

> [!important]
> Start with the **minimal known-good baseline**. Add one change at a time and verify behavior after each change.

---

## 6. udev Rules for PCI Runtime Power Management

## 6.1 Why udev rules are often needed

On many laptops, setting the module parameter alone is not enough.  
All relevant NVIDIA PCI functions also need permission to runtime-manage themselves.

Typical functions may include:

| PCI Function Class | Typical Meaning |
|---|---|
| `0x030000` | VGA controller |
| `0x030200` | 3D controller |
| `0x040300` | HDMI/DP audio |
| `0x0c0330` | USB xHCI controller, if exposed by the dGPU |
| `0x0c8000` | UCSI / Type-C controller, if exposed by the dGPU |

If one of these functions remains pinned `on`, the whole dGPU may fail to suspend.

## 6.2 Recommended udev rule set

Create:

```bash
sudo install -Dm644 /dev/stdin /etc/udev/rules.d/80-nvidia-pm.rules <<'EOF'
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030200", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x040300", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x0c0330", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x0c8000", TEST=="power/control", ATTR{power/control}="auto"
EOF
```

Reload rules and reboot:

```bash
sudo udevadm control --reload-rules
sudo reboot
```

> [!note]
> The `ACTION=="bind"` form is preferred here because it applies when the driver actually binds to the PCI function. This is generally more reliable than a simple `add` rule for runtime PM policy.

> [!important]
> For maximum runtime power savings, the NVIDIA audio function is usually better set to **`auto`**, not `on`.

### 6.3 Why some older guides tell you to keep audio `on`

Older advice sometimes keeps the NVIDIA HDMI/DP audio function forced `on` to avoid edge-case audio glitches. That trades away runtime suspend. If your goal is RTD3, start with `auto` and only change behavior if you have a real audio problem.

---

## 7. Practical RTD3 Checklist

Use this order. Do **not** skip the topology checks.

### 7.1 Firmware / GPU mode

Make sure the laptop is in a mode compatible with runtime dGPU suspend:

- **hybrid / Optimus**: good baseline
- **integrated only**: dGPU may be unavailable or mostly dormant
- **discrete only / dGPU only**: RTD3 while the session is active is generally unrealistic

### 7.2 Compositor GPU choice

If Hyprland is rendering on the NVIDIA GPU, the dGPU will stay awake.  
For battery-oriented hybrid setups:

- use the **iGPU** as the compositor’s primary renderer
- only offload specific applications to the dGPU
- include the dGPU in `AQ_DRM_DEVICES` only when needed for outputs or offload compatibility

### 7.3 External displays

If an external display is wired to the dGPU and active, the dGPU may need to remain awake. This is normal hardware behavior.

### 7.4 Disable persistence

Do **not** enable persistence mode or `nvidia-persistenced` on a battery-focused hybrid laptop unless you intentionally want the dGPU initialized continuously.

Check for the service:

```bash
systemctl status nvidia-persistenced.service
```

Disable it if present and unwanted:

```bash
sudo systemctl disable --now nvidia-persistenced.service
```

If your GPU/driver exposes persistence mode through `nvidia-smi`, inspect it with:

```bash
nvidia-smi -q | grep -i 'Persistence Mode'
```

> [!warning]
> Consumer platforms vary. The operational point is simple: do not keep persistence enabled on a hybrid laptop if you want RTD3.

### 7.5 Apply runtime-PM module and udev policy

- `NVreg_DynamicPowerManagement=0x02`
- udev rules forcing `power/control=auto` on NVIDIA PCI functions

### 7.6 Test in a truly idle state

Before checking runtime status:

- close `nvidia-smi` and `nvtop`
- close games, CUDA jobs, OBS, Sunshine, AI services, browsers offloaded to dGPU
- disconnect any dGPU-routed external monitor if you are specifically testing deep idle
- wait several seconds after last dGPU activity

---

## 8. Authoritative Verification Workflow

## 8.1 Find the NVIDIA PCI address

```bash
lspci -Dnn | grep -E '\[03(00|02|80)\]'
```

Example:

```text
0000:00:02.0 VGA compatible controller [0300]: Intel Corporation ...
0000:01:00.0 VGA compatible controller [0300]: NVIDIA Corporation ...
```

In this example, the dGPU base slot is `0000:01:00`.

## 8.2 Check runtime policy and current runtime state

Replace the PCI address with your actual NVIDIA function address:

```bash
cat "/sys/bus/pci/devices/0000:01:00.0/power/control"
cat "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status"
```

Expected idle-state targets:

- `power/control` → `auto`
- `runtime_status` → `suspended`

### 8.3 Check suspend residency time

```bash
cat "/sys/bus/pci/devices/0000:01:00.0/power/runtime_suspended_time"
```

If RTD3 is working and the device remains idle, this value should increase over time.

> [!note]
> `runtime_suspended_time` is typically reported in milliseconds.

### 8.4 Check all NVIDIA child PCI functions, not just function 0

A dGPU often exposes multiple functions such as:

- `0000:01:00.0`
- `0000:01:00.1`
- `0000:01:00.2`
- `0000:01:00.3`

If function `.1` or `.3` is stuck active, the whole device may fail to suspend.

### 8.5 Detailed NVIDIA driver power report

If present, inspect:

```bash
cat "/proc/driver/nvidia/gpus/0000:01:00.0/power"
```

This is often the most useful NVIDIA-specific diagnostic for runtime-D3 state.

> [!warning]
> Read this file for diagnostics. Do **not** treat it as a general write interface for normal tuning.

---

## 9. Bash Helper: Audit All NVIDIA Child Functions

Pass the base slot without a function suffix, for example `0000:01:00`.

```bash
#!/usr/bin/env bash
set -euo pipefail
gpu_base="${1:-0000:01:00}"
shopt -s nullglob

printf 'NVIDIA PCI runtime-PM audit for %s\n\n' "$gpu_base"

for dev in /sys/bus/pci/devices/"${gpu_base}".*; do
  [[ -e $dev ]] || continue

  pci="${dev##*/}"
  vendor="$(<"$dev/vendor")"
  class="$(<"$dev/class")"

  driver='none'
  [[ -L $dev/driver ]] && driver="$(basename "$(readlink -f "$dev/driver")")"

  control='n/a'
  runtime='n/a'
  suspended_ms='n/a'

  [[ -r $dev/power/control ]] && control="$(<"$dev/power/control")"
  [[ -r $dev/power/runtime_status ]] && runtime="$(<"$dev/power/runtime_status")"
  [[ -r $dev/power/runtime_suspended_time ]] && suspended_ms="$(<"$dev/power/runtime_suspended_time")"

  printf '%-12s vendor=%-8s class=%-10s driver=%-12s control=%-4s runtime=%-10s suspended_ms=%s\n' \
    "$pci" "$vendor" "$class" "$driver" "$control" "$runtime" "$suspended_ms"
done
```

Example usage:

```bash
bash ./check-nvidia-rtd3.sh 0000:01:00
```

Healthy idle output typically shows:

- `control=auto` on relevant functions
- `runtime=suspended` on most or all relevant functions when nothing is using the dGPU

---

## 10. Finding What Keeps the dGPU Awake

> [!important]
> The claim that “there is no way to see what is using the GPU” is false.

There are multiple useful attribution paths:

- NVIDIA’s own client list
- device-node ownership via `fuser` / `lsof`
- process monitors such as `nvidia-smi pmon`
- systemd service inspection
- application launch environment review

## 10.1 First-line attribution commands

```bash
sudo cat /proc/driver/nvidia/clients
nvidia-smi
nvidia-smi pmon -c 1
sudo fuser -v /dev/nvidia* /dev/dri/renderD* 2>/dev/null
sudo lsof /dev/nvidia* /dev/dri/renderD* 2>/dev/null
```

### What each command tells you

| Command | What it helps identify |
|---|---|
| `/proc/driver/nvidia/clients` | NVIDIA driver clients known to the kernel driver |
| `nvidia-smi` | Running GPU processes, utilization, memory use |
| `nvidia-smi pmon -c 1` | One-shot process monitor |
| `fuser` | Which PIDs currently hold device nodes open |
| `lsof` | Another view of open device files, often easier to script/filter |

> [!warning]
> Some of these tools can wake the dGPU. Use them deliberately, capture the information you need, then close them.

## 10.2 Common real-world offenders

Typical wake blockers include:

- `ollama` or other local AI/LLM services using CUDA
- background transcoders using NVENC/NVDEC
- OBS
- Sunshine or similar game-streaming stacks
- browsers or Electron applications accidentally launched on the dGPU
- notification daemons, bars, or helper services that initialize a Vulkan context on the dGPU
- `nvidia-persistenced`
- your own monitoring scripts or desktop widgets

## 10.3 Practical debugging order

1. Confirm firmware is in **hybrid** mode.
2. Confirm Hyprland’s primary renderer is the **iGPU**, not the dGPU.
3. Disconnect dGPU-routed external displays if testing deep idle.
4. Check `power/control` and `runtime_status`.
5. Inspect `/proc/driver/nvidia/clients`.
6. Run `fuser` / `lsof` on `/dev/nvidia*` and relevant `/dev/dri/renderD*`.
7. Stop suspicious services one at a time.
8. Re-check `runtime_status` after each change.

---

## 11. `ollama` and Similar AI Services

If `ollama` is configured to use CUDA, it can keep the dGPU awake while the service remains running.

Check both system and user units as applicable:

```bash
systemctl status ollama.service
systemctl --user status ollama.service
journalctl -b -u ollama.service
journalctl --user -b -u ollama.service
```

Disable it if you do not want a continuously available CUDA backend:

```bash
sudo systemctl disable --now ollama.service
systemctl --user disable --now ollama.service
```

> [!note]
> Use only the unit form that actually exists on your system. Some installations expose a system service, some a user service, and some neither.

---

## 12. Per-Service GPU Preference Overrides

If a specific long-lived service keeps touching the dGPU, override **that service**, not the whole system.

## 12.1 General systemd user drop-in example

```bash
mkdir -p ~/.config/systemd/user/example.service.d
cat > ~/.config/systemd/user/example.service.d/10-prefer-igpu.conf <<'EOF'
[Service]
# Vulkan-only override example. Use the correct ICD JSON for your iGPU vendor.
Environment=VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json
EOF

systemctl --user daemon-reload
systemctl --user restart example.service
```

> [!warning]
> `VK_DRIVER_FILES` is a **Vulkan-only** override. It does not control all graphics APIs.

> [!note]
> Older guides often use `VK_ICD_FILENAMES`. On current Vulkan loader stacks, prefer `VK_DRIVER_FILES`. Use `VK_ICD_FILENAMES` only if you are targeting an older compatibility path intentionally.

### Common ICD JSON examples

| Vendor | Typical path |
|---|---|
| Intel | `/usr/share/vulkan/icd.d/intel_icd.x86_64.json` |
| AMD | `/usr/share/vulkan/icd.d/radeon_icd.x86_64.json` |
| NVIDIA | `/usr/share/vulkan/icd.d/nvidia_icd.json` |

## 12.2 Example: `swaync` user-service override

If a specific notification daemon or helper repeatedly initializes on the wrong GPU, use a targeted override rather than a global hack.

```bash
mkdir -p ~/.config/systemd/user/swaync.service.d
cat > ~/.config/systemd/user/swaync.service.d/10-prefer-igpu.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/env VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json /usr/bin/swaync
EOF

systemctl --user daemon-reload
systemctl --user restart swaync.service
```

> [!warning]
> This example is only appropriate if:
> - the service actually uses Vulkan in your build/runtime path
> - Intel is your iGPU vendor
> - you have verified this specific service is part of the wake problem

Do **not** generalize a service-specific workaround into a system-wide default.

---

## 13. Monitoring Tools and Their Caveats

## 13.1 `nvidia-smi`

`nvidia-smi` is the standard NVIDIA management interface built on NVML.

Common commands:

```bash
nvidia-smi
nvidia-smi -q -d POWER
nvidia-smi pmon -c 1
```

### Power-limit example

```bash
sudo nvidia-smi -pl 90
```

This sets a temporary power cap in watts, if supported by the GPU/driver. It resets after reboot unless you reapply it elsewhere.

> [!note]
> Power limiting is a thermal/performance tuning tool. It is not the same as RTD3 and does not replace runtime suspend.

## 13.2 Persistence mode

If supported on your GPU/driver, persistence mode keeps the driver initialized between workloads. That is generally undesirable on hybrid laptops focused on battery life.

Inspect it:

```bash
nvidia-smi -q | grep -i 'Persistence Mode'
```

If your platform supports toggling it:

```bash
sudo nvidia-smi -pm 0
```

> [!warning]
> Do not enable persistence mode on a hybrid laptop unless you specifically want to sacrifice RTD3 behavior.

## 13.3 `nvtop`

```bash
nvtop
```

Useful for live GPU process monitoring, but it can interfere with idle testing.

## 13.4 `intel_gpu_top`

```bash
sudo intel_gpu_top
```

This is useful for confirming the **iGPU** is doing the desktop work while the dGPU should be idle.

## 13.5 `vainfo`

```bash
vainfo
```

Useful for checking hardware video acceleration capabilities. It is not an RTD3 tool, but it helps diagnose whether media apps are likely to touch the wrong GPU.

> [!warning]
> Some diagnostics themselves create graphics contexts. Use them for inspection, then close them before checking idle runtime status.

---

## 14. Suspend, Hibernate, and Resume

## 14.1 NVIDIA sleep helper services

If suspend or resume is unstable with the proprietary NVIDIA driver, first inspect and, if needed, enable the packaged helper services:

```bash
sudo systemctl enable nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service
```

Check them:

```bash
systemctl status nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service
```

### What these help with

Depending on driver generation and platform, these services may help with:

- resume corruption
- VRAM restoration issues
- black screens after suspend
- compositor instability after wake

> [!note]
> Many systems suspend and resume correctly without extra manual tuning. Enable these services when you are solving a real issue, or when your current Arch/NVIDIA packaging guidance recommends them for your exact stack.

## 14.2 Do not use `nvidia-persistenced` as a “fix” for suspend

`nvidia-persistenced` is not a battery-friendly solution for hybrid laptops. It can keep the dGPU active and interfere with runtime power savings.

## 14.3 If resume is still broken

If you still have resume issues after validating the helper services:

1. check kernel logs after resume:
   ```bash
   journalctl -b -k | grep -iE 'nvidia|pm:|suspend|resume|hibernate'
   ```
2. validate whether the issue is:
   - compositor-specific
   - external-monitor-specific
   - hibernate-only
   - Wayland-only vs Xwayland-app-only
3. only then consider targeted parameters such as:
   - `NVreg_PreserveVideoMemoryAllocations=1`
   - other documented resume-related knobs
4. apply one change at a time and re-test

> [!warning]
> Do not copy full “suspend fix” bundles from random guides. They often mix unrelated settings from different driver generations and break more than they fix.

---

## 15. Low-Level Interfaces: `/sys`, `/proc`, and `modprobe`

## 15.1 `/sys` is the authoritative runtime-PM interface

For RTD3 work, the most important files are usually:

```text
/sys/bus/pci/devices/<PCI-ID>/power/control
/sys/bus/pci/devices/<PCI-ID>/power/runtime_status
/sys/bus/pci/devices/<PCI-ID>/power/runtime_suspended_time
```

### Interpretation

| File | Meaning |
|---|---|
| `power/control` | Runtime-PM policy; `auto` allows runtime suspend, `on` pins device awake |
| `runtime_status` | Current runtime state, such as `active` or `suspended` |
| `runtime_suspended_time` | How long the device has accumulated in the suspended state |

> [!important]
> Prefer `runtime_status` over older or less useful paths such as `/sys/.../power_state` when diagnosing runtime suspend.

## 15.2 `/proc/driver/nvidia/gpus/.../power`

Example:

```bash
cat /proc/driver/nvidia/gpus/0000:01:00.0/power
```

This file is a driver-provided inspection interface that often reports:

- runtime D3 support status
- whether fine-grained PM is enabled
- current power-management conditions
- reasons runtime D3 may be blocked

> [!warning]
> Treat this file as a **read-only diagnostic interface** in normal use.

The safe rule is:

- use `/proc/.../power` to **observe**
- use `nvidia-smi` or documented module/sysfs configuration to **act**

## 15.3 `/proc` is virtual, not on-disk storage

`/proc` is a kernel-provided virtual filesystem. Reading a file there often causes the kernel or driver to generate live state on demand. That is why it is useful for diagnostics and why it should not be treated like normal static config storage.

## 15.4 `modprobe` and modprobe configuration

Useful command set:

```bash
modinfo -p nvidia
sudo modprobe nvidia
sudo modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia
```

> [!warning]
> You generally cannot safely unload NVIDIA modules while an active graphical session is using them.

Configuration files are read from:

```text
/usr/lib/modprobe.d/
/etc/modprobe.d/
```

Local files in `/etc/modprobe.d/` are where your persistent overrides belong.

---

## 16. Advanced Verification and Audit Commands

## 16.1 Inspect loaded NVIDIA module parameters

```bash
grep -R -H . /sys/module/nvidia/parameters 2>/dev/null
```

## 16.2 See whether packaged NVIDIA services exist

```bash
ls -1 /usr/lib/systemd/system/nvidia-*.service
```

## 16.3 Check the user session environment for suspect overrides

```bash
systemctl --user show-environment | grep -E '^(AQ_DRM_DEVICES|VK_DRIVER_FILES|VK_ICD_FILENAMES|LIBVA_DRIVER_NAME|__GLX_VENDOR_LIBRARY_NAME)='
```

## 16.4 Search the journal for power-management clues

```bash
journalctl -b -k | grep -iE 'nvidia|runtime|pm:|suspend|resume|d3'
journalctl -b | grep -iE 'nvidia|runtime|d3|cuda|nvenc|vulkan'
```

---

## 17. Troubleshooting Matrix

| Symptom | Likely Causes | First Checks |
|---|---|---|
| `runtime_status` never becomes `suspended` | active client, dGPU owns desktop, external monitor on dGPU, persistence enabled | `AQ_DRM_DEVICES`, external displays, `/proc/driver/nvidia/clients`, `fuser`, `lsof` |
| `power/control` is `on` after reboot | missing or ineffective udev rule | inspect `/etc/udev/rules.d/80-nvidia-pm.rules`, reboot, audit all child functions |
| Only function `.0` is `auto`, others are not | incomplete udev matching | inspect audio/USB/UCSI functions via `/sys/bus/pci/devices/<base>.*` |
| `nvidia-powerd` fails | unsupported platform | `journalctl -b -u nvidia-powerd.service`, disable service if unsupported |
| Battery life is poor despite low GPU utilization | GPU is still active, not in RTD3 | `runtime_status`, `runtime_suspended_time`, compositor GPU choice |
| dGPU wakes immediately after suspending | daemon or monitor polling GPU | stop `nvtop`, close widgets, inspect services and logs |
| Resume corruption / black screen after suspend | missing sleep helpers, compositor resume issue, VRAM restore issue | enable/check NVIDIA sleep services, inspect resume logs |
| GPU appears idle but won’t suspend with external monitor attached | output physically routed to dGPU | disconnect external display and re-test |
| `nvidia-smi` shows activity but you do not know why | hidden service or offloaded app | `/proc/driver/nvidia/clients`, `nvidia-smi pmon -c 1`, `fuser`, `lsof`, systemd unit inspection |

---

## 18. Command Reference

## 18.1 Runtime-PM verification

```bash
cat "/sys/bus/pci/devices/0000:01:00.0/power/control"
cat "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status"
cat "/sys/bus/pci/devices/0000:01:00.0/power/runtime_suspended_time"
cat "/proc/driver/nvidia/gpus/0000:01:00.0/power"
```

## 18.2 Module parameter verification

```bash
grep -H . /sys/module/nvidia/parameters/NVreg_DynamicPowerManagement
grep -R -H . /sys/module/nvidia/parameters 2>/dev/null
modinfo -p nvidia | grep NVreg_
```

## 18.3 Process attribution

```bash
sudo cat /proc/driver/nvidia/clients
nvidia-smi
nvidia-smi pmon -c 1
sudo fuser -v /dev/nvidia* /dev/dri/renderD* 2>/dev/null
sudo lsof /dev/nvidia* /dev/dri/renderD* 2>/dev/null
```

## 18.4 Service inspection

```bash
systemctl status nvidia-powerd.service
systemctl status nvidia-persistenced.service
systemctl status ollama.service
systemctl --user status ollama.service
systemctl status nvidia-suspend.service nvidia-hibernate.service nvidia-resume.service
```

## 18.5 Logs

```bash
journalctl -b -k | grep -iE 'nvidia|runtime|pm:|suspend|resume|d3'
journalctl -b -u nvidia-powerd.service
journalctl --user -b | grep -iE 'vulkan|cuda|nvidia'
```

---

## 19. Permanent Operational Rules

> [!important]
> Treat these as the long-term rules for hybrid-laptop sanity:
>
> 1. Use the iGPU for the compositor unless you intentionally want dGPU-primary behavior.
> 2. Offload only the applications that actually need the dGPU.
> 3. Do not leave monitoring tools running while testing idle power management.
> 4. Do not enable persistence mode or `nvidia-persistenced` on a battery-focused hybrid laptop.
> 5. Use `power/control`, `runtime_status`, and `runtime_suspended_time` as the low-level truth.
> 6. Use `/proc/driver/nvidia/clients`, `fuser`, `lsof`, and service inspection to find wake blockers.
> 7. Add advanced `NVreg_*` parameters only when you are solving a specific documented problem.
> 8. Re-test after every single change rather than applying a bundle of tweaks at once.

---

## 20. Glossary

| Term | Meaning |
|---|---|
| **RTD3** | Runtime D3 PCI power management; deep idle suspend state for the dGPU |
| **D3cold** | Deep suspended PCI device state commonly associated with successful RTD3 behavior |
| **P-state** | Active GPU performance state while the GPU is powered on |
| **Runtime PM** | Linux kernel framework for suspending idle devices during system runtime |
| **Dynamic Boost** | NVIDIA notebook feature that shifts power budget between CPU and GPU under load |
| **Persistence mode** | NVIDIA mode that keeps the driver initialized between workloads; usually bad for hybrid-laptop battery life |
| **UCSI** | USB Type-C Connector System Software Interface; some dGPUs expose a related controller function |
| **NVENC / NVDEC** | NVIDIA hardware video encode / decode engines |
| **NVML** | NVIDIA Management Library used by tools such as `nvidia-smi` and `nvtop` |
| **`VK_DRIVER_FILES`** | Vulkan loader environment variable for restricting/overriding discovered ICDs in a targeted context |

> [!summary]
> If RTD3 is your goal, the most important three truths are:
> - the dGPU must not be the compositor’s active render owner
> - no service or application may still be using it
> - every relevant NVIDIA PCI function must be allowed to runtime-suspend
