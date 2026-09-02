# Engine: `kokoro`

- **Class:** `KokoroEngine` (inherits from `TomlEngine`) — `engines/kokoro.py`
- **Engine types:** `kokoro` (alias: `dusky_kokoro`)
- **Default target:** `~/.config/dusky-kokoro/config.toml` (override with `TARGET_FILE` or `DUSKY_CONFIG`)

## Target format

Standard TOML v1.0 configuration for the Dusky Kokoro neural speech synthesis daemon:

```toml
[voice]
spec = "af_heart:0.40,af_bella:0.60"
blend = true
voice_1 = "af_heart"
weight_1 = 0.40
voice_2 = "af_bella"
weight_2 = 0.60
voice_3 = "none"
weight_3 = 0.00
speed = 1.00
lang = "auto"

[playback]
mpv_speed = 1.00
volume = 100
window = true

[engine]
provider = "cuda"
precision = "auto"
model_idle_timeout_s = 30.0

[daemon]
process_idle_timeout_s = 30.0
exit_when_idle = true
```

## Special Kokoro Features

1. **Multi-Speaker Voice Blending & Normalization:**
   - When modifying individual voice controls (`blend`, `voice_1`, `weight_1`, `voice_2`, `weight_2`, `voice_3`, `weight_3`), the engine automatically calculates mathematically normalized weights ($w_1 + w_2 + w_3 = 1.0$) and updates `voice.spec` atomically.
2. **Reverse State Decomposition:**
   - When loading an existing `config.toml`, if `voice_1` is unset, the engine parses `spec` (e.g. `"af_heart:0.4,af_bella:0.6"`) and populates `voice.blend`, `voice.voice_1`, `voice.weight_1`, etc. into the active cache.
3. **Live Socket IPC Hot-Reload:**
   - On every mutation, the engine sends a live reload command to `$XDG_RUNTIME_DIR/dusky-kokoro/control.sock` (with fallback to `trigger.sh --reload`).
4. **Virgin File Auto-Creation:**
   - If `config.toml` is missing, creates it with full comments and defaults, and ensures `~/contained_apps/uv/dusky_kokoro/config.toml` is symlinked.
5. **Dynamic Telemetry:**
   - Injects `daemon.status` ("RUNNING", "STANDBY (Socket)", "STOPPED") and `daemon.gpu_power_state` ("D3cold", "D0").

## Example items

```python
# Voice Selection & Blending
ConfigItem(label="Blend Voices", key="blend", scope="voice", type_="bool", default=True, group="Voices"),
ConfigItem(label="Primary Voice (V1)", key="voice_1", scope="voice", type_="cycle", default="af_heart", options=["af_heart", "af_bella", "af_nicole", ...], group="Voices"),
ConfigItem(label="Primary Weight (W1)", key="weight_1", scope="voice", type_="float", default=0.40, min_val=0.05, max_val=1.00, step=0.05, group="Voices"),

# Independent Dual Speed Controls
ConfigItem(label="Speech Gen Speed", key="speed", scope="voice", type_="float", default=1.00, min_val=0.50, max_val=2.00, step=0.05, group="Speed"),
ConfigItem(label="MPV Playback Speed", key="mpv_speed", scope="playback", type_="float", default=1.00, min_val=0.50, max_val=2.00, step=0.05, group="Speed"),
```
