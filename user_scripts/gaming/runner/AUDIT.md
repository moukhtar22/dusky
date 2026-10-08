# Runner lifecycle audit — 2026-09-30

The runner now removes reused and newly created game mount stacks after the
owned workload ends when `runner.auto_unmount_on_exit = true`. This applies
with `--no-mount` and `auto_mount = false` as well: those control setup, while
`--keep-mounted` or `auto_unmount_on_exit = false` controls retention.
Persistent upper-layer files and saves remain on disk.

## Confirmed faults and fixes

- A pre-mounted stack survived a successful game exit. A real fixture reproduced
  this before editing; automatic teardown now includes reused mounts.
- Mount helpers could publish a layer before failing or being interrupted, before
  the session registered cleanup. Mount setup now rolls back layers created by
  the attempt, and sessions register teardown before setup.
- Scope discovery began after launcher exit, potentially missing descendants.
  The supervisor observes the scope during launch and resolves its named unit
  if the leader has already disappeared. Scope names use a profile-path hash,
  allowing arbitrary profile names without invalid systemd unit characters.
- SIGTERM/HUP/QUIT during startup bypassed Python teardown. Session signal
  handlers now unwind startup; spawning defers interruption until the process
  cleanup callback exists. Repeated signals cannot interrupt teardown.
- Failed unmounts were ignored. Kernel mountinfo now verifies removal after
  each helper. A busy union keeps its lower mounted and returns cleanup failure
  (74 for an otherwise successful game), including an error in quiet mode.
- Mount locks covered only the union path. Launch, mount, unmount and sweep now
  lock lower, union, upper and work resources in a consistent order.
- A retained lower skipped cleaning the overlay work directory. Partial-stack
  recovery cleans the scratch directory before creating a new union.
- Lower-layer recovery could leave a union attached to the old lower. Recovery
  now removes the union first and checks detach results.
- Colon/backslash directory names broke fuse-overlayfs option parsing. Relative
  temporary symlinks avoid its parser, and canonical mount paths make symlinked
  game directories observable through mountinfo.
- fuse-overlayfs 1.18 ignores the supplied `auto_unmount` and `clone_fd` options.
  They were removed; upstream already initializes clone_fd. DwarFS supports
  these options and retains them.
- systemd expanded literal dollar tokens; CLI forwarding removed literal `--`.
  `--expand-environment=no` and unchanged forwarding preserve game arguments.
- Wine shutdown sent `-k` without waiting. It now follows with `-w`; the wait
  result is authoritative because `-k` returns 1 when no server is running.
- Environment setup registers Wine cleanup before prefix provisioning, covering
  interruptions while building the environment. Mount probes use the shared
  bounded subprocess helper rather than leaking a probe on interruption.
- Nonfinite timeouts and overlapping storage directories are rejected. Zero
  kill grace is honored. Log-lock timeouts cannot mask a completed launch.
- Removed KWin/wlr and old Hyprland socket discovery branches. Sandbox sockets
  use XDG runtime paths. Mesa chooses its hardware driver, and the runner no
  longer invents the NVIDIA provider name `NVIDIA-G0`.

## Verification

Installed environment: kernel `7.3.0-rc4-dusky-battery`, Python 3.14.7,
systemd 262, Hyprland 0.56.2, Bash 5.3.20, fzf 0.74.4, DwarFS 0.15.7,
fuse3 3.18.3, fuse-overlayfs 1.18, Wine-Staging 11.18, gamescope 3.16.31,
bubblewrap 0.13.0, util-linux 2.42.4, coreutils 9.12.
These are development-system observations; the final ISO manifest is not
available and must still be checked before shipping.

- Full lifecycle suite: **29 tests passed in 158.684 seconds**, including 20
  repeated real FUSE/systemd launches, normal/error exits, missing executables,
  pre-mounted stacks, SIGINT/TERM/HUP/QUIT, ignored TERM escalation, startup
  interruption, detached descendants, concurrent launches, scratch cleanup,
  persistent saves, daemon-crash recovery, helper/readiness failures, sandbox
  cleanup, and unusual/symlinked directory names.
