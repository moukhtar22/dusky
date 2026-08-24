# Engine: `waybar`

- **Class:** `WaybarEngine` — `engines/waybar_engine.py`
- **Engine types:** `waybar`
- **Default target:** `~/.config/waybar` (a DIRECTORY, not a file — `target_path` returns the config root so the UI file-watcher watches the whole folder)

## Target format

`~/.config/waybar/` contains one subdirectory per theme; a theme is any
subdir containing a `config.jsonc`. The "active" theme is applied by
symlinking `<theme>/config.jsonc` → `~/.config/waybar/config.jsonc` and
`<theme>/style.css` → `~/.config/waybar/style.css` (style symlink removed if
the theme has no `style.css`), then restarting waybar. The engine never edits
JSONC module internals — the only file mutation is the first top-level
`"position": "..."` string in the target theme's `config.jsonc` (invert
action). A small JSON state file at
`~/.config/dusky/settings/waybar/.dusky_waybar_state.json` records the last
applied theme (`active_theme_name`, `active_theme_index`).

## Scope / key mapping

Scope is **completely ignored** on writes — dispatch matches on `key` only.
Use `scope="DEFAULT"` for everything.

| key | effective type | semantics |
|---|---|---|
| `waybar` / `active_theme_number` | int | 1-based theme number; both keys are aliases, apply theme #N |
| `active_theme_name` | string | theme directory name; a numeric string is also accepted as 1-based number |
| `active_theme_index` | int | 0-based index |
| `toggle_forward` | bool trigger | next theme (wraps) — acts only when value is `"true"` |
| `toggle_backward` | bool trigger | previous theme (wraps) — acts only when value is `"true"` |
| `action_invert_pos` | bool trigger | flips `"position"` top↔bottom / left↔right (FIRST occurrence only) in the target theme's `config.jsonc`, then restarts |
| `action_heal_state` | bool trigger | re-applies the theme recorded in the state file (repairs broken/renamed symlinks) |

`load_state` publishes `active_theme_index`, `active_theme_name`,
`active_theme_number`, `waybar` and the two action keys as `False`, plus a
`DEFAULT/`-prefixed copy of each — the action keys are always `False` so push
buttons render "Apply" instead of "Active".

## Types & value handling

- `item_type` is ignored; `new_value` is coerced with `str(val).lower()`.
- Bool triggers act only on `"true"`; any other value is a silent no-op
  (still returns success).
- `active_theme_number`/`waybar`/`active_theme_index` require a parseable
  int; out-of-range → `(False, msg, "")`. `active_theme_name` that matches no
  theme dir and is not numeric → `(False, "Theme '...' not found.", "")`.
- `action_invert_pos` on a theme whose `config.jsonc` has no `"position"`
  key → `(False, "Position key not found in target config.jsonc", "")`.
- No `__DELETE__`/`nil` sentinels; every write is an immediate apply.
- Unknown keys: `(True, "", "")` — no changes, no restart.
- `write_value` delegates to `write_batch`, which re-runs `load_state()` first
  and uses the cached `active_theme_index` (clamped to 0 when the symlink is
  broken — prevents accidental edits to the last theme in the folder).

## Quirks

- Themes are discovered by globbing `*/config.jsonc` under the config root
  (sorted). If no themes exist, every write fails with
  `(False, "No valid themes found in ~/.config/waybar/", "")`.
- `load_state` resolves the active theme from the symlink; the state file is
  consulted ONLY when the symlink doesn't match a known theme dir. It never
  applies symlinks by itself.
- Applying a theme restarts waybar: non-blocking `flock` on
  `$XDG_RUNTIME_DIR/dusky_waybar_restart.lock` (5 retries), skips if another
  process already moved the symlink, SIGTERMs all same-uid waybar PIDs (polled
  up to 150 ms), SIGKILLs stragglers, relaunches `dusky-run waybar` (fallback
  `waybar`) in a new session (survives terminal close), then `os.utime`s the
  config root to force the UI file-watcher to reload.
- Symlink swap is atomic (`.tmp_link` + `os.replace`); state file writes are
  atomic too (`.tmp` + `os.replace`).
- `toggle_forward`/`toggle_backward` are NOT published by `load_state` —
  they only exist as write keys.

## Example items

```python
ConfigItem(label="Active Theme", key="waybar", scope="DEFAULT", type_="int",
           default=1, min_val=1, max_val=10, step=1, group="Theme"),
ConfigItem(label="Next Theme", key="toggle_forward", scope="DEFAULT", type_="bool",
           default=False, options=["trigger"], group="Theme"),
ConfigItem(label="Invert Position", key="action_invert_pos", scope="DEFAULT",
           type_="bool", default=False, options=["trigger"], group="Actions"),
ConfigItem(label="Heal State", key="action_heal_state", scope="DEFAULT",
           type_="bool", default=False, options=["trigger"], group="Actions"),
```