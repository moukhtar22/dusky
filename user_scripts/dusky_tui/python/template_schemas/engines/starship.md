# Engine: `starship`

- **Class:** `StarshipEngine` — `engines/starship.py`
- **Engine types:** `starship`
- **Default target:** `~/.config/starship.toml` (expanded + resolved; if the path is a directory, `starship.toml` is appended)

## Target format

Whole-file `starship.toml` — the engine never edits keys in place. Applying a prompt atomically replaces the entire file content with a bundled preset.

Prompt sources (resolved by `_resolve_prompt`):

* `dusky` (alias) → `user_scripts/starship/presets/dusky.toml` (official default theme)
* `preset:<name>` → `user_scripts/starship/presets/<name>.toml`
* `custom:<name>` → `~/.config/dusky/settings/starship/prompts/<name>.toml` (user-made, untracked, survives updates)

State file `~/.config/dusky/settings/starship/.dusky_starship_state.json` stores `{"active_prompt": "<selector>", "custom_prompt_name": "<name>"}` and is updated on every successful apply (pattern shared with the `waybar` engine).

If `~/.config/starship.toml` is a symlink (e.g. managed by `matugen`), the atomic write replaces the symlink itself with a regular file — the original matugen-generated file elsewhere is left untouched and a note is returned: `"Replaced matugen-managed symlink with a static config."`

## Scope / key mapping

Scope is **completely ignored** on writes — dispatch matches on `key` only. Use `scope="DEFAULT"` for everything.

| key | effective type | semantics |
|---|---|---|
| `active_prompt` | string | Selector for the active prompt. Accepts `dusky`, `preset:<name>`, or `custom:<name>`. Unknown name → `(False, "Prompt '…' not found.", "")`. Empty / `nil` → no-op |
| `custom_prompt_name` | string | In-memory name for the next custom prompt to save; stored in the state file only (no file write) |
| `action_save_custom` | bool trigger | Saves the *current* `~/.config/starship.toml` content as `~/.config/dusky/settings/starship/prompts/<sanitized custom_prompt_name>.toml` and switches `active_prompt` to `custom:<name>`. Fails on empty name or missing config file. Acts only when value is `"true"` |

`load_state` publishes `active_prompt`, `custom_prompt_name`, and `action_save_custom` (always `False`) plus their `DEFAULT/`-prefixed copies. Active prompt is resolved by (in order): saved state file if it matches an existing preset and hash-matches the current file, otherwise the current file's content hash is matched against bundled + custom presets, otherwise `preset:dusky`.

## Types & value handling

* `item_type` is ignored; trigger acts only on `"true"` (case-insensitive `"true"`).
* `active_prompt` values are stripped and normalized (`_normalize_name`); bare `dusky` is treated as `preset:dusky` for display but stored as the original selector.
* No `__DELETE__`/`nil` handling beyond the empty check for `active_prompt`.
* Hash matching uses `blake2b` (digest 16) of the whole file content.

## Quirks

* Whole-file swap is atomic (`tempfile.NamedTemporaryFile` in `config_path.parent` + `os.fsync` + `os.replace`) and preserves `uid`/`gid`/`mode` of the previous file when possible.
* State file writes are also atomic (`.tmp` + `os.replace`).
* If both bundled and custom preset dirs are missing, `load_state` still returns `preset:dusky` as the active prompt even though no file exists — the first apply will fail until a preset is present.
* Custom prompts are sanitized via `re.compile(r"[^\w\- ]+")` → `_` and stripped of leading/trailing ` _`.

## Example items

```python
ConfigItem(label="Active Prompt", key="active_prompt", scope="DEFAULT", type_="string",
           default="preset:dusky", options=["preset:dusky", "preset:pastel", "custom:my-custom-prompt"],
           group="Prompt"),
ConfigItem(label="Custom Name", key="custom_prompt_name", scope="DEFAULT", type_="string",
           default="my-custom-prompt", group="Prompt"),
ConfigItem(label="Save Current As Custom", key="action_save_custom", scope="DEFAULT",
           type_="bool", default=False, options=["trigger"], group="Actions"),
```
