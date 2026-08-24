# Engine: `monitor`

- **Class:** `MonitorLuaEngine` — `engines/monitor_engine.py`
- **Engine types:** `monitor`
- **Default target:** `~/Documents/monitors.lua`

## What it does

Extends the `lua` AST engine for Hyprland 0.55+ monitor blocks
(`hl.monitor({ output = "eDP-1", ... })`). Queries live monitors via
`hyprctl -j monitors all`, injects virtual hardware state (so defaults never
show `[Missing]`), validates fractional scaling, and auto-creates missing
monitor/global blocks.

## Scope / key mapping

**Per-monitor** — `scope="monitor/<name>"` (e.g. `monitor/eDP-1`). The engine
maps UI names to AST names (including `desc:` identifiers) automatically.
Keys:

| key | type | notes |
|---|---|---|
| `output` | string | monitor identifier (AST id) |
| `disabled` | bool | |
| `mode` | string | `preferred` / `highres` / `highrr` / `WxH@Hz` |
| `position` | string | `auto` or `x,y` |
| `scale` | float | fractional scaling; coerced to a mathematically perfect divisor of the physical resolution |
| `transform` | int | 0–7 rotation/flip |
| `vrr` | int | 0 / 1 / 2 |
| `bitdepth` | int | 8 / 10 |
| `cm` | string | color management preset |
| `sdr_eotf` | string | |
| `sdrbrightness` | float | |
| `sdrsaturation` | float | |
| `mirror` | string | mirrored output or empty |
| `icc` | string | ICC profile path or empty |
| `reserved_area` | int | |

**Global render/power** — scopes `misc`, `debug`, `render` (deep-merged via
`hl.config`): `misc/vrr` (int), `debug/vfr` (bool), `render/cm_sdr_eotf`
(string), `render/cm_fs_passthrough` (bool), `render/cm_auto_hdr` (bool).

Additional read-only capability keys are virtualized per monitor
(`supports_wide_color`, `supports_hdr`, `sdr_min_luminance`,
`sdr_max_luminance`, `min_luminance`, `max_luminance`, `max_avg_luminance`).

## Quirks

- `scale` values are validated/fixed against live physical resolution; only
  `gcd(W,H)/k` values pass cleanly — don't hardcode arbitrary floats as
  defaults if you can avoid it.
- Missing monitor blocks and missing global scopes are auto-injected before
  writing so the AST mutator can find them.

## Example items

```python
ConfigItem(label="Scale", key="scale", scope="monitor/eDP-1", type_="float",
           default=1.0, min_val=0.5, max_val=3.0, step=0.25, group="Output"),
ConfigItem(label="Vrr", key="vrr", scope="misc", type_="int", default=0,
           options=[0, 1, 2], group="Global"),
```
