# Force a UWSM/Hyprland Session to Use One Vulkan Driver on Arch Linux

> [!note]
> This is a **session-wide Vulkan loader override**, not a true system-wide graphics policy.
>
> It affects **native Vulkan applications that inherit your session environment** and use the standard Vulkan loader. It does **not** directly control OpenGL, CUDA, NVENC/NVDEC, VA-API, or other non-Vulkan stacks.

## Overview

Use this when you want your entire Wayland session to **default all Vulkan workloads to a specific driver manifest**, typically the **iGPU** for lower power draw and less heat.

On modern Arch Linux, the correct loader variable is:

- `VK_DRIVER_FILES` — **preferred**
- `VK_ICD_FILENAMES` — legacy compatibility alias seen in older documentation

For a hybrid laptop, this is most useful when the GPUs are exposed by **different Vulkan ICDs**, for example:

- **Intel iGPU + NVIDIA dGPU**
- **Intel iGPU + AMD dGPU**
- **AMD iGPU + NVIDIA dGPU**

> [!warning]
> This method does **not** select between two GPUs that are exposed by the **same ICD**.
>
> Example: an **AMD iGPU + AMD dGPU** pair both using **RADV** usually come from the same `radeon_icd` manifest. In that case, `VK_DRIVER_FILES` alone will not choose one GPU over the other. Use `MESA_VK_DEVICE_SELECT`, `DRI_PRIME`, or an application-specific selector instead.

---

## How It Works

The Vulkan loader discovers installed drivers through **ICD manifest files** (`*.json`), usually under:

- `/usr/share/vulkan/icd.d/`
- `/etc/vulkan/icd.d/`

`VK_DRIVER_FILES` replaces the loader's normal driver search list with the exact manifest file(s) you specify.

If you point it only at the iGPU's manifest, then:

- Vulkan applications see only that driver
- Vulkan offload to the hidden driver no longer works unless you override the environment again for a specific app

> [!tip]
> This is a **good default policy** for battery-saving sessions, but it is a blunt instrument. Use a per-app override when you need the dGPU only for selected workloads.

---

## Important Limits and Side Effects

## What This Affects

| Stack / feature | Affected by `VK_DRIVER_FILES`? | Notes |
| --- | --- | --- |
| Native Vulkan apps | Yes | The loader only sees the listed ICDs |
| DXVK / VKD3D-Proton | Yes | They are Vulkan clients |
| PRIME render offload for **Vulkan** | Yes | Usually stops working unless you override the variable per app |
| OpenGL / GLX / EGL | No | These use other selection mechanisms |
| CUDA / NVENC / NVDEC | No | Separate NVIDIA stacks |
| VA-API / VDPAU | No | Separate video stacks |

## What This Does *Not* Guarantee

Setting `VK_DRIVER_FILES` does **not** by itself power down the dGPU.

Actual power savings still depend on:

- runtime power management
- whether other software touches the dGPU
- whether your NVIDIA RTD3 / hybrid graphics configuration is correct

So the accurate statement is:

- this can keep **Vulkan workloads** off the dGPU
- it can **help** battery life and thermals
- it does **not** guarantee the dGPU is fully off in all cases

> [!warning]
> Older guides often imply this makes the dGPU "invisible to the system." That is incorrect.
>
> It only makes the dGPU's **Vulkan driver manifest invisible to the Vulkan loader** for affected processes.

---

## Common Arch Linux Vulkan ICD Manifest Names

Do **not** guess; always verify on the system itself.

That said, these are the common Arch package filenames:

| Driver stack | Common manifest(s) |
| --- | --- |
| Intel ANV (Mesa) | `/usr/share/vulkan/icd.d/intel_icd.x86_64.json` |
| Intel HASVK (older Intel generations, Mesa) | `/usr/share/vulkan/icd.d/intel_hasvk_icd.x86_64.json` |
| AMD RADV (Mesa) | `/usr/share/vulkan/icd.d/radeon_icd.x86_64.json` |
| AMDVLK | `/usr/share/vulkan/icd.d/amd_icd64.json` |
| NVIDIA proprietary | `/usr/share/vulkan/icd.d/nvidia_icd.json` |

If you run **32-bit Vulkan applications** such as Steam/Proton titles, also include the matching **i686/32-bit** manifest if installed.

Common multilib examples:

| Driver stack | Common 32-bit manifest |
| --- | --- |
| Intel ANV | `/usr/share/vulkan/icd.d/intel_icd.i686.json` |
| Intel HASVK | `/usr/share/vulkan/icd.d/intel_hasvk_icd.i686.json` |
| AMD RADV | `/usr/share/vulkan/icd.d/radeon_icd.i686.json` |
| AMDVLK | `/usr/share/vulkan/icd.d/amd_icd32.json` |

