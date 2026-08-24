# Engine: `pkg_throttle`

- **Class:** `PkgThrottleEngine` — `engines/pkg_throttle.py`
- **Engine types:** `pkg_throttle`
- **Default target:** the resolved RAPL `package-0` powercap domain dir (e.g. `/sys/class/powercap/intel-rapl:0`), or `/sys/class/powercap` if no domain is found. `TARGET_FILE` is ignored (the constructor takes `config_path` but never uses it).

## Target format

Not a config file: the engine reads/writes sysfs powercap files directly under the package-0 RAPL domain:

- `constraint_0_power_limit_uw`, `constraint_1_power_limit_uw`, `constraint_2_power_limit_uw`
- `constraint_0_time_window_us`, `constraint_1_time_window_us`
- `energy_uj` / `max_energy_range_uj` (telemetry only)

The domain is auto-detected: the first non-mmio `*rapl*` dir whose `name` is `package-0` and which has `constraint_0_power_limit_uw`.

## Scope / key mapping

All keys are `scope="DEFAULT"`. Exact key catalog:

| key | type | unit | sysfs file |
|---|---|---|---|
| `pl1` | int | watts | `constraint_0_power_limit_uw` |
| `pl2` | int | watts | `constraint_1_power_limit_uw` |
| `pl4` | int | watts | `constraint_2_power_limit_uw` |
| `pl1_time` | float | seconds | `constraint_0_time_window_us` |
| `pl2_time` | float | seconds | `constraint_1_time_window_us` |

`load_state()` emits both the bare key and a `DEFAULT/<key>` copy for every key the hardware exposes (a missing sysfs file means the key is simply absent from state).

## Types & value handling

- Values are multiplied by `1_000_000` on write (watts → µW, seconds → µs); division by `1_000_000` on load.
- Every write is verified by read-back. Success means: exact match, or (for `pl1`/`pl2`/`pl4`) within 5% of the requested value, or (for time keys) any successful write — the engine reports the quantized result (`quantized to 28.00s` / `quantized to 45 W`).
- Hardware rejection returns `Rejected by hardware! Locked at: 45 W` (false).
- Unknown key → `Unknown key: <key>`; unparseable value → `Invalid value: ...`; sysfs write failure → `Failed to write parameter (unsupported or permission denied)`; no domain → `No active RAPL domain found`.

## Quirks

- Requires root (`SUDO_USER` is honored when resolving the user home).
- A shared state file `/dev/shm/dusky_rapl_state.json` (flock-guarded, atomic) records the boot-time limits and a `modified` flag.
- Every successful write calls `save_persistent_state()` → `~/.config/dusky/settings/dusky_pkg_power`; `restore_state()` re-applies those limits (e.g. after reboot).
- `get_telemetry()` returns a live package-wattage bar (used by a CUSTOM_VIEWS tab).
- No `AUTH_REQUIRED` sentinel is used — failures are plain messages.

## Example items

```python
ConfigItem(label="Long-Term Limit (PL1)", key="pl1", scope="DEFAULT", type_="int",
           default=45, min_val=5, max_val=250, step=1, group="Power"),
ConfigItem(label="Short-Term Boost (PL2)", key="pl2", scope="DEFAULT", type_="int",
           default=80, min_val=5, max_val=250, step=1, group="Power"),
ConfigItem(label="PL1 Time Window", key="pl1_time", scope="DEFAULT", type_="float",
           default=28.0, min_val=0.1, max_val=60.0, step=0.1, group="Power"),
ConfigItem(label="PL2 Time Window", key="pl2_time", scope="DEFAULT", type_="float",
           default=2.0, min_val=0.1, max_val=60.0, step=0.1, group="Power"),
```