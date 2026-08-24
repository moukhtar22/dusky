# Engine: `cpu_core`

- **Class:** `CpuCoreEngine` — `engines/cpu_core.py`
- **Engine types:** `cpu_core`
- **Default target:** `/sys/devices/system/cpu` (fixed; `TARGET_FILE` is ignored — the constructor takes `config_path` but never uses it).

## Target format

Not a config file: per-CPU sysfs toggle files `/sys/devices/system/cpu/cpu<N>/online` containing `"1"` (online) or `"0"` (offline). Topology (P-core / E-core split) is auto-detected at construction from ACPI CPPC `highest_perf` (midpoint split when >1 distinct value) or, failing that, from `topology/core_type` and SMT sibling lists. Cores that were offline during detection are briefly brought online to read topology, then restored.

## Scope / key mapping

- `scope="DEFAULT"`, `key="cpu<N>"` where `<N>` is a real detected CPU id (e.g. `cpu0`, `cpu4`), `type_="bool"` (`true` = online, `false` = offline).
- `load_state()` emits both `cpu<N>` and `DEFAULT/cpu<N>` for every detected, non-locked core.
- Locked cores (the BSP — first CPU id — and any core without an `online` file) never appear in state and are rejected on write.

## Types & value handling

- Any value whose lowercase form is `true`/`1`/`yes` → online; anything else → offline.
- Key validation is strict: must start with `cpu` followed by digits, else `Invalid key: <key>`.
- Locked core → `CPU <N> is locked (BSP) and cannot be toggled`.
- Write failures map to `Failed to toggle CPU <N>: ...` where the detail is `Locked` (no online file), `Ignored` (write did not stick), or `Permission denied or locked`.

## Quirks

- Requires root (sysfs writes).
- State is persisted to `~/.config/dusky/settings/dusky_cores` on every successful toggle; `restore_state()` re-applies it (e.g. after reboot).
- `get_telemetry()` returns a live online-core-count/power bar (used by a CUSTOM_VIEWS tab).
- No `AUTH_REQUIRED` sentinel — failures are plain messages.
- Schema keys must match real CPU ids; a schema cannot know them statically, so list plausible ids (`cpu0`…`cpu<N-1>`) — unknown keys fail with `Invalid key`.

## Example items

```python
ConfigItem(label="Core 0 (BSP)", key="cpu0", scope="DEFAULT", type_="bool",
           default=True, group="P-Cores"),
ConfigItem(label="Core 4", key="cpu4", scope="DEFAULT", type_="bool",
           default=True, group="E-Cores"),
ConfigItem(label="Core 7", key="cpu7", scope="DEFAULT", type_="bool",
           default=False, group="E-Cores"),
```