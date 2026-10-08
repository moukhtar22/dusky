# Current architecture and invariants

Current production checked on 2026-09-29: engine, schema, launcher and lifecycle tests match the saved source fingerprints. UI differs only by compact footer spacing (`x += width` instead of `x += width + 2`); shortcut CSS already supplies horizontal padding. Observed runtime: Python 3.14.7, Textual 8.2.8, kernel 7.3.0-rc4-dusky-battery. Verify the final ISO package manifest before relying on package-specific behavior; these observations are not version pins.

## Lifecycle

`python/frontend/ui.py` owns these behaviors:

- Lightweight tab shells exist at startup. Ordinary and mixed option lists retain hidden warmup. Hidden custom bodies and their notices mount on first activation, preserving notice order, selection, help and scroll state.
- `_ensure_custom_body` shares a mount task per tab. Completion is awaited before exposure/focus. Failed partial mounts are cleaned up and retryable. A late completion cannot activate or focus a newer tab. Both tab-index/name lookup and sparse tabs remain supported, as do Widget classes and supplied instances.
- Framework `CustomRichTabWidget` activation depends on the active tab and loaded engines. Custom-only tabs depend on the default engine. Hidden invalidations mark content dirty without evaluating it. Framework Rich views do not render or poll while hidden.
- Expensive views use `prepare(app)` on the UI thread, `collect(prepared)` in a worker under the existing `_save_lock`, then `view(snapshot)` on the UI thread. The collector must not inspect/mutate Textual widgets; the renderer must not perform blocking system/engine reads.
- One collection is in flight per view, repeated requests coalesce, and generations suppress stale results after invalidation, hide or teardown. Shutdown drains blocking collectors before engine shutdown. Thread cancellation cannot interrupt a running blocking operation.
- Clean collector snapshots survive tab changes. Revisits immediately reuse retained data and resume active polling. Model/write generations and explicit refresh invalidate snapshots. External changes while hidden become visible on resumed polling or explicit refresh. Cheap legacy factories run on activation on the UI thread.
- Global loading, saves, presets, undo and validation remain independent of which tabs have been visited. Preserve this when changing laziness or engine scheduling.

Generic supplied Widget classes own their private timers/workers; the framework Rich-view polling contract does not guarantee their internals are idle while hidden.

## Layout

`ShortcutFlowLayout` computes footer wrapping during Textual layout. Arrow columns reserve their width. Completed option population and tab mutations use the documented `batch_update()` context; no batch spans backend I/O. Known default-engine telemetry reserves its space during initial mount. Discovery of a different telemetry engine can still change geometry later.

These choices remove application layout feedback and worker-side Rich rendering. Complete optical artifact elimination has not been verified with video. A prior real kitty Wayland smoke run negotiated synchronized output and completed without errors at an actual 62×31 viewport; that does not prove physical presentation quality or UFW performance.

## UFW I/O

`python/engines/ufw.py`: `load_state` supplies one explicit numbered-rule snapshot to all fourteen common-service checks. `is_service_allowed` and `get_banned_ips` accept optional snapshots; an empty supplied list is valid and must not trigger another read. Standalone getters retain their own-read behavior.

`network_manager/tui_firewall.py`: six Rich views separate worker collection from pure rendering. Dashboard banned-IP classification reuses its rule snapshot. No cross-engine parallelism is implemented; one UFW engine cannot benefit from cross-engine scheduling. Mutable engine operations remain serialized with saves.

## Expensive custom-view contract

```python
CUSTOM_VIEWS = {
    0: {
        'prepare': capture_selection,  # app -> immutable inputs/engine handle; UI thread
        'collect': read_system_data,   # prepared inputs -> snapshot; worker
        'view': render_snapshot,       # snapshot -> Rich content; UI thread
        'interval': 2.0,
        'show_options': False,
    },
}
```

A bound engine handle can be captured; its operations use the shared save lock. Legacy zero-argument and `factory(app)` APIs remain for cheap UI work. Collector exceptions are recorded and rejected by the profiler even when the UI displays an error. Generic Widgets need their own lifecycle/readiness tests.

## Verification status

- Original 131 tests are unchanged; 13 tests in `tests/test_startup_lifecycle.py` cover lazy mount/retry, focus/search, sparse/mixed tabs and notices, Widget instances/classes, footer widths, active polling, collection coalescing, thread separation, stale results, shutdown drain, cached revisit/invalidation, UFW read reuse and pure report rendering.
- Fresh complete discovery on 2026-09-29 passed **all 144 tests in 45.312 seconds**, including footer wrapping/non-overlap at 48/80/120 columns with the current compact spacing. The raw log is `verification-tests-20260929.log`; see `final-checklist.md` for current acceptance limits.
- The raw five-run UFW reference has no benchmark errors or nonzero external commands. Renderers ran on the main thread, collectors on executor threads; no hidden idle Rich factories/collectors were observed.
- The profiler previously rejected an intentional collector failure. Its private hooks and readiness assumptions must still be rechecked after framework/Textual changes.

Source fingerprints and current measurements are in `baseline-summary.json` and `baseline-ufw-8w.json`. They contain no dependency on old plans or backup files. Current production scope for the reference is UI, UFW engine and UFW schema; the launcher was unchanged. Future production edits require fresh tests and measurements.

## Handoff harness verification (2026-09-28)

This folder's harness passed one root-free INI smoke run and one privileged UFW run (tabs 1/2/14, 3.2-second idle observation). Both reports have empty error lists and no nonzero external commands. The smoke confirms lazy collector mounting and a retained clean revisit; loaded module fingerprints include the actual INI/UFW engines and frontend sources. An intentional collector exception was reported and rejected with a nonzero parent exit. Python syntax and scoped Git whitespace checks passed.

Raw functional checks are saved in `harness-verification.json`. The observed package power limits were 90/115 W, and cache conditions were uncontrolled; these were not matched to the five-run 8 W reference; do not interpret faster timings as new production gains. No production code changed during this handoff cleanup, and the production unit suite was not repeated.
