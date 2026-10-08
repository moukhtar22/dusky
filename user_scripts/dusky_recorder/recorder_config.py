"""Shared recorder defaults and first-run configuration creation."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

CONFIG_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "dusky/settings/dusky_recorder/config.conf"
)

DEFAULTS = {
    "window": "screen",
    "region": "",
    "fps": 60,
    "cursor": "yes",
    "show_indicator": "yes",
    "encoder": "gpu",
    "codec": "auto",
    "quality": "very_high",
    "bitrate_mode": "auto",
    "frame_mode": "vfr",
    "color_range": "limited",
    "tune": "performance",
    "low_power": "no",
    "container": "mp4",
    "output_dir": "~/Videos",
    "audio_output": "default_output",
    "audio_input": "none",
    "audio_codec": "aac",
    "audio_bitrate": 128,
    "replay_buffer": 0,
    "replay_storage": "ram",
    "restart_replay": "no",
    "date_folders": "no",
}


def ensure_config() -> None:
    """Publish a complete default config only when no user config exists."""
    if CONFIG_FILE.exists():
        return
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # A hard link publishes the complete file atomically without overwriting
    # settings created by another interface during simultaneous first launches.
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=CONFIG_FILE.parent, prefix=".config."
    ) as temporary:
        temporary.write("# Dusky Recorder settings — changes here are local to this user.\n")
        temporary.writelines(f"{key}={value}\n" for key, value in DEFAULTS.items())
        temporary.flush()
        try:
            os.link(temporary.name, CONFIG_FILE)
        except FileExistsError:
            pass


if __name__ == "__main__":
    ensure_config()
