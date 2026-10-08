#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEMD POWER MANAGER
===============================================================================
Target: /etc/systemd/logind.conf.d/99-power.conf
Engine: systemd_power (Modern Arch Linux, Kernel 7.2+, systemd 257+)
Manages system power keys, lid behavior, idle policies, inhibitor overrides,
and session lifecycle via systemd-logind drop-ins.
===============================================================================
"""

import sys
from pathlib import Path

# Robustly locate Dusky TUI root independent of user/root context
_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[1] / "dusky_tui"
if not _DUSKY_TUI_ROOT.exists():
    _DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING & METADATA
# =============================================================================
ENGINE_TYPE = "systemd_power"
TARGET_FILE = "/etc/systemd/logind.conf.d/99-power.conf"
APP_TITLE = "Dusky Power Manager"
REQUIRE_ROOT = True

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"

# Informative bottom banner explaining Suspend vs Sleep on relevant tabs
TAB_NOTICES = {
    0: {
        "level": "info",
        "message": "**Suspend vs Sleep**: `suspend` is immediate Suspend-to-RAM (fast 1–2s resume). `sleep` executes your dynamic `SleepOperation` policy (suspend-then-hibernate with automatic fallback).",
        "position": "bottom",
    },
    1: {
        "level": "info",
        "message": "**Suspend vs Sleep**: `suspend` is immediate Suspend-to-RAM (fast 1–2s resume). `sleep` executes your dynamic `SleepOperation` policy (suspend-then-hibernate with automatic fallback).",
        "position": "bottom",
    },
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = {
    0: "Power Keys",
    1: "Lid & Idle",
    2: "Inhibitors",
    3: "Session",
    4: "Profiles",
}

# Standard actions supported by systemd-logind Handle*Key and HandleLidSwitch
KEY_ACTIONS = [
    "poweroff",
    "reboot",
    "suspend",
    "hibernate",
    "hybrid-sleep",
    "suspend-then-hibernate",
    "sleep",
    "lock",
    "ignore",
    "halt",
    "kexec",
    "factory-reset",
]

KEY_ACTION_HINTS = [
    "Power off the system immediately",
    "Reboot the operating system",
    "Suspend directly to RAM (fast 1-2s resume)",
    "Hibernate system to disk/swap (0W power draw)",
    "Suspend to RAM and hibernate to swap",
    "Suspend first, hibernate after timeout",
    "Dynamic policy: runs configured SleepOperation",
    "Lock all active user graphical sessions",
    "Ignore event; do nothing",
    "Halt the machine hardware",
    "Reboot via kexec kernel jump",
    "Trigger system factory reset",
]

LID_ACTIONS = [
    "suspend",
    "ignore",
    "lock",
    "poweroff",
    "reboot",
    "hibernate",
    "hybrid-sleep",
    "suspend-then-hibernate",
    "sleep",
]

LID_ACTION_HINTS = [
    "Suspend directly to RAM (fast 1-2s resume)",
    "Ignore event; keep running with lid closed",
    "Lock active graphical user sessions",
    "Power off the system cleanly",
    "Reboot the operating system",
    "Hibernate system to disk (swap)",
    "Suspend to RAM and hibernate to swap",
    "Suspend first, hibernate after timeout",
    "Dynamic policy: runs configured SleepOperation",
]

IDLE_ACTIONS = [
    "ignore",
    "suspend",
    "lock",
    "poweroff",
    "reboot",
    "hibernate",
    "hybrid-sleep",
    "suspend-then-hibernate",
    "sleep",
]

IDLE_TIMEOUTS = [
    "5min",
    "10min",
    "15min",
    "20min",
    "30min",
    "45min",
    "1h",
    "2h",
    "infinity",
]

HOLDOFF_TIMEOUTS = [
    "0s",
    "5s",
    "10s",
    "15s",
    "30s",
    "45s",
    "60s",
]

# =============================================================================
# 4. PROFILE PAYLOADS
# =============================================================================
PROFILE_CLAMSHELL = {
    "Login.HandleLidSwitch": "ignore",
    "Login.HandleLidSwitchExternalPower": "ignore",
    "Login.HandleLidSwitchDocked": "ignore",
    "Login.IdleAction": "ignore",
    "Login.LidSwitchIgnoreInhibited": "no",
}

PROFILE_BATTERY_SAVER = {
    "Login.HandleLidSwitch": "suspend",
    "Login.HandleLidSwitchExternalPower": "suspend",
    "Login.IdleAction": "suspend",
    "Login.IdleActionSec": "15min",
    "Login.LidSwitchIgnoreInhibited": "yes",
}

PROFILE_WORKSTATION_ALWAYS_ON = {
    "Login.HandlePowerKey": "poweroff",
    "Login.HandlePowerKeyLongPress": "ignore",
    "Login.HandleSuspendKey": "ignore",
    "Login.HandleHibernateKey": "ignore",
    "Login.HandleLidSwitch": "ignore",
    "Login.HandleLidSwitchExternalPower": "ignore",
    "Login.HandleLidSwitchDocked": "ignore",
    "Login.IdleAction": "ignore",
    "Login.KillUserProcesses": "no",
}

# =============================================================================
# 5. CONFIGURATION SCHEMA
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: POWER & HARDWARE KEYS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Power Key",
            key="HandlePowerKey",
            scope="Login",
            type_="cycle",
            default="poweroff",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Hardware Keys",
            extended_help=(
                "**Power Key Action**\n\n"
                "Configures the action taken when the physical power key is pressed.\n\n"
                "- `poweroff`: Shuts down the machine cleanly.\n"
                "- `suspend`: Enters Suspend-to-RAM immediately (fast 1–2s resume, RAM powered).\n"
                "- `sleep`: Dynamic policy — runs your configured `SleepOperation` (e.g. suspend-then-hibernate with fallback).\n"
                "- `lock`: Screen-locks all active user sessions.\n"
                "- `ignore`: Disables logind response to the power button."
            ),
        ),
        ConfigItem(
            label="Power Key (Long Press)",
            key="HandlePowerKeyLongPress",
            scope="Login",
            type_="cycle",
            default="ignore",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Hardware Keys",
            extended_help=(
                "**Power Key Long Press Action**\n\n"
                "Action triggered when the power button is held down for approximately 4 seconds.\n"
                "Note: Hardware forced shutdown (holding ~10s) is handled by motherboard firmware."
            ),
        ),
        ConfigItem(
            label="Reboot Key",
            key="HandleRebootKey",
            scope="Login",
            type_="cycle",
            default="reboot",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Hardware Keys",
            extended_help=(
                "**Reboot Key Action**\n\n"
                "Action triggered when the dedicated hardware reboot button is pressed."
            ),
        ),
        ConfigItem(
            label="Reboot Key (Long Press)",
            key="HandleRebootKeyLongPress",
            scope="Login",
            type_="cycle",
            default="poweroff",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Hardware Keys",
            extended_help=(
                "**Reboot Key Long Press Action**\n\n"
                "Action taken when the physical reboot button is pressed and held."
            ),
        ),
        ConfigItem(
            label="Suspend Key",
            key="HandleSuspendKey",
            scope="Login",
            type_="cycle",
            default="suspend",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Sleep Keys",
            extended_help=(
                "**Suspend / Sleep Key Action**\n\n"
                "Action taken when the keyboard sleep/moon button is pressed."
            ),
        ),
        ConfigItem(
            label="Suspend Key (Long Press)",
            key="HandleSuspendKeyLongPress",
            scope="Login",
            type_="cycle",
            default="hibernate",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Sleep Keys",
            extended_help=(
                "**Suspend Key Long Press Action**\n\n"
                "Action taken when the sleep button is pressed and held down."
            ),
        ),
        ConfigItem(
            label="Hibernate Key",
            key="HandleHibernateKey",
            scope="Login",
            type_="cycle",
            default="hibernate",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Sleep Keys",
            extended_help=(
                "**Hibernate Key Action**\n\n"
                "Action taken when the keyboard hibernate button is pressed."
            ),
        ),
        ConfigItem(
            label="Hibernate Key (Long Press)",
            key="HandleHibernateKeyLongPress",
            scope="Login",
            type_="cycle",
            default="ignore",
            options=KEY_ACTIONS,
            hints=KEY_ACTION_HINTS,
            group="Sleep Keys",
            extended_help=(
                "**Hibernate Key Long Press Action**\n\n"
                "Action taken when the hibernate button is held down."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: LID & IDLE BEHAVIOR
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Lid Switch (Battery)",
            key="HandleLidSwitch",
            scope="Login",
            type_="cycle",
            default="suspend",
            options=LID_ACTIONS,
            hints=LID_ACTION_HINTS,
            group="Laptop Lid",
            extended_help=(
                "**Laptop Lid Switch (Battery Power)**\n\n"
                "Action taken when the laptop lid is closed while running on battery power.\n\n"
                "- `suspend`: Enters Suspend-to-RAM immediately (fast 1–2s resume, RAM powered).\n"
                "- `sleep`: Dynamic policy — executes your configured `SleepOperation` (e.g. suspend-then-hibernate with fallback).\n"
                "- `ignore`: Keeps system running with lid closed (clamshell mode)."
            ),
        ),
        ConfigItem(
            label="Lid Switch (AC Power)",
            key="HandleLidSwitchExternalPower",
            scope="Login",
            type_="cycle",
            default="suspend",
            options=LID_ACTIONS,
            hints=LID_ACTION_HINTS,
            group="Laptop Lid",
            extended_help=(
                "**Laptop Lid Switch (External AC Power)**\n\n"
                "Action taken when the laptop lid is closed while plugged into AC/charger.\n\n"
                "- `suspend`: Direct Suspend-to-RAM.\n"
                "- `sleep`: Dynamic SleepOperation policy.\n"
                "- `ignore`: Prevents laptop from sleeping when plugged in."
            ),
        ),
        ConfigItem(
            label="Lid Switch (Docked)",
            key="HandleLidSwitchDocked",
            scope="Login",
            type_="cycle",
            default="ignore",
            options=LID_ACTIONS,
            hints=LID_ACTION_HINTS,
            group="Laptop Lid",
            extended_help=(
                "**Laptop Lid Switch (Docked / Multi-Monitor)**\n\n"
                "Action taken when the lid is closed while plugged into a docking station "
                "or connected to external monitors (clamshell mode)."
            ),
        ),
        ConfigItem(
            label="Lid Holdoff Timeout",
            key="HoldoffTimeoutSec",
            scope="Login",
            type_="cycle",
            default="30s",
            options=HOLDOFF_TIMEOUTS,
            group="Laptop Lid",
            extended_help=(
                "**Lid Switch Holdoff Timeout**\n\n"
                "Suppresses lid switch events for this period immediately following system boot "
                "or resume from suspend, preventing accidental re-suspension when opening/closing."
            ),
        ),
        ConfigItem(
            label="Idle Action",
            key="IdleAction",
            scope="Login",
            type_="cycle",
            default="ignore",
            options=IDLE_ACTIONS,
            group="System Idle",
            extended_help=(
                "**System Idle Action**\n\n"
                "Configures the action taken when all active user sessions report idle "
                "and no idle inhibitor locks are active."
            ),
        ),
        ConfigItem(
            label="Idle Timeout",
            key="IdleActionSec",
            scope="Login",
            type_="cycle",
            default="30min",
            options=IDLE_TIMEOUTS,
            group="System Idle",
            extended_help=(
                "**Idle Action Delay**\n\n"
                "Delay duration after which `IdleAction` triggers once the system becomes idle."
            ),
        ),
        ConfigItem(
            label="Default Sleep Operation",
            key="SleepOperation",
            scope="Login",
            type_="cycle",
            default="suspend-then-hibernate suspend",
            options=[
                "suspend-then-hibernate suspend",
                "suspend",
                "hibernate",
                "hybrid-sleep",
                "suspend-then-hibernate",
            ],
            group="System Sleep",
            extended_help=(
                "**Default Sleep Operation**\n\n"
                "Specifies the sleep operation executed when the generic 'sleep' action is triggered. "
                "Defaults to attempting suspend-then-hibernate and falling back to suspend."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: INHIBITOR LOCK OVERRIDES
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Ignore Lid Inhibitors",
            key="LidSwitchIgnoreInhibited",
            scope="Login",
            type_="cycle",
            default="yes",
            options=["yes", "no"],
            group="Inhibitor Overrides",
            extended_help=(
                "**Ignore Lid Switch Inhibitors**\n\n"
                "When 'yes', closing the laptop lid triggers the configured action regardless "
                "of whether an application (e.g. video player, browser) holds an inhibitor lock."
            ),
        ),
        ConfigItem(
            label="Ignore Power Key Inhibitors",
            key="PowerKeyIgnoreInhibited",
            scope="Login",
            type_="cycle",
            default="no",
            options=["no", "yes"],
            group="Inhibitor Overrides",
            extended_help=(
                "**Ignore Power Key Inhibitors**\n\n"
                "When 'yes', pressing the power button triggers the configured action immediately, "
                "even if an application holds an inhibitor lock."
            ),
        ),
        ConfigItem(
            label="Ignore Suspend Key Inhibitors",
            key="SuspendKeyIgnoreInhibited",
            scope="Login",
            type_="cycle",
            default="no",
            options=["no", "yes"],
            group="Inhibitor Overrides",
            extended_help=(
                "**Ignore Suspend Key Inhibitors**\n\n"
                "When 'yes', keyboard suspend key executes immediately, overriding desktop inhibitors."
            ),
        ),
        ConfigItem(
            label="Ignore Hibernate Inhibitors",
            key="HibernateKeyIgnoreInhibited",
            scope="Login",
            type_="cycle",
            default="no",
            options=["no", "yes"],
            group="Inhibitor Overrides",
            extended_help=(
                "**Ignore Hibernate Key Inhibitors**\n\n"
                "When 'yes', hibernate button executes ignoring all application inhibitor locks."
            ),
        ),
        ConfigItem(
            label="Ignore Reboot Inhibitors",
            key="RebootKeyIgnoreInhibited",
            scope="Login",
            type_="cycle",
            default="no",
            options=["no", "yes"],
            group="Inhibitor Overrides",
            extended_help=(
                "**Ignore Reboot Key Inhibitors**\n\n"
                "When 'yes', reboot key triggers reboot immediately without waiting for applications."
            ),
        ),
        ConfigItem(
            label="Max Inhibit Delay (Seconds)",
            key="InhibitDelayMaxSec",
            scope="Login",
            type_="int",
            default=5,
            min_val=0,
            max_val=60,
            step=1,
            group="Inhibitor Timeouts",
            extended_help=(
                "**Maximum Inhibit Delay**\n\n"
                "Specifies the maximum time (seconds) a delay-inhibitor lock can hold off a shutdown "
                "or suspend request before logind ignores it and proceeds."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: SESSION & VIRTUAL TERMINALS
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Kill User Processes",
            key="KillUserProcesses",
            scope="Login",
            type_="cycle",
            default="no",
            options=["no", "yes"],
            group="Session Lifecycle",
            extended_help=(
                "**Kill User Processes on Logout**\n\n"
                "Configures whether all background processes belonging to a user are killed "
                "when the user logs out. Defaults to 'no' on modern systems to allow tmux/screen/user services."
            ),
        ),
        ConfigItem(
            label="Kill Exclude Users",
            key="KillExcludeUsers",
            scope="Login",
            type_="string",
            default="root",
            group="Session Lifecycle",
            extended_help=(
                "**Users Spared from KillUserProcesses**\n\n"
                "Space-separated list of usernames whose background processes are never killed on logout."
            ),
        ),
        ConfigItem(
            label="User Service Stop Delay",
            key="UserStopDelaySec",
            scope="Login",
            type_="cycle",
            default="10s",
            options=["0s", "5s", "10s", "30s", "1min", "infinity"],
            group="Session Lifecycle",
            extended_help=(
                "**Per-User Service Stop Delay**\n\n"
                "Specifies how long to keep the per-user service manager (`user@.service`) alive "
                "after full logout. Accelerates rapid logout/login cycles."
            ),
        ),
        ConfigItem(
            label="Stop Idle Session Timeout",
            key="StopIdleSessionSec",
            scope="Login",
            type_="cycle",
            default="infinity",
            options=["infinity", "15min", "30min", "1h", "2h"],
            group="Session Lifecycle",
            extended_help=(
                "**Terminate Idle Sessions Timeout**\n\n"
                "Automatically terminates user sessions that have been idle for longer than this duration."
            ),
        ),
        ConfigItem(
            label="Reserve VT Number",
            key="ReserveVT",
            scope="Login",
            type_="int",
            default=6,
            min_val=0,
            max_val=16,
            step=1,
            group="Virtual Terminals",
            extended_help=(
                "**Reserved Emergency Virtual Terminal**\n\n"
                "Specifies which virtual terminal (e.g. VT6) is unconditionally reserved for `autovt@.service`."
            ),
        ),
        ConfigItem(
            label="Auto VT Count",
            key="NAutoVTs",
            scope="Login",
            type_="int",
            default=6,
            min_val=1,
            max_val=16,
            step=1,
            group="Virtual Terminals",
            extended_help=(
                "**Automatic Virtual Terminals Count**\n\n"
                "Specifies how many virtual terminals (VTs) are dynamically allocated by default."
            ),
        ),
        ConfigItem(
            label="Remove IPC on Logout",
            key="RemoveIPC",
            scope="Login",
            type_="cycle",
            default="yes",
            options=["yes", "no"],
            group="Virtual Terminals",
            extended_help=(
                "**Remove IPC Objects on Logout**\n\n"
                "Controls whether System V and POSIX IPC shared memory/semaphores are removed when a user logs out."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: PROFILES & TOOLS
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="System Defaults",
            key="preset_factory_defaults",
            scope="Login",
            type_="preset",
            default=None,
            preset_payload={"__ALL_DEFAULTS__": True},
            confirm_message="Reset all power management configurations to system defaults?",
            group="Profiles",
            extended_help=(
                "**Restore Upstream Defaults**\n\n"
                "Reverts all power keys, lid switch, idle, and session configurations to default system settings."
            ),
        ),
        ConfigItem(
            label="Laptop Clamshell (Docked)",
            key="preset_clamshell",
            scope="Login",
            type_="preset",
            default=None,
            preset_payload=PROFILE_CLAMSHELL,
            confirm_message="Apply Laptop Clamshell profile (Lid close ignored while docked / charging)?",
            group="Profiles",
            extended_help=(
                "**Laptop Clamshell Mode**\n\n"
                "Prevents laptop from sleeping when the lid is closed while connected to AC power or external monitors."
            ),
        ),
        ConfigItem(
            label="Mobile Battery Saver",
            key="preset_battery_saver",
            scope="Login",
            type_="preset",
            default=None,
            preset_payload=PROFILE_BATTERY_SAVER,
            confirm_message="Apply Mobile Battery Saver profile (Aggressive suspend on lid close & 15m idle)?",
            group="Profiles",
            extended_help=(
                "**Mobile Battery Saver Profile**\n\n"
                "Suspends immediately on lid close (even if inhibited) and suspends after 15 minutes of idle time."
            ),
        ),
        ConfigItem(
            label="Workstation Always-On",
            key="preset_workstation_always_on",
            scope="Login",
            type_="preset",
            default=None,
            preset_payload=PROFILE_WORKSTATION_ALWAYS_ON,
            confirm_message="Apply Workstation Always-On profile (Sleep and lid close actions ignored)?",
            group="Profiles",
            extended_help=(
                "**Workstation Always-On Profile**\n\n"
                "Ensures the system never sleeps or shuts down accidentally due to lid close or keyboard sleep keys."
            ),
        ),
        ConfigItem(
            label="View Active Inhibitors",
            key="action_view_inhibitors",
            scope="DEFAULT",
            type_="action",
            default="systemd-inhibit --list",
            force_interactive=True,
            group="Diagnostics",
            extended_help=(
                "**List Active System Inhibitors**\n\n"
                "Runs `systemd-inhibit --list` to display active applications blocking sleep or shutdown."
            ),
        ),
        ConfigItem(
            label="Reload Logind Daemon",
            key="action_reload_logind",
            scope="DEFAULT",
            type_="action",
            default="systemctl reload systemd-logind.service",
            confirm_message="Reload systemd-logind configuration now?",
            popup_message="systemd-logind configuration reloaded.",
            group="Diagnostics",
            extended_help=(
                "**Live Daemon Reload**\n\n"
                "Signals systemd-logind to immediately re-read all drop-ins and base configuration files."
            ),
        ),
        ConfigItem(
            label="View Logind Status",
            key="action_status_logind",
            scope="DEFAULT",
            type_="action",
            default="systemctl status systemd-logind.service --no-pager",
            force_interactive=True,
            group="Diagnostics",
            extended_help=(
                "**Inspect Daemon Status**\n\n"
                "Runs `systemctl status systemd-logind.service` to inspect daemon health, uptime, and recent journal entries."
            ),
        ),
    ],
}

# =============================================================================
# 6. DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    import sys
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(
            subprocess.run(
                [sys.executable, str(main_router), str(script_path)] + sys.argv[1:]
            ).returncode
        )
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
