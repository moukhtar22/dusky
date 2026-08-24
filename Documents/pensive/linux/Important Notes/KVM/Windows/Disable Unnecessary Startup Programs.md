---
title: "Disable Unnecessary Startup Programs"
tags:
  - kvm
  - windows
  - performance
---

# Disable Unnecessary Startup Programs

> [!abstract] Goal
> Cut VM boot time and idle RAM by disabling apps that auto-start. No effect on installed software itself.

## Inside Windows VM

### Method A — Task Manager (Win11 22H2+)

1. `Ctrl + Shift + Esc` → **Startup apps** (or **Startup** tab)
2. For each non-essential entry (e.g. OneDrive, Teams, Spotify, Adobe updater) → **Disable**
3. Keep: **Windows Security**, **VirtIO / QEMU Guest Agent**, **Realtek / audio**, **VDD** (if passthrough)

### Method B — Settings

1. **Settings → Apps → Startup** → toggle **Off** for noise apps

## Verify

Reboot VM → Task Manager → **Startup apps** shows disabled list; `msconfig` → **Startup** mirrors it.

> [!warning] Don’t disable
> - `VirtIO-FS Service` / `QEMU Guest Agent` / `spice-agent` — needed for shared folder & clipboard ([[Setting up Shared Directory Between Guest_win11 and Host]], [[Add QEMU Guest Agent Channel]])
> - `Microsoft Defender` if you rely on built-in AV (on Defender-stripped custom ISOs this service is absent — bonus).

See: [[Optimize Windows Performance]], [[Disable Unnecessary Scheduled Tasks]].
