---
title: "Visual Effects — Tune for Speed"
tags:
  - kvm
  - windows
  - performance
---

# Visual Effects — Tune for Speed

> [!info] Why
> Transparency, animations, and shadows burn guest GPU/CPU for little benefit in a VM — especially with `virtio`/`QXL` emulated video. Turn them down for snappier UX and lower Looking Glass bandwidth.

## Steps (inside Windows VM)

1. Press `Win` → type `performance` → **Adjust the appearance and performance of Windows**

2. **Visual Effects** tab:
   - Choose **Adjust for best performance** (disables all), then optionally re-tick:
     - ☑ *Show thumbnails instead of icons*
     - ☑ *Smooth edges of screen fonts*
   - Or pick **Custom** and keep only those two.

3. **Apply** → **OK**

> [!tip] Also trim
> **Settings → Personalization → Colors** → **Transparency effects → Off**
> **Settings → Accessibility → Visual effects → Animation effects → Off**

Revert by choosing **Let Windows choose** or **Adjust for best appearance**.

See: [[Optimize Windows Performance]].
