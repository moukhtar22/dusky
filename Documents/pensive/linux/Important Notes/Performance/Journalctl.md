# `journalctl`: Querying and Maintaining the systemd Journal

> [!note]
> `journalctl` is the primary interface for reading logs from `systemd-journald` on Arch Linux. It can read kernel logs, system service logs, and systemd-managed user-service logs from a single structured journal.
>
> A separate syslog daemon is **optional**, not required. However, not every application logs to the journal: some still write directly to plain files, remote logging backends, or custom locations.

> [!info]
> On modern desktop systems, especially those using systemd-managed user sessions, Wayland components, or UWSM-managed sessions, many relevant logs are in the **user journal**. Use `journalctl --user ...` in addition to the system journal.

> [!note]
> Reading the **system** journal requires root or membership in one of the groups granted journal read access, typically `systemd-journal`, `adm`, or `wheel`. Maintenance operations require root.

---

## Quick Reference

| Task | Command |
|---|---|
| Show current boot logs | `journalctl -b` |
| Jump to end of current boot in pager | `journalctl -b -e` |
| Last 200 lines from current boot | `journalctl -b -n 200` |
| Previous boot | `journalctl -b -1` |
| Follow new logs live | `journalctl -f` |
| Follow one service live | `journalctl -f -u sshd.service` |
| Kernel messages from previous boot | `journalctl -k -b -1` |
| Errors and worse from current boot | `journalctl -b -p err` |
| Logs from the last hour | `journalctl --since "1 hour ago"` |
| Regex search message text | `journalctl -g 'timeout|failed' -b` |
| Current user journal for this boot | `journalctl --user -b` |
| Show available boots | `journalctl --list-boots` |
| Show available invocations of a unit | `journalctl --list-invocations -u sshd.service` |
| Disk usage | `sudo journalctl --disk-usage` |
| Rotate and remove old archived logs | `sudo journalctl --rotate --vacuum-time=2weeks` |

---

## What the Journal Is

`systemd-journald` collects and indexes log records from multiple sources:

- the kernel
- system services
- user services managed by the systemd user manager
- unit `stdout`/`stderr`
- native journal clients
- optionally syslog-compatible clients

Unlike classic plain-text syslog files, the journal is:

- **structured**: records contain fields such as `_SYSTEMD_UNIT`, `_PID`, `PRIORITY`, `MESSAGE`
- **indexed**: field-based filtering is fast and precise
- **binary on disk**: use `journalctl` to read it
- **optionally persistent** across reboots

> [!tip]
> Prefer **structured filters** such as `-u`, `_PID=`, `SYSLOG_IDENTIFIER=`, `PRIORITY=`, and `--since` over piping large output to `grep`.

---

## Storage, Persistence, and Access

### Journal Storage Modes

`systemd-journald` stores logs according to `Storage=` in `journald.conf`.

| `Storage=` value | Behavior | Primary location |
|---|---|---|
| `auto` | Use persistent storage **only if** `/var/log/journal/` exists; otherwise use volatile storage | `/var/log/journal/` or `/run/log/journal/` |
| `persistent` | Keep logs across reboots; may use `/run/log/journal/` temporarily until `/var` is available | `/var/log/journal/` |
| `volatile` | Keep logs only until reboot | `/run/log/journal/` |
| `none` | Do not store journal data locally | none |

### Persistent vs Volatile Journals

- **Volatile journal**: `/run/log/journal/`
  - lost at reboot
- **Persistent journal**: `/var/log/journal/`
  - survives reboot

Under `Storage=auto`, if `/var/log/journal/` does **not** exist, journald uses `/run/log/journal/` and does **not** automatically create `/var/log/journal/`.

### Enable Persistent Logging

