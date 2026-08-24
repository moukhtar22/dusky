# Engine: `toml`

- **Class:** `TomlEngine` — `engines/toml.py`
- **Engine types:** `toml` (alias: `toml_engine`)
- **Default target:** any standard TOML file (set `TARGET_FILE`; e.g.
  `~/.config/dusky/settings/dusky_keys/config.toml`, `starship.toml`)

## Target format

Standard TOML v1.0 with nested tables:

```toml
[display]
buffer_size = 100

[display.border]
width = 2
color = "#a8c8ff"
```

## Scope / key mapping

- `scope` = dotted table path (`"display"`, `"display.border"`; `/` accepted).
- `key` = the table key (may itself contain dots, treated as nesting).
- Deeply nested tables are created automatically on write.

## Types & value handling

- `bool` → `true`/`false`; `int`/`float` → native numbers; strings are JSON-
  escaped; lists/tuples and inline dicts are supported; TOML date/time objects
  are preserved (never stringified).
- `None` / `"nil"` / `"__DELETE__"` **removes** the key.

## Quirks

- **Anti-clobber:** refuses to write if the existing file has a TOML syntax
  error (never destroys a file with a typo).
- Keys containing spaces/special chars are quoted automatically.
- Output is regenerated (comments are not preserved).

## Example items

```python
ConfigItem(label="Buffer Size", key="buffer_size", scope="display", type_="int",
           default=100, min_val=1, max_val=10000, step=1, group="Display"),
ConfigItem(label="Border Width", key="width", scope="display.border", type_="int",
           default=2, min_val=0, max_val=20, step=1, group="Border"),
```
