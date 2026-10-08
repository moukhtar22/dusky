# Dusky Kokoro final pass — 2026-10-01

## Installer correction — 5.1.2

A fresh user checkout failed because `.gitignore` excluded the required
`trigger.sh`. The installer copies this generic script unchanged; it does not
create a personalized version. User paths come from environment variables and
`install-path` at runtime. Removed the Kokoro ignore entry so the source hook
ships with the installer. The installer still requires the complete source
bundle, including `dusky_main.py` and `trigger.sh`.

Setup now has one short backend menu with a numeric recommended default.
Tested models are selected automatically; `--models` remains the explicit
override. Installer and trigger help are grouped, 29 and 33 lines respectively.
Normal installation prints progress steps and a short completion message;
full diagnostics are retained in `<install dir>/install.log`. `--verbose`
shows them live. Failed steps stop immediately and display the cause and log
location. The runtime and inference settings were not changed in this update.

Verification: reproduced the original missing-hook failure; fresh isolated CPU
and NVIDIA installations passed using cached dependencies and validated models,
including synthesis/speed-control checks. Paths containing spaces passed.
The matched lean CPU bundle printed 85 lines before versus 16 after (about 81%
less terminal output). All menu selections, Enter and invalid input were
exercised in a pseudo-terminal. Forced failures in quiet and verbose modes
stopped before subsequent commands. **50 regression tests**, Bash syntax and
ShellCheck passed. Fixtures/logs: `/tmp/kokoro-installer-fix` (temporary).

Publish `trigger.sh` together with the installer; users missing it must update
the complete directory before retrying. No network bootstrap downloads or
machine-specific trigger generation were introduced.

---

## Runtime result: 5.1.1

The source and installed runtime are now 5.1.1. CPU and CUDA long runs, fresh
cached CPU installation, repeated offline installation, playback failure
recovery, socket activation and custom paths passed. Existing user configuration
was preserved. **45 regression tests pass**, including installer and TUI fixtures.
Python compilation, Bash syntax and ShellCheck pass. Results below supersede
CPU model defaults and test counts in the historical September 30 report.

### Changes in this pass

- Automatic CPU selection now prefers the tested FP16 GPU export, which also
  runs on CPU, followed by f32 and INT8. CPU installs cache FP16 GPU plus INT8;
  explicit precision remains authoritative. Intel installations prefer f32.
- Stop and newer interrupt requests invalidate older text preparation. A full
  enqueue queue rejects before expensive preparation. Worker startup timeout
  covers configuration transfer, and pipe/JSON/timeout failures reap the worker.
  A replacement worker waits for interrupted workers to finish exiting.
- Archive finalization failure no longer leaves waiting clients without a final
  event. Invalid WAVs are removed when possible; narration continues after
  archive failures. Worker telemetry reports the actual fallback model.
- Offline installation recovers a valid complete `.part` even when the target
  file is invalid, and preserves invalid files when recovery is impossible.
- Both TUIs honor custom trigger paths; the Bash TUI also reads the saved install
  location without requiring a trailing newline. Status uses the actual socket's
  PID directory. GPU telemetry discovers DRM cards and filters the requested
  provider's vendor instead of assuming card0/card1.

### Measured CPU choice and P/E cores

This host has six P cores and eight E cores. Isolated eight-thread short-phrase
runs took **4.49 s with INT8 versus 0.91–0.92 s with FP16 GPU**: approximately
4.9× faster for the same input text (audio differs slightly: 2.219 versus 2.304 s).
This is an observed CPU inference gain on this host, not a hardware-wide promise.
Longer FP16 CPU phrases of 211/320 characters took about 4.00/5.57 s for
12.95/17.71 s of audio. The latest v1.1 f32 export was also tested, but took
about 4.54/6.33 s. Sampled peak synthesis-process RSS was approximately
710 MiB for FP16 GPU versus 882 MiB for f32; INT8 remains a smaller download.

For the faster model, a separate isolated scheduling comparison gave warm
short-phrase timings of 1.106–1.107 s at four threads, 0.913–0.920 s at eight,
1.78–1.94 s at fourteen, and 0.913–0.921 s on the six P cores alone.
P-core affinity provided no meaningful advantage over eight threads with normal
OS scheduling. Disabling spinning on P cores took 0.955–1.063 s. Keep normal
scheduling, the automatic eight-thread cap and explicit overrides. Automatic
thread counts honor the process's available CPUs, including a single-CPU limit.
[ONNX Runtime threading documentation](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)

### Stress and recovery results

