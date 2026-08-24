---
title: "Looking Glass — Muxless Passthrough (Native <shmem>)"
tags:
  - kvm
  - vfio
  - looking-glass
  - arch
  - wayland
---

# Looking Glass — Muxless Passthrough (Native `<shmem>`, Aug 2026)

> [!info] Stack
> **Arch · Kernel 7.1.8 · systemd 261 · libvirt 12.6 · QEMU 11.1 · B7+ · Windows 10 (de-bloated)**
> Even on MUX-laptops, follow this muxless path — works across modes.

```
Windows Guest (NVIDIA pass-through)   Kernel SHM                      Host
NVIDIA → VDD (ghost monitor)  →  /dev/shm/looking-glass (RAM, ivshmem-plain)  →  looking-glass-client (Wayland)
LG Host (capture)                 memory-backend-file SHARE
```

| Comp | Role |
|---|---|
| **VDD** | Headless render target (IDD) |
| **LG Host** | Captures NVIDIA fb → shm |
| **`/dev/shm/looking-glass`** | `tmpfs` `ivshmem-plain` region — zero-copy |
| **`looking-glass-client`** | Reads shm, renders via OpenGL on Wayland |
| **`xfreerdp3`** | Rescue RDP while emulated display disabled (`freerdp` pkg) |

## Prereqs

- [ ] VFIO bound (`vfio-pci` per [[Host PC  Preparation for GPU isolation]])
- [ ] VM is Windows 10 libvirt (`qemu:///system`)
- [ ] `kvm` group: `groups | grep kvm`
- [ ] packages: `sudo pacman -S --needed linux-headers` (+ `dkms` if you ever used `kvmfr`)

## Phase 1 — Host: shm sizing & persistence

### 1.1 Packages

```bash
paru -S --needed looking-glass freerdp   # 25_looking_glass.py:install_packages fallback paru
# Windows side: download matching LG Host from https://looking-glass.io/downloads (versions must match exactly)
```

### 1.2 Size (SDR, double-buffer + 10 MiB, power-of-two)

`size = round_up_pow2(width × height × 4 × 2 + 10 MiB)` — `ivshmem-plain` requires power-of-two.

| Target | Frame pair +10 | Final |
|---|---|---|
| 1920×1080 | 16.6 +10 → 25.8 | **32 MiB** (33554432) |
| 2560×1440 | 29.5 +10 → 38.1 | **64 MiB** (67108864) ← laptop 1440p recommendation |
| 3840×2160 | 66.4 +10 → 73.3 | **128 MiB** (134217728) |

> Windows 10 guests lack HDR — keep **SDR**; HDR would overflow static buffer.

### 1.3 tmpfiles (boot-persistent, `f` = create-if-missing, never truncate live guest)

```bash
sudo tee /etc/tmpfiles.d/10-looking-glass.conf <<'EOF'
# Managed by Arsonix (Phase 5)
f /dev/shm/looking-glass 0660 new kvm - -
EOF
# replace `new` with your $USER
```

### 1.4 Create & pre-allocate (bypass `fs.protected_regular=1`)

Do **not** `ftruncate`/sparse — pipeline uses unconditional `posix_fallocate` (tmpfs on 7.1 guarantees it) + `O_EXCL|O_NOFOLLOW` (symlink TOCTOU defense):

```bash
sudo rm -f /dev/shm/looking-glass                         # clear ownership lock
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
# 64 MiB for 1440p (match table above):
sudo fallocate -l 64M /dev/shm/looking-glass
# pipeline does: fd = os.open(/dev/shm/... , O_CREAT|O_EXCL|O_RDWR|O_NOFOLLOW); posix_fallocate; fchown new:kvm; fchmod 0660
sudo chown new:kvm /dev/shm/looking-glass
sudo chmod 0660 /dev/shm/looking-glass
ls -lh /dev/shm/looking-glass   # 64M new kvm
```

> [!warning] Why `posix_fallocate` unconditionally
> Sparse `fallocate` fallback masked real tmpfs ENOSPC. On 7.1 `tmpfs` `fallocate` always succeeds; any failure should abort (host OOM risk), not silently create sparse file causing mid-frame faults. `25_*.py:stage_shm` enforces this.

## Phase 2 — Domain XML: native `<shmem>` (no `xmlns:qemu` hack)

We previously used `<qemu:commandline>` raw `-device ivshmem-plain` / `-object memory-backend-file`. **Deleted** — bypasses libvirt device model, breaks validation/migration, desyncs from `<memballoon>`/NUMA, needs `xmlns:qemu` hatch. Libvirt emits identical QEMU args from `<shmem>` and **creates/labels** `/dev/shm/<name>` itself.

> [!abstract] The ONLY XML needed (inside `<devices>`)
> ```xml
> <shmem name='looking-glass'>
>   <model type='ivshmem-plain'/>
>   <size unit='M'>64</size>
> </shmem>
> <memballoon model='none'/>
> ```
> No `xmlns:qemu`, no `<qemu:commandline>`, no `kvmfr` module. Pipeline helper `25_*.py:transform_domain` strips any legacy `<qemu:commandline>` with `looking-glass|kvmfr|ivshmem` before injecting this.

