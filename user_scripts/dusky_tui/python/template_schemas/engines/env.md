# Engine: `env`

- **Class:** `ShellEnvEngine` — `engines/environment_variables.py`
- **Engine types:** `env`
- **Default target:** `/etc/locale.conf` — also used for `/etc/vconsole.conf`,
  `/etc/environment`, `~/.config/uwsm/env`

## Target format

Strict `KEY=VALUE` lines (no spaces around `=`), optional `export` prefix,
optional inline comments:

```
LANG=en_US.UTF-8
export WAYLAND_DISPLAY=wayland-1
KEYMAP=us
```

## Scope / key mapping

- `scope` is always `"DEFAULT"`.
- `key` = the variable name. `export ` prefix and inline comments are
  preserved automatically; duplicates are indexed with `:N`.

## Types & value handling

- Values are auto-quoted only when they contain shell metacharacters or
  spaces; existing quote style (`"` vs `'`) is preserved.
- `bool` → lowercase `true`/`false`.
- `"__DELETE__"` / `"nil"` **comments the line out** (context preserved).

## Quirks

- Critical for boot files: never writes spaces around `=` (a boot-breaking
  mistake this engine prevents).
- Sudo/pkexec-safe UID/GID inheritance; atomic tmpfile writes with
  `sudo -n tee` fallback → `AUTH_REQUIRED`.

## Example items

```python
ConfigItem(label="Language", key="LANG", scope="DEFAULT", type_="string",
           default="en_US.UTF-8", group="Locale"),
ConfigItem(label="Keymap", key="KEYMAP", scope="DEFAULT", type_="string",
           default="us", group="Console"),
```