| Final workload | Result |
| --- | --- |
| CUDA: 251 segments | 1403.82 s of archived audio in 149.13 s; warm first audio 719 ms |
| CPU: 36 segments | 196.87 s of archived audio in 73.97 s; warm first audio 2104 ms |
| Sampled daemon RSS | 52.2 MiB CUDA; 52.0 MiB CPU; synthesis-worker memory is additional |
| Maximum observed status latency during narration | 73.4 ms CUDA; 91.8 ms CPU |
| Recovery | Each profile passed 30 stop/unload cycles, reload, two 4-million-character preparation races, final synthesis and shutdown; no observed orphan workers |
| Archive integrity | WAV sample counts agree with reported durations |
| Tight CUDA arena budgets | 256/512 MiB completed with CPU fallback; 1024 MiB stayed on CUDA |
| Sampled process VRAM for those budgets | 388/622/790 MiB, respectively; not a total VRAM cap or a measured maximum for all documents |
| Accelerator required at 256 MiB | Failure reported instead of silent CPU fallback |
| Actual mpv failure | Terminal error delivered; next job completed after configuration reload |
| Invalid archive directory | Playback completed with archive=null |
| Temporary systemd units | Verification, cold activation, headless synthesis, idle exit and reactivation passed with paths containing spaces; units removed afterward |

Headless playback used mpv's untimed null output. Throughput runs are completion
and responsiveness checks, not matched before/after CUDA speed measurements.
Concurrent workloads occurred during the CUDA run; no GPU speedup is claimed.
CPU model/scheduling comparisons above were run separately without other audit
inference workloads. First-audio measurements above followed a loaded engine.

A fresh CPU-only environment with cached dependencies and models passed its
installation self-test (RTF around 0.31 and effective speed control). Installed
CUDA self-test also passed. A request through the installed trigger and actual
Wayland/audio device completed 3.45 s of speech with a valid archive and final
event, followed by automatic service idle exit. A one-CPU affinity fixture also
completed synthesis and speed-control verification. In the CPU-only environment,
requesting CUDA completed through CPU fallback, while requiring acceleration
failed correctly. Physical 2 GiB/older GPUs, AMD, Intel acceleration,
other CPU architectures, and perceptual language correctness remain unqualified.
The historical language limitations below still apply. Final ISO package/build
qualification is still required; no per-machine P/E affinity policy was added.