- Final unit run: **12 passed**, with 18 opt-in integration tests skipped.
  This includes the subsequently added repeated-signal teardown test.
  The startup-hook integration test was rerun after final callback changes.
- **43/43 installed DwarFS profiles** mounted, resolved a readable executable,
  and unmounted. This inspected game assets without running all 43 games.
- Actual window-close tests: The Roottrees are Dead (native Wayland), Mesmalie
  (Wine Wayland), and Roottrees inside gamescope each exited **0**, with both
  mount layers absent afterward. Idle inhibition worked in their logs.
- A disposable Wine `cmd /c start /b ...` fixture completed its delayed child
  before session teardown and returned 0.
- The original runner changed `$HOME` and `${USER}` and discarded literal `--`.
  The edited runner preserved all five supplied argument tokens exactly.
- Doctor returned no FAIL/WARN checks on this development system.
- All 50 profiles parsed without configuration errors. Eight were unavailable:
  `fatal_claw`, `portal_collection`, and `portal_collection_install` have missing
  game directories; `blasphemous`, `bloodthief`, `dicey_dungeons`, `portal`,
  `portal_2`, and `portal_collection` request host Xwayland, which is absent.
  The existing profile selections were preserved; those selections need
  revision for a strictly Wayland-only ISO.
- Python compilation and Git whitespace checks passed. Final mountinfo and
  systemd checks found no fixture mounts or `master-runner-*.scope` workloads.

Run the tests from this directory:

```sh
python3.14 -m unittest discover -s tests -v
RUNNER_INTEGRATION=1 python3.14 -m unittest discover -s tests -v
```

Evidence is retained under
`${XDG_STATE_HOME:-$HOME/.local/state}/master-runner/audits/2026-09-30/`.
The real game test initialized Mesmalie's previously uninitialized Wine prefix
and wrote normal runtime/game configuration; it did not delete game saves.

## Scope and limits

The measured improvements concern lifecycle correctness, not frame rate.
No FPS or memory-performance gain is claimed. This is not proof that every
engine, installer, GPU, HDR setup, or future package release works. UMU is not
installed here, and its runtime was not exercised. The tests ran on the
available Intel development system, not separate AMD/NVIDIA hardware.

SIGKILL, power loss, and kernel failure cannot execute Python cleanup.
A later mount attempt recovers stale endpoints; `unmount`/`unmount-all` can
remove retained stacks. Busy mounts are reported and retained until their
users release them. With `runner.use_systemd_scope = false`, a descendant
that deliberately leaves the process group is outside group supervision;
keep the default scope enabled for daemonizing workloads.

## Before/after scores

Scores below are observed pass percentages for the named probes, not an
overall quality rating. N/A means no defensible numerical measurement.

| Area / probe | Before | After | Evidence | Result |
|---|---:|---:|---|---|
| Reused-mount automatic cleanup | 0 | 100 | One real premounted-exit reproduction | Better |
| Path handling: space/comma/colon/backslash | 50 | 100 | Two of four versus four of four real mounts | Better |
| Literal game tokens preserved | 40 | 100 | Two of five versus five of five supplied tokens | Better |
| FPS / performance | N/A | N/A | No gameplay benchmark | Unverified |

## Interface references

Used installed help/man pages and exercised affected flags against the versions
above. The corresponding upstream sources explain the parser and expansion:

