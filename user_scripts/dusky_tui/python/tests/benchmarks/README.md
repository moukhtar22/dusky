# Dusky TUI: current benchmark handoff

Start here. This folder is self-contained; no previous AI plan or RAM workspace is required. Production code is adjacent under `../../frontend`, `../../engines` and `../../main`; the UFW schema is `../../../../network_manager/tui_firewall.py`.

## Contents

- [implementation.md](implementation.md): current architecture, contracts and verification limits.
- [final-checklist.md](final-checklist.md): final architectural checklist and fresh full-suite verification, 2026-09-29.
- [plan.md](plan.md): investigation priorities for further optimization; hypotheses require measurements.
- [benchmark_startup.py](benchmark_startup.py): fresh-process profiler of the actual launcher.
- [fixture.py](fixture.py): root-free functional smoke workload using a temporary INI file.
- [baseline-ufw-8w.json](baseline-ufw-8w.json): unmodified five-run reference for the recorded production snapshot, dated 2026-09-28.
- [baseline-summary.json](baseline-summary.json): current-only reference summary and source identities.
- [harness-verification.json](harness-verification.json): fresh one-run smoke and privileged UFW checks of this harness; not a matched 8 W comparison.

The raw reference preserves original recorded absolute paths as provenance. Those paths are not runtime dependencies. Its profiler predates this folder's launcher-default and source-fingerprint improvements. Current UI differs from that reference only by the user's compact footer spacing (`x += width`, with existing shortcut padding retained); engine, schema, launcher and lifecycle-test hashes still match. Always capture a fresh baseline before changing production code; the reference is one machine/workload, not a performance target for every installation.

## Run the benchmarks

From an installed Dusky tree:

```sh
cd "$HOME/user_scripts/dusky_tui/python/tests/benchmarks"
python3 benchmark_startup.py fixture.py --runs 1 --tabs 1,2 --observe 0.5 --timeout 60 --output /tmp/dusky-smoke.json
sudo python3 benchmark_startup.py "$HOME/user_scripts/network_manager/tui_firewall.py" --runs 5 --tabs 1,2,14 --observe 3.2 --timeout 180 --label '8 W configured; headless; uncontrolled caches' --output /tmp/dusky-ufw-current.json
```

Set and confirm the intended power envelope yourself before collecting comparisons. The profiler records available powercap limits; it does not change them or prove that actual power consumption is 8 W. Use the same firewall state, power settings, viewport, schema, navigation, observation duration and background workload for comparisons. It performs read/inspection and tab navigation, with no simulated save/reset actions; schema startup behavior still runs normally. UFW requires root, and the benchmark deliberately rejects launcher sudo re-execution. Inspect every run's errors, stderr and external command return codes: nonzero commands may be recorded without failing the workload.

The default launcher is adjacent `../../main/main.py`; `--launcher PATH` overrides it. In isolated checkouts, inspect `environment.source_sha256` paths to confirm the schema has not imported another installation. Schemas can alter `sys.path`. Run the harness's `--help` for all options. Reports should go outside this handoff folder unless deliberately replacing the reference with a documented new measurement.

For real terminal output, run from an interactive kitty terminal on Wayland, adding `--mode terminal` to the smoke or UFW command. Do not redirect terminal output; use `--output` for JSON. Record actual `viewport_width`/`viewport_height`, terminal version, scale and synchronized-output detection. Requested 120×40 does not guarantee that viewport. To assess opening artifacts, also capture and inspect opening-frame video; this profiler cannot certify optical flicker or tearing.

## Interpret the report correctly

- `first_compositor_callback_ms`: first eligible Textual display callback, measured from the parent's spawn request. Headless mode delivers no terminal frame.
- `first_driver_flush_return_ms`: terminal mode only; driver return is not presentation acknowledgement.
- `active_data_refresh_ms`: initial-tab dependency/content readiness followed by a refresh callback. It is not measured keyboard-input-to-pixel TTI.
- `boot_refresh_ms`: all startup/discovery complete followed by refresh. It can occur after usable initial content.
- `compose_step` spans measure generator execution only, excluding Textual DOM mounting/layout between yields. Widget counts are counts, not allocation bytes.
- Import spans are inclusive and overlap. Use a separate `python3 -X importtime …` run for attribution; tracing itself changes timing. No separate warm-import benchmark is provided.
- Switch durations include programmatic activation, readiness and refresh callbacks. Read `warmed_before`, `retained_content_before` and `dirty_collector_before`; clean revisits may reuse cached system data.
- `--observe` examines idle callbacks after boot, not idle before first display. Hidden Rich polling should be absent; generic supplied Widgets manage their own timers.
- CPU time and peak RSS cover the whole worker workload, including observation, navigation and shutdown. They are not startup-only metrics.
- Fresh interpreters do not imply cold filesystem/bytecode caches. Milestones and inclusive spans must not be summed. Five runs justify sample summaries, not tail-latency guarantees.
- Readiness instrumentation uses private framework/Textual state. Revalidate it after upgrades or lifecycle changes; arbitrary Widget readiness has no collector signal. Headless timeout kills the process group; terminal timeout kills only the child.

## Verification when changing production

```sh
python3 -m unittest discover -s "$HOME/user_scripts/dusky_tui/python/tests" -p 'test_*.py'
```

The current suite comprises the original 131 tests and 13 startup/lifecycle additions (144 total). See `implementation.md` for the precise last verification scope. Run focused tests while iterating, then the complete suite for a production change. Check performance under the configured envelope and inspect real terminal rendering separately.