Preferred approach:

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald.service
sudo journalctl --flush
```

- `systemd-tmpfiles` creates the directory with the correct metadata expected by systemd.
- `journalctl --flush` moves logs from `/run/log/journal/` into `/var/log/journal/` once persistent storage is available.

Explicit configuration via a drop-in is cleaner for long-term systems:

```ini
# /etc/systemd/journald.conf.d/10-persistent.conf
[Journal]
Storage=persistent
```

Apply changes:

```bash
sudo systemctl restart systemd-journald.service
sudo journalctl --flush
```

> [!note]
> Rebooting also applies `Storage=` changes cleanly. `systemd-journal-flush.service` normally flushes runtime logs to `/var/log/journal/` during boot when persistent storage is enabled.

### Access Control

By default:

- every user can read their **own** per-user journal
- root can read everything
- members of `systemd-journal`, `adm`, and `wheel` can read the system journal and other users' journals

### Default Retention and Size Limits

For persistent journals, the default limits are dynamic. In practice, the important defaults are:

- `SystemMaxUse=`: roughly **10%** of the filesystem, capped at **4 GiB**
- `SystemKeepFree=`: roughly **15%** of the filesystem, capped at **4 GiB**

The effective cap is whichever limit is reached first.

A practical retention drop-in:

```ini
# /etc/systemd/journald.conf.d/20-retention.conf
[Journal]
Storage=persistent
SystemMaxUse=1G
SystemKeepFree=500M
MaxRetentionSec=1month
```

Inspect the effective journald configuration:

```bash
systemd-analyze cat-config systemd/journald.conf
```

Inspect journald's own logs for applied limits and storage behavior:

```bash
journalctl -b -u systemd-journald.service
```

---

## How `journalctl` Filtering Works

### Default Behavior

Without arguments:

```bash
journalctl
```

`journalctl` shows all journal entries accessible to the calling user, starting with the **oldest**.

This often includes:

- system journal entries
- kernel messages
- per-user journal entries the user is allowed to read

Use `--system` or `--user` to narrow the source explicitly.

### Source Selection

| Option | Meaning |
|---|---|
| `--system` | Only system services and kernel logs |
| `--user` | Only current user's journal |
| default | All journals accessible to the caller |

> [!note]
> Historical per-user journals across reboots are only useful when persistent journaling is enabled.

### Structured Match Semantics

A raw match is written as:

```bash
FIELD=VALUE
```

Rules:

- **different fields** are combined with logical **AND**
- **same field repeated** is combined with logical **OR**
- `+` between groups creates a logical **OR** between groups

Examples:

```bash
# All logs from sshd.service with PID 1234
journalctl _SYSTEMD_UNIT=sshd.service _PID=1234

# All logs from either sshd.service or dbus.service
journalctl _SYSTEMD_UNIT=sshd.service _SYSTEMD_UNIT=dbus.service

# (sshd.service AND PID 1234) OR (dbus.service)
journalctl _SYSTEMD_UNIT=sshd.service _PID=1234 + _SYSTEMD_UNIT=dbus.service
```

### Common Journal Fields

| Field | Meaning |
|---|---|
| `MESSAGE` | Human-readable log message |
| `PRIORITY` | Syslog severity, `0` to `7` |
| `SYSLOG_IDENTIFIER` | Tag/identifier such as `sudo`, `sshd`, `NetworkManager` |
| `_SYSTEMD_UNIT` | System unit name |
| `_SYSTEMD_USER_UNIT` | User unit name |
| `_PID` | Process ID |
| `_UID` | Numeric user ID |
| `_COMM` | Command name |
| `_EXE` | Canonical executable path |
| `_BOOT_ID` | Boot session ID |
| `_TRANSPORT` | Log transport, e.g. `kernel`, `stdout`, `syslog`, `journal` |
| `MESSAGE_ID` | Structured message identifier used by some components |

### Discover Available Fields and Values

List all field names currently present in the journal:

```bash
journalctl -N
```

List all values observed for a field:

```bash
journalctl -F _SYSTEMD_UNIT
journalctl -F SYSLOG_IDENTIFIER
journalctl -F _BOOT_ID
```

---

## Core Usage

## Viewing and Following Logs

| Command | Meaning |
|---|---|
| `journalctl` | Show all accessible logs, oldest first |
| `journalctl -r` | Reverse order, newest first |
| `journalctl -n 100` | Show the newest 100 entries |
| `journalctl -e` | Jump to the end in the pager |
| `journalctl -f` | Show recent entries and follow new ones |
| `journalctl --no-tail -f` | Show all matching entries, then follow |
| `journalctl --no-pager` | Disable the pager |

Examples:

```bash
# Most practical "what just happened?" starting point
journalctl -b -e

