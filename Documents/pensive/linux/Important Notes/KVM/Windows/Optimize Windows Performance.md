---
title: "Optimize Windows Performance — Hub"
tags:
  - kvm
  - windows
  - performance
---

# Optimize Windows Performance — Hub

> [!summary] Goal
> Strip stock Win11 bloat that burns RAM/CPU in a VM. Apply in this order; each links to a focused guide.

| Area | Note | Action |
|---|---|---|
| Prefetch | [[Disable SysMain]] | `services.msc` → `SysMain` → **Disabled** + Stop |
| Search | [[Disable Windows Web Search]] | `regedit` `HKCU\Software\Policies\Microsoft\Windows\Explorer` → `DisableSearchBoxSuggestions=1` |
| Clock | [[Disable useplatformclock]] | `bcdedit /set useplatformclock No` (with Hyper-V clocks `hpet=no`, `hypervclock=yes`) |
| Background work | [[Disable Unnecessary Scheduled Tasks]] | `Get-ScheduledTask` → `Disable-ScheduledTask` (`\Microsoft\Windows\Defrag\ScheduledDefrag`, etc.) |
| Boot | [[Disable Unnecessary Startup Programs]] | Task Manager → **Startup** → Disable non-essential |
| Aesthetics | [[Adjust the Visual Effects in Windows 11]] | `Adjust the appearance and performance` → *Adjust for best performance* or custom (no transparency) |

> [!tip] Also consider
> - [[Optional Enable Hardware Security on Windows]] — **not** recommended for gaming (Memory Integrity cost).
> - Custom de-bloated ISO tip: see [[Conclusion Win]].
> - After storage passthrough, run `winsat formal` only if Windows complains about drive assessment.

Next: [[Setting up Shared Directory Between Guest_win11 and Host]] (virtiofs).
