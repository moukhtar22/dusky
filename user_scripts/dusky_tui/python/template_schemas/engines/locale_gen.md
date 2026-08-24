# Engine: `locale_gen`

- **Class:** `LocaleGenEngine` — `engines/locale_gen.py`
- **Engine types:** `locale_gen`
- **Default target:** `/etc/locale.gen`

## Target format

glibc locale list; lines are toggled by commenting/uncommenting:

```
#en_US.UTF-8 UTF-8
en_GB.UTF-8 UTF-8
```

## Scope / key mapping

- `scope="DEFAULT"`.
- `key` = the locale code, either the short form (`en_US.UTF-8`) or the full
  two-token form (`en_US.UTF-8 UTF-8`). `bool`: `true` uncomments, `false`
  comments out. New locales are appended if missing.

## Action keys

Two ways to invoke these (they mix file toggles and systemd commands in one
batch; the engine routes a key to the action path when `itype == "action"`, the
key starts with `action_`, or the key is `ntp_sync`/`rtc_local`):

| key | style | command |
|---|---|---|
| `action_locale_gen` | `action` | `default="locale-gen"` → runs `locale-gen` |
| `ntp_sync` | `action` (shell) **or** `bool` | action: `default="timedatectl set-ntp true"`; bool: engine runs `timedatectl set-ntp true|false` |
| `action_set_timezone` | `action` (interactive) **or** `string`/`picker` | action: fzf pipeline in `default`; string/picker: engine runs `timedatectl set-timezone <value>` |
| `action_set_lang` | `action` (interactive) **or** `string`/`picker` | action: fzf pipeline in `default`; string/picker: engine runs `localectl set-locale LANG=<value>` |
| `action_set_keymap` | `action` (interactive) **or** `string`/`picker` | action: fzf pipeline in `default`; string/picker: engine runs `localectl set-keymap <value>` |

> The engine handler also recognizes bare `set_timezone` / `set_lang` /
> `set_keymap` names, but the write path **never routes them** (no `action_`
> prefix) — always use the `action_`-prefixed keys.
>
> Reference schemas (`tui_system_region.py`) use `type_="action"` with
> interactive `fzf`-based defaults, e.g.
> `default="tz=$(timedatectl list-timezones | fzf --prompt='Select System Timezone > ') && [ -n \"$tz\" ] && timedatectl set-timezone \"$tz\""`.

## Quirks

- Requires root (`/etc/locale.gen` + systemd commands). Set `REQUIRE_ROOT = True`
  or rely on the `AUTH_REQUIRED` flow.
- `default="nil"` is the serialized-`None` sentinel, not a shell command — if
  an `action` item is clicked with that default, the TUI executes the literal
  command `nil`. Use `default="locale-gen"` (or `":"` for a no-op label row).

## Example items

```python
# Generated per locale in the base file:
ConfigItem(label="en_US.UTF-8", key="en_US.UTF-8", scope="DEFAULT",
           type_="bool", default=True, group="Locales"),
ConfigItem(label="Compile Locales", key="action_locale_gen", scope="DEFAULT",
           type_="action", default="locale-gen", group="Actions"),
```