### Reproduce this final pass

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile dusky_main.py tui_kokoro.py tests/*.py
bash -n kokoro_installer.sh trigger.sh tui/kokoro_tui.sh
shellcheck kokoro_installer.sh trigger.sh tui/kokoro_tui.sh
# Resolve the saved install location, or export DUSKY_HOME for another install:
IFS= read -r install_dir < "${XDG_CONFIG_HOME:-$HOME/.config}/dusky-kokoro/install-path" || true
install_dir=${DUSKY_HOME:-$install_dir}
"$install_dir/.venv/bin/python" tests/stress_kokoro.py --models-dir "$install_dir/models" --provider cuda
"$install_dir/.venv/bin/python" tests/stress_kokoro.py --models-dir "$install_dir/models" --provider cpu --paragraphs 35
./trigger.sh --synth
```

Final-pass JSON, WAVs, benchmarks and logs: `/tmp/dusky-kokoro-final` (temporary).
The regression/stress files must be included in the ISO source/package.
The installer correction above resolves the previously ignored `trigger.sh`.

---

# Historical audit — 2026-09-30

## Result and scope

Updated the source and installed runtime to Dusky Kokoro 5.1.0. Installation,
CUDA synthesis, CPU synthesis, playback, archiving, socket activation and the
regression suite passed on this machine. These results establish the tested
behavior; they do not establish flawless operation on every GPU or language.
This directory implements **text to speech**. The separate Parakeet speech to
text setup was outside this audit.

Read all eight supplied files in full, inspected the installed configuration,
units, runtime and direct TUI/Hyprland call sites, and compared upstream
Kokoro/ONNX Runtime examples and documentation. No security audit was performed.

## Environment and installed outcome

- Python 3.14.7, Bash 5.3.20, systemd 262, Hyprland 0.56.2, uv 0.12.20,
  mpv 0.41.0 and Poppler 26.08.0.
- RTX 3050 Ti Laptop GPU, 4 GiB; NVIDIA driver 615.71.09. An Intel integrated
  GPU is also present. Testing used a Wayland/Hyprland session.
- Development kernel: **7.3.0-rc5-dusky-battery**, rather than a final 7.3 release.
  Final ISO package versions/build features still require release validation.
- Installed ONNX Runtime GPU 1.30.0, NumPy 2.5.3 and kokoro-onnx 0.6.1.
  Exactly one ONNX Runtime distribution is installed in the isolated environment.
- Existing voice, playback and timeout preferences were preserved. The configured
  arena budget remains 2048 MiB. A fresh template is in `config.toml.new`.
- The user socket is enabled; the daemon starts on demand and exits when idle.

## Fixes

| Area | Finding | Change |
| --- | --- | --- |
| Long text | Repeatedly scanning and slicing the remaining paragraph was quadratic; unmatched Markdown brackets also caused excessive work. | Scan with a cursor; restrict link patterns to their actual delimiters; scan Markdown fences linearly; prepare text outside the control loop. |
| Audio buffering | `prefetch_segments = 4` actually allowed 512 queued segments. | Honor the configured count. Audio buffering stays bounded by segment sizes and queue capacity; text storage still scales with document length. |
| Worker lifecycle | Reload reacquired the same lock; interrupt handling could leave unreaped processes; worker configuration could be stale. | Serialize lifecycle and I/O, transfer the complete config and paths, bound startup, and terminate/reap interrupted workers. |
| Runtime failures | Worker errors lost their cause; provider selection could report CUDA despite fallback; invalid waveforms reached silence trimming. | Frame state/errors with PCM, check finite/nonempty model output, report the active backend and retry eligible accelerator inference failures once on CPU. |
| Low memory | The arena setting was advertised as a total VRAM cap; global GPU-zero utilization could trigger repeated reloads. | Clarify the arena budget, disable large cuDNN workspaces by default, constrain TensorRT workspace, and use actual inference failure for CPU fallback. |
| Installation | Stale model sizes, inappropriate Python/backend wheel selection, automatic package changes, weak download validation and successful completion after failed self-tests. | Correct manifests, validate model hashes/voice tensors, recover complete partial downloads, remove invalid partials, require explicit system dependency installation and fail failed self-tests. |
| Offline/repeat installs | Ordinary reruns upgraded dependencies and had no explicit offline contract. | Reuse the lock by default; add `--upgrade`, `--offline` and `--ort-wheel`. Offline installation requires cached dependencies/models and preinstalled system tools. |
| Documents | PDF extraction referenced an unimported module; EPUB chapters followed ZIP order. | Import subprocess and follow the EPUB package spine; report unreadable referenced chapter files. |
| Archives/playback | Same-second archives could collide; archive writes blocked control handling; mpv failures could be reported as completion. | Unique archive names, writes outside the event loop, playback continuation after archive write failure, and check player outcome. |
| CPU threading | Automatic threading used every available CPU despite poorer measured latency. | Cap automatic inference threads at eight; retain explicit overrides. |
| Configuration/TUIs | Invalid types/nonfinite numbers, zero-weight blends, omitted voice three, missing keys written into the wrong TOML section, outdated provider names and stale PID location. | Validate inputs, normalize positive weights, preserve voice three independently, insert keys in the proper section, use modern provider names and runtime PID paths. |
| Paths | Custom install locations were forgotten; quoted systemd working directories and custom control paths were inconsistent. | Persist the install path, quote command arguments correctly, honor custom config/socket paths and validate generated units with spaces. |
| Portability | X11 paths, a fixed zram mount and forced integrated-GPU environment assumptions. | Use the requested Wayland scope and normal XDG archive paths; let the active graphics stack select its rendering device. |
| Diagnostics | Synthesis cleanup crashed when no executor existed; offline benchmarks retained every PCM chunk. | Correct cleanup, stream WAV output, and propagate diagnostic failures. |

## Measured results

Measurements are from this laptop and its current power state, not universal
performance claims. RTF = synthesis time / audio duration; lower is faster.

| Check | Before | After | Interpretation |
| --- | --- | --- | --- |
| Split a 500,000-character paragraph | 55.368 s | 0.284 s | About 195× faster in this focused benchmark. |
| Normalize 100,000 unmatched `[` characters | Exceeded a 10 s timeout | 0.077 s | Pathological input now finishes promptly. |
| Normalize 75,000 characters of unmatched code fences | Not a matched timing | 0.0046 s | Linear fence handling, including unclosed blocks and longer closing fences. |
| Matched warm CUDA inference, same model/runtime/texts | RTF 0.081786 | RTF 0.081749 | Essentially unchanged; no meaningful inference speed gain claimed. |
| Shared regression cases | 8/21 pass | 21/21 pass | Thirteen failing cases fixed; final suite has 27 passing tests including six added cases. |
| Configured audio prefetch of four | 512 queued segments allowed | Four queued segments allowed | 128× lower queue capacity by code comparison; not a measured 128× reduction in total RSS. |

Long run: **251 segments, 1366.48 seconds of archived audio, 118.1 seconds of
job wall time**, with a 3.631-second cold first-audio delay. Sampled peak daemon
RSS was 52.0 MiB, peak synthesis-process VRAM was 428 MiB, and maximum observed
status request latency was 2 ms. Headless mpv used its untimed null audio output
for this throughput test; real-time playback naturally takes the audio duration.
The archived sample count was checked against the reported duration. This checks
pipeline completion, not a human transcription of every spoken word.

The actual installed service subsequently completed a real Wayland/audio-device
playback request: 3.62 seconds of audio, cold first audio after 2.473 seconds,
archived WAV, successful final event. Its offline self-test reported CUDA RTF
around 0.0813 and an effective generation speed control.

### Memory budgets and old GPUs

The 256/512/1024/2048 MiB arena tests all completed. The two smaller budgets
fell back to CPU; sampled process VRAM peaks were 380/630 MiB. The 1024 and
2048 MiB tests stayed on CUDA and peaked at 1080 MiB. A separate ONNX Runtime
1.30.0 check at 1024 MiB also stayed on CUDA with a 1080 MiB peak. These tests
cover several segment lengths, including near the configured maximum.

For a **supported 2 GiB NVIDIA GPU**, 1024 MiB is a reasonable starting arena
budget, with CPU fallback enabled and maximum cuDNN workspace disabled. This
is a recommendation based on the measured workload, not a physical 2 GiB card
qualification. Desktop/compositor allocations and other applications also need
space. The CUDA arena budget does **not** cap driver contexts, every library
allocation, or total process VRAM. [ONNX Runtime CUDA options](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)

Current CUDA 13 dropped Maxwell, Pascal and Volta support. Automatic installation
therefore selects CPU on detected NVIDIA cards below compute capability 7.5;
a larger arena cannot make those cards supported. CPU installations require
no NVIDIA runtime. [NVIDIA CUDA 13 release notes](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html)

CPU fallback completes the work, but real-time narration is not guaranteed:
the tested 92 MB INT8 model needed roughly 8.1–8.7 seconds for about 5.1 seconds
of audio at four threads. Automatic CPU threads are now limited to eight available CPUs (explicit settings
remain authoritative). On this 14-CPU system, the short-phrase eight-thread
runs took 3.53–3.59 seconds; the automatic 14-thread runs took 3.93–6.29 seconds.
Four threads took 3.61–3.63 seconds, two took 4.14–4.16, and one took 5.18–5.21.
This measured comparison motivated the cap; it is not a universal optimum.
CPU thread count and model choice still need measurements on each ISO hardware
class. The repeated offline CPU install self-test completed the same 9.297
seconds of audio in 12.959 seconds with the cap, versus 16.592 seconds with
the original uncapped automatic thread count (about 22% less synthesis time).

### Model and backend selection

- Default GPU export remains the tested `model-files-v1.0` FP16 GPU model
  (177,464,787 bytes). The newer v1.1 generic FP16 export produced **NaN audio**
  on the tested CUDA backend. It remains an explicit experimental selection,
  protected by waveform validation and fallback.
- Default CPU/fallback export remains v1.0 INT8 (92,361,271 bytes). In the same
  four-thread comparison, the newer v1.1 INT8 export took about 11.1–11.3 seconds
  versus 8.1–8.7 seconds for v1.0, with slightly different audio durations.
  The latest export was therefore not made the default.
- The newer v1.1 full precision model passed CUDA synthesis and speed-control
  verification (sample RTF 0.0978). It is larger and remains optional.
- Model hashes are recorded in the installer. v1.1 asset/voices hashes came from
  release metadata; the older default export hashes were calculated from the
  tested installed artifacts. Voice tensors were all validated as finite with
  shape `(510, 1, 256)`.
- CPU and CUDA installs were exercised. TensorRT, AMD MIGraphX, Intel OpenVINO
  and physical older/low-VRAM cards were **not hardware-qualified**.
- ROCmExecutionProvider was removed upstream from ONNX Runtime 1.23 onward.
  AMD now uses MIGraphX with an explicitly supplied matching Python/runtime build.
  [ROCm notice](https://onnxruntime.ai/docs/execution-providers/ROCm-ExecutionProvider.html),
  [MIGraphX documentation](https://onnxruntime.ai/docs/execution-providers/MIGraphX-ExecutionProvider.html)
- The examined PyPI OpenVINO runtime did not provide a Python 3.14 Linux wheel.
  Explicit Intel installs need a matching `--ort-wheel`; automatic selection
  uses CPU. Its provider settings now use documented `load_config` properties.
  [OpenVINO provider documentation](https://onnxruntime.ai/docs/execution-providers/OpenVINO-ExecutionProvider.html)

Upstream comparisons: [kokoro-onnx source/examples](https://github.com/thewh1teagle/kokoro-onnx),
[model-files-v1.0](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0),
[model-files-v1.1](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1),
[uv metadata overrides](https://docs.astral.sh/uv/concepts/resolution/).
The existing ONNX design provides fast measured CUDA inference without adding a
PyTorch runtime or another daemon layer; the audit retained that design.

## Verification coverage

- Python regression suite: 27 passing cases; Python compilation, Bash syntax
  checks and ShellCheck passed.
- Fresh CPU and NVIDIA installations; repeated offline CPU/NVIDIA installations
  with populated caches; installation paths containing spaces.
- Complete partial download recovery, corrupt complete partial replacement,
  invalid final download rejection/removal, and offline corruption rejection
  without deleting the existing artifact. Corruption tests used local download
  fixtures; fresh installs exercised actual package/model acquisition.
- Actual systemd cold activation, synthesis, idle exit and reactivation.
  Generated units passed `systemd-analyze --user verify`; generated custom
  install/config/socket paths with spaces passed activation, trigger routing
  and PID checks using temporary user units.
- Pause/resume, stop, unload during synthesis, reload followed by another job,
  malformed requests, ten stop/restart cycles, concurrent status requests and
  clean shutdown.
- Long text, CJK text without spaces, unbroken words, abbreviations, Markdown,
  token limits, voice weights, queue rejection and deduplication regression cases.
- Real PDF/Poppler and EPUB extraction through CLI submission, synthesis,
  headless playback and WAV archiving. EPUB spine order checked independently.
- All 54 voice styles produced finite CUDA audio: aggregate 102.209 seconds of
  audio generated in 13.778 seconds. This is numerical/runtime validation,
  **not perceptual language or pronunciation certification**.
- Both TUI blending implementations exercised, including voice two at zero
  weight with an active third voice. Python TUI template and actual TOML writes
  loaded successfully through the daemon configuration parser.

## Remaining limits

Unclosed fenced code now extends to the document end, as Markdown specifies.
With `read_code_blocks = false`, that content is omitted; enable the setting
if code should be narrated. Closing fences may be longer than their opener.

Japanese Kanji pronunciation is inadequate with the bundled espeak path: tested
characters can be verbalized as “Chinese letter”. A dedicated supported Japanese
G2P path would be required before claiming correct Japanese book narration.
Other languages, names, abbreviations, unusual Unicode and voice blends also
need perceptual checks. Upstream itself documents uneven language/voice support.
[Upstream voice guidance](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)

Misaki was examined, but its published Python requirement did not match this
Python 3.14 baseline. It was not introduced as an untested dependency override.

Very long documents still consume memory proportional to prepared text and
segments, and WAV archiving consumes disk proportional to audio duration.
Standard WAV size limits and archive write failures disable the archive for that
job while playback continues; this is not unlimited archival capacity. Archive
retention removes older files according to the existing configured policy.

An empty dependency cache or missing models cannot support offline bootstrap.
The ISO must include the system tools, Python, model files and the matching
uv cache/lock for its selected backend. Final ISO versions, alternative
architectures and actual hardware classes need validation before distribution.

## Reproduction

From this directory:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile dusky_main.py tui_kokoro.py
bash -n kokoro_installer.sh trigger.sh tui/kokoro_tui.sh
shellcheck kokoro_installer.sh trigger.sh tui/kokoro_tui.sh
./trigger.sh --synth
systemd-analyze --user verify "$HOME/.config/systemd/user/dusky-kokoro.service" "$HOME/.config/systemd/user/dusky-kokoro.socket"
```

Ordinary rerun: `./kokoro_installer.sh --yes`; intentional dependency refresh:
`./kokoro_installer.sh --upgrade --yes`; cached deployment:
`./kokoro_installer.sh --offline --yes`. Use `--hw cpu` for the portable CPU
profile and `--hw nvidia` for a supported NVIDIA installation. System tools must
already exist unless `--install-system-deps` is explicitly supplied online.

Detailed local fixtures, benchmark JSON, WAVs and logs from this audit are in
`/tmp/dusky-kokoro-audit`; they are temporary evidence and are not part of the ISO.
No Git add, commit, push, reset or restore was performed. Unrelated concurrent
workspace/index changes were left alone.
