#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: GPU SCREEN RECORDER SCHEMA (NATIVE INI)
Targets: Pure Wayland | Arch Linux | GPU Screen Recorder 5.13+
===============================================================================
"""

import subprocess
from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "ini"
TARGET_FILE = "~/.config/dusky_recorder/config.conf"
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
def fetch_audio_devices():
    """Polls gpu-screen-recorder for active hardware to populate TUI Pickers."""
    out_opts = ["none", "default_output"]
    out_hints = ["No Output", "Default Desktop Audio"]
    in_opts = ["none", "default_input"]
    in_hints = ["No Input", "Default Microphone"]
    
    try:
        # Run command with strict timeout to prevent UI blocking if GSR hangs
        res = subprocess.run(
            ["gpu-screen-recorder", "--list-audio-devices"], 
            capture_output=True, text=True, timeout=1.5, check=False
        )
        
        for line in res.stdout.strip().split('\n'):
            if '|' not in line: 
                continue
                
            dev_id, dev_desc = line.split('|', 1)
            
            if not dev_id or dev_id in ('default_output', 'default_input'): 
                continue
            if not dev_desc:
                dev_desc = dev_id
                
            if 'output' in dev_id:
                out_opts.append(dev_id)
                out_hints.append(dev_desc)
            elif 'input' in dev_id:
                in_opts.append(dev_id)
                in_hints.append(dev_desc)
    except Exception:
        pass # Gracefully fall back to 'none' and 'default' if parsing fails
        
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
            default="region",
            options=["screen", "portal", "region"],
            group="Target",
            extended_help="**Capture Target** (`-w`)\n\n`screen` captures the primary Wayland output. `portal` uses the native Wayland picker. `region` utilizes Slurp to draw a custom area."
        ),
        ConfigItem(
            label="Region",
            key="region",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Target",
            extended_help="**Region String**\n\nSpecify exact coordinates (e.g., `1280x720+100+50`). If left blank, Slurp will automatically execute so you can draw the capture zone."
        ),
        ConfigItem(
            label="FPS",
            key="fps",
            scope="DEFAULT",
            type_="int",
            default=60,
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
            default="yes",
            options=["yes", "no"],
            group="Playback",
            extended_help="**Show Cursor** (`-cursor`)\n\nToggle whether your mouse cursor is visible in the final output file."
        ),
        ConfigItem(
            label="Indicator",
            key="show_indicator",
            scope="DEFAULT",
            type_="cycle",
            default="yes",
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
            default="gpu",
            options=["gpu", "cpu"],
            group="Hardware",
            extended_help="**Encoder Device** (`-encoder`)\n\n`gpu` strictly forces NVENC/VAAPI/AMF for zero-overhead capture. `cpu` falls back to software encoding."
        ),
        ConfigItem(
            label="Tune",
            key="tune",
            scope="DEFAULT",
            type_="cycle",
            default="performance",
            options=["performance", "quality"],
            group="Hardware",
            extended_help="**Encoder Tuning** (`-tune`)\n\nNVIDIA ONLY. Adjusts the silicon bias towards raw encoding speed or visual fidelity."
        ),
        ConfigItem(
            label="Power",
            key="low_power",
            scope="DEFAULT",
            type_="cycle",
            default="no",
            options=["yes", "no"],
            group="Hardware",
            extended_help="**Low Power Mode** (`-low-power`)\n\nAMD ONLY. Allows the GPU to enter a lower power state during recording. Best used alongside the 'content' Timing mode."
        ),
        ConfigItem(
            label="Codec",
            key="codec",
            scope="DEFAULT",
            type_="picker",
            default="auto",
            options=[
                "auto", "h264", "hevc", "av1", "vp8", "vp9",
                "hevc_hdr", "av1_hdr", "hevc_10bit", "av1_10bit",
                "h264_vulkan", "hevc_vulkan", "av1_vulkan", 
                "hevc_10bit_vulkan", "av1_10bit_vulkan", "av1_hdr_vulkan"
            ],
            hints=[
                "Automatic", "Max Compatibility", "H.265 (Efficiency)", "AV1 (Compression)", "Open WebM", "Open WebM High",
                "HEVC + HDR", "AV1 + HDR", "HEVC 10-bit", "AV1 10-bit",
                "Fixes Nvidia downclock", "Vulkan HEVC", "Vulkan AV1",
                "Vulkan HEVC 10-bit", "Vulkan AV1 10-bit", "Vulkan AV1 HDR"
            ],
            group="Format",
            extended_help="**Video Codec** (`-k`)\n\nVulkan codecs are highly recommended for NVIDIA Wayland users to prevent the 'cuda p2 state' GPU downclock bug."
        ),
ConfigItem(
            label="Quality",
            key="quality",
            scope="DEFAULT",
            type_="string",
            default="very_high",
            options=["ultra", "very_high", "high", "medium", "low", "40000", "80000"],
            group="Format",
            warning_msg="CRITICAL: If you type a custom numeric bitrate here, you MUST change the Bitrate Mode below to 'cbr' or the recorder will crash.",
            extended_help="**Quality / Bitrate** (`-q`)\n\nIf Bitrate is 'auto/vbr', select a text preset (e.g., 'very_high'). If Bitrate is 'cbr', type a raw numeric value in kbps (e.g., '40000')."
        ),
        ConfigItem(
            label="Bitrate",
            key="bitrate_mode",
            scope="DEFAULT",
            type_="cycle",
            default="auto",
            options=["auto", "qp", "vbr", "cbr"],
            group="Format",
            extended_help="**Bitrate Mode** (`-bm`)\n\n`cbr` (Constant Bitrate) is heavily recommended when using the Replay Buffer to strictly govern RAM usage."
        ),
        ConfigItem(
            label="Timing",
            key="frame_mode",
            scope="DEFAULT",
            type_="cycle",
            default="vfr",
            options=["vfr", "cfr", "content"],
            group="Format",
            extended_help="**Frame Rate Mode** (`-fm`)\n\n`content` syncs the video exactly to captured screen updates to minimize idle resource usage."
        ),
        ConfigItem(
            label="Range",
            key="color_range",
            scope="DEFAULT",
            type_="cycle",
            default="limited",
            options=["limited", "full"],
            group="Format",
            extended_help="**Color Range** (`-cr`)\n\n`full` provides deeper colors but may cause washed-out blacks on incompatible web players. `limited` is universally safe."
        ),
        ConfigItem(
            label="Container",
            key="container",
            scope="DEFAULT",
            type_="cycle",
            default="mp4",
            options=["mp4", "mkv", "flv", "webm"],
            group="Output",
            extended_help="**Container Format** (`-c`)\n\n`mkv` is fundamentally safer against system crashes and file corruption. `mp4` possesses broader web compatibility."
        ),
        ConfigItem(
            label="Directory",
            key="output_dir",
            scope="DEFAULT",
            type_="string",
            default="~/Videos",
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
            default="default_output",
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
            default="none",
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
            default="opus",
            options=["opus", "aac", "flac"],
            group="Encoding",
            extended_help="**Audio Codec** (`-ac`)\n\n`opus` is the modern default and vastly superior codec for MP4/MKV containers."
        ),
        ConfigItem(
            label="Kbps",
            key="audio_bitrate",
            scope="DEFAULT",
            type_="int",
            default=128,
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
            default=0,
            min_val=0,
            max_val=86400,
            step=10,
            is_parent=True,
            expanded=True,
            group="Buffer",
            extended_help="**Replay Buffer Size** (`-r`)\n\nRolling buffer duration in seconds. Set to `0` to completely disable the Instant Replay daemon."
        ),
        ConfigItem(
            label="Storage",
            key="replay_storage",
            scope="DEFAULT",
            type_="cycle",
            default="ram",
            options=["ram", "disk"],
            parent_ref="replay_buffer",
            extended_help="**Storage Medium** (`-replay-storage`)\n\nRAM is significantly faster but eats system memory. Disk saves RAM but continuously thrashes your SSD lifespan."
        ),
        ConfigItem(
            label="Restart",
            key="restart_replay",
            scope="DEFAULT",
            type_="cycle",
            default="no",
            options=["yes", "no"],
            parent_ref="replay_buffer",
            extended_help="**Restart On Save** (`-restart-replay-on-save`)\n\nIf enabled, completely clears the rolling buffer immediately after a clip is dumped to storage."
        ),
        ConfigItem(
            label="Folders",
            key="date_folders",
            scope="DEFAULT",
            type_="cycle",
            default="no",
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
            extended_help="**Vulkan Override**\n\nInstantly configures the pipeline to use the experimental Vulkan HEVC codec, bypassing the notorious Nvidia CUDA downclock bug."
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
        ConfigItem(
            label="Reset",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System",
            confirm_message="Are you absolutely sure you want to purge all configuration data and factory reset?",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Nuclear Factory Reset**\n\nReverts every single configuration key across all tabs back to its programmed default state."
        ),
    ]
}