> [!warning]
> On Arch, **Mesa AMD Vulkan is usually `radeon_icd...`**, not `amd_icd...`.
>
> `amd_icd64.json` is typically **AMDVLK**, not RADV.

---

## Prerequisites

Install the basic inspection tools:

```bash
sudo pacman -S --needed vulkan-tools jq
```

`jq` is optional but useful for inspecting manifest contents.

---

## Step 1: Identify the Correct Manifest

### List all installed ICD manifests

```bash
shopt -s nullglob
printf '%s\n' /usr/share/vulkan/icd.d/*.json /etc/vulkan/icd.d/*.json | sort -u
```

### Inspect each manifest's library path

```bash
shopt -s nullglob
for f in /usr/share/vulkan/icd.d/*.json /etc/vulkan/icd.d/*.json; do
  [[ -e $f ]] || continue
  jq -r --arg f "$f" '"\($f) -> \(.ICD.library_path // .library_path // "unknown")"' "$f"
done
```

### Check what Vulkan currently sees

```bash
vulkaninfo --summary
```

Use this to confirm:

- the current GPUs Vulkan enumerates
- the vendor/device names
- whether your working iGPU path is ANV, HASVK, RADV, AMDVLK, or NVIDIA

> [!tip]
> For Intel systems, do **not** assume `intel_icd` is always correct. Some older Intel GPUs use **HASVK** instead of **ANV**.

---

## Step 2: Test the Override Temporarily First

Always test with a one-shot environment before making it permanent.

### Example: force Intel ANV

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
vulkaninfo --summary
```

### Example: force AMD RADV

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
vulkaninfo --summary
```

### If you use 32-bit Vulkan apps

Include both manifests, separated by `:` on Linux:

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json \
vulkaninfo --summary
```

If this test fails, do **not** persist it. Fix the manifest selection first.

> [!warning]
> If you set only the `x86_64` manifest and then launch a 32-bit Vulkan game, the game may fail with loader or driver initialization errors.

---

## Step 3: Persist the Override

## Preferred: UWSM Session Environment

If Hyprland is launched under **UWSM**, set the variable in UWSM's session environment so it is available **before** the compositor, portals, and user units start.

Edit:

```bash
mkdir -p ~/.config/uwsm
nvim ~/.config/uwsm/env
```

Add:

```bash
# Force native Vulkan workloads to Intel ANV by default.
# Include both 64-bit and 32-bit manifests if you use Steam/Proton.
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

If you only use 64-bit native apps:

```bash
export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json
```

> [!note]
> UWSM environment files use shell-style syntax, so `export VAR=value` is correct here.

---

## Good Alternative: `environment.d`

If you want a session-manager-neutral method that also integrates well with the user systemd environment, use `environment.d`.

Create:

```bash
mkdir -p ~/.config/environment.d
nvim ~/.config/environment.d/90-vulkan-driver.conf
```

Add:

```ini
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

Use this if:

- you do not use UWSM
- you want a cleaner user-environment approach than putting the variable in compositor config
- you want systemd user services to see the variable reliably

> [!note]
> `environment.d` files are **not shell syntax**. Do **not** use `export` there.

---

## Fallback Only: Hyprland `env =`

If you launch Hyprland directly and deliberately want the setting in Hyprland config, add it there.

Edit:

```bash
nvim ~/.config/hypr/hyprland.conf
```

Add:

```ini
# Force native Vulkan workloads to Intel ANV by default.
env = VK_DRIVER_FILES,/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

> [!warning]
> `env =` in Hyprland is the **least complete** session-wide method here.
>
> It is fine for many normal app launch paths, but it is not the best place for environment policy if you use:
>
> - UWSM
> - systemd user units
> - D-Bus activated services
> - xdg-desktop-portal launched components
>
> Prefer **UWSM** or **`environment.d`** when possible.

> [!warning]
> Do **not** rely on `hyprctl reload` for this kind of change.
>
> A fresh login is the correct way to apply or remove a session-wide environment override.

---

## Step 4: Start a Fresh Session

Log out completely and log back in.

A reboot is **not** required.

---

## Verification

## Confirm the variable exists in your shell

```bash
printenv VK_DRIVER_FILES
```

## Confirm the user systemd environment sees it

```bash
systemctl --user show-environment | grep '^VK_DRIVER_FILES='
```

## Confirm Vulkan now enumerates the intended device path

```bash
vulkaninfo --summary
```

