# Engine: `systemd`

- **Class:** `SystemdEngine` — `engines/systemd.py`
- **Engine types:** `systemd`
- **Default target:** (none — set TARGET_FILE; engine reports `/etc/systemd/system` but no file is ever read or written)

## Target format

File-less engine. It executes `systemctl` directly: `systemctl enable --now <unit>` / `systemctl disable --now <unit>` for user scope, and `sudo -n systemctl …` for system scope. The schema `TARGET_FILE` value is cosmetic.

## Scope / key mapping

- `scope` must be exactly `"user"` (→ `systemctl --user`) or `"system"` (→ `sudo -n systemctl`). Anything else is treated as system scope.
- `key` = the full unit name including suffix, e.g. `bluetooth.service`, `update_checker.timer`. Schema must only reference units that exist.
- State key format: `user/<unit>` and `system/<unit>`; value `"true"` if the unit is **active** (`systemctl is-active` / `list-units --state=active`), `"false"` otherwise.

## Types & value handling

- `type_` must be `bool` (the engine ignores `item_type` entirely).
- `"true"` → enable; any other serialized value (`"false"`) → disable. `--now` is always used, so enabling also starts and disabling also stops.
- `ConfigItem.serialize` converts Python `True`/`False` to `"true"`/`"false"` before the engine sees them — so `default=True`/`default=False` work directly.
- Units missing from state (not installed, or `load_state` failed) are simply absent: the UI falls back to the schema default. No `__DELETE__`/`unset`/`nil` semantics — bool toggles only.

## Quirks

- `load_state` scans `systemctl list-unit-files --type=service,timer` (installed) and `list-units --type=service,timer --state=active` (active) per scope; a unit that is enabled-but-stopped is reported `"false"`. There is no separate "enabled" bit.
- System-scope writes go through `sudo -n` (never interactive). If stderr contains `password is required`, `sudo:`, `polkit`, or `terminal is required`, the engine returns `(False, "AUTH_REQUIRED", …)` and the TUI prompts for a password. Writes have a 15 s timeout; batch writes 20 s.
- `write_batch` groups changes into at most 4 transactions (user/system × enable/disable); any auth failure marks the whole batch `AUTH_REQUIRED`.
- The UI may call `load_state_for_units(...)` (targeted `systemctl is-active` batch) for deferred tabs instead of `load_state()`; both return the same `user/<unit>`/`system/<unit>` shape.

## Example items

```python
ConfigItem(label="Night Light", key="hyprsunset.service", scope="user",
           type_="bool", default=False,
           extended_help="**Unit:** `hyprsunset.service`\n**Scope:** User\n\nBlue light filter."),
ConfigItem(label="SSH Server", key="sshd.service", scope="system",
           type_="bool", default=False,
           extended_help="**Unit:** `sshd.service`\n**Scope:** System\n\nOpenSSH daemon."),
ConfigItem(label="Update Checker", key="update_checker.timer", scope="user",
           type_="bool", default=True, group="Timers"),
```