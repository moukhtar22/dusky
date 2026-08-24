# Engine: `bridged_ini`

- **Class:** `BridgedIniEngine` — `engines/bridged_ini.py`
- **Engine types:** `bridged_ini`
- **Default target:** inherits `IniConfigEngine`'s constructor default `/etc/pacman.conf` — always set `TARGET_FILE` (examples: `/etc/systemd/logind.conf`, `/etc/ssh/sshd_config`)

## Target format

Identical to `ini`, except the engine **also reads commented-out lines** so
system defaults that ship commented out (e.g. `#NAutoVTs=6`) are shown as real
values instead of `[Missing]`.

```ini
[Login]
#NAutoVTs=6
#ReserveVT=6
HandlePowerKey=poweroff
```

## Scope / key mapping

Identical to `ini` — see [ini.md](./ini.md):

- `scope` = `[section]` name, or `"DEFAULT"` for root keys.
- `key` = INI key; valueless commented flags read as `True`.

## State precedence (important)

Active (uncommented) keys **always supersede** commented defaults. If both
`NAutoVTs=6` and `#NAutoVTs=6` exist, only the active value is reported.

## Quirks

- Writes behave exactly like `ini`: enabling a commented key uncomments it
  (the engine writes the active line, replacing the dormant one's value).
- This engine exists specifically so a TUI shows the file's *defaults* without
  striking them through — pick it for configs whose shipped defaults are
  commented out.

## Example items

```python
ConfigItem(label="Auto VT Allocation", key="NAutoVTs", scope="Login", type_="int",
           default=6, min_val=1, max_val=16, step=1, group="Terminal"),
ConfigItem(label="Reserve VT", key="ReserveVT", scope="Login", type_="int",
           default=6, min_val=0, max_val=16, step=1, group="Terminal"),
```
