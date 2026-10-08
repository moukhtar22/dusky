# Visualizer audit — 2026-10-02

The existing single-threaded GLib design is retained. GTK 3 is the installed
backend supported by gtk-layer-shell; replacing the toolkit would be a separate
migration, not a reliability fix.

## Fixes

- Serialize shortcut startup in a shared standard-library client. Open the FIFO
  write-only/nonblocking so a stale FIFO cannot silently accept a command. Clients
  never unlink the daemon's FIFO. Startup sends idempotent `enable`; an active
  service receives `toggle`. Delivery means a complete write to a live reader,
  not an acknowledgement that the daemon has applied the command.
- Buffer fragmented newline-delimited IPC commands; remove the owned FIFO during
  shutdown and retain the instance-lock inode to prevent split-lock races.
  Abort startup if locking or FIFO creation fails, and clean up on startup errors.
- Preserve current settings on malformed JSON or invalid UTF-8. Remove the
  750 ms file-monitor suppression that discarded valid external edits. Read
  colors when their directory becomes available. Normalize configuration during
  setup without discarding unknown extension keys or overwriting invalid JSON.
- Clamp all effect intensities. Normalize odd bar counts down to an even count
  for Cava's default stereo output. Reject multiline source values that would
  accidentally alter the generated INI configuration.
- Clear stale audio targets on Cava failures; reap killed subprocesses, expose
  stderr in the journal, and use bounded exponential restart delays. Remove
  obsolete Cava smoothing options. Extract only the latest complete audio frame.
  Unify low-signal/idle thresholds so rendering can settle and stop.
- Free OpenGL resources while their context is current, including partial
  initialization failures. Track pending fallback work for cleanup. Correct the
  bottom-wave fade denominator and avoid automatic GL redraws outside the timer.
- Bind geometry and layer output to the same monitor; recreate the window on
  monitor add/remove and geometry changes. Allocate smaller linear surfaces while
  reserving space for glow and dots. Match Cairo geometry to configured height.
- Provide visible Cairo fallbacks for every style. Spectrum, aurora, and lightning
  approximate a line; psychedelic and kaleidoscope approximate a circle. Perimeter
  has a software frame renderer. These preserve availability, not shader parity.
- Remove duplicate TUI bootstrap code, respect XDG_CONFIG_HOME for settings/theme,
  and expose the existing monitor style in the TUI.

## Verification

Observed environment: Python 3.14.7, Bash 5.3.20, systemd 262,
Hyprland 0.56.2, GTK 3.24.52, gtk-layer-shell 0.10.1, PyGObject 3.56.3,
PyOpenGL 3.1.10, Cava 1.0.0, PipeWire 1.6.9, GLib 2.88.3, Mesa 26.2.3.
The final ISO package manifest was not available; these are the tested installed
versions, not a claim that the unpublished ISO has identical packages.

- All 20 regression tests pass, including 126 Cairo style/position/bar-count
  combinations, fragmented IPC/audio, config failures, lock contention, idle
  decay, and active/inactive/stale-FIFO client behavior.
- Live Wayland tests initialize OpenGL 4.6 and observe nontransparent framebuffer
  pixels for all 14 styles, real PipeWire Cava reads, overlay control,
  disable/enable, renderer switching, and forced GPU-failure fallback.
- A real Cava process consuming synthetic stereo 440 Hz PCM emitted 90 complete
  72-bar frames, reached 1000/1000 amplitude, and produced no stderr errors.
- Twenty concurrent real shell wrappers with a fixture systemctl produced exactly
  one startup, one enable, and nineteen intact toggles.
- The same live smoke test also passed as a transient systemd user service under
  the configured 256M/512M memory limits and NoNewPrivileges/UMask settings.
  It exited successfully; peak service memory was 140.6M during this short test.
- Python compilation, Bash syntax, ShellCheck, and systemd unit verification pass.
  The installed service unit matches the audited source unit and was unchanged.
- Default linear surface on the tested output: 1280×720 before, 1280×424 after
  (41.1% less framebuffer area). This is an allocation result, not a measured
  frame-time or CPU improvement.

Run from this directory:

```sh
python3 -m unittest discover -s tests -v
python3 tests/live_smoke.py
```

The live test briefly displays temporary surfaces and uses isolated config/cache
and lock paths. It does not change compositor rules or saved user settings.
Physical hotplug, every GPU/driver, prolonged operation, and visual parity between
shader effects and software approximations remain unverified. An unavailable
accessibility bus emitted a dbind warning in this session; rendering tests passed.

## Interfaces checked

- [GTK GLArea lifecycle and context management](https://docs.gtk.org/gtk3/class.GLArea.html)
- [Hyprland instance-scoped IPC](https://wiki.hypr.land/IPC/)
- [Hyprland Lua layer rules](https://wiki.hypr.land/configuring/core/rules/layer-rules/)
- [Cava 1.0.0 configuration](https://github.com/karlstav/cava/blob/1.0.0/example_files/config)
- Installed Cava, hyprctl, and systemd-run help and GI method documentation.

## Second pass — lifecycle review

The top-level startup/control/reload/render/shutdown interactions were reviewed
again. Four additional regression tests reproduced five failing cases before the
fixes (GPU retry has both disabled and simultaneous-enable cases).

- Clear a prior GPU failure when its setting changes, before disabled/enabled
  early returns. Explicit renderer changes now permit a fresh GPU attempt even
  when made while disabled or in the same edit that enables the visualizer.
- Cancel a window's queued Cairo fallback when that window is destroyed or
  replaced. Its successor cannot be overwritten by a stale fallback callback.
  Shutdown uses the same cleanup path.
- Recalculate window geometry after bar-count changes, including the margin
  needed for wide dots on large outputs.
- Missing config/theme files now preserve the current settings during reload,
  matching malformed-file behavior. A temporarily missing config cannot silently
  re-enable a disabled visualizer. Initial startup still uses defaults; use
  `--reset` to explicitly restore them.

All 20 regression tests and the live Wayland smoke test were rerun after these
changes. No new global architectural issue was found within the reviewed paths.
Physical hotplug and long-duration/multi-driver behavior remain unverified.
