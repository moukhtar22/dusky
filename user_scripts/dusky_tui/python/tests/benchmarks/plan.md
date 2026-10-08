# Further optimization: measure before changing architecture

Read `README.md` and `implementation.md`, then run a fresh baseline. Existing lazy custom bodies, retained snapshots, active collector ownership, UFW read reuse and layout batching are already implemented. Preserve their contracts; do not implement them again.

## 1. Identify the current critical path

The saved five-run, configured 8 W UFW reference has median first headless display callback 3.30 s, initial data/refresh readiness 7.17 s, UFW state load 1.37 s and inclusive UI import 0.91 s. Startup performs six external commands, two numbered-rule reads, one collector and one Rich renderer. These overlapping timings cannot be added or subtracted to infer a precise critical path. Inspect per-run command/collector spans and their start times before choosing an optimization.

Reproduce with the same state and envelope, retain raw samples, and distinguish:

1. Process/import work before app construction.
2. Initial shell creation, DOM mount/layout and first refresh.
3. Active engine load and initial view collection.
4. Background option warmup and global discovery.
5. First custom visit versus clean cached revisit.

If readiness measurements disagree with observed usable content, improve instrumentation first. Add separate timings for genuine mount/layout phases or input processing only where the question requires them. The current compose generator timer does not measure the full DOM.

## 2. Prioritize demonstrated expensive work

- **Initial UFW collection:** examine slow commands and duplicate reads across state load and dashboard. A shared snapshot may help only if freshness, ownership, failure behavior and invalidation remain explicit. Do not introduce a broad cache merely to save one read.
- **Imports:** attribute actual launcher imports separately. Consider pruning/deferment only for expensive features unused at startup. Markdown/help deferral is a candidate, not a proven win; visible Markdown schemas still need it. Preserve help mounting/focus and measure net benefit with tracing disabled.
- **First custom visits:** the reference Sockets first visit is about 1.85 s and Reports 0.89 s; clean revisits are about 0.12 s and 0.19 s. Improve actual worker reads/rendering where measured. Do not hide costs by eagerly polling invisible tabs or silently extending stale-data lifetimes.
- **Global discovery / multiple engines:** profile a representative multi-engine schema before considering concurrency. If independent loads dominate, compare bounded concurrency 1/2/4 under the low-power envelope, preserving save serialization, determinism, load failures and shutdown. More CPU parallelism is not automatically faster at 8 W; no process pool is justified by the single-engine reference.
- **Mounting/layout:** only deepen laziness or batching if measured DOM/layout work is material. Ordinary option warmup and global operations are intentional. Use `batch_update()` around completed synchronous mutations, never across blocking reads or awaited loads.

Each candidate needs one clear bottleneck, one focused change, a fresh comparison using the same harness revision, and explicit tradeoffs. Retain a change only if benefit exceeds run variability without degrading correctness or first interaction.

## 3. Verify visual behavior directly

Use real kitty on Wayland with recorded actual viewport, font, scale, terminal/version and synchronized-output negotiation. Inspect opening-frame video at narrow and normal widths. Exercise rapid tab changes, long footer shortcuts, telemetry appearance and custom load failures. A headless callback or terminal flush is insufficient proof of artifact elimination. Separate app layout problems from terminal presentation.

## 4. Required preservation and acceptance

Preserve the original 131 tests plus 13 current lifecycle tests. Cover any new edge case with a focused test, then run the complete suite for production changes. Check failed/slow collectors, overlapping navigation, search focus, partial-mount retry, save/refresh invalidation and shutdown while a collector is running. Generic Widget lifecycles need separate tests if touched.

Compare first display, active readiness, boot, first visits, cached revisits, command counts, hidden callbacks and whole-workload CPU/RSS. Check raw errors and command outcomes. Report sample count and variability; do not make tail guarantees from five runs. Validate real terminal behavior independently. Verify the final ISO package versions and documented interfaces before adopting version-specific behavior.

No numeric startup promise is justified for every schema/hardware combination. Current references establish a reproducible investigation starting point, not an unfinished mandatory rewrite.
