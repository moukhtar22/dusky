# Dusky Keylogger 2.0.0

Opt-in typing statistics for Linux 7.3+, Python 3.14.7+, systemd 262,
Wayland and Hyprland. Installation leaves the service disabled on a fresh
system. Collection starts only when you run the foreground daemon or
explicitly start/enable its system service. Reinstallation preserves an
existing service's enabled state.

The collector reads physical keyboard events through evdev 2.0+, classifies
presses, and stores individual events and US-layout character estimates in
SQLite WAL. The CLI and Rich dashboard read that history.

## Counting rules

- Count initial presses, including modifiers, navigation and function keys.
- Exclude releases, autorepeat, mouse/gamepad buttons, and most keys pressed
  while Ctrl or Super is held. Modifiers and Backspace remain counted.
- Track modifier/lock state per input device. Split keyboards exposing separate
  devices do not share held modifier state.
- Characters assume US QWERTY. Remaps, other layouts, Alt shortcuts, dead keys,
  compose/IME input, pasted text and application edits are not reconstructed.
- WPM estimates printable presses / 5 / active minutes; it is not a timed typing
  test. An active minute contains at least one counted press.
- Transcripts show Backspace as `⌫` and Delete as `⌦`, without undoing characters.
  They describe input history rather than the final contents of a document.

## Architecture

- Nonblocking evdev readers with read-only device opens and batched callbacks.
- One per-client event-type mask admits KEY/LED and implicit SYN events;
  other event types never enter that client's ring buffer.
- On `SYN_DROPPED`, discard through the next `SYN_REPORT` and hydrate held
  keys/LEDs. Lost presses cannot be recovered.
- inotify watches `/dev/input` for hotplug/permission changes, with a 30-second
  safety rescan.
- A dedicated SQLite thread receives a bounded queue, retries failed batches
  while idle, and stops consuming the queue during an outage.
- A per-database `keys.db.lock` permits one collector. Ownership lasts until
  its SQLite worker exits, including after a shutdown timeout; the lock file
  stays in place and needs no stale-file cleanup.
- Kernel epoch timestamps retain microseconds; calendar fields record local
  time at collection. Active-minute counts distinguish repeated DST minutes.
- WAL with `synchronous=NORMAL` favors throughput. Unflushed batches and recent
  commits can be lost on a process crash or power failure. Extended storage
  outages can overflow the 20,000-row producer buffer; truncation is logged.

## Install

```bash
python3 keylogger_installer.py           # install; do not start collection
python3 keylogger_installer.py --enable  # explicitly enable and start
python3 keylogger_installer.py --dry-run
python3 keylogger_installer.py --status
```

The installer adds missing `input` membership using `usermod -aG`, preserving
other groups. The system service obtains the group immediately. Foreground
use needs a fresh login after membership changes; `dusky daemon` also adds
missing membership and reports when a fresh login is needed.

The venv uses the installed system Python, without downloading another
interpreter. `--offline` uses cached uv packages/build requirements, or pip
local sources (`PIP_FIND_LINKS`); provision those dependencies on the ISO.
An empty cache cannot satisfy an offline install. Existing installed files
must be reinstalled to apply changes to the systemd service template.

## Usage

```bash
dusky daemon
dusky stats --period week
dusky stats --period today --json
dusky dashboard
dusky status
dusky devices
dusky events --limit 40
dusky text --period today
dusky export --format markdown --out ~/typing.md
# Synthetic records only; prefer a separate testing directory:
dusky seed --days 7 --data-dir /tmp/dusky-demo
```

Default data: `~/.config/dusky/settings/keylogger/data/keys.db`.
Default config: `~/.config/dusky/settings/keylogger/config.json`.
`DUSKY_KEYLOGGER_CONFIG` selects a different config. Data paths use
`DUSKY_KEYLOGGER_DATA_DIR` > config `data_dir` > default, with `~`, environment
variables and paths relative to your home supported.

The service permits writes inside the default config tree. For a custom data
path outside it, create the directory and add it to the unit's `ReadWritePaths`
with `sudo systemctl edit dusky_keylogger`, then restart. Explicit environment
overrides in existing units still take precedence over JSON settings.

`persistent_enabled=false` discards new presses after daemon restart. It does
not collect new text into a separate ephemeral store. Existing history remains
available for exports. `ephemeral_enabled=false` skips automatic transcript
exports; explicit `--out` still exports. Dashboard previews read existing
history independently of this export toggle.

Exports default to `/tmp/dusky-typed-<period>-<date>-<uid>.[txt|md]` (mode 0600).
A custom export directory is only ephemeral if the system clears it. Metadata
goes to stderr; stdout contains the transcript exactly. UID suffixes prevent
collisions between users. Existing directory permissions are preserved.

Legacy config is copied to the canonical config path on first load. Legacy
`~/.local/share/dusky-keylogger/` data is detected by installer status, but is
not automatically moved. Stop all collectors before copying or deleting a
SQLite database; copy it only after its WAL has been checkpointed, or use
SQLite's backup interface. The TUI purge stops the system service and leaves
it stopped; stop any foreground daemon yourself before using purge.
Purge refuses deletion while an updated foreground collector owns the database.
Previously installed collectors without this lock still need manual shutdown.

Individual event rows retain typed characters, including password input.
Exporting to `/tmp` does not remove those characters from the persistent DB.

## Verification

```bash
python -m pytest tests
DUSKY_TEST_UINPUT=1 python -m pytest tests/test_live_input.py
```

The optional live test creates and exclusively grabs its own virtual keyboard;
it does not read physical keyboards. See [AUDIT.md](AUDIT.md) for the findings,
benchmark conditions, and remaining limitations.