# Live tail the last 50 lines of a service and continue following
journalctl -n 50 -f -u NetworkManager.service
```

> [!note]
> `journalctl -f` implies a tail of recent entries first. If you need the full matching history before following, use `--no-tail -f`.

> [!note]
> `journalctl -e` implies `--pager-end` and, unless otherwise specified, effectively limits display to the current/latest boot with a bounded tail. It is intended for interactive viewing, not scripting.

---

## Filtering by Boot

| Command | Meaning |
|---|---|
| `journalctl -b` | Current boot |
| `journalctl -b -1` | Previous boot |
| `journalctl -b -2` | Two boots ago |
| `journalctl --list-boots` | List recorded boots |
| `journalctl -b <boot-id>` | Query a specific boot by ID |

Examples:

```bash
journalctl --list-boots
journalctl -b -1 -e
journalctl -b 6b7a7d3f8d8a4d42b2af2e0f3e0ad9d1
```

Boot offsets are subtle:

- `-b` on the local system means the current boot
- `-b -1` means the previous boot
- positive offsets count from the **oldest** boot in the selected journal set and are rarely used

---

## Filtering by Time

### Absolute Time

```bash
# Since a specific date/time
journalctl --since "2026-03-17 10:30:00"

# Between two timestamps
journalctl --since "2026-03-17 10:30:00" --until "2026-03-17 10:35:00"
```

### Relative Time

```bash
journalctl --since "1 hour ago"
journalctl --since "20 min ago"
journalctl --since "yesterday"
journalctl --since "-90min"
```

> [!warning]
> A date without a time means midnight at the start of that date.  
> For example, this does **not** mean “the full day”:
>
> ```bash
> journalctl --since "2026-03-17"
> ```
>
> To query one full day, bound it explicitly:
>
> ```bash
> journalctl --since "2026-03-17" --until "2026-03-18"
> ```

> [!tip]
> `-o short-full` prints timestamps in a format accepted directly by `--since` and `--until`.

---

## Filtering by Unit, User Unit, Process, Identifier, or Executable

### Units

| Command | Meaning |
|---|---|
| `journalctl -u sshd.service` | Logs for a system unit |
| `journalctl -u 'pipewire*'` | Logs for units matching a pattern |
| `journalctl --user -u waybar.service` | Logs for a user unit |
| `journalctl --user-unit=waybar.service` | Explicit user-unit form |

Examples:

```bash
journalctl -u bluetooth.service
journalctl -f -u NetworkManager.service
journalctl --user -b -u pipewire.service
```

> [!tip]
> Quote unit globs such as `'pipewire*'` so the shell does not expand them before `journalctl` sees them.

> [!note]
> `-u` is richer than a raw `_SYSTEMD_UNIT=...` match. It also includes related messages about the unit from systemd itself and relevant coredump records.

### Process, Identifier, and Executable Filters

```bash
# Specific PID
journalctl _PID=1

# Specific syslog identifier/tag
journalctl -t sudo

# Exclude a noisy identifier
journalctl -T dbus-daemon

# Specific executable path
journalctl /usr/bin/dbus-daemon

