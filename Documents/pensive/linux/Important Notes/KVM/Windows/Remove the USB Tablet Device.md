---
title: "Remove the USB Tablet Device (Windows Delta)"
tags:
  - kvm
  - windows
  - performance
  - input
aliases:
  - Windows Tablet Stub
---

# Remove the USB Tablet Device (Windows)

> [!tip] Merged — canonical source
> **Shared trade + steps + XML + release keys + `rawMouse=yes` lives in [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input#Part C — Input: Tablet vs Mouse (both OSes)]].** This stub retains Windows filename for `[[wikilink]]` stability and Windows note below.

## Windows note

Same trade as Linux: default **USB Tablet** (absolute pointer, seamless capture) polls → extra idle CPU. Remove for **minimum latency/idle** (gaming/passthrough with Looking Glass `rawMouse=yes`); keep for casual desktop.

- Shut off VM → **VM Details → Tablet (Input/USB) → Remove → Apply**; XML `<!-- delete: <input type='tablet' bus='usb'/> -->`; keep `<input type='mouse' bus='virtio'/>` + keyboard.
- After removal: mouse **captured** (`virt-viewer` `Ctrl_L + Alt_L` to release; Looking Glass `F6` / `escapeKey=64` + `rawMouse=yes`).

> [!warning] Don't remove both pointer devices — keep at least one `mouse`/`keyboard`.

See: canonical [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]], [[Looking Glass]].