Read the listed `deviceName` / GPU entries and confirm they match the intended GPU.

### Optional: inspect loader behavior during troubleshooting

```bash
VK_LOADER_DEBUG=error,warn vulkaninfo --summary
```

This is useful if the loader rejects a manifest or cannot load the corresponding shared library.

---

## Optional: Check Whether the dGPU Is Actually Runtime-Suspended

First identify the dGPU PCI address:

```bash
lspci -Dnn | grep -E 'VGA|3D|Display'
```

Then inspect its runtime power state, for example:

```bash
cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status
```

Typical values include:

- `active`
- `suspended`
- `unsupported`

> [!warning]
> `nvidia-smi` is **not** a great primary verification tool for power-saving checks on laptops.
>
> On many systems it can wake the NVIDIA GPU just by querying it, which disturbs the measurement.

---

## Reverting the Change

Remove or comment out the line in the file you used:

- `~/.config/uwsm/env`
- `~/.config/environment.d/90-vulkan-driver.conf`
- `~/.config/hypr/hyprland.conf`

Then log out and back in.

### Example: UWSM revert

```bash
# export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

### Example: Hyprland revert

```ini
# env = VK_DRIVER_FILES,/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

---

## One-Off Overrides While Keeping the Session Default

Even if the whole session defaults to the iGPU, you can still launch a specific native Vulkan app with a different manifest list.

### Simple shell override

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json your-vulkan-app
```

### Override for a systemd-managed launch path

```bash
systemd-run --user --scope -E VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json your-vulkan-app
```

> [!note]
> If the target application can be 32-bit, append the matching 32-bit manifest too, if your driver stack provides one.

This means the session default does **not** permanently remove dGPU access; it only changes the default environment. A per-process override can still opt back into the dGPU.

---

## Troubleshooting

### `vulkaninfo` fails after setting `VK_DRIVER_FILES`

Likely causes:

- wrong manifest path
- wrong driver stack selected
- 64-bit manifest forced into a 32-bit app path
- sandboxed app using different filesystem paths

Check with:

```bash
VK_LOADER_DEBUG=error,warn vulkaninfo --summary
```

---

### Steam / Proton / older 32-bit titles fail

Add the matching 32-bit manifest:

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json:/usr/share/vulkan/icd.d/intel_icd.i686.json
```

Or the corresponding `radeon_icd.i686.json`, `intel_hasvk_icd.i686.json`, or other correct 32-bit manifest for your stack.

---

### AMD iGPU + AMD dGPU are still both visible

Expected if both are exposed by the same ICD, usually `radeon_icd`.

Use one of these instead:

- `MESA_VK_DEVICE_SELECT`
- `DRI_PRIME`
- per-app GPU selection inside the application
- desktop launch wrappers for specific apps

---

### `prime-run` still works for OpenGL but not Vulkan

Expected.

`VK_DRIVER_FILES` only controls the **Vulkan** loader. OpenGL offload uses a different mechanism.

---

### A launcher, portal, or user service ignores the setting

Move the variable out of Hyprland config and into:

- `~/.config/uwsm/env`, or
- `~/.config/environment.d/*.conf`

Those are more reliable for session-wide policy.

---

### Flatpak Vulkan apps break or ignore the override

Possible cause: the manifest path from the host does not exist inside the Flatpak sandbox.

This method is primarily reliable for **native host applications**. For Flatpak, test separately and use Flatpak-specific overrides only if the in-sandbox path is valid.

---

## Recommended Decision Matrix

| Goal | Best method |
| --- | --- |
| Force one **native Vulkan app** to the iGPU or dGPU | Prefix the command with `VK_DRIVER_FILES=...` |
| Force one **systemd-managed** app | `systemd-run --user --scope -E VK_DRIVER_FILES=...` or a service override |
| Set a **session default** under UWSM | `~/.config/uwsm/env` |
| Set a **session default** without UWSM | `~/.config/environment.d/*.conf` |
| Pick between **two AMD GPUs on RADV** | `MESA_VK_DEVICE_SELECT` / `DRI_PRIME` |
| Use NVIDIA offload for a single Vulkan app | Per-app override with the NVIDIA manifest list |

---

## Bottom Line

- On modern Arch, use **`VK_DRIVER_FILES`**, not old `VK_ICD_FILENAMES` docs.
- Prefer **UWSM env** or **`environment.d`** for a real session-wide default.
- Include **32-bit manifests** if you use Steam/Proton or other 32-bit Vulkan apps.
- This works best for **mixed-vendor** hybrid systems.
- It does **not** replace proper runtime power management, and it does **not** choose between GPUs behind the same ICD.
