# Dusky Keylogger 2.0.0

Always-on keystroke statistics daemon for Arch Linux (Kernel 7.1+, Python 3.14.6+).

Captures raw physical key presses from kernel `/dev/input/event*` via evdev
(bypassing Wayland/X11), classifies them, resolves US-layout characters,
and persists them to SQLite WAL. Analytics via CLI and a Rich live dashboard.

This is a statistics daemon, not a stealth logger. It runs as your user,
under systemd, with the data directory mode 0700.

## Architecture (v2)

- **evdev drain path**: `loop.add_reader(fd)` + `InputDevice.read()` batches.
  The event loop never blocks on disk.
- **SYN_DROPPED**: discard to next SYN_REPORT, then EVIOCGKEY + EVIOCGLED.
- **EVIOCSMASK**: this client only receives EV_KEY + EV_LED (+ EV_SYN).
- **inotify** on `/dev/input` for hot-plug (ATTRIB covers the udev ACL race).
- **Dedicated SQLite writer thread**. WAL + `query_only` readers.
- **Kernel timestamps** (`event.sec` / `event.usec`), not `time.time()`.

## Install (Arch)

```bash
python3 keylogger_installer.py --enable
# log out/in if you were just added to group input
systemctl status dusky_keylogger
```

## Usage

```bash
dusky daemon                  # foreground (systemd uses this)
dusky stats --period week
dusky stats --period today --json
dusky dashboard               # live Rich dashboard (matugen theme)
dusky status
dusky devices
dusky events --limit 40
dusky seed --days 7           # synthetic data, testing only
```

## Data

Default: `~/.config/dusky/settings/keylogger/data/keys.db`
Override: `DUSKY_KEYLOGGER_DATA_DIR` or `config.json` `data_dir`.
Legacy `~/.local/share/dusky-keylogger/` is detected by `keylogger_installer.py --status`.

Keystroke databases contain passwords you typed. Treat the file as secret.
