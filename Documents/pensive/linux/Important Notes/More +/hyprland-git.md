# Hyprland Git & Stable Switching Guide

Step-by-step commands to switch between Hyprland Git (bleeding-edge AUR) and Stable (official Arch repositories).

---

## 1. Switch to Hyprland Git (AUR)

Run this command to upgrade Hyprland and all companion libraries to their `-git` master branches in one coordinated transaction:

```bash
paru -Syu --needed base-devel hyprland-git aquamarine-git hyprcursor-git hyprgraphics-git hyprlang-git hyprtoolkit-git hyprutils-git hyprwire-git hyprland-guiutils-git hyprland-protocols-git hyprwayland-scanner-git udis86-git
```

> **Note**: Confirm `y` when prompted to replace conflicting stable packages (`hyprland`, `aquamarine`, etc.).

### If using Hyprland Plugins:
`hyprpm` is packaged separately for git:
```bash
paru -S --needed hyprpm-git
hyprpm update
```

### Verification:
After installation, log out and back in (or restart your session), then verify ABI and build:
```bash
hyprctl version
```

---

## 2. Switch Back to Stable Hyprland (Official Repos)

To revert cleanly back to official Arch repository releases, replace all git packages with standard repo packages:

```bash
sudo pacman -Syu --needed hyprland aquamarine hyprcursor hyprgraphics hyprlang hyprtoolkit hyprutils hyprwire hyprland-guiutils hyprland-protocols hyprwayland-scanner
```

> **Note**: Confirm `y` to remove the conflicting `-git` packages.

### If using Hyprland Plugins on Stable:
```bash
sudo pacman -S --needed hyprpm
hyprpm update
```

---

## Troubleshooting & Known Gotchas

### CMake Error: `CMakeDetermineСCompiler.cmake` on `hyprland-protocols-git`
If `hyprland-protocols-git` fails with `No CMAKE_С_COMPILER could be found`, upstream has a Cyrillic `С` (`U+0421`) homoglyph typo in `CMakeLists.txt`.

**Quick Fix**:
Add a `prepare()` step in its PKGBUILD (`~/.cache/paru/clone/hyprland-protocols-git/PKGBUILD`):
```bash
prepare() {
  cd "$_pkgsrc"
  sed -i 's/LANGUAGES .*/LANGUAGES C)/' CMakeLists.txt
}
```
Then build and install it:
```bash
cd ~/.cache/paru/clone/hyprland-protocols-git && makepkg -si
```
