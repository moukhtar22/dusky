# Verifying GPU Acceleration on Arch Linux

> [!note] Related notes
> - [[Nvidia RAW]]
> - [[Nvidia Packages]]
> - [[CPU]]

## Scope

This note is the canonical reference for verifying **hardware-accelerated graphics** on modern **Arch Linux** systems, especially when running:

- **Wayland**
- **Hyprland**
- **UWSM**
- **single-GPU or multi-GPU** setups
- **Intel / AMD / NVIDIA** drivers

It assumes the appropriate drivers are already installed.

---

## What to Verify on a Modern Wayland System

On current Linux desktops, GPU acceleration is not reliably reduced to a single `Yes/No` line.

The old X11-era command:

```bash
glxinfo | grep "direct rendering"
```

is **not sufficient** on a modern Wayland session because it only checks **GLX**, which is relevant to **X11/Xwayland clients**, not to the Wayland compositor itself.

Under **Hyprland**, a healthy graphics stack means all of the following are correct:

1. The **kernel DRM/KMS driver** is loaded and bound to the GPU.
2. **Hyprland/Aquamarine** is using the intended DRM device.
3. **OpenGL/EGL** uses a hardware renderer, not software rasterization.
4. **Vulkan** sees the correct hardware ICD/device.
5. **VA-API** video decode uses the correct userspace driver, if needed.

> [!warning]
> `direct rendering: Yes` does **not** guarantee hardware acceleration, and `glxinfo` can fail entirely if Xwayland is disabled. Prefer the checks in this note.

---

## Install Verification Tools

Install the standard userspace tools used in this note:

```bash
sudo pacman -S --needed mesa-utils vulkan-tools libva-utils pciutils
```

### What these packages provide

| Package | Important tools |
|---|---|
| `mesa-utils` | `glxinfo`, `glxgears`, `eglinfo` |
| `vulkan-tools` | `vulkaninfo`, `vkcube` |
| `libva-utils` | `vainfo` |
| `pciutils` | `lspci` |

Optional but useful:

```bash
sudo pacman -S --needed drm_info
```

---

## Fast Verification Checklist

If you only need a quick pass/fail workflow, run these in order:

```bash
lspci -nnk -d ::03xx
ls -l /dev/dri/by-path
eglinfo -B
glxinfo -B
vulkaninfo --summary
vainfo
```

Then look for these failure indicators:

- `llvmpipe`
- `softpipe`
- `swrast`
- `kms_swrast`
- `lavapipe`

If any of those appear as the active renderer for the workload you care about, you are not using full hardware acceleration for that path.

---

## 1. Inspect GPU Topology

### List all display-class PCI devices

```bash
lspci -nnk -d ::03xx
```

This shows:

- all GPUs / display controllers
- PCI IDs
- the **kernel driver in use**
- alternative kernel modules

Example:

```text
00:02.0 VGA compatible controller [0300]: Intel Corporation Alder Lake-P GT2 [Iris Xe Graphics] [8086:46a6] (rev 0c)
	Kernel driver in use: i915
	Kernel modules: i915
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile] [10de:25a0] (rev a1)
	Kernel driver in use: nvidia
	Kernel modules: nouveau, nvidia_drm, nvidia
```

### Map PCI devices to DRM nodes

```bash
ls -l /dev/dri/by-path
```

Example:

```text
pci-0000:00:02.0-card   -> ../card1
pci-0000:00:02.0-render -> ../renderD128
pci-0000:01:00.0-card   -> ../card0
pci-0000:01:00.0-render -> ../renderD129
```

### Understand the node types

| Node type | Purpose |
|---|---|
| `/dev/dri/cardN` | DRM/KMS scan-out device used by compositors for displays/connectors |
| `/dev/dri/renderDN` | Render node used for rendering/compute/video decode without display ownership |

> [!note]
> For **Hyprland/Aquamarine GPU selection**, use **card nodes**, not render nodes.

### Resolve a specific stable by-path entry

```bash
readlink -f /dev/dri/by-path/pci-0000:00:02.0-card
readlink -f /dev/dri/by-path/pci-0000:01:00.0-card
```

This is useful because `card0`, `card1`, etc. can change across boots, while `/dev/dri/by-path/...` tracks the PCI device.

### Optional: check which GPU firmware marked as boot VGA

```bash
grep . /sys/class/drm/card*/device/boot_vga 2>/dev/null
```

`boot_vga=1` often identifies the firmware-primary GPU, but it is **not** the only valid choice for a Wayland compositor.

