# Engine: `autostart`

- **Class:** `AutostartLuaEngine` — `engines/autostart_engine.py`
- **Engine types:** `autostart`
- **Default target:** inherits the `lua` engine's constructor default `~/Documents/hyprland.lua` — always set `TARGET_FILE` (real schema: `~/.config/hypr/misc/autostart.lua`)

## What it does

Bridges the standard `lua` AST engine for `hl.config` tables, and additionally
manages `hl.exec_cmd(...)` autostart lines inside the
`hl.on("hyprland.start", function() ... end)` block by **commenting /
uncommenting** them (`-- ` prefix).

## Scope / key mapping

- Autostart entries: `scope="autostart"`, `key` = one of the **fixed catalog**
  names below. Each is a `bool` (`True` = present & uncommented).
- Any other scope/key falls through to the underlying `lua` engine.

## Fixed key catalog (`autostart/<name>`)

Interface & background: `awww_daemon`, `waybar`, `waybar_timer`, `nm_applet`,
`gnome_keyring`, `xhost_root`, `hypridle`, `layout_notify`,
`audio_visualizer`, `wayclick`, `hyprpm_reload`.

Clipboard: `cliphist_text`, `cliphist_image`, `cliphist_db_text`,
`cliphist_db_image`, `clip_persist`.

Environment: `systemd_env`, `dbus_env`, `session_target`, `shutdown_target`,
`choom_oom`.

Glance dashboards: `glance_cpu`, `glance_cpu_power`, `glance_ram`,
`glance_ram_temp`, `glance_zram`, `glance_temp`, `glance_battery`,
`glance_battery_percent`, `glance_battery_watts`, `glance_battery_time`,
`glance_gpu_power`, `glance_gpu_usage`, `glance_gpu_mem`, `glance_network`,
`glance_uptime`, `glance_workspace`, `glance_clock`, `glance_clock_short`,
`glance_disk`, `glance_disk_read`, `glance_disk_write`, `glance_disk_temp`,
`glance_stopwatch`, `glance_timer`, `glance_hud`, `glance_world_ny`,
`glance_world_tokyo`, `glance_world_london`.

> The catalog lives in `AUTOSTART_DEFAULTS` in `engines/autostart_engine.py`
> with the canonical command string for each entry. Only these names are
> writable; unknown keys are silently skipped.

## Types & value handling

- `bool` only: `true` → uncomment (or insert the canonical `hl.exec_cmd`
  line just before `end)` of the start block); `false` → comment out.
- If no start block exists, the engine appends a new
  `hl.on("hyprland.start", ...)` block.

## Example items

```python
ConfigItem(label="Waybar", key="waybar", scope="autostart", type_="bool",
           default=True, group="Interface"),
ConfigItem(label="Cliphist Text", key="cliphist_text", scope="autostart",
           type_="bool", default=False, group="Clipboard"),
```
