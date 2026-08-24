---
title: "Guest Integration — Agent, Clipboard & Input (Unified)"
tags:
  - kvm
  - spice
  - arch
  - windows
  - libvirt
aliases:
  - Guest Integration Unified
---

# Guest Integration — Agent, Clipboard & Input (Unified)

> [!abstract] Goal
> Clean power ops, clipboard, and input model. Shared pattern: **SPICE channel** + **QEMU Guest Agent channel** + input device choice. Deltas: Windows uses `virtio-win-guest-tools`; Linux uses `spice-vdagent`/`qemu-guest-agent` via `pacman`.

## Part A — Two channels (both OSes, virt-manager)

### 1. SPICE agent channel (clipboard)

Already present if VM has **SPICE graphics** (`graphics type='spice'` + `listen type='none'`).

Check:

```bash
virsh -c qemu:///system dumpxml win11 | grep -A2 "com.redhat.spice.0"
virsh -c qemu:///system dumpxml archlinux | grep -A2 "com.redhat.spice.0"
# expect: <channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/>
```

If missing: **Add Hardware → Channel → Name: `com.redhat.spice.0` → Type: `spicevmc`** → Apply. Same for Boxes `qemu:///session` (see [[gnome-boxes]]).

### 2. QEMU Guest Agent channel (host-initiated power/FS/trim)

Add in virt-manager:

1. VM Details → **Add Hardware → Channel**
2. **Name:** `org.qemu.guest_agent.0` → **Finish → Apply**

XML (both OSes, same):

```xml
<channel type="unix">
  <source mode="bind"/>
  <target type="virtio" name="org.qemu.guest_agent.0"/>
</channel>
<!-- keep SPICE clipboard channel too -->
<channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/></channel>
```

Inside guest — install agent then verify from host:

```bash
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-info"}' | python3 -m json.tool
virsh -c qemu:///system qemu-agent-command archlinux '{"execute":"guest-info"}' | python3 -m json.tool
```

> [!info] Why both channels?
> - `org.qemu.guest_agent.0` → host commands: `shutdown --mode agent` (clean), `domifaddr --source agent`, `domfsinfo`, `domfstrim`.
> - `com.redhat.spice.0` → SPICE clipboard/passthrough (Looking Glass `spice=enable` relies on this).

Usage (both OSes once agent present):

```bash
virsh -c qemu:///system shutdown win11 --mode agent   # clean > --mode acpi
virsh -c qemu:///system reboot win11 --mode agent
virsh -c qemu:///system domifaddr win11 --source agent   # guest-reported IP (vs lease)
virsh -c qemu:///system domfsinfo win11
virsh -c qemu:///system domfstrim win11   # after Storage discard=unmap
```

Without agent, `shutdown` injects ACPI — guest may ignore it.

## Part B — OS-specific agent install

> [!info] Windows route — `virtio-win-guest-tools.exe`
> 1. Inside Windows (after [[Install a Windows Virtual Machine on KVM]] `viostor`/`NetKVM`): **File Explorer → CD Drive (E:)** (`virtio-win.iso`) → run **`virtio-win-guest-tools.exe`** (meta-installer, not per-arch MSI).
> 2. Reboot. Installs: `qxl`/`virtio-gpu` fallback, `spice-agent`, **QEMU Guest Agent**, `viostor`/`NetKVM`/`vioinput`/`viofs`.
> 3. `Win+R` → `services.msc` → **QEMU Guest Agent** → **Startup: Automatic** → Start. Also **Spice Agent** → Automatic.
> 4. Verify `Device Manager → Mice` no ghost `vioinput` cursor vanishing; if cursor vanishes → uninstall mouse device → re-run `virtio-win-guest-tools` (Looking Glass section notes this).
>
> Tip: after tools, `virt-manager` **View → Scale Display → ✅ Auto resize VM with window** works.

