# Engine: `hyprlock`

- **Class:** `HyprlockEngine` — `engines/hyprlock.py`
- **Engine types:** `hyprlock`
- **Default target:** `~/.config/hypr/hyprlock.conf`

## Target format

`~/.config/hypr/hyprlock.conf` contains a `source = ...` directive pointing to the active theme inside `~/.config/hypr/hyprlock_themes/<theme_folder>/hyprlock.conf`.

```conf
source = ~/.config/hypr/hyprlock_themes/006_stacked_clock/hyprlock.conf
```

A small JSON state file at `~/.config/dusky/settings/hyprlock/.dusky_hyprlock_state.json` records the last applied theme (`active_theme_folder`, `active_theme_name`, `active_theme_index`).

## Scope / key mapping

Scope is ignored on writes — dispatch matches on `key` only. Use `scope="DEFAULT"` for everything.

| key | effective type | semantics |
|---|---|---|
| `hyprlock` / `active_theme_number` | int | 1-based theme number; apply theme #N |
| `active_theme_folder` | string | theme folder name (e.g. `006_stacked_clock`) |
| `active_theme_name` | string | theme display name from `theme.json` or folder name |
| `active_theme_index` | int | 0-based index |
| `toggle_forward` | bool trigger | next theme chronologically (wraps) |
| `toggle_backward` | bool trigger | previous theme chronologically (wraps) |
| `action_heal_state` | bool trigger | re-applies the theme recorded in the state file |
| `action_test_lock` | bool trigger | launches `hyprlock` to preview the active lockscreen |

`load_state` publishes `active_theme_index`, `active_theme_folder`, `active_theme_name`, `active_theme_number`, `hyprlock`, `source`, plus a `DEFAULT/`-prefixed copy of each. Action keys are initialized to `False` so push buttons render "Apply" instead of "Active".

## Types & value handling

- `item_type` is coerced appropriately; bool triggers act on `"true"`, `"1"`, or `"yes"`.
- `active_theme_number`/`hyprlock`/`active_theme_index` require parseable ints in bounds.
- `active_theme_folder`/`active_theme_name`/`theme` matches against folder names, display names from `theme.json`, or 1-based numeric strings.
- Mutations use atomic file writes (`tempfile.NamedTemporaryFile` + `os.replace`), preserving existing non-source lines and comments in `hyprlock.conf`.
- Zero hardcoded usernames: all paths are resolved dynamically via user home and XDG environment.

## Example items

```python
ConfigItem(label="Active Theme", key="hyprlock", scope="DEFAULT", type_="int",
           default=1, min_val=1, max_val=10, step=1, group="Themes"),
ConfigItem(label="Next Theme", key="toggle_forward", scope="DEFAULT", type_="bool",
           default=False, options=["trigger"], group="Navigation"),
ConfigItem(label="Test Lock Screen", key="action_test_lock", scope="DEFAULT",
           type_="bool", default=False, options=["trigger"], group="Actions"),
ConfigItem(label="Heal Configuration", key="action_heal_state", scope="DEFAULT",
           type_="bool", default=False, options=["trigger"], group="Actions"),
```