# Specific command name
journalctl _COMM=Hyprland
```

Path filtering has special semantics:

- executable binary path → `_EXE=...`
- executable script path → `_COMM=script-name`
- device node path → `_KERNEL_DEVICE=...` matches for the device and ancestors

---

## Filtering by Kernel Messages

| Command | Meaning |
|---|---|
| `journalctl -k` | Kernel messages for the current boot |
| `journalctl -k -b -1` | Kernel messages from previous boot |
| `journalctl -k -f` | Follow kernel messages live |

Examples:

```bash
journalctl -k
journalctl -k -b -1 -p warning
```

> [!note]
> `journalctl -k` implies the current boot unless you specify another boot explicitly.

> [!info]
> `journalctl -k` is usually better than [[Dmesg]] for historical kernel analysis because it is filterable and persistent. `dmesg` still reads the current kernel ring buffer directly and remains useful in some low-level scenarios.

---

## Filtering by Priority and Facility

### Priority

Priorities follow syslog severity numbering: **lower number = more severe**.

| Priority | Number | Meaning |
|---|---:|---|
| `emerg` | 0 | System unusable |
| `alert` | 1 | Immediate action required |
| `crit` | 2 | Critical condition |
| `err` | 3 | Error |
| `warning` | 4 | Warning |
| `notice` | 5 | Significant normal event |
| `info` | 6 | Informational |
| `debug` | 7 | Debug |

Examples:

```bash
# Error and more severe
journalctl -b -p err

# Warning through alert, inclusive
journalctl -b -p warning..alert

# Exact priority only
journalctl -b PRIORITY=4
# or
journalctl -b -p 4..4
```

> [!note]
> A single level such as `-p err` means “`err` **and all more severe levels**”, i.e. `0..3`.

### Facility

Preferred syntax:

```bash
journalctl --facility=authpriv
journalctl --facility=daemon,user
journalctl --facility=help
```

Raw field syntax also works:

```bash
journalctl SYSLOG_FACILITY=10
```

> [!note]
> `SYSLOG_FACILITY=` is only meaningful for syslog-style messages. Many native journal entries do **not** carry a facility. In practice, unit, identifier, boot, time, and priority filters are usually more reliable.

Common facility examples:

- `kern`
- `user`
- `daemon`
- `auth`
- `authpriv`
- `cron`
- `local0` … `local7`

---

## Regex Search in Message Text

Use the built-in regex filter:

```bash
journalctl -g 'timeout|failed'
journalctl -b -u sshd.service -g 'authentication|disconnect'
```

Facts about `--grep` / `-g`:

- it matches **only** the `MESSAGE=` field
- it uses **PCRE2** regular expressions
- if the pattern is all lowercase, matching is case-insensitive by default
- force behavior with `--case-sensitive=yes` or `--case-sensitive=no`

Examples:

```bash
journalctl -g 'segfault|coredump'
journalctl -g 'USB.*reset' --case-sensitive=yes
```

> [!tip]
> Piping to `grep` is still valid, but it searches the rendered text output, not the structured journal fields. Prefer `-g` for message text and field matches for metadata.

---

## Invocation-Based Debugging

When a unit restarts frequently, boot-level filtering is often too broad. Use **invocation** filtering.

List invocations of a unit:

```bash
journalctl --list-invocations -u sshd.service
```

Show the latest invocation:

```bash
journalctl -u sshd.service -I
```

Show the previous invocation:

```bash
journalctl -u sshd.service -I -1
```

This is often the cleanest way to inspect one restart/crash cycle of a service.

> [!tip]
> Prefer invocation filtering over raw PID filtering for systemd services that respawn, because PIDs change across restarts.

---

## User Journals

For systemd user services:

```bash
journalctl --user
journalctl --user -b
journalctl --user -u pipewire.service
journalctl --user -p warning
```

Useful workflow:

```bash
systemctl --user list-units --type=service
journalctl --user -b -u <unit>.service
```

> [!info]
> If Hyprland, your status bar, notification daemon, portal, or UWSM-managed session components are started as user units, their logs will usually be in the **user** journal, not the system journal.

---

## Offline and Alternate Sources

`journalctl` can inspect journals outside the running host.

| Command | Use case |
|---|---|
| `journalctl -D /mnt/var/log/journal -e` | Read a specific journal directory |
| `journalctl --root=/mnt -b -1` | Read journals from a mounted root filesystem |
| `journalctl --image=/path/to/disk.img -b` | Read journals from a disk image or block device |
| `journalctl -M mycontainer` | Read from a running local container |
| `journalctl -m` | Merge local and remote journals |

Examples:

```bash
journalctl --root=/mnt -b -1 -p err..alert
journalctl -D /mnt/var/log/journal -u systemd-journald.service
```

> [!warning]
> If the target system uses a separate `/var` filesystem, ensure it is mounted under the chosen root before using `--root=/mnt`. Otherwise, point `-D` directly at the real journal directory.

---

## Journal Namespaces

Some services may log into a **journal namespace** instead of the default journal.

Query a namespace:

```bash
journalctl --namespace=ssh
```

Query all namespaces, interleaved:

```bash
journalctl --namespace='*'
```

Query the default namespace plus one named namespace:

```bash
journalctl --namespace='+ssh'
```

> [!note]
> If expected logs are “missing”, a non-default namespace is one possible reason.

---

## Output Formats, Paging, and Automation

### Recommended Output Modes

| Mode | Use |
|---|---|
| `-o short-full` | Best general-purpose human-readable format; locale-independent timestamps |
| `-o short-iso-precise` | ISO 8601 with microseconds |
| `-o with-unit` | Good for templated units and unit-centric reading |
| `-o verbose` | Show all fields in each entry |
| `-o json` | Machine-readable, one JSON object per line |
| `-o json-pretty` | Human-readable JSON |
| `-o cat` | Message text only |

Examples:

```bash
journalctl -b -o short-full
journalctl -u sshd.service -o with-unit
journalctl -u sshd.service -o verbose
journalctl -u sshd.service -o json --no-pager
```

Restrict fields in structured output:

```bash
journalctl -u sshd.service \
  -o json \
  --output-fields=MESSAGE,PRIORITY,_PID,_SYSTEMD_UNIT