> [!info] Linux route — `spice-vdagent` + `qemu-guest-agent`
> Inside **guest** Arch:
> ```bash
> sudo pacman -S --needed spice-vdagent qemu-guest-agent xf86-video-qxl  # or video virtio + accel3d for virgl
> sudo systemctl enable --now spice-vdagentd.service
> sudo systemctl enable --now qemu-guest-agent.service
> # Hyprland guest: user agent must be in user session, not root:
> # systemd-user (UWSM-aware, preferred) — ~/.config/systemd/user/spice-vdagent-user.service → ExecStart=/usr/bin/spice-vdagent; WantedBy=graphical-session.target
> # or Hyprland exec-once: exec-once = uwsm app -- /usr/bin/spice-vdagent
> # Wayland → Xwayland bridge if host→guest works but guest Wayland app → host fails:
> # guest: sudo pacman -S --needed xorg-xwayland wl-clipboard xclip ; wl-paste --type text --watch xclip -selection clipboard
> ```
> Full Hyprland/UWSM + Boxes plumbing: [[gnome-boxes]] (scope `qemu:///session` vs `qemu:///system`, Xwayland, `systemd-user` bridge) + [[gnome-boxes/linux]] (concise guest agents). For `qemu:///system` Arch guests (this index), same `spice-vdagent` chain applies but without Boxes `session` wrapper — just ensure SPICE graphics + both channels + daemons.

## Part C — Input: Tablet vs Mouse (both OSes)

> [!abstract] Trade
> Default **USB Tablet** gives *absolute* pointer (seamless mouse capture, no grab). Cost: constant poll → extra idle CPU/context switches. Removing it cuts idle overhead; you then deal with classic capture.

### When to remove

- Goal **minimum latency/idle CPU** (gaming / passthrough with Looking Glass `rawMouse=yes`) → remove.
- Goal **casual desktop** → keep tablet, latency diff minor.

### Steps (VM shutoff recommended)

1. VM Details (lightbulb) → **Tablet** (Input / USB) → **Remove** → **Apply**

Alternative XML (`virsh edit`):

```xml
<!-- delete: --> <input type='tablet' bus='usb'/>
<!-- keep at least: -->
<input type='mouse' bus='virtio'/> <input type='keyboard' bus='virtio'/>
<!-- or ps2 fallback: <input type='mouse' bus='ps2'/> <input type='keyboard' bus='ps2'/> -->
```

### After removal

Mouse is **captured** inside VM window (classic `virt-viewer`). Release keys:

- `Ctrl_L + Alt_L` (or configured `grabToggle`) — `virt-viewer` preferences
- Looking Glass: `escapeKey=64` (`F6` per `60_configure_client_ini.py`) toggles capture; `rawMouse=yes` then gives raw host mouse.

> [!warning] Don't remove both pointer devices
> Keep at least one `mouse`/`keyboard` input or you'll have no input. Applies to both OSes.

## Boxes `qemu:///session` note (why `gnome-boxes` stays separate)

- `gnome-boxes` → `qemu:///session` (per-user) — SPICE WebDAV channel `org.spice-space.webdav.0` for folder sharing (not `virtiofs`), `spice-guest-tools` on Windows guest via `spice-space.org` (see [[gnome-boxes/Windows]]).
- `virt-manager` `qemu:///system` → `virtiofs` shared folder via `memoryBacking memfd shared` + `filesystem type='mount' driver='virtiofs'` (see [[Setting up Shared Directory Between Guest_win11 and Host]]).
- Clipboard plumbing for Boxes Hyprland guests is intentionally **comprehensive** in [[gnome-boxes]] (scope, channel, `spice-vdagent-user.service`, Wayland bridge). That file remains canonical for Boxes; this unified note covers `qemu:///system` VMs.

## Verify (both OSes)

```bash
virsh -c qemu:///system dumpxml win11 | grep -E "guest_agent|spice.0|input type"
virsh -c qemu:///system dumpxml archlinux | grep -E "guest_agent|spice.0|input type"
# inside Windows: services.msc → QEMU Guest Agent + Spice Agent Running
# inside Arch guest: systemctl status spice-vdagentd qemu-guest-agent; pgrep -af spice-vdagent; pgrep -af Xwayland
```

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[Add QEMU Guest Agent Channel]] (Windows detail), [[Remove the USB Tablet Device]] (latency trade), [[gnome-boxes]] + [[gnome-boxes/linux]] (Boxes agents), [[Looking Glass]] (input escapeKey), `25_looking_glass.py:apply_latency_tuning`.
