#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: GAMING RUNNER — MASTER GAME PROFILE CONFIGURATION SCHEMA
===============================================================================
Target:  ~/user_scripts/gaming/runner/config.toml          (global defaults)
         ~/user_scripts/gaming/runner/profiles/*.toml      (per-game overrides)
         ~/user_scripts/gaming/runner/presets/*.toml       (archetype reference)
Engine: TOML (TomlEngine) — dotted table path, deep nesting, atomic commits

Scope/Key Mapping:
  scope = dotted table path (e.g. "graphics", "graphics.gamescope",
          "runtime.wine", "storage", "sandbox", "performance", "audio",
          "runner", "paths")
  key   = table key (may contain dots for nesting)
  UID   = "scope.key" (or "key" when scope == "DEFAULT")

Architecture:
  - Global defaults live in config.toml and cascade via deep_merge:
      Global Defaults -> Preset Chain (extends) -> Profile -> CLI Overrides
  - Per-game TOML files in profiles/ override any preset/global key.
  - This TUI edits the GLOBAL config directly (TARGET_FILE) and exposes
    high-frequency per-game overrides via target_file_override so a single
    TUI can tweak GPU / FPS / Gamescope / MangoHud / GameMode per game
    without leaving the interface.
  - No username is hardcoded. All paths are resolved via Path.home() / "~"
    expansion so the schema works for any user.

Design Principles:
  - Comprehensive but not exhaustive: only the most-tweaked knobs are
    surfaced. Rare/structural keys (game_dir, dwarfs_image, executable,
    working_dir, arguments, dll_overrides, redistributables, hooks, env)
    stay in the raw TOML for direct editing.
  - Safe defaults match _template.toml and config.toml / preset defaults.
  - Anti-clobber & atomic commits are handled by TomlEngine.
===============================================================================
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve Dusky TUI root without hardcoding username
# ---------------------------------------------------------------------------
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# ---------------------------------------------------------------------------
# Dynamic path resolution — NEVER hardcode /home/<user>
# ---------------------------------------------------------------------------
_GAMING_ROOT = Path.home() / "user_scripts" / "gaming" / "runner"
_PROFILES_DIR = _GAMING_ROOT / "profiles"
_PRESETS_DIR = _GAMING_ROOT / "presets"
_GLOBAL_CONFIG = _GAMING_ROOT / "config.toml"

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "toml"
# Use tilde so Path.expanduser() resolves per-user dynamically.
TARGET_FILE = "~/user_scripts/gaming/runner/config.toml"
APP_TITLE = "Dusky Games — Master Runner"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

GLOBAL_POPUP = {
    "title": "Dusky Games",
    "message": "**Global** tabs → defaults for all games. **Per-Game** → overrides for one game. Missing keys inherit via `presets/*.toml` (`extends`).",
    "level": "info",
    "require_confirm": False,
    "cancel_quits": False,
}

TAB_NOTICES = {
    0: {
        "level": "info",
        "message": "Global runner defaults — cascades to all profiles unless a per-game override exists. Per-game tweaks live in the **Per-Game** tab.",
        "position": "top",
    },
    6: {
        "level": "warning",
        "message": "Per-Game quick tweaks — each game is a collapsible folder. Expand a game to tweak GPU / FPS / Gamescope / MangoHud etc. Changes write directly to `profiles/<game>.toml`.",
        "position": "top",
    },
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Global",
    "Graphics",
    "Gamescope",
    "Performance",
    "Wine / Proton",
    "System",
    "Per-Game",
    "Presets",
]

# =============================================================================
# 4. PROFILE DISCOVERY (dynamic, no hardcodes)
# =============================================================================
def _discover_profiles() -> list[tuple[str, str]]:
    """Return sorted list of (profile_id, display_name) excluding templates."""
    profiles: list[tuple[str, str]] = []
    try:
        if not _PROFILES_DIR.is_dir():
            return profiles
        for p in sorted(_PROFILES_DIR.glob("*.toml")):
            if p.name.startswith("_"):
                continue
            pid = p.stem
            display = pid
            try:
                import tomllib  # Python 3.11+
                with open(p, "rb") as f:
                    data = tomllib.load(f)
                meta_name = data.get("meta", {}).get("name")
                if isinstance(meta_name, str) and meta_name.strip():
                    display = meta_name.strip()
                else:
                    # Fallback: prettify pid
                    display = pid.replace("_", " ").title()
            except Exception:
                display = pid.replace("_", " ").replace("-", " ").title()
            # Clamp display length for label sanity
            if len(display) > 28:
                display = display[:25] + "..."
            profiles.append((pid, display))
    except Exception:
        pass
    return profiles

_DISCOVERED_PROFILES = _discover_profiles()

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA: dict[int, list[ConfigItem]] = {
    # -------------------------------------------------------------------------
    # TAB 0: GLOBAL — runner lifecycle, paths, storage defaults
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Profiles Dir",
            key="profiles_dir",
            scope="runner",
            type_="string",
            default="profiles",
            group="Runner",
            extended_help="**Profiles Directory**\n\nRelative or absolute directory containing per-game TOML profiles. Default `profiles` resolves to `~/user_scripts/gaming/runner/profiles`. Changing this requires a restart of the runner.",
        ),
        ConfigItem(
            label="Presets Dir",
            key="presets_dir",
            scope="runner",
            type_="string",
            default="presets",
            group="Runner",
            extended_help="**Presets Directory**\n\nDirectory containing base preset archetypes (`base_native`, `base_wine_dxvk`, etc.). Profiles inherit via `extends = \"<preset>\"`.",
        ),
        ConfigItem(
            label="Default GPU",
            key="default_gpu",
            scope="runner",
            type_="cycle",
            default="auto",
            options=["auto", "discrete", "integrated"],
            group="Runner",
            extended_help="**Global Default GPU**\n\nFallback when a profile's `graphics.gpu` is `auto`.\n- **auto**: Heuristic (heavy 3D → dGPU, light/2D → iGPU).\n- **discrete**: Force dGPU (PRIME offload / __NV_PRIME_RENDER_OFFLOAD=1).\n- **integrated**: Force iGPU (power-efficient, low heat).",
        ),
        ConfigItem(
            label="Desktop Notify",
            key="desktop_notifications",
            scope="runner",
            type_="bool",
            default=True,
            group="Runner",
            extended_help="**Desktop Notifications**\n\nIf enabled, the runner emits `notify-send` on launch / exit via DBus. Disable for silent scripting.",
        ),
        ConfigItem(
            label="Auto Unmount",
            key="auto_unmount_on_exit",
            scope="runner",
            type_="bool",
            default=True,
            group="Runner",
            extended_help="**Auto Unmount on Exit**\n\nIf enabled, DwarFS + fuse-overlayfs layers are torn down after the game process exits (and on SIGINT/SIGTERM via atexit). Disable to keep the union mounted for inspection.",
        ),
        ConfigItem(
            label="Clean Stale Workdirs",
            key="clean_stale_workdirs",
            scope="runner",
            type_="bool",
            default=True,
            group="Runner",
            extended_help="**Clean Stale Overlay Workdirs**\n\nIf enabled, `.game-root-work` scratch dirs are auto-cleaned before mount (required by fuse-overlayfs).",
        ),
        # --- PATHS ---
        ConfigItem(
            label="Sandbox Base",
            key="sandbox_base",
            scope="paths",
            type_="string",
            default="~/.local/share/game_sandboxes",
            group="Paths",
            extended_help="**Sandbox Base Directory**\n\nBase for Bubblewrap `sandbox_home` isolation trees. Supports `~` and `$HOME` expansion. Per-profile `sandbox.sandbox_home` overrides this.",
        ),
        ConfigItem(
            label="Wine Prefix Base",
            key="wine_prefix_base",
            scope="paths",
            type_="string",
            default="~/.local/share/wineprefixes",
            group="Paths",
            extended_help="**Wine Prefix Base**\n\nDefault parent for Wine prefix provisioning when `runtime.wine.prefix_dir` is relative. Supports `~` expansion.",
        ),
        ConfigItem(
            label="Desktop Entry Dir",
            key="desktop_entry_dir",
            scope="paths",
            type_="string",
            default="~/.local/share/applications",
            group="Paths",
            extended_help="**Desktop Entry Directory**\n\nWhere `master_runner.py install-desktop` writes `*.desktop` launchers.",
        ),
        # --- STORAGE GLOBALS ---
        ConfigItem(
            label="DwarFS Cache %",
            key="dwarfs_cache_percent",
            scope="storage",
            type_="int",
            default=25,
            min_val=5,
            max_val=90,
            step=5,
            group="Storage",
            extended_help="**DwarFS Cache (% RAM)**\n\nDecompressed block cache as % of total RAM. Default 25%. Higher → fewer stutters, more RAM pressure. Calculated from `/proc/meminfo` at mount time.",
        ),
        ConfigItem(
            label="Tidy Interval",
            key="dwarfs_tidy_interval",
            scope="storage",
            type_="string",
            default="15m",
            options=["5m", "15m", "30m", "1h"],
            group="Storage",
            extended_help="**DwarFS Tidy Interval**\n\nHow often the DwarFS block cache evictor runs. Examples: `15m`, `30m`, `1h`. Backed by `-o tidy_interval=` mount option.",
        ),
        ConfigItem(
            label="Tidy Max Age",
            key="dwarfs_tidy_max_age",
            scope="storage",
            type_="string",
            default="30m",
            options=["10m", "30m", "1h", "2h"],
            group="Storage",
            extended_help="**Inactive Block Max Age**\n\nAfter this idle period, cached blocks are evicted. `-o tidy_max_age=` mount option.",
        ),
        ConfigItem(
            label="Persistent Overlay",
            key="persistent_overlay",
            scope="storage",
            type_="bool",
            default=True,
            group="Storage",
            extended_help="**Persistent Overlay**\n\nKeep `overlay-storage` (save files, user configs) across launches. If false, the write layer is volatile.",
        ),
        ConfigItem(
            label="Auto Clean Workdir",
            key="auto_clean_workdir",
            scope="storage",
            type_="bool",
            default=True,
            group="Storage",
            extended_help="**Auto-Clean Overlay Workdir**\n\nClean `overlay_work` scratch dir before each mount. Required for fuse-overlayfs correctness.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: GRAPHICS — GPU, Wayland, Vulkan ICD
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="GPU",
            key="gpu",
            scope="graphics",
            type_="cycle",
            default="auto",
            options=["auto", "discrete", "integrated"],
            group="GPU",
            extended_help="**GPU Selection**\n\n- **auto**: Heuristic (native/light → iGPU, Wine/UE5/heavy → dGPU).\n- **discrete**: Force dGPU via PRIME (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`, `VK_ICD_FILENAMES=nvidia_icd.json` or `radeon` for AMD).\n- **integrated**: Force iGPU (`MESA_LOADER_DRIVER_OVERRIDE=iris` or `radeonsi`).\nPer-game overrides this in **Per-Game** tab.",
        ),
        ConfigItem(
            label="Wayland Native",
            key="wayland_native",
            scope="graphics",
            type_="bool",
            default=True,
            group="Wayland",
            extended_help="**Wayland Native Surfaces**\n\nIf true, sets `SDL_VIDEODRIVER=wayland`, `GDK_BACKEND=wayland`, `QT_QPA_PLATFORM=wayland`, `PROTON_ENABLE_WAYLAND=1`. Disable for legacy X11-only engines.",
        ),
        ConfigItem(
            label="Prefer XWayland",
            key="prefer_xwayland",
            scope="graphics",
            type_="bool",
            default=False,
            group="Wayland",
            extended_help="**Force XWayland**\n\nForces `SDL_VIDEODRIVER=x11`, `GDK_BACKEND=x11`, `QT_QPA_PLATFORM=xcb`, and `DISPLAY=:0`. Useful for Unity 2017 / Source 1 / FNA titles that query XRandR root geometry. Prefer Gamescope **embedded** for a cleaner Wayland-native sandbox instead.",
        ),
        ConfigItem(
            label="Vulkan ICD",
            key="vulkan_icd",
            scope="graphics",
            type_="cycle",
            default="auto",
            options=["auto", "nvidia", "intel", "radv", "amd"],
            group="Vulkan",
            extended_help="**Vulkan ICD Selector**\n\n- **auto**: Runner auto-detects (`nvidia_icd.json`, `intel_icd.json`, `radeon_icd.*.json`).\n- **nvidia / intel / radv / amd**: Force a specific ICD via `VK_ICD_FILENAMES`. `radv` maps to `radeonsi`/`amd` ICDs.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: GAMESCOPE — Wayland micro-compositor sandbox
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Enable Gamescope",
            key="enabled",
            scope="graphics.gamescope",
            type_="bool",
            default=False,
            is_parent=True,
            expanded=False,
            group="Gamescope",
            extended_help="**Gamescope Micro-Compositor**\n\nWraps the game in a nested Wayland compositor (`gamescope --backend wayland --expose-wayland`). Recommended for legacy X11 titles on pure Wayland (Hyprland) to provide an isolated Xwayland presentation without system XWayland. Expand to tune resolution / rate / FSR / tearing.",
        ),
        ConfigItem(
            label="Mode",
            key="mode",
            scope="graphics.gamescope",
            type_="cycle",
            default="embedded",
            options=["embedded", "fullscreen", "borderless", "nested"],
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Gamescope Mode**\n\n- **embedded** (`-b`): Borderless window (recommended).\n- **fullscreen** (`-f`): Exclusive fullscreen.\n- **borderless / nested**: Alternative presentation modes.",
        ),
        ConfigItem(
            label="Render Width",
            key="width",
            scope="graphics.gamescope",
            type_="int",
            default=0,
            min_val=0,
            max_val=7680,
            step=1,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Internal Render Width (-w)**\n\nInternal render resolution width. `0` → auto-detect monitor width via `hyprctl` / `wlr-randr` / DRM. Non-zero forces a fixed render target.",
        ),
        ConfigItem(
            label="Render Height",
            key="height",
            scope="graphics.gamescope",
            type_="int",
            default=0,
            min_val=0,
            max_val=4320,
            step=1,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Internal Render Height (-h)**\n\nInternal render resolution height. `0` → auto. See Render Width for detection chain.",
        ),
        ConfigItem(
            label="Output Width",
            key="output_width",
            scope="graphics.gamescope",
            type_="int",
            default=0,
            min_val=0,
            max_val=7680,
            step=1,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Output Width (-W)**\n\nMonitor output width. `0` → native display. Allows upscaling/downscaling between render and output.",
        ),
        ConfigItem(
            label="Output Height",
            key="output_height",
            scope="graphics.gamescope",
            type_="int",
            default=0,
            min_val=0,
            max_val=4320,
            step=1,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Output Height (-H)**\n\nMonitor output height. `0` → native.",
        ),
        ConfigItem(
            label="Refresh Rate",
            key="refresh_rate",
            scope="graphics.gamescope",
            type_="int",
            default=0,
            min_val=0,
            max_val=500,
            step=5,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Refresh / Framerate Cap (-r / --framerate-limit)**\n\n`0` → auto-detect via `hyprctl` / `wlr-randr` / DRM sysfs; fallback 60Hz. If `performance.fps_limit > 0`, that value is used as framerate-limit. Set explicitly to cap Gamescope presentation rate.",
        ),
        ConfigItem(
            label="FSR Upscaling",
            key="fsr_upscaling",
            scope="graphics.gamescope",
            type_="bool",
            default=False,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**AMD FSR Upscaling (-F fsr)**\n\nEnable FidelityFX Super Resolution spatial upscaler inside Gamescope. Internally adds `-F fsr --fsr-sharpness <value>`.",
        ),
        ConfigItem(
            label="FSR Sharpness",
            key="fsr_sharpness",
            scope="graphics.gamescope",
            type_="int",
            default=2,
            min_val=0,
            max_val=20,
            step=1,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**FSR Sharpness (0–20)**\n\nControls FSR edge sharpness. Default 2 (subtle). Higher → sharper, but more aliasing.",
        ),
        ConfigItem(
            label="Allow Tearing",
            key="allow_tearing",
            scope="graphics.gamescope",
            type_="bool",
            default=True,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Immediate Flips (--immediate-flips)**\n\nLow-latency immediate presentation (allows tearing). Recommended for latency-sensitive titles; disable for strict vsync.",
        ),
        ConfigItem(
            label="Force Grab Cursor",
            key="force_grab_cursor",
            scope="graphics.gamescope",
            type_="bool",
            default=False,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**Force Grab Cursor (--force-grab-cursor)**\n\nConfine cursor to Gamescope window. Useful for FPS titles with raw mouse capture.",
        ),
        ConfigItem(
            label="HDR",
            key="hdr",
            scope="graphics.gamescope",
            type_="bool",
            default=False,
            parent_ref="graphics.gamescope.enabled",
            extended_help="**HDR Surface (--hdr-enabled)**\n\nEnable HDR surface presentation. Requires HDR-capable monitor and compositor support.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: PERFORMANCE — GameMode, MangoHud, FPS cap, CPU governor
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="GameMode",
            key="gamemode",
            scope="performance",
            type_="bool",
            default=True,
            group="Performance",
            extended_help="**Feral GameMode (gamemoderun)**\n\nWraps the pipeline with `gamemoderun` if available. Requests CPU governor / I/O priority boosts via the GameMode daemon. Requires `gamemode` and `libgamemode` installed.",
        ),
        ConfigItem(
            label="MangoHud",
            key="mangohud",
            scope="performance",
            type_="bool",
            default=False,
            is_parent=True,
            expanded=False,
            group="Overlay",
            extended_help="**MangoHud Telemetry Overlay**\n\nInjects `MANGOHUD=1` (or `mangoapp` inside Gamescope, else `mangohud` wrapper). Expand to pick a preset and tie FPS cap into `MANGOHUD_CONFIG=fps_limit=<fps>` and `DXVK_FRAME_RATE`.",
        ),
        ConfigItem(
            label="MangoHud Preset",
            key="mangohud_preset",
            scope="performance",
            type_="string",
            default="",
            options=["", "minimal", "full", "fps_only", "horizontal"],
            parent_ref="performance.mangohud",
            extended_help="**MangoHud Config Preset**\n\nAppended as `MANGOHUD_CONFIG=preset=<preset>,fps_limit=<fps>`. Leave empty for MangoHud defaults. Examples: `minimal`, `full`, `fps_only`.",
        ),
        ConfigItem(
            label="FPS Limit",
            key="fps_limit",
            scope="performance",
            type_="int",
            default=0,
            min_val=0,
            max_val=480,
            step=5,
            options=[0, 30, 60, 90, 120, 144, 165, 240, 360],
            group="Frame Cap",
            extended_help="**Per-Game FPS Limiter**\n\n- **0**: Auto / uncapped (no limiter).\n- **>0**: Applied as `MANGOHUD_CONFIG=fps_limit=<n>` when MangoHud is on, otherwise `DXVK_FRAME_RATE=<n>` (unless Gamescope is enabled, where `--framerate-limit <n>` + Gamescope's `-r` take precedence). Common: 60, 90, 120, 144.",
        ),
        ConfigItem(
            label="CPU Governor",
            key="cpu_governor",
            scope="performance",
            type_="cycle",
            default="performance",
            options=["performance", "powersave", "schedutil", "ondemand", "conservative"],
            group="CPU",
            extended_help="**CPU Governor Hint**\n\nDocumented preferred governor for the session. The runner logs this and GameMode may request it. Actual switching is via `cpupower` / GameMode daemon.",
        ),
        ConfigItem(
            label="Nice Level",
            key="process_priority",
            scope="performance",
            type_="int",
            default=0,
            min_val=-20,
            max_val=19,
            step=1,
            group="CPU",
            extended_help="**Process Nice Level**\n\nSets `nice` priority for the game process.\n- **-20**: Highest priority (requires privs).\n- **0**: Default.\n- **19**: Lowest / background.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: WINE / PROTON — runtime.wine pipeline
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Runtime Type",
            key="type",
            scope="runtime",
            type_="cycle",
            default="native",
            options=["native", "wine", "proton", "umu", "script"],
            group="Runtime",
            extended_help="**Runtime Type**\n\n- **native**: Linux ELF / shell script launched directly (chmod +x auto-applied).\n- **wine**: Windows `.exe` via Wine-Staging / Proton (`wine_binary` + `prefix_dir`). Global default is informational; per-game `runtime.type` decides execution path.",
        ),
        ConfigItem(
            label="Wine Binary",
            key="wine_binary",
            scope="runtime.wine",
            type_="string",
            default="wine",
            options=["wine", "wine64", "proton", "GE-Proton", "~/.local/share/Steam/compatibilitytools.d/GE-Proton11-6-x86_64/files/bin/wine"],
            group="Wine",
            extended_help="**Wine / Proton Binary**\n\nExecutable used for `wine <exe>`. `wine` / `wine64` resolve via PATH; custom absolute paths (e.g. `~/.local/share/Steam/compatibilitytools.d/.../bin/wine`) are supported with `~` expansion.",
        ),
        ConfigItem(
            label="Arch",
            key="arch",
            scope="runtime.wine",
            type_="cycle",
            default="win64",
            options=["win64", "win32"],
            group="Wine",
            extended_help="**WINEARCH**\n\nWine prefix architecture. `win64` for modern 64-bit titles, `win32` for legacy 32-bit.",
        ),
        ConfigItem(
            label="Sync Mode",
            key="sync_mode",
            scope="runtime.wine",
            type_="cycle",
            default="auto",
            options=["auto", "ntsync", "fsync", "esync", "server"],
            group="Wine",
            extended_help="**Thread Synchronization Primitive**\n\n- **fsync**: `WINEFSYNC=1` (recommended, requires fsync kernel).\n- **esync**: `WINEESYNC=1`.\n- **ntsync**: `WINENTSYNC=1` (Linux ntsync driver).\n- **server**: No userspace sync (fallback).",
        ),
        ConfigItem(
            label="Large Address Aware",
            key="large_address_aware",
            scope="runtime.wine",
            type_="bool",
            default=True,
            group="Wine",
            extended_help="**WINE_LARGE_ADDRESS_AWARE**\n\nExpose 4GB address space to 32-bit processes. Also injects `WINE_LARGE_ADDRESS_AWARE=1` into env.",
        ),
        ConfigItem(
            label="DXVK",
            key="dxvk",
            scope="runtime.wine",
            type_="bool",
            default=True,
            group="Translators",
            extended_help="**DXVK (D3D9/10/11 → Vulkan)**\n\nEnable DXVK translation. `WINEDLLOVERRIDES` maps `dxgi/d3d11/d3d9=n`. Pair with `dxvk_nvapi` for DLSS/Reflex via `DXVK_ENABLE_NVAPI=1`.",
        ),
        ConfigItem(
            label="VKD3D",
            key="vkd3d",
            scope="runtime.wine",
            type_="bool",
            default=True,
            group="Translators",
            extended_help="**VKD3D-Proton (D3D12 → Vulkan)**\n\nEnable VKD3D for D3D12 titles (UE5, modern AAA). Preset `base_unreal_engine_5` forces this on with `VKD3D_CONFIG=force_static_cbv` and `VKD3D_DEBUG=info`.",
        ),
        ConfigItem(
            label="NVAPI (DLSS/Reflex)",
            key="dxvk_nvapi",
            scope="runtime.wine",
            type_="bool",
            default=False,
            group="Translators",
            extended_help="**DXVK-NVAPI**\n\nExpose NVIDIA NVAPI via DXVK for DLSS and Reflex. Sets `DXVK_ENABLE_NVAPI=1`. Requires NVIDIA dGPU and `dxvk-nvapi` layer installed.",
        ),
        ConfigItem(
            label="Wine Debug",
            key="debug",
            scope="runtime.wine",
            type_="string",
            default="fixme-all",
            options=["fixme-all", "-all", "warn+all", "err+all", "fixme-all,err+all"],
            group="Wine",
            extended_help="**WINEDEBUG Filter**\n\nControls Wine log verbosity.\n- `fixme-all`: Hide stub warnings (default).\n- `-all`: Silence everything (max perf, hides errors).\n- `warn+all` / `err+all`: Verbose debug.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: SYSTEM — Audio, DwarFS details, Sandbox
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Audio Driver",
            key="driver",
            scope="audio",
            type_="cycle",
            default="pipewire",
            options=["pipewire", "pulseaudio", "alsa"],
            group="Audio",
            extended_help="**Audio Backend**\n\nSets the preferred audio server. `pipewire` is the Arch default; `PIPEWIRE_LATENCY` is applied when `driver=pipewire`.",
        ),
        ConfigItem(
            label="PipeWire Latency",
            key="pipewire_latency",
            scope="audio",
            type_="string",
            default="128/48000",
            options=["32/48000", "64/48000", "128/48000", "256/48000", "512/48000", "1024/48000"],
            group="Audio",
            extended_help="**PIPEWIRE_LATENCY Quantum**\n\nBuffer size / sample rate (`<quantum>/<rate>`). Lower → less latency, more CPU. Injected as `PIPEWIRE_LATENCY` env.",
        ),
        ConfigItem(
            label="DwarFS Cache %",
            key="dwarfs_cache_percent",
            scope="storage",
            type_="int",
            default=25,
            min_val=5,
            max_val=90,
            step=5,
            group="Storage",
            extended_help="**DwarFS Cache (% RAM) — System Default**\n\nMirrors runner `storage.dwarfs_cache_percent` for reference. Per-profile overrides live directly in `profiles/*.toml` under `[storage]` if you need per-game tuning.",
        ),
        ConfigItem(
            label="Persistent Overlay",
            key="persistent_overlay",
            scope="storage",
            type_="bool",
            default=True,
            group="Storage",
            extended_help="**Persistent Overlay — System Default**\n\nGlobal toggle mirroring `[storage] persistent_overlay`. Per-profile `[storage] persistent_overlay` overrides it.",
        ),
        ConfigItem(
            label="Auto Clean Workdir",
            key="auto_clean_workdir",
            scope="storage",
            type_="bool",
            default=True,
            group="Storage",
            extended_help="**Auto-Clean Workdir — System Default**\n\nMirrors global `storage.auto_clean_workdir`.",
        ),
        # --- SANDBOX ---
        ConfigItem(
            label="Sandbox",
            key="enabled",
            scope="sandbox",
            type_="bool",
            default=False,
            is_parent=True,
            expanded=False,
            group="Sandbox",
            extended_help="**Bubblewrap Sandbox (bwrap)**\n\nWrap the game in `bwrap` with selective bind mounts. Expand to configure isolation. Requires `bwrap` installed.",
        ),
        ConfigItem(
            label="Isolate HOME",
            key="isolate_home",
            scope="sandbox",
            type_="bool",
            default=True,
            parent_ref="sandbox.enabled",
            extended_help="**Isolate $HOME**\n\nBind `sandbox_home` over `$HOME` so the game cannot read the real home. Save games remain in the sandbox tree.",
        ),
        ConfigItem(
            label="Sandbox HOME",
            key="sandbox_home",
            scope="sandbox",
            type_="string",
            default="~/.local/share/game_sandboxes/example_game",
            parent_ref="sandbox.enabled",
            extended_help="**Sandbox HOME Path**\n\nDirectory that is bind-mounted as `$HOME` inside the sandbox. Supports `~` / `$HOME` / `$GAME_DIR` expansion. Per-profile, set to `~/.local/share/game_sandboxes/<game_id>`.",
        ),
        ConfigItem(
            label="Bind GPU",
            key="bind_gpu",
            scope="sandbox",
            type_="bool",
            default=True,
            parent_ref="sandbox.enabled",
            extended_help="**Bind GPU Nodes**\n\nPass `/dev/dri` and `/dev/nvidia*` into the sandbox (`--dev-bind-try`). Disable to block GPU access (software rendering).",
        ),
        ConfigItem(
            label="Bind Sound",
            key="bind_sound",
            scope="sandbox",
            type_="bool",
            default=True,
            parent_ref="sandbox.enabled",
            extended_help="**Bind Sound Sockets**\n\nPass PipeWire / PulseAudio sockets (`/run/user/<uid>/pipewire-0`, `/run/user/<uid>/pulse`). Disable for silent isolation.",
        ),
        ConfigItem(
            label="Bind Wayland",
            key="bind_wayland",
            scope="sandbox",
            type_="bool",
            default=True,
            parent_ref="sandbox.enabled",
            extended_help="**Bind Wayland Socket**\n\nPass `$WAYLAND_DISPLAY` socket (`/run/user/<uid>/wayland-1`) so the game can present. Disable to block display (headless).",
        ),
        ConfigItem(
            label="Allow Network",
            key="bind_network",
            scope="sandbox",
            type_="bool",
            default=False,
            parent_ref="sandbox.enabled",
            extended_help="**Allow Network Access**\n\nIf false, sandbox uses `--unshare-net` to block internet. Enable for online/multiplayer titles.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 6: PER-GAME — dynamic quick tweaks (generated at import time)
    # -------------------------------------------------------------------------
    6: [],

    # -------------------------------------------------------------------------
    # TAB 7: PRESETS & ACTIONS — user presets, built-ins, tools
    # -------------------------------------------------------------------------
    7: [
        ConfigItem(
            label="Open Profiles Folder",
            key="action_open_profiles",
            scope="DEFAULT",
            type_="action",
            default="xdg-open ~/user_scripts/gaming/runner/profiles >/dev/null 2>&1 &",
            group="Tools",
            extended_help="**Open Profiles Folder**\n\nOpens `~/user_scripts/gaming/runner/profiles` in your file manager so you can inspect or hand-edit any `*.toml` (e.g. `game_dir`, `executable`, `hooks`, `env`, `dll_overrides`).",
        ),
        ConfigItem(
            label="Open Global Config",
            key="action_open_global",
            scope="DEFAULT",
            type_="action",
            default="xdg-open ~/user_scripts/gaming/runner/config.toml >/dev/null 2>&1 &",
            group="Tools",
            extended_help="**Open Global Config**\n\nOpens `config.toml` (global defaults) in your `$EDITOR` / file manager for raw inspection.",
        ),
        ConfigItem(
            label="Open Presets Folder",
            key="action_open_presets",
            scope="DEFAULT",
            type_="action",
            default="xdg-open ~/user_scripts/gaming/runner/presets >/dev/null 2>&1 &",
            group="Tools",
            extended_help="**Open Presets Folder**\n\nOpens `presets/` containing `base_native.toml`, `base_wine_dxvk.toml`, etc. Useful to audit what `extends = \"...\"` inherits.",
        ),
        ConfigItem(
            label="Validate Profiles",
            key="action_validate",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'python3 ~/user_scripts/gaming/runner/master_runner.py validate; echo \"[validate] done — press Enter\"; read'",
            group="Tools",
            force_interactive=True,
            extended_help="**Validate All Profiles**\n\nRuns `master_runner.py validate` in an interactive terminal to check DwarFS image presence, executable resolution, and TOML syntax. Failures are highlighted in the matrix.",
        ),
        ConfigItem(
            label="System Doctor",
            key="action_doctor",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'python3 ~/user_scripts/gaming/runner/master_runner.py doctor; echo \"[doctor] done — press Enter\"; read'",
            group="Tools",
            force_interactive=True,
            extended_help="**System Doctor**\n\nRuns `master_runner.py doctor` — checks kernel `vm.max_map_count`, Wayland session, GPU DRM nodes, required binaries (`dwarfs`, `fuse-overlayfs`, `wine`, `gamescope`, etc.), and PipeWire socket.",
        ),
        ConfigItem(
            label="List Profiles (Installed Only)",
            key="action_list",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'python3 ~/user_scripts/gaming/runner/master_runner.py list; echo \"[list] done — press Enter\"; read'",
            group="Tools",
            force_interactive=True,
            extended_help="**List Installed Games**\n\nRuns `master_runner.py list` (dynamic filtered view — only profiles whose `game_dir` / `dwarfs_image` are present on disk). Use `list --all` in a terminal to see polluted/hidden profiles.",
        ),
        ConfigItem(
            label="Factory Reset — Global Defaults",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Reset **all** global settings to factory defaults? This backs up `config.toml` first (via the router's --backup).",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset**\n\nReverts every global item to its `default` via `{\"__ALL_DEFAULTS__\": True}`. Per-game `profiles/*.toml` files are **not** touched. Use the router's `--backup` / `--restore` for rollbacks.",
        ),
        ConfigItem(
            label="Performance — dGPU + GameMode + 144Hz",
            key="preset_performance",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Apply **Performance** global preset? Sets dGPU, GameMode, 144Hz Gamescope, MangoHud, performance governor, and PipeWire low latency.",
            preset_payload={
                "runner.default_gpu": "discrete",
                "runner.desktop_notifications": True,
                "runner.auto_unmount_on_exit": True,
                "runner.clean_stale_workdirs": True,
                "graphics.wayland_native": True,
                "graphics.prefer_xwayland": False,
                "graphics.vulkan_icd": "auto",
                "graphics.gamescope.enabled": True,
                "graphics.gamescope.mode": "embedded",
                "graphics.gamescope.refresh_rate": 144,
                "graphics.gamescope.fsr_upscaling": False,
                "graphics.gamescope.allow_tearing": True,
                "graphics.gamescope.force_grab_cursor": False,
                "graphics.gamescope.hdr": False,
                "performance.gamemode": True,
                "performance.mangohud": True,
                "performance.mangohud_preset": "minimal",
                "performance.fps_limit": 144,
                "performance.cpu_governor": "performance",
                "performance.process_priority": -5,
                "audio.driver": "pipewire",
                "audio.pipewire_latency": "128/48000",
                "runtime.wine.sync_mode": "auto",
                "runtime.wine.dxvk": True,
                "runtime.wine.vkd3d": True,
                "runtime.wine.dxvk_nvapi": False,
                "storage.persistent_overlay": True,
                "storage.auto_clean_workdir": True,
                "sandbox.enabled": False,
            },
            extended_help="**Performance Preset**\n\nStrict snapshot that prefers dGPU, enables Gamescope 144Hz + immediate flips, MangoHud minimal, GameMode, `performance` governor, and 128/48000 PipeWire quantum. Omitted global keys revert to defaults (strict snapshot semantics).",
        ),
        ConfigItem(
            label="Power Saving — iGPU + 60Hz + Powersave",
            key="preset_power_saving",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Apply **Power Saving** preset? Forces iGPU, disables Gamescope, caps 60 FPS, powersave governor, and silences MangoHud.",
            preset_payload={
                "runner.default_gpu": "integrated",
                "runner.desktop_notifications": True,
                "runner.auto_unmount_on_exit": True,
                "runner.clean_stale_workdirs": True,
                "graphics.wayland_native": True,
                "graphics.prefer_xwayland": False,
                "graphics.vulkan_icd": "auto",
                "graphics.gamescope.enabled": False,
                "graphics.gamescope.mode": "embedded",
                "graphics.gamescope.refresh_rate": 60,
                "graphics.gamescope.fsr_upscaling": False,
                "graphics.gamescope.allow_tearing": False,
                "graphics.gamescope.force_grab_cursor": False,
                "graphics.gamescope.hdr": False,
                "performance.gamemode": False,
                "performance.mangohud": False,
                "performance.mangohud_preset": "",
                "performance.fps_limit": 60,
                "performance.cpu_governor": "powersave",
                "performance.process_priority": 0,
                "audio.driver": "pipewire",
                "audio.pipewire_latency": "256/48000",
                "runtime.wine.sync_mode": "auto",
                "runtime.wine.dxvk": True,
                "runtime.wine.vkd3d": False,
                "runtime.wine.dxvk_nvapi": False,
                "storage.persistent_overlay": True,
                "storage.auto_clean_workdir": True,
                "sandbox.enabled": False,
            },
            extended_help="**Power Saving Preset**\n\nOptimizes for battery / thermals: iGPU, no Gamescope, 60 FPS cap, `powersave` / `schedutil`-friendly, MangoHud off, relaxed PipeWire quantum. Strict snapshot — unlisted keys reset to defaults.",
        ),
        ConfigItem(
            label="Balanced — Auto GPU + 90Hz",
            key="preset_balanced",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Apply **Balanced** preset? Auto GPU, Wayland native, 90Hz cap, MangoHud minimal, schedutil governor.",
            preset_payload={
                "runner.default_gpu": "auto",
                "runner.desktop_notifications": True,
                "runner.auto_unmount_on_exit": True,
                "runner.clean_stale_workdirs": True,
                "graphics.wayland_native": True,
                "graphics.prefer_xwayland": False,
                "graphics.vulkan_icd": "auto",
                "graphics.gamescope.enabled": False,
                "graphics.gamescope.mode": "embedded",
                "graphics.gamescope.refresh_rate": 90,
                "graphics.gamescope.fsr_upscaling": False,
                "graphics.gamescope.allow_tearing": True,
                "graphics.gamescope.force_grab_cursor": False,
                "graphics.gamescope.hdr": False,
                "performance.gamemode": True,
                "performance.mangohud": False,
                "performance.mangohud_preset": "",
                "performance.fps_limit": 90,
                "performance.cpu_governor": "schedutil",
                "performance.process_priority": 0,
                "audio.driver": "pipewire",
                "audio.pipewire_latency": "128/48000",
                "runtime.wine.sync_mode": "auto",
                "runtime.wine.dxvk": True,
                "runtime.wine.vkd3d": True,
                "runtime.wine.dxvk_nvapi": False,
                "storage.persistent_overlay": True,
                "storage.auto_clean_workdir": True,
                "sandbox.enabled": False,
            },
            extended_help="**Balanced Preset**\n\nMiddle ground: `auto` GPU heuristic, no forced Gamescope, 90Hz cap, GameMode on, `schedutil` governor. Good for mixed 2D/3D workloads.",
        ),
    ],
}

# ---------------------------------------------------------------------------
# 6. PER-GAME DYNAMIC INJECTION (Tab index 6)
# ---------------------------------------------------------------------------
_PER_GAME_HELP_GPU = "**Per-Game GPU**\n\nOverrides `graphics.gpu` for this game only.\n- **auto**: Inherit heuristic (or global `runner.default_gpu`).\n- **discrete**: Force dGPU for this title.\n- **integrated**: Force iGPU (e.g. light indie / 2D)."

_PER_GAME_HELP_RUNTIME = "**Per-Game Runtime Type**\n\nOverrides `runtime.type` for this game.\n- **native**: Linux ELF binary / script.\n- **wine**: Windows executable via Wine-Staging.\n- **proton**: Steam Proton container.\n- **umu**: Unified launcher (umu-run).\n- **script**: Custom shell script launcher."

_PER_GAME_HELP_WAYLAND = "**Per-Game Wayland Native**\n\nOverrides `graphics.wayland_native` for this game. Enables Wine's pure Wayland driver (`waylanddrv`) for zero-X11 presentation on Hyprland."

_PER_GAME_HELP_DXVK = "**Per-Game DXVK**\n\nOverrides `runtime.wine.dxvk` for this game. Translates Direct3D 9/10/11 into Vulkan. Disable to fall back to WineD3D OpenGL."

_PER_GAME_HELP_VKD3D = "**Per-Game VKD3D-Proton**\n\nOverrides `runtime.wine.vkd3d` for this game. Translates Direct3D 12 into native Vulkan via VKD3D-Proton."

_PER_GAME_HELP_ICD = "**Per-Game Vulkan ICD**\n\nOverrides `graphics.vulkan_icd` for this game.\n- **auto**: Auto-detects based on active GPU vendor.\n- **nvidia / intel / radv / amd**: Forces a specific Vulkan ICD manifest."

_PER_GAME_HELP_FPS = "**Per-Game FPS Limit**\n\nOverrides `performance.fps_limit` for this game.\n- **0**: Uncapped / auto.\n- **>0**: Caps via Gamescope `--framerate-limit`, or `MANGOHUD_CONFIG` / `DXVK_FRAME_RATE` when Gamescope is off."

_PER_GAME_HELP_GS = "**Per-Game Gamescope**\n\nOverrides `graphics.gamescope.enabled` for this game. Toggle per title — e.g. enable for Unity 2017 / Source 1 / FNA titles that need an embedded Xwayland sandbox, disable for native Wayland titles."

_PER_GAME_HELP_MH = "**Per-Game MangoHud**\n\nOverrides `performance.mangohud` for this game. Shows telemetry (frametime, GPU/CPU load) and also enables FPS limiting via `MANGOHUD_CONFIG`."

_PER_GAME_HELP_GM = "**Per-Game GameMode**\n\nOverrides `performance.gamemode` for this game. `true` wraps launch with `gamemoderun` (if installed)."

_PER_GAME_HELP_EXT = "**Per-Game Preset (extends)**\n\nOverrides the archetype preset this profile inherits:\n- **base_native**: Linux ELF / shell.\n- **base_unity_native**: Unity 2021+ native.\n- **base_wine_dxvk**: Wine-Staging + DXVK (D3D9-11).\n- **base_unreal_engine_5**: UE5 / D3D12 + VKD3D."

_PER_GAME_HELP_NVAPI = "**Per-Game DLSS / NVAPI**\n\nOverrides `runtime.wine.dxvk_nvapi` for this game. Enables NVIDIA DLSS Super Resolution, Frame Generation, and Reflex through DXVK-NVAPI."
_PER_GAME_HELP_FSR = "**Per-Game FSR Upscaling**\n\nOverrides `graphics.gamescope.fsr_upscaling` for this game. Enables AMD FidelityFX Super Resolution spatial upscaling when Gamescope is active."
_PER_GAME_HELP_UNMOUNT = "**Per-Game Auto Unmount**\n\nOverrides `runner.auto_unmount_on_exit` for this game. Automatically unmounts DwarFS and OverlayFS layers upon game exit (guarded by MountLease)."

_PER_GAME_HELP_SYNC = "**Per-Game Wine Sync**\n\nOverrides `runtime.wine.sync_mode` for Wine titles.\n- **auto** (recommended): Auto-probes /dev/ntsync on Linux 7.1+; falls back to fsync.\n- **ntsync**: Forces in-kernel NT sync primitives.\n- **fsync**: Forces `WINEFSYNC=1`.\n- **esync**: Forces `WINEESYNC=1`.\n- **server**: Standard wineserver sync."

_PER_GAME_HELP_XWAYLAND = "**Per-Game XWayland**\n\nOverrides `graphics.prefer_xwayland` for this game. Forces `DISPLAY=:0` XWayland presentation. Useful for Unity 2017 / Source 1 titles that query XRandR root geometry; prefer Gamescope embedded for a cleaner sandbox."
_PER_GAME_HELP_WINE_BIN = "**Per-Game Wine Binary**\n\nOverrides `runtime.wine.wine_binary` for this game. Path or name of Wine/Proton binary (`wine`, `wine64`, `GE-Proton`, or absolute path to custom build)."
_PER_GAME_HELP_ARCH = "**Per-Game Wine Arch**\n\nOverrides `runtime.wine.arch` for this game.\n- **win64**: 64-bit prefix (default).\n- **win32**: Legacy 32-bit prefix."
_PER_GAME_HELP_LAA = "**Per-Game Large Address Aware**\n\nOverrides `runtime.wine.large_address_aware` for this game. Exposes 4 GB address space to 32-bit processes (`WINE_LARGE_ADDRESS_AWARE=1`)."
_PER_GAME_HELP_WINE_DBG = "**Per-Game Wine Debug**\n\nOverrides `runtime.wine.debug` for this game. WINEDEBUG filter:\n- **fixme-all**: Hide stubs (default).\n- **-all**: Silence all.\n- **warn+all/err+all**: Verbose."
_PER_GAME_HELP_MH_PRESET = "**Per-Game MangoHud Preset**\n\nOverrides `performance.mangohud_preset`. Appended as `MANGOHUD_CONFIG=preset=<preset>`. Empty = MangoHud defaults."
_PER_GAME_HELP_CPU_GOV = "**Per-Game CPU Governor**\n\nHint for `performance.cpu_governor` per game. Logged and may be requested via GameMode; actual switching via `cpupower`."
_PER_GAME_HELP_PRIO = "**Per-Game Nice Level**\n\nOverrides `performance.process_priority` nice value. -20 (high) to 19 (low), 0 = default."
_PER_GAME_HELP_GS_MODE = "**Per-Game Gamescope Mode**\n\nOverrides `graphics.gamescope.mode` for this game. `embedded` (-b) recommended, `fullscreen` (-f), `borderless`/`nested` alternatives."
_PER_GAME_HELP_GS_W = "**Per-Game Render Width**\n\nOverrides `graphics.gamescope.width` (`-w`). 0 = auto-detect, else fixed render target width."
_PER_GAME_HELP_GS_H = "**Per-Game Render Height**\n\nOverrides `graphics.gamescope.height` (`-h`). 0 = auto."
_PER_GAME_HELP_GS_OW = "**Per-Game Output Width**\n\nOverrides `graphics.gamescope.output_width` (`-W`). 0 = native display."
_PER_GAME_HELP_GS_OH = "**Per-Game Output Height**\n\nOverrides `graphics.gamescope.output_height` (`-H`). 0 = native."
_PER_GAME_HELP_GS_RR = "**Per-Game Refresh Rate**\n\nOverrides `graphics.gamescope.refresh_rate` (`-r`). 0 = auto-detect via hyprctl/DRM, else fixed Hz. FPS limit may override via `--framerate-limit`."
_PER_GAME_HELP_GS_SHARP = "**Per-Game FSR Sharpness**\n\nOverrides `graphics.gamescope.fsr_sharpness` (0–20). Only when FSR upscaling is enabled."
_PER_GAME_HELP_GS_TEAR = "**Per-Game Allow Tearing**\n\nOverrides `graphics.gamescope.allow_tearing` (`--immediate-flips`). Low-latency but may tear."
_PER_GAME_HELP_GS_GRAB = "**Per-Game Force Grab Cursor**\n\nOverrides `graphics.gamescope.force_grab_cursor` (`--force-grab-cursor`). Confine cursor for FPS titles."
_PER_GAME_HELP_GS_HDR = "**Per-Game Gamescope HDR**\n\nOverrides `graphics.gamescope.hdr` (`--hdr-enabled`). Requires HDR monitor/compositor."

def _get_profile_value(pid: str, scope: str, key: str, fallback):
    """Read a single value from profiles/<pid>.toml with preset inheritance fallback."""
    try:
        import tomllib
        p = _PROFILES_DIR / f"{pid}.toml"
        if not p.is_file():
            return fallback
        with open(p, "rb") as f:
            data = tomllib.load(f)

        def _lookup(d: dict, sc: str, k: str):
            if sc == "DEFAULT":
                return d.get(k)
            cur = d
            for part in sc.split("."):
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(part)
            if isinstance(cur, dict) and k in cur:
                return cur[k]
            return None

        # 1. Direct profile lookup
        val = _lookup(data, scope, key)
        if val is not None:
            return val

        # 2. Preset inheritance lookup (extends)
        extends = data.get("extends")
        if isinstance(extends, str) and extends:
            preset_file = _PRESETS_DIR / f"{extends}.toml"
            if preset_file.is_file():
                with open(preset_file, "rb") as pf:
                    preset_data = tomllib.load(pf)
                val = _lookup(preset_data, scope, key)
                if val is not None:
                    return val

        return fallback
    except Exception:
        return fallback

def _build_per_game_items() -> list[ConfigItem]:
    if not _DISCOVERED_PROFILES:
        return [
            ConfigItem(
                label="No profiles discovered",
                key="per_game_empty",
                scope="DEFAULT",
                type_="action",
                default=":",
                group="Games",
                extended_help="**No Profiles Found**\n\nNo `profiles/*.toml` files were discovered (excluding `_template.toml`). Create one via `cp profiles/_template.toml profiles/<game_id>.toml` or run `python3 ~/user_scripts/gaming/runner/master_runner.py init --help`.",
            )
        ]

    items: list[ConfigItem] = []
    for pid, display in _DISCOVERED_PROFILES:
        menu_key = f"menu_{pid}"
        profile_toml = f"~/user_scripts/gaming/runner/profiles/{pid}.toml"

        # Parent folder — one per game, collapsed by default
        items.append(
            ConfigItem(
                label=display,
                key=menu_key,
                scope="DEFAULT",
                type_="menu",
                default=None,
                is_parent=True,
                expanded=False,
                group="Games",
                extended_help=f"**{display}  (`{pid}`)**\n\nProfile: `~/user_scripts/gaming/runner/profiles/{pid}.toml`\nExpand to tweak this game's high-frequency overrides. These write directly to the profile TOML and shadow the global defaults / preset chain. Keep `game_dir`, `executable`, `hooks`, `env`, and `dwarfs_image` in the raw TOML.",
            )
        )

        # Per-game defaults are read from the current profile so the TUI does NOT show them as edited.
        # This makes the TUI's default == profile's actual value, so is_modified is False on first load.
        _rt_def = _get_profile_value(pid, "runtime", "type", "native")
        _gpu_def = _get_profile_value(pid, "graphics", "gpu", "auto")
        _wayland_def = _get_profile_value(pid, "graphics", "wayland_native", True)
        _icd_def = _get_profile_value(pid, "graphics", "vulkan_icd", "auto")
        _dxvk_def = _get_profile_value(pid, "runtime.wine", "dxvk", True)
        _vkd3d_def = _get_profile_value(pid, "runtime.wine", "vkd3d", True)
        _fps_def = _get_profile_value(pid, "performance", "fps_limit", 60)
        # Ensure int
        try:
            _fps_def = int(_fps_def)
        except Exception:
            _fps_def = 60
        _gs_def = _get_profile_value(pid, "graphics.gamescope", "enabled", False)
        _mh_def = _get_profile_value(pid, "performance", "mangohud", False)
        _gm_def = _get_profile_value(pid, "performance", "gamemode", True)
        _ext_def = _get_profile_value(pid, "DEFAULT", "extends", "base_native")
        _sync_def = _get_profile_value(pid, "runtime.wine", "sync_mode", "auto")
        # Additional per-game defaults for extended coverage (mirrors global tabs)
        _xway_def = _get_profile_value(pid, "graphics", "prefer_xwayland", False)
        _winebin_def = _get_profile_value(pid, "runtime.wine", "wine_binary", "wine")
        _arch_def = _get_profile_value(pid, "runtime.wine", "arch", "win64")
        _laa_def = _get_profile_value(pid, "runtime.wine", "large_address_aware", True)
        _dbg_def = _get_profile_value(pid, "runtime.wine", "debug", "fixme-all")
        _mh_preset_def = _get_profile_value(pid, "performance", "mangohud_preset", "")
        _cpu_gov_def = _get_profile_value(pid, "performance", "cpu_governor", "performance")
        _prio_def = _get_profile_value(pid, "performance", "process_priority", 0)
        try:
            _prio_def = int(_prio_def)
        except Exception:
            _prio_def = 0
        _gs_mode_def = _get_profile_value(pid, "graphics.gamescope", "mode", "borderless")
        _gs_w_def = _get_profile_value(pid, "graphics.gamescope", "width", 0)
        _gs_h_def = _get_profile_value(pid, "graphics.gamescope", "height", 0)
        _gs_ow_def = _get_profile_value(pid, "graphics.gamescope", "output_width", 0)
        _gs_oh_def = _get_profile_value(pid, "graphics.gamescope", "output_height", 0)
        _gs_rr_def = _get_profile_value(pid, "graphics.gamescope", "refresh_rate", 0)
        _gs_sharp_def = _get_profile_value(pid, "graphics.gamescope", "fsr_sharpness", 5)
        _gs_tear_def = _get_profile_value(pid, "graphics.gamescope", "allow_tearing", True)
        _gs_grab_def = _get_profile_value(pid, "graphics.gamescope", "force_grab_cursor", False)
        _gs_hdr_def = _get_profile_value(pid, "graphics.gamescope", "hdr", False)
        try:
            _gs_w_def = int(_gs_w_def)
        except Exception:
            _gs_w_def = 0
        try:
            _gs_h_def = int(_gs_h_def)
        except Exception:
            _gs_h_def = 0
        try:
            _gs_ow_def = int(_gs_ow_def)
        except Exception:
            _gs_ow_def = 0
        try:
            _gs_oh_def = int(_gs_oh_def)
        except Exception:
            _gs_oh_def = 0
        try:
            _gs_rr_def = int(_gs_rr_def)
        except Exception:
            _gs_rr_def = 0
        try:
            _gs_sharp_def = int(_gs_sharp_def)
        except Exception:
            _gs_sharp_def = 5

        # Runtime Type
        items.append(
            ConfigItem(
                label="Runtime Type",
                key="type",
                scope="runtime",
                type_="cycle",
                default=_rt_def if _rt_def in ("native", "wine", "proton", "umu", "script") else "native",
                options=["native", "wine", "proton", "umu", "script"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_RUNTIME,
            )
        )
        # GPU
        items.append(
            ConfigItem(
                label="GPU",
                key="gpu",
                scope="graphics",
                type_="cycle",
                default=_gpu_def if _gpu_def in ("auto", "discrete", "integrated") else "auto",
                options=["auto", "discrete", "integrated"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GPU,
            )
        )
        # Wayland Native
        items.append(
            ConfigItem(
                label="Wayland Native",
                key="wayland_native",
                scope="graphics",
                type_="bool",
                default=bool(_wayland_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_WAYLAND,
            )
        )
        # DXVK
        items.append(
            ConfigItem(
                label="DXVK (D3D11/9)",
                key="dxvk",
                scope="runtime.wine",
                type_="bool",
                default=bool(_dxvk_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_DXVK,
            )
        )
        # VKD3D
        items.append(
            ConfigItem(
                label="VKD3D (D3D12)",
                key="vkd3d",
                scope="runtime.wine",
                type_="bool",
                default=bool(_vkd3d_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_VKD3D,
            )
        )
        # Vulkan ICD
        items.append(
            ConfigItem(
                label="Vulkan ICD",
                key="vulkan_icd",
                scope="graphics",
                type_="cycle",
                default=_icd_def if _icd_def in ("auto", "nvidia", "intel", "radv", "amd") else "auto",
                options=["auto", "nvidia", "intel", "radv", "amd"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_ICD,
            )
        )
        # FPS
        items.append(
            ConfigItem(
                label="FPS Limit",
                key="fps_limit",
                scope="performance",
                type_="int",
                default=_fps_def,
                min_val=0,
                max_val=480,
                step=5,
                options=[0, 30, 60, 90, 120, 144, 165, 240, 360],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_FPS,
            )
        )
        # Gamescope enabled
        items.append(
            ConfigItem(
                label="Gamescope",
                key="enabled",
                scope="graphics.gamescope",
                type_="bool",
                default=bool(_gs_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS,
            )
        )
        # MangoHud
        items.append(
            ConfigItem(
                label="MangoHud",
                key="mangohud",
                scope="performance",
                type_="bool",
                default=bool(_mh_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_MH,
            )
        )
        # GameMode
        items.append(
            ConfigItem(
                label="GameMode",
                key="gamemode",
                scope="performance",
                type_="bool",
                default=bool(_gm_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GM,
            )
        )
        # FSR Upscaling (when Gamescope is used)
        _fsr_def = _get_profile_value(pid, "graphics.gamescope", "fsr_upscaling", False)
        items.append(
            ConfigItem(
                label="FSR Upscaling",
                key="fsr_upscaling",
                scope="graphics.gamescope",
                type_="bool",
                default=bool(_fsr_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_FSR,
            )
        )
        # DLSS / DXVK-NVAPI
        _nvapi_def = _get_profile_value(pid, "runtime.wine", "dxvk_nvapi", False)
        items.append(
            ConfigItem(
                label="DLSS (NVAPI)",
                key="dxvk_nvapi",
                scope="runtime.wine",
                type_="bool",
                default=bool(_nvapi_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_NVAPI,
            )
        )
        # Auto Unmount
        _unmount_def = _get_profile_value(pid, "runner", "auto_unmount_on_exit", True)
        items.append(
            ConfigItem(
                label="Auto Unmount",
                key="auto_unmount_on_exit",
                scope="runner",
                type_="bool",
                default=bool(_unmount_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_UNMOUNT,
            )
        )
        # Extends (preset)
        items.append(
            ConfigItem(
                label="Preset (extends)",
                key="extends",
                scope="DEFAULT",
                type_="picker",
                default=_ext_def if _ext_def in ("base_native", "base_unity_native", "base_wine_dxvk", "base_unreal_engine_5", "base_proton_umu", "handheld_720p") else "base_native",
                options=["base_native", "base_unity_native", "base_wine_dxvk", "base_unreal_engine_5", "base_proton_umu", "handheld_720p"],
                hints=[
                    "Native Linux ELF / shell",
                    "Unity 2021+ native Linux",
                    "Wine-Staging + DXVK (D3D9-11)",
                    "UE5 / D3D12 + VKD3D-Proton",
                    "Proton / UMU launcher",
                    "Compact 720p Gamescope with FSR",
                ],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_EXT,
            )
        )
        # Wine sync mode
        items.append(
            ConfigItem(
                label="Wine Sync",
                key="sync_mode",
                scope="runtime.wine",
                type_="cycle",
                default=_sync_def if _sync_def in ("auto", "ntsync", "fsync", "esync", "server") else "auto",
                options=["auto", "ntsync", "fsync", "esync", "server"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_SYNC,
            )
        )
        # Prefer XWayland
        items.append(
            ConfigItem(
                label="Prefer XWayland",
                key="prefer_xwayland",
                scope="graphics",
                type_="bool",
                default=bool(_xway_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_XWAYLAND,
            )
        )
        # Wine Binary
        items.append(
            ConfigItem(
                label="Wine Binary",
                key="wine_binary",
                scope="runtime.wine",
                type_="string",
                default=str(_winebin_def),
                options=["wine", "wine64", "proton", "GE-Proton"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_WINE_BIN,
            )
        )
        # Wine Arch
        items.append(
            ConfigItem(
                label="Wine Arch",
                key="arch",
                scope="runtime.wine",
                type_="cycle",
                default=_arch_def if _arch_def in ("win64", "win32") else "win64",
                options=["win64", "win32"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_ARCH,
            )
        )
        # Large Address Aware
        items.append(
            ConfigItem(
                label="Large Address Aware",
                key="large_address_aware",
                scope="runtime.wine",
                type_="bool",
                default=bool(_laa_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_LAA,
            )
        )
        # Wine Debug
        items.append(
            ConfigItem(
                label="Wine Debug",
                key="debug",
                scope="runtime.wine",
                type_="string",
                default=str(_dbg_def),
                options=["fixme-all", "-all", "warn+all", "err+all"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_WINE_DBG,
            )
        )
        # MangoHud Preset
        items.append(
            ConfigItem(
                label="MangoHud Preset",
                key="mangohud_preset",
                scope="performance",
                type_="string",
                default=str(_mh_preset_def),
                options=["", "minimal", "full", "fps_only", "horizontal"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_MH_PRESET,
            )
        )
        # CPU Governor
        items.append(
            ConfigItem(
                label="CPU Governor",
                key="cpu_governor",
                scope="performance",
                type_="cycle",
                default=_cpu_gov_def if _cpu_gov_def in ("performance", "powersave", "schedutil", "ondemand", "conservative") else "performance",
                options=["performance", "powersave", "schedutil", "ondemand", "conservative"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_CPU_GOV,
            )
        )
        # Process Priority
        items.append(
            ConfigItem(
                label="Nice Level",
                key="process_priority",
                scope="performance",
                type_="int",
                default=_prio_def,
                min_val=-20,
                max_val=19,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_PRIO,
            )
        )
        # Gamescope Mode
        items.append(
            ConfigItem(
                label="GS Mode",
                key="mode",
                scope="graphics.gamescope",
                type_="cycle",
                default=_gs_mode_def if _gs_mode_def in ("embedded", "fullscreen", "borderless", "nested", "windowed") else "borderless",
                options=["embedded", "fullscreen", "borderless", "nested"],
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_MODE,
            )
        )
        # Gamescope Width
        items.append(
            ConfigItem(
                label="GS Render W",
                key="width",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_w_def,
                min_val=0,
                max_val=7680,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_W,
            )
        )
        items.append(
            ConfigItem(
                label="GS Render H",
                key="height",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_h_def,
                min_val=0,
                max_val=4320,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_H,
            )
        )
        items.append(
            ConfigItem(
                label="GS Output W",
                key="output_width",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_ow_def,
                min_val=0,
                max_val=7680,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_OW,
            )
        )
        items.append(
            ConfigItem(
                label="GS Output H",
                key="output_height",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_oh_def,
                min_val=0,
                max_val=4320,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_OH,
            )
        )
        items.append(
            ConfigItem(
                label="GS Refresh",
                key="refresh_rate",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_rr_def,
                min_val=0,
                max_val=500,
                step=5,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_RR,
            )
        )
        items.append(
            ConfigItem(
                label="GS Sharpness",
                key="fsr_sharpness",
                scope="graphics.gamescope",
                type_="int",
                default=_gs_sharp_def,
                min_val=0,
                max_val=20,
                step=1,
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_SHARP,
            )
        )
        items.append(
            ConfigItem(
                label="GS Tearing",
                key="allow_tearing",
                scope="graphics.gamescope",
                type_="bool",
                default=bool(_gs_tear_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_TEAR,
            )
        )
        items.append(
            ConfigItem(
                label="GS Grab Cursor",
                key="force_grab_cursor",
                scope="graphics.gamescope",
                type_="bool",
                default=bool(_gs_grab_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_GRAB,
            )
        )
        items.append(
            ConfigItem(
                label="GS HDR",
                key="hdr",
                scope="graphics.gamescope",
                type_="bool",
                default=bool(_gs_hdr_def),
                parent_ref=menu_key,
                target_file_override=profile_toml,
                extended_help=_PER_GAME_HELP_GS_HDR,
            )
        )
        # Quick launch action per game (convenience)
        items.append(
            ConfigItem(
                label=f"▶ Launch {display}",
                key=f"action_launch_{pid}",
                scope="DEFAULT",
                type_="action",
                default=f"bash -c 'python3 ~/user_scripts/gaming/runner/master_runner.py run {pid}; echo \"[{pid}] exit:$? — press Enter\"; read'",
                parent_ref=menu_key,
                target_file_override=profile_toml,
                force_interactive=True,
                extended_help=f"**Launch {display}**\n\nExecutes `master_runner.py run {pid}` in an interactive terminal. The game runs with the currently saved TOML state (global + preset + this profile). Prefer the Rich menu `master_runner.py menu` for fuzzy-finder launches.",
            )
        )

    # Footer tools inside Per-Game tab (outside any folder, same tab)
    items.append(
        ConfigItem(
            label="────────── Bulk Tools ──────────",
            key="per_game_divider",
            scope="DEFAULT",
            type_="action",
            default=":",
            group="Tools",
            extended_help="**Bulk Tools**\n\nUtilities that operate on all discovered profiles.",
        )
    )
    items.append(
        ConfigItem(
            label="Validate All Profiles",
            key="action_per_game_validate",
            scope="DEFAULT",
            type_="action",
            default="bash -c 'python3 ~/user_scripts/gaming/runner/master_runner.py validate; echo \"[validate] done — press Enter\"; read'",
            group="Tools",
            force_interactive=True,
            extended_help="**Validate All Profiles**\n\nRuns `master_runner.py validate` to audit DwarFS images, executables, and TOML syntax across all profiles.",
        )
    )
    items.append(
        ConfigItem(
            label="Open Profiles in Editor",
            key="action_per_game_open",
            scope="DEFAULT",
            type_="action",
            default="xdg-open ~/user_scripts/gaming/runner/profiles >/dev/null 2>&1 &",
            group="Tools",
            extended_help="**Open Profiles Folder**\n\nOpens `~/user_scripts/gaming/runner/profiles` for direct hand-editing of `game_dir`, `executable`, `hooks`, `env`, etc.",
        )
    )
    return items

SCHEMA[6] = _build_per_game_items()

# =============================================================================
# DIRECT EXECUTION HANDLER (router invocation)
# =============================================================================
if __name__ == "__main__":
    import subprocess

    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