```

Other useful output switches:

```bash
journalctl --utc
journalctl -q
journalctl --truncate-newline
```

- `--utc`: print timestamps in UTC
- `-q`: suppress `-- Reboot --` and similar informational markers
- `--truncate-newline`: only show the first line of multiline messages

### Pager Behavior

By default, interactive output is paged through `less`. Long lines are often chopped because systemd passes `SYSTEMD_LESS=FRSXMK` by default.

One-off wrapped output:

```bash
SYSTEMD_LESS=FRXMK journalctl -b -u NetworkManager.service
```

Disable the pager entirely:

```bash
journalctl --no-pager
```

> [!tip]
> For scripts, CI, copy/paste, and support bundles, always use `--no-pager`.

### Script-Safe Usage

For automation:

- use `--no-pager`
- prefer `-o json`
- prefer `--utc`
- use `--cursor-file` for incremental reads
- do **not** parse the default human output format

Incremental read pattern:

```bash
#!/usr/bin/env bash
set -euo pipefail

cursor_file="${XDG_STATE_HOME:-$HOME/.local/state}/journal/example.cursor"
mkdir -p -- "${cursor_file%/*}"

journalctl \
  --no-pager \
  --utc \
  --output=json \
  --cursor-file="$cursor_file" \
  --unit=sshd.service \
  --priority=warning
