#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: GPU SCREEN RECORDER SCHEMA (NATIVE INI)
Targets: Pure Wayland | Arch Linux | GPU Screen Recorder 6.1.3+
===============================================================================
"""

import os
import subprocess
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recorder_config import CONFIG_FILE, DEFAULTS, ensure_config

ensure_config()

# Hand off before loading the schema so hardware discovery runs once, in the router.
if __name__ == "__main__":
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    if not main_router.is_file():
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
    os.execv(sys.executable, [sys.executable, str(main_router), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "ini"
TARGET_FILE = str(CONFIG_FILE)
APP_TITLE   = "GPU Screen Recorder"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE        = "auto"
THEME_FILE          = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB    = "Profiles"

# =============================================================================
# 3. DYNAMIC HARDWARE DISCOVERY
# =============================================================================
def fetch_audio_devices() -> tuple[list[str], list[str], list[str], list[str]]:
    """Discover PulseAudio/PipeWire sources, keeping defaults when unavailable."""
    out_opts = ["none", "default_output"]
    out_hints = ["No Output", "Default Desktop Audio"]
    in_opts = ["none", "default_input"]
    in_hints = ["No Input", "Default Microphone"]
    try:
        result = subprocess.run(
            ["gpu-screen-recorder", "--list-audio-devices"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1.5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return out_opts, out_hints, in_opts, in_hints

    seen = {"none", "default_output", "default_input"}
    for line in result.stdout.splitlines():
        dev_id, separator, description = line.partition("|")
        if not separator or not dev_id or dev_id in seen:
            continue
        seen.add(dev_id)
        # Monitor sources capture sinks; other sources include physical and virtual mics.
        if dev_id.endswith(".monitor"):
            out_opts.append(dev_id)
            out_hints.append(description or dev_id)
        else:
            in_opts.append(dev_id)
            in_hints.append(description or dev_id)
    return out_opts, out_hints, in_opts, in_hints

OUT_OPTS, OUT_HINTS, IN_OPTS, IN_HINTS = fetch_audio_devices()

# =============================================================================
# 4. TABS (STRICTLY ONE WORD)
# =============================================================================
TABS = [
    "Capture",
    "Video",
    "Audio",
    "Replay",
    "Profiles"
]

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {

    # -------------------------------------------------------------------------
    # TAB 0: CAPTURE
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Source",
            key="window",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["window"],
            options=["screen", "portal", "region"],
            group="Target",
            extended_help="**Capture Target** (`-w`)\n\n`screen` captures the first output reported by the recorder. `portal` uses the native Wayland picker. `region` utilizes Slurp to draw a custom area."
        ),
        ConfigItem(
            label="Region",
            key="region",
            scope="DEFAULT",
            type_="string",
            default=DEFAULTS["region"],
            group="Target",
            extended_help="**Region String**\n\nSpecify logical Wayland coordinates (e.g., `1280x720+100+50`); the recorder applies output scaling. If left blank, Slurp will automatically execute so you can draw the capture zone."
        ),
        ConfigItem(
            label="FPS",
            key="fps",
            scope="DEFAULT",
            type_="int",
            default=DEFAULTS["fps"],
            min_val=1,
            max_val=360,
            step=1,
            group="Playback",
            extended_help="**Frame Rate** (`-f`)\n\nTarget maximum frames per second for the video recording."
        ),
        ConfigItem(
            label="Cursor",
            key="cursor",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["cursor"],
            options=["yes", "no"],
            group="Playback",
            extended_help="**Show Cursor** (`-cursor`)\n\nToggle whether your mouse cursor is visible in the final output file."
        ),
        ConfigItem(
            label="Indicator",
            key="show_indicator",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["show_indicator"],
            options=["yes", "no"],
            group="Playback",
            extended_help="**Recording Indicator**\n\nToggle the blinking red dot notification that appears while recording."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: VIDEO (ENCODING & FORMATS)
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Encoder",
            key="encoder",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["encoder"],
            options=["gpu", "cpu"],
            group="Hardware",
            extended_help="**Encoder Device** (`-encoder`)\n\n`gpu` uses a supported hardware encoder. `cpu` selects software H.264 encoding; choose auto or h264 as the codec."
        ),
        ConfigItem(
            label="Tune",
            key="tune",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["tune"],
            options=["performance", "quality"],
            group="Hardware",
            extended_help="**Encoder Tuning** (`-tune`)\n\nNVIDIA ONLY. Adjusts the silicon bias towards raw encoding speed or visual fidelity."
        ),
        ConfigItem(
            label="Power",
            key="low_power",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["low_power"],
            options=["yes", "no"],
            group="Hardware",
            extended_help="**Low Power Mode** (`-low-power`)\n\nCurrently affects AMD GPUs. Allows a lower power state during recording, subject to driver behavior. For portal capture, content timing can further reduce encoding work when idle."
        ),
        ConfigItem(
            label="Codec",
            key="codec",
            scope="DEFAULT",
            type_="picker",
            default=DEFAULTS["codec"],
            options=[
                "auto", "h264", "hevc", "av1", "vp8", "vp9",
                "hevc_hdr", "av1_hdr", "hevc_10bit", "av1_10bit",
                "h264_vulkan", "hevc_vulkan", "av1_vulkan",
                "hevc_10bit_vulkan", "av1_10bit_vulkan", "av1_hdr_vulkan"
            ],
            hints=[
                "Automatic", "Max Compatibility", "H.265 (Efficiency)", "AV1 (Compression)", "Open WebM", "Open WebM High",
                "HEVC + HDR", "AV1 + HDR", "HEVC 10-bit", "AV1 10-bit",
                "Experimental Vulkan H.264", "Vulkan HEVC", "Vulkan AV1",
                "Vulkan HEVC 10-bit", "Vulkan AV1 10-bit", "Vulkan AV1 HDR"
            ],
            group="Format",
            extended_help="**Video Codec** (`-k`)\n\nVulkan codecs are experimental and depend on GPU and driver support. They can avoid CUDA downclocking on affected NVIDIA drivers."
        ),
        ConfigItem(
            label="Quality",
            key="quality",
            scope="DEFAULT",
            type_="string",
            default=DEFAULTS["quality"],
            options=["ultra", "very_high", "high", "medium", "40000", "80000"],
            group="Format",
            extended_help="**Quality / Bitrate** (`-q`)\n\nUse a text quality preset or a positive numeric bitrate in kbps. Numeric values automatically select CBR when recording starts. CBR requires a numeric value."
        ),
        ConfigItem(
            label="Bitrate",
            key="bitrate_mode",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["bitrate_mode"],
            options=["auto", "qp", "vbr", "cbr"],
            group="Format",
            extended_help="**Bitrate Mode** (`-bm`)\n\n`cbr` (Constant Bitrate) is heavily recommended when using the Replay Buffer to strictly govern RAM usage."
        ),
        ConfigItem(
            label="Timing",
            key="frame_mode",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["frame_mode"],
            options=["vfr", "cfr", "content"],
            group="Format",
            extended_help="**Frame Rate Mode** (`-fm`)\n\n`content` follows captured updates where supported (including portal capture). The recorder ignores this setting for direct Wayland monitor capture."
        ),
        ConfigItem(
            label="Range",
            key="color_range",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["color_range"],
            options=["limited", "full"],
            group="Format",
            extended_help="**Color Range** (`-cr`)\n\nChoose limited or full signal range to match your playback workflow. Full range does not increase color depth; mismatched interpretation can change blacks and whites."
        ),
        ConfigItem(
            label="Container",
            key="container",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["container"],
            options=["mp4", "mkv", "flv", "webm"],
            group="Output",
            extended_help="**Container Format** (`-c`)\n\nMP4 offers broad compatibility; MKV can be easier to recover after interrupted recording. WebM needs GPU encoding with VP8, VP9, or AV1; auto selects an available encoder. FLV uses H.264/AAC."
        ),
        ConfigItem(
            label="Directory",
            key="output_dir",
            scope="DEFAULT",
            type_="string",
            default=DEFAULTS["output_dir"],
            group="Output",
            extended_help="**Output Directory** (`-o`)\n\nThe absolute destination folder. The backend shell wrapper automatically enforces tilde (`~`) expansion."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: AUDIO (DYNAMIC ROUTING)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Output",
            key="audio_output",
            scope="DEFAULT",
            type_="picker",
            default=DEFAULTS["audio_output"],
            options=OUT_OPTS,
            hints=OUT_HINTS,
            group="Routing",
            extended_help="**Desktop Audio**\n\nSelect the output device to capture desktop audio. 'Default Desktop Audio' automatically tracks the system-wide fallback sink."
        ),
        ConfigItem(
            label="Input",
            key="audio_input",
            scope="DEFAULT",
            type_="picker",
            default=DEFAULTS["audio_input"],
            options=IN_OPTS,
            hints=IN_HINTS,
            group="Routing",
            extended_help="**Microphone Audio**\n\nSelect the input device to capture microphone audio. 'Default Microphone' automatically tracks the system-wide fallback source."
        ),
        ConfigItem(
            label="Codec",
            key="audio_codec",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["audio_codec"],
            options=["opus", "aac"],
            group="Encoding",
            extended_help="**Audio Codec** (`-ac`)\n\nOpus is the recorder default for MP4/MKV and works in WebM. AAC offers broad MP4 playback compatibility. FLAC is disabled in the installed recorder baseline."
        ),
        ConfigItem(
            label="Kbps",
            key="audio_bitrate",
            scope="DEFAULT",
            type_="int",
            default=DEFAULTS["audio_bitrate"],
            min_val=0,
            max_val=512,
            step=32,
            group="Encoding",
            extended_help="**Audio Bitrate** (`-ab`)\n\nBitrate in kbps. Use `0` to allow the encoder to select the optimal automatic bitrate."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: REPLAY (HYBRID FOLDER)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Duration",
            key="replay_buffer",
            scope="DEFAULT",
            type_="int",
            default=DEFAULTS["replay_buffer"],
            min_val=0,
            max_val=86400,
            step=10,
            is_parent=True,
            expanded=True,
            group="Buffer",
            extended_help="**Replay Buffer Size** (`-r`)\n\nRolling buffer duration: use 2–86400 seconds, or `0` to disable replay. Starting a recording with replay enabled starts the rolling buffer; save a clip with the replay action."
        ),
        ConfigItem(
            label="Storage",
            key="replay_storage",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["replay_storage"],
            options=["ram", "disk"],
            parent_ref="replay_buffer",
            extended_help="**Storage Medium** (`-replay-storage`)\n\nRAM stores the rolling buffer in memory. Disk reduces RAM usage by continuously writing the buffer to storage."
        ),
        ConfigItem(
            label="Restart",
            key="restart_replay",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["restart_replay"],
            options=["yes", "no"],
            parent_ref="replay_buffer",
            extended_help="**Restart On Save** (`-restart-replay-on-save`)\n\nClear the rolling buffer after saving the whole replay buffer."
        ),
        ConfigItem(
            label="Folders",
            key="date_folders",
            scope="DEFAULT",
            type_="cycle",
            default=DEFAULTS["date_folders"],
            options=["yes", "no"],
            parent_ref="replay_buffer",
            extended_help="**Organize By Date** (`-df`)\n\nForces saved replays into dynamically generated date-based subdirectories."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: PROFILES
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Nvidia",
            key="preset_vulkan",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Overrides",
            preset_payload={
                "encoder": "gpu",
                "codec": "hevc_vulkan",
                "quality": "very_high",
                "bitrate_mode": "auto",
                "frame_mode": "vfr"
            },
            extended_help="**Vulkan Override**\n\nInstantly configures the pipeline to use the experimental Vulkan HEVC codec, which may avoid CUDA downclocking on affected NVIDIA drivers. Requires Vulkan video support."
        ),
        ConfigItem(
            label="Replay",
            key="preset_replay_safe",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Overrides",
            preset_payload={
                "quality": "40000",
                "bitrate_mode": "cbr",
                "replay_buffer": 60,
                "replay_storage": "ram"
            },
            extended_help="**Stable Replay Preset**\n\nConfigures the application for predictable Instant Replay usage by forcing Constant Bitrate (CBR) to strictly manage RAM consumption."
        ),
    ]
}