- [systemd-run arguments and environment expansion](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd-run.xml)
- [fuse-overlayfs 1.18 parser and initialization](https://raw.githubusercontent.com/containers/fuse-overlayfs/v1.18/main.c)
- [fuse-overlayfs mount interface](https://github.com/containers/fuse-overlayfs/blob/main/fuse-overlayfs.1.md)
- [Mesa driver selection environment](https://docs.mesa3d.org/envvars.html)
- [Hyprland window-close dispatcher](https://wiki.hypr.land/Configuring/Basics/Dispatchers/)

## Second pass and Lutris comparison

Reviewed the committed runner (`ff25bce2`) against the downloaded Lutris 0.5.23
source in `/mnt/zram1/lutris-master`. The comparison covered its game launch,
stop and post-exit paths, monitored-command wrapper, subreaper/process watcher,
Wine/UMU command construction, prefix handling, DLL installation/restoration,
runtime library paths, XDG settings and Steam discovery. Unrelated service,
GUI, emulator and translation code was outside this launcher comparison.

Useful ideas adopted from Lutris:

- Architecture-aware translator destinations: a win32 prefix needs x32 DLLs in
  `system32`; win64 uses x64 there and x32 in `syswow64`. The old runner always
  selected x64 for `system32`. A disposable win32 fixture reproduced this.
  DXVK, VKD3D and NVAPI now share the correct destination mapping. DLSS no
  longer falls back to putting a 32-bit library in 64-bit `system32` when
  `syswow64` is absent. DLL disabling/restoration is tested too.
- Lutris's data directory follows XDG. Translator discovery and recognition
  now honor `XDG_DATA_HOME/lutris/runtime`. Steam's existing installation
  path is retained; its directory layout is independently defined by Steam.

Other reproduced faults corrected:

- A direct child killed by SIGTERM returned Python's -15, which would become
  shell status 241. The session now records/returns 143, consistent with scope
  launches and runner interruption. See the documented
  [Python return-code convention](https://docs.python.org/3.14/library/subprocess.html#subprocess.Popen.returncode).
- `--reprovision` mutated the shared profile even during a dry-run. It now
  creates a session-specific profile, so subsequent launches retain their
  configured provisioning policy.
- Wine inspected a nested `prefix/pfx` while launching and shutting down at
  `prefix`. Environment construction, shutdown and the session lock now use
  the actual Wine prefix. UMU's environment contract is preserved.
- An interruption after the game succeeded could leave only the successful
  game record. The interrupted phase now gets its own failure record; config
  patch errors use the same exception path without duplicate records.

The runner retains cgroup supervision. Lutris's watcher excludes processes by
name and relies on a subreaper, while its client also searches process command
lines and UUID environments. Those heuristics are unnecessary for the runner's
owned systemd scope and could miss legitimate workloads. The delayed Windows
child fixture verifies that the runner waits for the workload after its first
launcher exits. No Lutris runtime downloads or legacy platform branches were
introduced.

Second-pass verification:

- **40 tests passed in 214.523 seconds**, with both FUSE/scope and Wine
  integration enabled. This includes 20 repeated launches, the prior failure
  and cleanup cases, stopped-process shutdown, post-launch interruption,
  normalized direct-child status, win32/win64 DLL wiring/restoration, custom
  XDG discovery and a real nested Wine prefix with a delayed Windows child.
- All six new focused regression tests failed against the committed baseline;
  all six pass against the edited runner. The baseline run used a disposable
  copy and did not modify the committed source or installed prefixes.
- **43/43 installed DwarFS profiles** again mounted, exposed a readable game
  executable and unmounted. The three real native Wayland/Wine Wayland/
  gamescope window-close checks again returned 0 with both layers gone.
- All 50 profiles still parse; the same eight remain unavailable. Doctor's
  39 checks contain no FAIL/WARN results. Installed package versions remain
  those recorded above; relevant installed command help was rechecked.
- Compilation, scoped Git whitespace/diff review and final mount/scope
  inspection passed. The limitations in the first-pass report still apply;
  DLL placement fixtures do not prove NVIDIA execution or a win32 Wine build.

Run every test from this directory:

```sh
RUNNER_INTEGRATION=1 RUNNER_WINE_INTEGRATION=1 python3.14 -m unittest discover -s tests -v
```

Evidence is in `audits/2026-09-30/second-pass/` under the state directory
documented above. Scores are pass percentages for the named regression groups,
not an overall quality score or a guarantee about all games.

| Area / probe group | Before | After | Evidence | Result |
|---|---:|---:|---|---|
| Session correctness | 0 | 100 | Four reproduced regressions: 0/4 versus 4/4 passing | Better |
| DLL wiring and XDG discovery | 0 | 100 | Two regression tests: 0/2 versus 2/2 passing | Better |
| Performance / efficiency | N/A | N/A | No frame-rate or resource-use benchmark | Unverified |