### 2.1 Edit

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system dumpxml --inactive win_10_dusky > /tmp/old.xml
# interactively:
virsh -c qemu:///system edit win_10_dusky
```

### 2.2 Add `<shmem>` + fix companions

Inside `<devices>`:

```xml
<shmem name="looking-glass">
  <model type="ivshmem-plain"/>
  <size unit="M">64</size>  <!-- match Phase 1 -->
</shmem>
```

Also:

- `<memballoon model='none'/>` (balloon steals DMA-linear memory → latency)
- `<cpu mode='host-passthrough' …><topology sockets='1' dies='1' cores='X' threads='Y'/></cpu>` where `X×Y = vcpu` (`25_*.py:apply_cpu_topology` derives `threads = host_smt if vcpu%host_smt==0 else 1`)
- `<channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>` (clipboard)

Pipeline redefines → `virsh define`; if guest `running`, power-cycle needed (reboot not enough — old QEMU device model still resident).

```bash
virsh -c qemu:///system define /tmp/new.xml
virsh -c qemu:///system start win_10_dusky
virsh -c qemu:///system domstate win_10_dusky  # running
```

## Phase 3 — Windows: drivers + VDD/LG Host

Via RDP (rescue) since emulated display goes dark:

### 3.1 IP → `xfreerdp3`

```bash
virsh -c qemu:///system domifaddr win_10_dusky --source lease   # 192.168.122.x
xfreerdp3 /v:192.168.122.45 /u:Administrator /cert:ignore /dynamic-resolution
```

(`freerdp` ships `xfreerdp3`; check `which xfreerdp3`)

### 3.2 Inside RDP

- **VirtIO drivers** → mount `virtio-win.iso` → `virtio-win-guest-tools.exe` (vioinput + spice-agent)
- **NVIDIA driver** → standard installer → reboot
- **LG Host** → matching build → install to `C:\Program Files\Looking Glass (host)\` → autostart (Task)
- **Disable emulated adapter:** `devmgmt.msc` → Display adapters → Right-click **Red Hat QXL** / *Microsoft Basic* → **Disable device → Yes** (RDP stays via independent channel; NVIDIA wakes)
- **VDD:** <https://github.com/VirtualDrivers/Virtual-Display-Driver> → `VirtualDriverControl.exe` install → `C:\VirtualDisplayDriver\vdd_settings.xml` → **SDR** single 1440p (see below) → disable/reenable device to flush DWM
  ```xml
  <?xml version='1.0' encoding='utf-8'?>
  <VirtualDisplaySettings>
     <Monitors>1</Monitors>
     <Resolution><Width>2560</Width><Height>1440</Height><RefreshRate>144</RefreshRate></Resolution>
  </VirtualDisplaySettings>
  ```
  Set VDD monitor as **Primary** (drag to Monitor 1, NVIDIA GPU indicated).

## Phase 4 — Host Wayland (Hyprland) `client.ini`

Pipeline `60_configure_client_ini.py` creates/merges `~/.config/looking-glass/client.ini` atomic `0660` for operator, shmFile from `state.json` (`/dev/shm/looking-glass`).

Manual:

```bash
mkdir -p ~/.config/looking-glass
nvim ~/.config/looking-glass/client.ini
```
```ini
; Looking Glass Client — Hyprland / Wayland / Kernel 7.1.8 (Aug 2026)
[app]
shmFile=/dev/shm/looking-glass
allowDMA=yes
renderer=opengl
[opengl]
vsync=no
preventBuffer=yes
mipmap=yes
amdPinnedMem=yes
[wayland]
fractionScale=no
warpSupport=yes
[win]
autoResize=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes
[input]
escapeKey=64   ; KEY_F6
rawMouse=yes
hideCursor=yes
[spice]
enable=yes
clipboard=yes
```

### Launch

```bash
looking-glass-client -f /dev/shm/looking-glass -m KEY_F6
# or if client.ini correct: looking-glass-client
```
**Keys:** `F6` capture, `F6+Q` quit, `F6+F` fullscreen, `F6+D` FPS, `F6+O` overlay.

## Phase 5 — Troubleshoot

| Symptom | Fix |
|---|---|
| **Black screen** | LG window black → `F6` capture → blind `Win+P` → ↓↓ → `Enter` (Project: PC only → Extend) wakes NVIDIA |
| **Permission denied** `/dev/shm/looking-glass` | `fs.protected_regular` lock → `sudo rm -f /dev/shm/looking-glass; sudo systemd-tmpfiles --create …; sudo fallocate…; chown new:kvm; chmod 0660` |
| **Size mismatch** | XML `size` bytes vs file mismatch → delete/recreate matching size (table §1.2) |
| **Clipboard broken** | needs `spice-agent` service in Windows → `Get-Service spice-agent`; reinstall `virtio-win-guest-tools` |
| **xfreerdp3 not found** | `pacman -Q freerdp; which xfreerdp3` |

## Appendix — Legacy `qemu:commandline` (archived)

> [!warning] Historical path — do not use
> Old notes (`Looking Glass_old.md`) declared `xmlns:qemu` on `<domain>` +:
> ```xml
> <qemu:commandline>
>   <qemu:arg value="-device"/><qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
>   <qemu:arg value="-object"/><qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
> </qemu:commandline>
> ```
> Replaced by native `<shmem>` (same QEMU args, validated by libvirt, `/dev/shm` managed, no xmlns escape hatch). Pipeline strips this block.

See: `25_looking_glass.py`, `60_configure_client_ini.py`, [[The RDP method to disable display driver]].
