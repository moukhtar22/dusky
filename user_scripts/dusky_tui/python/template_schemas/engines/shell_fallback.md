# Engine: `shell_fallback`

- **Class:** `ShellFallbackEngine` — `engines/shell_fallback.py`
- **Engine types:** `shell_fallback`
- **Default target:** any shell env file containing fallback definitions (set
  `TARGET_FILE`)

## Target format

Bash "fallback" definitions:

```bash
readonly KEY="${KEY:-DEFAULT_VAL}"
readonly ENABLE_DEBUG="${ENABLE_DEBUG:-false}"
```

## Scope / key mapping

- `scope` is always `"DEFAULT"`.
- `key` = the variable name.
- State key: `DEFAULT/<key>` → the fallback value.

## Types & value handling

- `bool`: values are coerced to lowercase `true`/`false` according to the schema
  item type.
- Strings are written as a double-quoted default word inside the `:-` fallback
  slot, with shell metacharacters escaped. They round-trip as literal text.
- Numeric values are written inline. Leading whitespace and trailing comments
  are preserved.
- Values must be single-line text without NUL characters. Missing keys or scopes
  other than `DEFAULT` return an error before any file is changed.

## Quirks

- Atomic commits with permission/ownership preservation and nanosecond mtime
  TOCTOU guard.

## Example items

```python
ConfigItem(label="Debug", key="ENABLE_DEBUG", scope="DEFAULT", type_="bool",
           default=False, group="Runtime"),
ConfigItem(label="Cache Path", key="CACHE_PATH", scope="DEFAULT", type_="string",
           default="~/.cache/app", group="Runtime"),
```
