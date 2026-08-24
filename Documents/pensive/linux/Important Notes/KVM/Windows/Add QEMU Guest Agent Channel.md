---
title: "QEMU Guest Agent Channel (Windows Delta)"
tags:
  - kvm
  - windows
  - spice
  - arch
aliases:
  - Windows Agent Stub
---

# QEMU Guest Agent Channel (Windows)

> [!tip] Merged — canonical source
> **Shared both-channels + 3-part input model lives in [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]].** This stub keeps **only Windows install** detail inline.

## Windows install (after canonical Add Hardware)

1. Canonical already did: **Add Hardware → Channel → Name `org.qemu.guest_agent.0`** + `com.redhat.spice.0`. XML:
   ```xml
   <channel type="unix"><source mode="bind"/><target type="virtio" name="org.qemu.guest_agent.0"/></channel>
   <channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/></channel>
   ```
2. Inside Windows: install **QEMU Guest Agent** via `virtio-win-guest-tools.exe` (CD Drive E:) → set **Services → QEMU Guest Agent → Automatic → Start** + **Spice Agent → Automatic**.

Verify host:

```bash
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-info"}' | python3 -m json.tool
virsh -c qemu:///system shutdown win11 --mode agent   # clean > --mode acpi
```

See: canonical [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]] (both OSes, Linux `spice-vdagent`/`qemu-guest-agent` via `pacman`), [[Looking Glass]].