---

## 2. Verify Kernel Driver Binding

### Confirm the expected kernel module is loaded

```bash
lsmod | grep -E 'nvidia(_drm|_modeset|_uvm)?|amdgpu|i915|xe'
```

### Check kernel logs for DRM/driver errors

```bash
sudo journalctl -b -k | grep -iE 'drm|nvidia|amdgpu|i915|xe'
```

Look for:

- firmware load failures
- modeset failures
- GPU hangs / resets
- missing KMS
- BAR / PCI resource issues

### NVIDIA-specific: verify DRM modesetting is enabled

```bash
cat /sys/module/nvidia_drm/parameters/modeset
```

Expected output:

```text
Y
```

> [!warning]
> Proprietary NVIDIA Wayland sessions require `nvidia_drm.modeset=1`. If this is not enabled, Hyprland may fail to start correctly or fall back to broken behavior.

---

## 3. Verify Hardware Rendering in Userspace

## EGL / OpenGL (Wayland-relevant)

For native Wayland/EGL validation, use:

```bash
eglinfo -B
```

Focus on the renderer/vendor lines. A healthy setup should show your real GPU vendor/renderer.

### Good signs

- Intel renderer string
- AMD `radeonsi` / Radeon renderer
- NVIDIA renderer/vendor strings
- Mesa hardware renderer matching your GPU

### Bad signs

Any of the following indicate software rendering:

- `llvmpipe`
- `softpipe`
- `swrast`
- `kms_swrast`

> [!note]
> `eglinfo -B` is generally more relevant than a plain GLX check when you are running a Wayland compositor.

---

## GLX / Xwayland

To verify the Xwayland OpenGL path:

```bash
glxinfo -B
```

Useful fields:

- `direct rendering`
- `OpenGL vendor string`
- `OpenGL renderer string`
- `OpenGL core profile version string`

### Good signs

- Renderer matches Intel / AMD / NVIDIA hardware
- `direct rendering: Yes`

### Bad signs

- Renderer is `llvmpipe`, `softpipe`, or `swrast`

> [!warning]
> `glxinfo -B` only tests the **GLX/Xwayland** path. It is **not** authoritative for the Wayland compositor itself.

> [!note]
> If Xwayland is disabled, `glxinfo` may fail with an “unable to open display” error. That does **not** automatically mean Wayland rendering is broken.

---

## Vulkan

To verify Vulkan device enumeration:

```bash
vulkaninfo --summary
```

What to check:

- your real GPU appears in the device list
- the intended driver/ICD is present
- the active application is not selecting a software Vulkan implementation

### Software Vulkan indicator

- `lavapipe`

> [!note]
> On some systems, software Vulkan (`lavapipe`) may be installed alongside hardware drivers. Its presence in the list is not automatically a problem. It is only a problem if your applications or compositor are actually using it.

---

## VA-API Video Decode

To check VA-API:

```bash
vainfo
```

This verifies video decode acceleration and which VA-API driver is being used.

### Common driver names

| Vendor | Typical `LIBVA_DRIVER_NAME` |
|---|---|
| Intel | `iHD` |
| Intel, older generations | `i965` |
| AMD | `radeonsi` |
| NVIDIA with `libva-nvidia-driver` | `nvidia` |

If auto-detection is ambiguous on a hybrid system, test explicitly:

```bash
LIBVA_DRIVER_NAME=iHD vainfo
LIBVA_DRIVER_NAME=radeonsi vainfo
LIBVA_DRIVER_NAME=nvidia vainfo
```

> [!note]
> `vainfo` checks **video decode**, not general 3D rendering. A system can have working OpenGL/Vulkan and still have broken VA-API, or vice versa.

---

## 4. Visual Smoke Tests

These are **functional smoke tests**, not benchmarks.

### Vulkan smoke test

```bash
vkcube
```

If a rotating cube window appears, Vulkan is functioning for that path.

### GLX/Xwayland smoke test

```bash
glxgears
```

If the gears animate smoothly, GLX/Xwayland rendering is at least functional.

> [!warning]
> `glxgears` is **not** a performance benchmark. Ignore the FPS as a measure of real GPU performance.

> [!note]
> `glxgears` tests the GLX/Xwayland path, not native Wayland compositor rendering.

---

## 5. Hyprland + UWSM + Multi-GPU Reference

## Critical Concepts

### `AQ_DRM_DEVICES` is the correct variable for current Hyprland

