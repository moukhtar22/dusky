# Engine: `systemd_power`

- **Class:** `SystemdPowerEngine` — `engines/systemd_power.py`
- **Engine types:** `systemd_power`, `systemd_logind`, `power_engine`
- **Default target:** `/etc/systemd/logind.conf.d/99-power.conf` (a systemd-logind drop-in)

## Target format

A single **managed** `[Login]` drop-in configuration for `systemd-logind`:

```ini
# Managed strictly by Dusky TUI - Systemd Power Manager
# Target: systemd-logind drop-in configuration
[Login]
HandlePowerKey=poweroff
HandleLidSwitch=suspend
HandleLidSwitchDocked=ignore
IdleActionSec=30min
```

## Scope / key mapping

- `scope="Login"` (or `"DEFAULT"`). Values are normalized under the `[Login]` section header.
- Fixed key catalog with compile-time defaults for systemd 257+ on Arch Linux:

| key | type | default | notes |
|---|---|---|---|
| `HandlePowerKey` | `cycle` | `poweroff` | Physical power button action |
| `HandlePowerKeyLongPress` | `cycle` | `ignore` | Power button 4s hold action |
| `HandleRebootKey` | `cycle` | `reboot` | Physical reboot button action |
| `HandleRebootKeyLongPress` | `cycle` | `poweroff` | Reboot button long press action |
| `HandleSuspendKey` | `cycle` | `suspend` | Suspend/Sleep key action |
| `HandleSuspendKeyLongPress` | `cycle` | `hibernate` | Suspend key long press action |
| `HandleHibernateKey` | `cycle` | `hibernate` | Hibernate key action |
| `HandleHibernateKeyLongPress` | `cycle` | `ignore` | Hibernate key long press action |
| `HandleLidSwitch` | `cycle` | `suspend` | Laptop lid closed (battery power) |
| `HandleLidSwitchExternalPower` | `cycle` | `suspend` | Laptop lid closed (on AC power) |
| `HandleLidSwitchDocked` | `cycle` | `ignore` | Laptop lid closed with external monitor |
| `HoldoffTimeoutSec` | `cycle` | `30s` | Lid event suppression after boot/resume |
| `IdleAction` | `cycle` | `ignore` | Action when system is idle |
| `IdleActionSec` | `cycle` | `30min` | Idle duration before IdleAction triggers |
| `SleepOperation` | `cycle` | `suspend-then-hibernate suspend` | Operation mode for 'sleep' action |
| `LidSwitchIgnoreInhibited` | `cycle` | `yes` | Ignore lid close inhibitor locks |
| `PowerKeyIgnoreInhibited` | `cycle` | `no` | Ignore power button inhibitors |
| `SuspendKeyIgnoreInhibited` | `cycle` | `no` | Ignore suspend button inhibitors |
| `HibernateKeyIgnoreInhibited` | `cycle` | `no` | Ignore hibernate button inhibitors |
| `RebootKeyIgnoreInhibited` | `cycle` | `no` | Ignore reboot button inhibitors |
| `InhibitDelayMaxSec` | `int` | `5` | Delay inhibitor max holdoff timeout |
| `KillUserProcesses` | `cycle` | `no` | Terminate user sessions on logout |
| `KillExcludeUsers` | `string` | `root` | Users spared from KillUserProcesses |
| `UserStopDelaySec` | `cycle` | `10s` | user@.service retention after logout |
| `ReserveVT` | `int` | `6` | Emergency autovt reserved VT number |
| `NAutoVTs` | `int` | `6` | Number of autovts allocated |
| `RemoveIPC` | `cycle` | `yes` | Remove user IPC objects on logout |
| `StopIdleSessionSec` | `cycle` | `infinity` | Terminate idle sessions timeout |

## Hierarchy & Virtualization

1. **Tier 1 (Base defaults):** Compile-time upstream defaults are automatically virtualized so items never render as `[Missing]`.
2. **Tier 2 (Base config):** Dormant and active keys from `/etc/systemd/logind.conf` are bridged into state.
3. **Tier 3 (Drop-in):** Active keys in `/etc/systemd/logind.conf.d/99-power.conf` take absolute priority.

## Quirks & Daemon Reload

- Writes are atomic (`tempfile` + `os.replace` / `sudo tee` fallback).
- After every successful commit, the engine reloads `systemd-logind` via `systemctl reload systemd-logind.service` (with fallback to `pkill -HUP -x systemd-logind`).
- `REQUIRE_ROOT = True` should be set on schemas targeting this engine to enable seamless sudo elevation and drop-in management.
