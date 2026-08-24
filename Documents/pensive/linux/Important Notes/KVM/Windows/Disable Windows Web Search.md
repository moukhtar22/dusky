---
title: "Disable Bing Web Search in Start"
tags:
  - kvm
  - windows
  - performance
  - registry
---

# Disable Bing Web Search in Start

> [!info] Why
> Start/Taskbar search querying Bing adds latency and telemetry in a VM — disable for instant local search.

## Registry (Current User)

1. `Win + R` → `regedit` → **Yes** (UAC)
2. Navigate:

```
Computer\HKEY_CURRENT_USER\Software\Policies\Microsoft\Windows
```

3. Right-click `Windows` → **New → Key** → name `Explorer`
4. Select `Explorer` → right-click pane → **New → DWORD (32-bit) Value** → name `DisableSearchBoxSuggestions`
5. Double-click → **Value data:** `1` (Hex or Decimal) → **OK**
6. Log off/on or reboot.

Verify: `reg query "HKCU\Software\Policies\Microsoft\Windows\Explorer" /v DisableSearchBoxSuggestions` → `0x1`.

> [!tip] Undo
> Delete the `DisableSearchBoxSuggestions` value or set `0`.
