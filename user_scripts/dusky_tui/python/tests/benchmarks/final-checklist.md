# Final architecture checklist — 2026-09-29

The original AI proposals were audited against current code, rather than accepted as implementation requirements. The revised architecture is implemented; not every literal original proposal was appropriate or implemented.

## Proposal coverage

| Proposal | Checked current status | Evidence / reason |
|---|---|---|
| Lazy hidden custom bodies/notices | Done | `_ensure_custom_body` shares and awaits mount completion, cleans failed partial mounts, preserves focus and retries. |
| Only active tab has any full widgets | Deliberately partial | Ordinary/mixed option lists and lightweight shells retain warmup. Custom bodies are lazy. Original 75% allocation claim is not established; saved widget count is 106, not an allocation-byte measure. |
| Hidden custom factories/timers stopped | Done for framework Rich views | Active-tab and engine-readiness ownership; hidden invalidations stay dirty. Generic supplied Widgets own their private timers. |
| Atomic screen batching | Corrected implementation | Installed Textual 8.2.8 has `batch_update()`, no `App.batch`. Completed option/tab mutations are batched. No batch across backend I/O; entire startup is not held behind a repaint barrier. |
| Dynamic multi-core engine pool | Deferred | Engine batches are still sequential inside an off-thread load. One measured UFW engine cannot benefit from cross-engine parallelism; bounded independent-engine concurrency needs a representative multi-engine benchmark. |
| Expensive view I/O outside UI thread | Done | UFW `prepare`/`collect`/`view` separation; worker reads share save lock. Main-thread Rich rendering, coalescing, stale-result suppression, cancellation drain and cached revisits are tested. |
| Process pool for parsing | Not introduced | No measured need justifies serialization, process startup or mutable-engine ownership complexity. |
| Lazy heavy imports/dialogs | Deferred / originally mischaracterized | `webcolors` is already lazy in core types. UI has no direct `difflib`/`pygments` imports. Markdown/help is still eager, with indirect parser/highlighter imports. Dialogs are inline classes, not the proposed separately imported `ExportDialog`/`ColorPickerDialog` modules. No import-speed gain is claimed. |
| Preserve public use and global operations | Checked | Existing APIs remain usable; collector keys are additive. Save/preset/undo/global discovery remain independent of tab visitation. All original tests plus additions pass. |
| Subsecond 8 W startup / instantaneous switching | Not achieved or promised | Saved reference: first headless display callback 3.30 s; active readiness 7.17 s; first custom visits can require real reads. Clean revisits reuse snapshots. |
| Zero optical flicker/tearing | Unverified | Layout feedback loops are addressed, but no opening-frame video proves physical presentation. Terminal flush/callbacks are not optical frames. |
| Profiler, syntax and regression gates | Passed within stated scope | Real launcher smoke, all 144 tests, compiled syntax checks and scoped Git whitespace checks. No fresh matched 8 W comparison in this audit. |

The UFW rule-snapshot reuse is implemented in addition to the original framework proposals. `load_state` reads numbered rules once for fourteen services; dashboard banned-IP classification reuses its own rules snapshot. Empty explicit snapshots do not trigger another read. This targets demonstrated I/O, despite the original plan's unsupported assertion that schema/engine changes were unnecessary.

## User's compact footer spacing

Current `ShortcutFlowLayout.arrange` advances `x += width`. Width already includes `child.styles.gutter.width`, and `.footer-shortcut` has `padding: 0 1`. Removing the additional two columns therefore removes extra inter-widget spacing while keeping padded widgets adjacent. It does not change pre-paint wrapping or overlap protection. It remains in place.

SHA-256 comparison confirms this is the sole UI difference from the saved 8 W production snapshot: temporarily substituting the old line in memory reproduces the reference hash. UFW engine, schema, launcher and lifecycle-test hashes still match. Raw reference JSON and fingerprints remain unmodified; its timings are recorded measurements, not fresh measurements of the compact footer.

## Fresh verification

- Full suite: **144 tests passed in 45.312 seconds**; includes original 131 and thirteen additions. See [verification-tests-20260929.log](verification-tests-20260929.log). Asyncio slow-callback diagnostics occurred, with no failures.
- Actual-launcher root-free smoke: one process, tabs 1/2, 0.5-second observation; zero errors and four completed visit/revisit measurements. Lazy collector mounting and clean cached revisit exercised. Raw report: [verification-smoke-20260929.json](verification-smoke-20260929.json).
- Production UI, UFW engine and schema compiled successfully; Python 3.14.7 / Textual 8.2.8 checked locally. Source paths/hashes are in the smoke report.
- No production code changed during this audit. The user's committed footer preference is preserved. No new 8 W performance gain or optical artifact guarantee is claimed.
