# Engine: `json`

- **Class:** `JsonEngine` — `engines/json_engine.py`
- **Engine types:** `json`
- **Default target:** any JSON/JSONC file (set `TARGET_FILE`; e.g. Waybar
  configs, app configs with comments)

## Target format

Standard JSON, or JSONC with `//` line comments, `/* */` block comments, and
trailing commas (stripped on read).

```jsonc
{
  "display": {
    "border": { "width": 2, "color": "#a8c8ff" },
    "refresh_rate": 60
  }
}
```

## Scope / key mapping

- `scope` = dotted path to the parent object (`"display"`, `"display/border"` —
  both `/` and `.` separators are accepted).
- `key` = property name (may itself contain dots, treated as nesting).
- Deeply nested objects are traversed/created automatically.

## Types & value handling

- `bool` / `int` / `float` are coerced to native JSON types.
- Strings that look like JSON arrays/objects (`[...]`, `{...}`) are parsed as
  structured values.
- `None` / `"nil"` **removes** the key from the object.

## Quirks

- Comments are stripped on read and **not preserved on write** (the file is
  re-serialized with `indent=4`) — prefer this engine for JSON/JSONC files
  where comment preservation isn't critical; the Waybar engine handles theme
  switching without touching JSONC internals.
- Atomic tmpfile writes with permission preservation.

## Example items

```python
ConfigItem(label="Border Width", key="width", scope="display.border",
           type_="int", default=2, min_val=0, max_val=20, step=1, group="Border"),
ConfigItem(label="Border Color", key="color", scope="display.border",
           type_="color", default="#a8c8ff", group="Border"),
```
