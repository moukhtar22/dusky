# Engine: `matugen`

- **Class:** `MatugenEngine` — `engines/matugen.py`
- **Engine types:** `matugen, matugen_toml`
- **Default target:** `~/.config/matugen/config.toml` (expanded + resolved).

## Target format

Matugen's `config.toml`, containing `[templates.<name>]` blocks. The engine toggles whole blocks between active and commented-out by adding/removing exactly one outer `# ` or `#` prefix per line:

```toml
[templates.gtk3]
target = "gtk3"
template = "..."
# post_hook = '''...'''

# [templates.gtk4]
# target = "gtk4"
```

## Scope / key mapping

- `scope="DEFAULT"`; the scope string is ignored by the writer.
- `key` = the template name as it appears after `[templates.` (letters, digits, `_`, `.`, `-`; optional quoting), e.g. `gtk3`, `waybar`, `master_dump`. Keys may be written as bare `gtk3` or `DEFAULT/gtk3` (the part after the last `/` is used).
- `bool`: `true` uncomments the block header and every line in the block; `false` comments out every non-blank line (`# ` prefix).
- `load_state()` emits both the bare key and `DEFAULT/<key>` copies; commented headers are `False`, active ones `True`.

## Types & value handling

- Boolean or string values; truthy strings are `true`, `1`, `yes`, `on`, `t`, `y` (case-insensitive).
- If a key is not present in the file, it is silently skipped (nothing is appended) — schemas should only list templates that already exist in the file.
- If the block is already in the requested state, it is left untouched (protects inner comments).
- If nothing changed, the engine returns `True, "No modifications required."`.

## Quirks

- Block boundaries are found by scanning for the next section header (with blank-line lookahead so a blank line + header also terminates the block).
- Multiline strings are tracked with triple single quotes (`'''`) and triple double quotes (`"""`) so blocks containing them are not cut short or corrupted.
- Commenting skips blank lines; uncommenting strips only one outer `#` / `# ` prefix, so internal block comments survive.
- Concurrency guard: if the file's mtime changed after load, the write is refused with `File <name> was modified externally. Reload required.` — the schema UI must re-load state first.
- If the target file does not exist, `load_state()` returns `{}` and any write fails (`Target configuration file ... does not exist.`).
- Writes are atomic (temp file + `os.replace`) and preserve the original file permissions.

## Example items

```python
ConfigItem(label="GTK3 Theme", key="gtk3", scope="DEFAULT", type_="bool",
           default=True, group="Templates"),
ConfigItem(label="Waybar Template", key="waybar", scope="DEFAULT", type_="bool",
           default=False, group="Templates"),
ConfigItem(label="Master Dump", key="master_dump", scope="DEFAULT", type_="bool",
           default=True, group="Templates"),
```