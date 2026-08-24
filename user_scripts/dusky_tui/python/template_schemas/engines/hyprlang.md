# Engine: `hyprlang`

- **Class:** `HyprlangEngine` — `engines/hyprlang.py`
- **Engine types:** `hyprlang`
- **Default target:** any Hyprland-ecosystem config (set `TARGET_FILE`):
  `hyprland.conf`, `hypridle.conf`, `hyprpaper.conf`, `hyprlock.conf`

## Target format

C-style braces, `$variables`, inline `category:key = value`, and nested blocks.

```conf
$mainMod = SUPER

general {
    gaps_in = 5
    border_size = 2
}

decoration:rounding = 10

device[trackpad] {
    sensitivity = 0.5
}

listener {
    timeout = 300
    onetime = true
}
```

## Scope / key mapping

- `scope="DEFAULT"` → root assignments and `$variables` (`key="$mainMod"`).
- `scope=<block name>` → keys inside a block (`general` → `key="gaps_in"`).
- Inline `category:key = value` at root → `scope="category"`, `key="key"`.
- Duplicate blocks are indexed: `listener:1`, `listener:2`, … — use the
  indexed scope for every block after the first (state stores both the plain
  name for the first occurrence and the indexed name for all).
- Special blocks like `device[trackpad] {` use the written block name as
  scope (bracket form only — the block-open parser does **not** recognize the
  colon form `device:trackpad {`, so such blocks pass through untouched and
  their inner keys fall back to `DEFAULT` scope).

## Types & value handling

- Values are written verbatim: `bool` → `true`/`false`, ints/floats raw,
  strings as-is.
- `"__DELETE__"` removes the assignment line (comments/whitespace preserved).
- Comments (`#`) and escaped hashes (`##`) are preserved.

## Quirks

- After a successful write the engine auto-reloads: restarts `hypridle.service`
  for hypridle files, `hyprctl hyprpaper reload` for hyprpaper, and
  `hyprctl reload` for everything else.
- New keys are inserted just above the block's closing brace; missing blocks
  are created at EOF.

## Example items

```python
ConfigItem(label="Inner Gaps", key="gaps_in", scope="general", type_="int",
           default=5, min_val=0, max_val=50, step=1, group="Layout"),
ConfigItem(label="Rounding", key="rounding", scope="decoration", type_="int",
           default=10, min_val=0, max_val=30, step=1, group="Decor"),
ConfigItem(label="Timeout", key="timeout", scope="listener:1", type_="int",
           default=300, min_val=1, max_val=86400, step=1, group="Idle"),
```
