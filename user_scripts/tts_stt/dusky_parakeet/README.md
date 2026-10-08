# Dusky Parakeet

Local speech transcription for Arch Linux, Wayland and Hyprland.

## Install

Keep the installer, runtime scripts, service file and this README together,
then run:

```bash
./dusky_installer.py
# Unattended, with cached dependencies and models:
./dusky_installer.py --yes --offline
```

The trigger source, `dusky_trigger.py`, is shared by everyone. The installer
copies it into the application and creates a small launcher using the installed
Python environment. Ship the trigger with the installer; it is required input.

Requirements: Linux 7.3+, GIL-enabled Python 3.14.7+, systemd 262+, `uv`,
PipeWire and the system packages checked by the installer. NVIDIA acceleration
requires driver 580+ and Turing or newer (compute capability 7.5+). Unsupported
older NVIDIA cards use CPU inference with automatic selection. AMD installations
use the packaged CPU runtime; installing system ROCm alone does not enable GPU
inference inside the isolated environment.

Tested packages: ONNX Runtime 1.30.0, NumPy 2.5.3, onnx-asr 0.12.0,
sounddevice 0.5.6 and Silero VAD 6.2.1. CUDA runtime 13.0 is retained for driver
580 compatibility. Versions are qualified together rather than upgraded blindly.

Offline installation requires system packages already installed, the necessary
wheels **and resolver metadata** in the `uv` cache, local Parakeet model files,
and Silero in the existing installation or
`${XDG_CACHE_HOME:-~/.cache}/dusky-stt/silero-v6.2.1.onnx`.
Use `--model-dir DIR` for an existing model. The default model is English
Parakeet v2, quantized to int8; `--model nemo-parakeet-tdt-0.6b-v3` selects v3.
Missing offline dependencies are reported rather than downloaded.

## Use

```bash
dusky_trigger                         # Start/stop live dictation
dusky_trigger --start --push           # Capture; type after stopping
dusky_trigger --stop
dusky_trigger --file ~/audio.m4a --wait # Print this job's transcript
dusky_trigger --status
dusky_trigger --unload                 # Release worker RAM/VRAM
dusky_trigger --help
```

Hyprland binding:

```ini
bind = SUPER, S, exec, dusky_trigger
```

File transcripts are saved and copied to the clipboard; they are never typed
into the focused application. Microphone output follows `--output-mode`.
Transcripts and per-file job results live under the configured `state_dir`.
A failed or cancelled file job returns failure, preserving completed text when
available. A successful silent file produces an empty transcript.

## Performance and hardware

The capture daemon uses CPU ONNX Runtime exclusively. A separate worker owns
ASR and CUDA, allowing GPU memory to be released by terminating that process.

File decoding streams bounded chunks, normally at most 20 seconds, preferring
nearby quiet passages to avoid cutting words. Continuous speech can still cross
a boundary; model errors remain possible. Microphone capture and final inference
run separately. Overflows and failed phrases mark the result partial.

CPU inference uses up to eight available logical CPUs by default, including CPU
operators in CUDA models. This respects the process CPU allocation and avoids
assuming a particular P/E core layout. `--cpu-threads N` allows hardware-specific
tuning; `0` restores automatic selection. Requests remain sequential. ORT background telemetry is disabled before initialization.

`--gpu-mem-limit-mb` limits each CUDA provider arena, **not total process VRAM**.
The default reserves headroom based on detected VRAM. Int8 is the small-memory
choice; fp32 is rejected on GPUs below 3 GiB. Physical 2 GiB devices and AMD
acceleration have not been tested. Use `--hardware cpu` where CUDA is unsupported
or memory is insufficient. Parakeet v3, fp16/fp32 and hours-long natural recordings
also remain unqualified; the tested recognition configuration is v2/int8.

## Service and maintenance

- **Enabled unit:** worker preloads and stays resident for lower startup latency.
- **Disabled unit:** the trigger starts the service on demand; worker and daemon
  stop after a job. `--unload` releases the worker immediately in either mode.

Reinstalling preserves existing settings unless a corresponding option is given.
Installation stages and verifies the new application before replacement; a
post-deployment failure restores the previous application and entry points.
`--no-systemd` installs the unit without reloading, enabling or starting it.

```bash
dusky_verify         # Installed runtime; idle on-demand mode is valid
dusky_verify live    # Record three seconds and check finalization
dusky_verify d3      # Unload worker and inspect the configured GPU's power state
dusky_trigger --logs
./dusky_installer.py --uninstall
```

Installer output is concise; full details are in the displayed `install.log`.
`--verbose` prints detailed command output. `DUSKY_APP_DIR` overrides the
application location, and `DUSKY_CONFIG` overrides the trigger's configuration.
XDG data/config/cache/state paths are discovered at installation. Uninstalling
retains models and transcripts. The legacy `keep_audio` setting is retained on
upgrade but currently does not save microphone audio.

## Development checks

`tests/` contains regression, long-file and virtual-microphone checks. These
files are for development and are not deployed by the installer.

```bash
~/.local/lib/dusky-stt/.venv-main/bin/python -m unittest discover -s tests -v
```

Run from this source directory. The stress scripts document their arguments
at the top of each file.