Modern Hyprland uses **Aquamarine** for its DRM backend. For GPU selection and priority, use:

```bash
AQ_DRM_DEVICES
```

> [!warning]
> Old guides that use `WLR_DRM_DEVICES` are for wlroots-based compositor behavior and are **not** the correct reference for current Hyprland/Aquamarine GPU selection.

### `AQ_DRM_DEVICES` uses a colon-separated priority list

Example shape:

```bash
export AQ_DRM_DEVICES="/dev/dri/card1:/dev/dri/card0"
```

The **first entry** is the preferred device.

### Do not put raw `/dev/dri/by-path/...` paths directly into `AQ_DRM_DEVICES`

This is a subtle but important failure case.

These paths include PCI BDF strings such as:

```text
/dev/dri/by-path/pci-0000:00:02.0-card
```

Because `AQ_DRM_DEVICES` itself is **colon-separated**, raw by-path entries contain extra `:` characters and become ambiguous/broken.

#### Correct approach

Resolve the by-path symlink at session start:

```bash
export AQ_DRM_DEVICES="$(readlink -f -- '/dev/dri/by-path/pci-0000:00:02.0-card'):$(readlink -f -- '/dev/dri/by-path/pci-0000:01:00.0-card')"
```

This expands to a safe runtime value like:

```text
/dev/dri/card1:/dev/dri/card0
```

> [!note]
> This gives you both:
> - **stable PCI-based selection**
> - **colon-safe runtime expansion**

### `AQ_DRM_DEVICES` controls compositor device priority, not generic app offload

It determines which DRM/KMS device Hyprland/Aquamarine prefers.

It does **not** by itself mean:

- every application renders on that GPU
- every Vulkan app uses that GPU
- PRIME offload is automatically configured

Those are separate topics.

---

## 6. UWSM Environment Drop-In Examples

The following examples assume shell-style UWSM environment drop-ins in:

```bash
~/.config/uwsm/env.d/
```

Create the directory if needed:

```bash
mkdir -p ~/.config/uwsm/env.d
```

> [!warning]
> Environment changes are applied when the session starts. A simple `hyprctl reload` is **not** enough. Log out and start the UWSM/Hyprland session again.

---

## Example Topology: Intel iGPU + NVIDIA dGPU

Given this hardware:

```bash
lspci -d ::03xx
```

```text
00:02.0 VGA compatible controller: Intel Corporation Alder Lake-P GT2 [Iris Xe Graphics] (rev 0c)
01:00.0 VGA compatible controller: NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile] (rev a1)
```

and this mapping:

```bash
ls -l /dev/dri/by-path
```

```text
pci-0000:00:02.0-card   -> ../card1
pci-0000:00:02.0-render -> ../renderD128
pci-0000:01:00.0-card   -> ../card0
pci-0000:01:00.0-render -> ../renderD129
```

Then:

- **Intel-first session** resolves to `card1:card0`
- **NVIDIA-first session** resolves to `card0:card1`

---

## Example A: Intel-Primary Hyprland Session

This is usually the most stable and power-efficient choice on hybrid laptops.

```bash
cat > ~/.config/uwsm/env.d/gpu <<'EOF'
export AQ_DRM_DEVICES="$(readlink -f -- '/dev/dri/by-path/pci-0000:00:02.0-card'):$(readlink -f -- '/dev/dri/by-path/pci-0000:01:00.0-card')"
export MOZ_ENABLE_WAYLAND=1
export ELECTRON_OZONE_PLATFORM_HINT=auto
export LIBVA_DRIVER_NAME=iHD
EOF
```

### Notes

- `LIBVA_DRIVER_NAME=iHD` is correct for modern Intel GPUs using `intel-media-driver`.
- On older Intel generations, the correct VA-API driver may instead be `i965`.
- `MOZ_ENABLE_WAYLAND=1` and `ELECTRON_OZONE_PLATFORM_HINT=auto` are **application compatibility hints**, not compositor acceleration controls.

---

## Example B: NVIDIA-Primary Hyprland Session

Use this only when you intentionally want Hyprland driven by the proprietary NVIDIA stack.

```bash
cat > ~/.config/uwsm/env.d/gpu <<'EOF'
export AQ_DRM_DEVICES="$(readlink -f -- '/dev/dri/by-path/pci-0000:01:00.0-card'):$(readlink -f -- '/dev/dri/by-path/pci-0000:00:02.0-card')"
export GBM_BACKEND=nvidia-drm
export __GLX_VENDOR_LIBRARY_NAME=nvidia
export MOZ_ENABLE_WAYLAND=1
export ELECTRON_OZONE_PLATFORM_HINT=auto
export LIBVA_DRIVER_NAME=nvidia
EOF
```