```

This reads entries after the saved cursor and updates the cursor file to the newest processed entry.

---

## Maintenance and Integrity

### Disk Usage

```bash
sudo journalctl --disk-usage
```

### Rotate and Vacuum Archived Journals

```bash
# Rotate current active files, then vacuum old archived files
sudo journalctl --rotate --vacuum-size=1G
sudo journalctl --rotate --vacuum-time=2weeks
sudo journalctl --rotate --vacuum-files=10
```

> [!warning]
> Vacuuming permanently deletes archived journal files. Keep enough history for troubleshooting, auditing, and incident response.

Important behavior:

- `--vacuum-*` only removes **archived** journal files
- active files are not deleted until they are rotated
- combine `--rotate` with `--vacuum-*` for predictable results

### Sync vs Flush

These are commonly confused:

| Command | Meaning |
|---|---|
| `journalctl --sync` | Force unwritten journal data to the backing filesystem and wait for completion |
| `journalctl --flush` | Move runtime journal data from `/run/log/journal/` into `/var/log/journal/` when persistent storage is available |

### Verify Journal Integrity

```bash
sudo journalctl --verify
```

This checks journal file consistency. If Forward Secure Sealing (FSS) is configured, `--verify` can also validate authenticity with the matching verification key.

### Message Catalog Explanations

```bash
journalctl -x
journalctl --list-catalog
```

`-x` augments some messages with explanatory catalog text.

> [!warning]
> Do **not** use `-x` when attaching logs to bug reports or support requests unless specifically asked. It adds extra explanatory text that is not part of the original log stream.

---

## Troubleshooting Patterns

### 1. Previous Boot Failure

```bash
journalctl -b -1 -p err..alert -e
```

### 2. A Service Failed or Restarted Repeatedly

```bash
journalctl -u NetworkManager.service -I
journalctl --list-invocations -u NetworkManager.service
journalctl -u NetworkManager.service -b -p warning
```

Also inspect the unit:

```bash
systemctl status NetworkManager.service
systemctl cat NetworkManager.service
```

### 3. Kernel / Driver / Hardware Problems

```bash
journalctl -k -b -p warning
journalctl -k -b -1
journalctl -k -g 'i915|amdgpu|nvidia|usb|nvme'
```

### 4. Authentication or Authorization Problems

```bash
journalctl --facility=authpriv,auth -b
journalctl -t sudo -b
journalctl -u sshd.service -b
```

### 5. User Session / Wayland / UWSM Problems

```bash
systemctl --user list-units --type=service
journalctl --user -b -p warning
journalctl --user -u <relevant-user-unit>.service -e
```

If the compositor or session helper is not a user unit, search by command or identifier:

```bash
journalctl --user _COMM=Hyprland
journalctl --user -t xdg-desktop-portal
```

### 6. Logs Are Missing

Check these causes:

1. **Persistence is disabled**
   ```bash
   systemd-analyze cat-config systemd/journald.conf
   ```
2. **You do not have permission to read the system journal**
3. **The logs are in the user journal**
   ```bash
   journalctl --user -b
   ```
4. **The service logs to a non-default target**
   ```bash
   systemctl cat <unit>.service
   ```
5. **The service uses a journal namespace**
   ```bash
   journalctl --namespace='*' -b
   ```
6. **Messages were rate-limited**
   ```bash
   journalctl -u systemd-journald.service -b -g 'rate|suppressed'
   ```

### 7. Export Logs for Support or Archival

```bash
journalctl -b -u sshd.service -o short-full --no-pager > sshd-current-boot.log
journalctl -b -1 -o short-full --no-pager > previous-boot.log
```

For machine-readable export:

```bash
journalctl -b -o json --no-pager > current-boot.jsonl
```

---

## Related Tools

- [[Systemctl]] — manage and inspect units
- [[Dmesg]] — read the current kernel ring buffer directly
- [[Coredumpctl]] — inspect coredumps recorded by `systemd-coredump`
- `systemd-analyze cat-config` — inspect the effective configuration after drop-ins are applied
- `systemd-cat` — write test messages into the journal

Example test message:

```bash
systemd-cat -t demo echo "hello from journal"
journalctl -t demo -n 1
```

---

## References

- [`journalctl(1)`](https://man.archlinux.org/man/journalctl.1)
- [`systemd-journald.service(8)`](https://man.archlinux.org/man/systemd-journald.service.8)
- [`journald.conf(5)`](https://man.archlinux.org/man/journald.conf.5)
- [`systemd.journal-fields(7)`](https://man.archlinux.org/man/systemd.journal-fields.7)
- [`systemd.time(7)`](https://man.archlinux.org/man/systemd.time.7)
- [ArchWiki: Systemd/Journal](https://wiki.archlinux.org/title/Systemd/Journal)

--- 

## Summary

`journalctl` is most effective when used with **structured filters**:

- narrow by **boot**: `-b`
- narrow by **unit**: `-u`
- narrow by **time**: `--since`, `--until`
- narrow by **severity**: `-p`
- narrow by **message regex**: `-g`
- inspect **user services** with `--user`
- use **invocation filtering** for restart-heavy services
- use `-o short-full` for human output and `-o json` for automation

For persistent, reliable history on Arch, ensure journald is configured to store logs in `/var/log/journal/` and manage retention with `SystemMaxUse=`, `SystemKeepFree=`, and `MaxRetentionSec=`.
