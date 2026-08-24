---
title: "Disable SysMain (SuperFetch) in VM"
tags:
  - kvm
  - windows
  - performance
---

# Disable SysMain (SuperFetch) in VM

> [!note] Why
> `SysMain` preloads apps into RAM — useful on bare-metal HDD, wasteful in a VM where host already caches and RAM is scarce. Disabling frees CPU/RAM.

## Inside Windows VM

1. `Win + R` → `services.msc` → Enter
2. Scroll to **SysMain** (press `S` to jump)
3. Right-click → **Properties**:
   - **Startup type:** `Disabled`
   - **Service status:** **Stop** (if Running)
4. **Apply** → **OK**

Verify: `services.msc` shows `SysMain` — `Disabled` / `Stopped`.

> [!success] Done — persists across reboot.

See: [[Optimize Windows Performance]] hub, [[Disable Unnecessary Scheduled Tasks]].