### Notes

- `GBM_BACKEND=nvidia-drm` is for the proprietary NVIDIA GBM path.
- `__GLX_VENDOR_LIBRARY_NAME=nvidia` affects **GLX/Xwayland** clients.
- `LIBVA_DRIVER_NAME=nvidia` only makes sense if `libva-nvidia-driver` is installed and you intentionally want NVIDIA VA-API.
- Do **not** set these NVIDIA-specific variables globally unless NVIDIA is the intended session-primary GPU.

> [!note]
> On many laptops, the internal panel is wired to the iGPU. Even if a dGPU exists, an **Intel-primary** compositor is often the best default unless you have a specific display-routing or performance reason to prefer NVIDIA.

---

## Example C: AMD-Primary Hyprland Session

For an AMD primary GPU:

```bash
cat > ~/.config/uwsm/env.d/gpu <<'EOF'
export AQ_DRM_DEVICES="$(readlink -f -- '/dev/dri/by-path/pci-0000:03:00.0-card')"
export MOZ_ENABLE_WAYLAND=1
export ELECTRON_OZONE_PLATFORM_HINT=auto
export LIBVA_DRIVER_NAME=radeonsi
EOF
```

---

## Inspect the Active Session Environment

After logging back in, verify the active user environment:

```bash
systemctl --user show-environment | grep -E 'AQ_DRM_DEVICES|GBM_BACKEND|__GLX_VENDOR_LIBRARY_NAME|LIBVA_DRIVER_NAME|MOZ_ENABLE_WAYLAND|ELECTRON_OZONE_PLATFORM_HINT'
```

Also verify interactively:

```bash
printf '%s\n' "$AQ_DRM_DEVICES"
```

To inspect priority order line-by-line:

```bash
tr ':' '\n' <<<"$AQ_DRM_DEVICES"
```

---

## 7. Testing the Secondary GPU Explicitly

On hybrid systems, it is often useful to verify the non-primary GPU independently.

## Mesa-based offload testing

For Intel/AMD hybrid setups using Mesa offload:

```bash
DRI_PRIME=1 glxinfo -B
DRI_PRIME=1 vulkaninfo --summary
```

## Proprietary NVIDIA offload testing

On Arch, the usual method is `prime-run` from `nvidia-prime`:

```bash
prime-run glxinfo -B
prime-run vulkaninfo --summary
```

> [!note]
> `prime-run` is primarily useful for testing or launching applications on the NVIDIA dGPU. It is separate from selecting the compositor's DRM device with `AQ_DRM_DEVICES`.

## Testing VA-API against a specific render node

This is the cleanest way to test decode acceleration per GPU:

```bash
vainfo --display drm --device "$(readlink -f /dev/dri/by-path/pci-0000:00:02.0-render)"
vainfo --display drm --device "$(readlink -f /dev/dri/by-path/pci-0000:01:00.0-render)"
```

Use `*-render` here, not `*-card`.

---

## 8. Hyprland and UWSM Log Inspection

Under a UWSM-managed session, user-space compositor logs are best checked through the **user journal**, not Xorg logs.

### User journal

```bash
journalctl --user -b | grep -iE 'Hyprland|Aquamarine|gbm|egl|drm|renderer'
```

### Kernel journal

```bash
sudo journalctl -b -k | grep -iE 'drm|nvidia|amdgpu|i915|xe'
```

> [!warning]
> `~/.local/share/xorg/Xorg.0.log` is not the primary diagnostic target for a Hyprland Wayland session. Use the journal instead.

---

## 9. Common Failure Indicators

| Symptom | Meaning | Typical cause |
|---|---|---|
| `llvmpipe`, `softpipe`, `swrast`, `kms_swrast` | Software OpenGL/EGL rasterization | Missing/broken driver, wrong environment override, failed compositor/GBM path |
| `lavapipe` as active Vulkan device | Software Vulkan | Missing/broken Vulkan ICD or wrong app/device selection |
| `glxinfo` cannot open display | No GLX/Xwayland context | Running outside graphical session, Xwayland disabled, wrong environment |
| `vainfo` fails or selects wrong driver | Broken/ambiguous video decode path | Missing VA-API driver, wrong `LIBVA_DRIVER_NAME`, hybrid mismatch |
| No `/dev/dri/card*` or `/dev/dri/renderD*` | DRM stack not initialized correctly | Missing KMS, bad driver bind, boot issue |
| Hyprland starts on wrong GPU | Wrong Aquamarine device priority | Incorrect `AQ_DRM_DEVICES` order or bad path selection |

---

## 10. Troubleshooting Workflow

### 1. Confirm driver binding first

```bash
lspci -nnk -d ::03xx
```

This is more important than just `lsmod`, because a module can be loaded without being bound to the device.

### 2. Check for software-forcing environment variables

```bash
env | grep -E 'LIBGL_ALWAYS_SOFTWARE|MESA_LOADER_DRIVER_OVERRIDE|VK_DRIVER_FILES|VK_ICD_FILENAMES|AQ_DRM_DEVICES|GBM_BACKEND|__GLX_VENDOR_LIBRARY_NAME|LIBVA_DRIVER_NAME'
```

Variables like these can force or break device selection.

### 3. Re-check device mapping

```bash
ls -l /dev/dri/by-path
```

If needed:

```bash
readlink -f /dev/dri/by-path/pci-0000:00:02.0-card
readlink -f /dev/dri/by-path/pci-0000:01:00.0-card
```

### 4. Re-check compositor logs

```bash
journalctl --user -b | grep -iE 'Hyprland|Aquamarine|gbm|egl|drm|renderer'
```

### 5. Re-check kernel logs

```bash
sudo journalctl -b -k | grep -iE 'drm|nvidia|amdgpu|i915|xe'
```

### 6. Verify that your session was restarted after env changes

A Hyprland config reload does **not** replace the process environment. After changing UWSM env files, log out completely and start a fresh session.

---

## 11. Permissions and User Groups

> [!warning]
> Do **not** treat membership in the `video` group as a universal fix.

On a normal Arch desktop using **systemd-logind** and a local graphical login, access to `/dev/dri/*` is usually granted by **seat ACLs**, not by static group membership.

### Practical rule

- **Normal local UWSM/Hyprland session:** group membership is usually **not** the issue.
- **Container / remote / unusual launch method:** permissions may require additional handling.

If you suspect a seat/ACL issue, check that you are in a proper local login session rather than adding yourself to `video` blindly.

---

## 12. Recommended Interpretation Rules

Use these rules when evaluating output:

1. **Do not rely on only one tool.**
   - `eglinfo`, `glxinfo`, `vulkaninfo`, and `vainfo` each test different paths.

2. **Software renderer strings are the main red flags.**
   - `llvmpipe`, `softpipe`, `swrast`, `kms_swrast`, `lavapipe`

3. **Hybrid systems can legitimately show different GPUs for different APIs.**
   - compositor scan-out, Xwayland GLX, Vulkan apps, and VA-API decode are related but not identical paths

4. **For Hyprland GPU selection, use `AQ_DRM_DEVICES`, not legacy wlroots guidance.**

5. **Use `/dev/dri/by-path` for discovery, but resolve it with `readlink -f` before feeding paths into `AQ_DRM_DEVICES`.**

---

## Minimal Reference Command Set

```bash
sudo pacman -S --needed mesa-utils vulkan-tools libva-utils pciutils

lspci -nnk -d ::03xx
ls -l /dev/dri/by-path

eglinfo -B
glxinfo -B
vulkaninfo --summary
vainfo

lsmod | grep -E 'nvidia(_drm|_modeset|_uvm)?|amdgpu|i915|xe'
sudo journalctl -b -k | grep -iE 'drm|nvidia|amdgpu|i915|xe'
journalctl --user -b | grep -iE 'Hyprland|Aquamarine|gbm|egl|drm|renderer'
```

---

## Bottom Line

For a modern **Arch + Wayland + Hyprland + UWSM** system:

- use **`eglinfo -B`** and **`glxinfo -B`** instead of relying on `direct rendering: Yes/No`
- validate **Vulkan** with **`vulkaninfo --summary`**
- validate **video decode** with **`vainfo`**
- inspect **kernel + user journals**, not Xorg logs
- on multi-GPU Hyprland systems, use **`AQ_DRM_DEVICES`**
- when building `AQ_DRM_DEVICES`, discover with `/dev/dri/by-path/...` but resolve with **`readlink -f`** before session start

If the active renderer is your real GPU vendor and **not** `llvmpipe`/`lavapipe`, the acceleration path is working for that API.
