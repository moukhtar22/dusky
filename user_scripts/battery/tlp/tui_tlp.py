#!/usr/bin/env python3
"""TLP 1.11 configuration schema; saving and applying are separate operations."""

import subprocess
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "tlp"
TARGET_FILE = "/etc/tlp.conf"
REQUIRE_ROOT = True
APP_TITLE = "TLP Configurator"
DEFAULT_MODE = "manual"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"
TABS = ["General", "Processor", "Storage", "Graphics", "Radio", "USB", "PCIe", "Audio", "Battery", "Presets"]
GLOBAL_POPUP = {
    "title": "Editing TLP Settings",
    "message": (
        "Performance (PRF), Balanced (BAL), and Power Saver (SAV) are profiles, independent of the power source. "
        "AC and battery select profiles in General. 'nil' removes an override from /etc/tlp.conf: "
        "TLP defaults or drop-ins then apply. It does not reset hardware immediately. "
        "Save edits before Apply Saved Settings; applying also leaves TLP manual mode. "
        "Hardware-specific controls only work where supported. Use the diagnostic actions to check capabilities."
    ),
    "level": "info",
    "require_confirm": False,
    "cancel_quits": False,
}


def choices(pattern: str) -> list[str]:
    """Offer values supported by every readable device matching the sysfs path."""
    available = []
    for path in Path("/sys").glob(pattern):
        try:
            values = set(path.read_text().replace("[", "").replace("]", "").split())
        except OSError:
            continue
        if values:
            available.append(values)
    return sorted(set.intersection(*available)) if available else []


def setting(label: str, key: str, group: str, *, options: list[str] | None = None,
            help_: str = "", parent: str | None = None) -> ConfigItem:
    return ConfigItem(
        label=label, key=key, scope="DEFAULT", type_="picker" if options is not None else "string",
        default="nil", options=["nil", *options] if options is not None else [],
        group=group, parent_ref=parent, extended_help=help_ or None,
    )


def profiles(label: str, key: str, group: str, *, options: list[str] | None = None,
             help_: str = "") -> list[ConfigItem]:
    parent = f"menu_{key.lower()}"
    return [
        ConfigItem(label=label, key=parent, scope="DEFAULT", type_="menu", default=None,
                   is_parent=True, group=group, extended_help=help_ or None),
        *[setting(name, f"{key}_ON_{suffix}", group, options=options, parent=parent)
          for name, suffix in (("Performance", "PRF"), ("Balanced", "BAL"), ("Power Saver", "SAV"))],
    ]


def action(label: str, command: str, help_: str = "") -> ConfigItem:
    return ConfigItem(label=label, key=f"action_{command.replace(' ', '_').replace('-', '_')}",
                      scope="DEFAULT", type_="action",
                      default=(f'{command}; _tlp_status=$?; '
                               'printf "\\nPress Enter to return to TLP Configurator... "; '
                               'IFS= read -r _tlp_reply; exit "$_tlp_status"'),
                      group="Apply" if command == "tlp start" else "Diagnostics",
                      force_interactive=True, extended_help=help_ or None)


SCHEMA = {
    0: [
        action("Apply Saved Settings", "tlp start",
               "Save first. Applies the saved configuration and resumes automatic operation according to TLP_AUTO_SWITCH. "
               "Removing a setting does not necessarily undo a previously applied hardware value; a reboot may be needed."),
        action("Show TLP Status", "tlp-stat -s"),
        action("Show Effective Config", "tlp-stat -c"),
        action("Show Config Overrides", "tlp-stat --cdiff"),
        setting("Enable TLP", "TLP_ENABLE", "General", options=["0", "1"],
                help_="0 disables further TLP actions; it does not undo previously applied settings."),
        setting("Disable TLP Defaults", "TLP_DISABLE_DEFAULTS", "General", options=["0", "1"],
                help_="1 applies only explicit settings, except TLP's essential operation defaults."),
        setting("Warning Output", "TLP_WARN_LEVEL", "General", options=["0", "1", "2", "3"],
                help_="0: off; 1: background syslog; 2: terminal; 3: both."),
        setting("Message Colors", "TLP_MSG_COLORS", "General", help_="Four ANSI codes: error, warning, notice, success."),
        setting("Automatic Switching", "TLP_AUTO_SWITCH", "Switching", options=["0", "1", "2"],
                help_="0: disabled; 1: always switch on power-source changes; 2: smart, respecting a manually selected profile."),
        setting("Profile on AC", "TLP_PROFILE_AC", "Switching", options=["PRF", "BAL", "SAV"]),
        setting("Profile on Battery", "TLP_PROFILE_BAT", "Switching", options=["PRF", "BAL", "SAV"]),
        setting("Default Profile", "TLP_PROFILE_DEFAULT", "Switching", options=["PRF", "BAL", "SAV"],
                help_="Used with automatic switching disabled or when no power supply is detected."),
        setting("Ignore Supply Classes", "TLP_PS_IGNORE", "Switching", help_="Space-separated classes: AC, USB, BAT."),
        *profiles("Platform Profile", "PLATFORM_PROFILE", "Platform",
                  options=choices("firmware/acpi/platform_profile_choices"),
                  help_="Choices are read from firmware. Use nil for TLP's portable profile preferences. "
                  "Existing space-separated preference lists are valid and are tried in order."),
        *profiles("Suspend Mode", "MEM_SLEEP", "Suspend", options=choices("power/mem_sleep"),
                  help_="Supported modes are read from the kernel. nil leaves this unconfigured. "
                  "s2idle is software standby; deep is suspend to RAM. Check resume behavior on your hardware."),
    ],
    1: [
        action("Show CPU Capabilities", "tlp-stat -p"),
        *profiles("Scaling Driver Mode", "CPU_DRIVER_OPMODE", "Driver",
                  options=(["active", "passive", "guided"] if Path("/sys/devices/system/cpu/amd_pstate/status").exists()
                           else ["active", "passive"] if Path("/sys/devices/system/cpu/intel_pstate/status").exists() else []),
                  help_="Intel pstate supports active/passive; AMD pstate also supports guided. Other drivers do not support this setting. "
                  "Changing mode can change the available governors; reopen this TUI after applying a driver-mode change."),
        *profiles("Scaling Governor", "CPU_SCALING_GOVERNOR", "Governor",
                  options=choices("devices/system/cpu/cpufreq/policy*/scaling_available_governors"),
                  help_="Choices come from the active drivers. powersave in active pstate mode allows dynamic scaling; "
                  "powersave on generic drivers selects the lowest frequency. Use nil to leave the governor unconfigured."),
        *profiles("Energy Policy", "CPU_ENERGY_PERF_POLICY", "Policy",
                  options=["performance", "balance_performance", "default", "balance_power", "power"],
                  help_="EPP/EPB requires a supported CPU and driver. See CPU diagnostics for available preferences."),
        *profiles("Minimum Frequency (kHz)", "CPU_SCALING_MIN_FREQ", "Frequency",
                  help_="Use kHz, as reported in CPU diagnostics. For intel_pstate, prefer performance percentages."),
        *profiles("Maximum Frequency (kHz)", "CPU_SCALING_MAX_FREQ", "Frequency",
                  help_="Use kHz. Set matching min/max limits for all profiles to avoid retaining the previous profile's limits."),
        *profiles("Minimum Performance %", "CPU_MIN_PERF", "Pstate",
                  help_="Intel pstate only, 0..100. This limits active CPU performance, not idle sleep states."),
        *profiles("Maximum Performance %", "CPU_MAX_PERF", "Pstate", help_="Intel pstate only, 0..100."),
        *profiles("Allow CPU Boost", "CPU_BOOST", "Boost", options=["0", "1"]),
        *profiles("Intel HWP Dynamic Boost", "CPU_HWP_DYN_BOOST", "Boost", options=["0", "1"],
                  help_="Intel pstate active mode with HWP only; raises minimum performance briefly after I/O waits."),
        setting("NMI Watchdog", "NMI_WATCHDOG", "Kernel", options=["0", "1"]),
    ],
    2: [
        action("Show Disk Capabilities", "tlp-stat -d"),
        action("Show Configured Disk IDs", "tlp diskid"),
        setting("Disk Targets", "DISK_DEVICES", "Disks",
                help_="Space-separated device names or disk IDs. No machine-specific targets are supplied by this TUI. "
                "APM, spindown, and scheduler lists correspond to these targets in order."),
        setting("I/O Schedulers", "DISK_IOSCHED", "Disks",
                help_="One value per target disk: keep or a supported scheduler. Check Disk Capabilities."),
        setting("APM Class Denylist", "DISK_APM_CLASS_DENYLIST", "Disks", help_="Classes: sata, ata, usb, ieee1394."),
        *profiles("Dirty Page Timeout (sec)", "MAX_LOST_WORK_SECS", "Timeouts",
                  help_="Sets dirty page writeback timeouts. Longer intervals may increase unsaved data after power loss."),
        *profiles("Disk APM Levels", "DISK_APM_LEVEL", "Power", help_="1..254, 255 to disable, or keep per disk; only supported drives."),
        *profiles("Disk Spindown Timeouts", "DISK_SPINDOWN_TIMEOUT", "Power",
                  help_="One value per disk: 0 disables, 1..240 means 5-second units, 241..251 means 30-minute units; "
                  "252 means 21 minutes, 253 is vendor-defined, 254 reserved, 255 means 21 minutes 15 seconds. keep leaves it unchanged."),
        *profiles("SATA Link Power", "SATA_LINKPWR", "SATA",
                  options=["min_power", "med_power_with_dipm", "medium_power", "max_performance"]),
        setting("SATA Host Denylist", "SATA_LINKPWR_DENYLIST", "SATA", help_="Space-separated host names from Disk Capabilities."),
        *profiles("Disk Runtime PM", "AHCI_RUNTIME_PM", "Runtime", options=["on", "auto"],
                  help_="Disk devices and SATA ports: on disables runtime PM; auto enables it. PCIe controllers use PCIe Runtime PM."),
        setting("Runtime PM Delay (sec)", "AHCI_RUNTIME_PM_TIMEOUT", "Runtime"),
    ],
    3: [
        action("Show GPU Capabilities", "tlp-stat -g"),
        *profiles("Intel GPU Power Profile", "INTEL_GPU_POWER_PROFILE", "Intel", options=["base", "power_saving"],
                  help_="Requires a supported Intel GPU using the xe driver."),
        *profiles("Intel Minimum GPU MHz", "INTEL_GPU_MIN_FREQ", "Intel", help_="Check GPU diagnostics for supported clocks and driver."),
        *profiles("Intel Maximum GPU MHz", "INTEL_GPU_MAX_FREQ", "Intel"),
        *profiles("Intel Boost GPU MHz", "INTEL_GPU_BOOST_FREQ", "Intel"),
        *profiles("AMD DPM Performance", "RADEON_DPM_PERF_LEVEL", "AMD", options=["high", "auto", "low"],
                  help_="Requires amdgpu or radeon with dynamic power management."),
        *profiles("AMD Backlight Modulation", "AMDGPU_ABM_LEVEL", "AMD", options=["0", "1", "2", "3", "4"],
                  help_="Supported AMD display hardware only. 0 disables; 1..4 progressively trade image fidelity for savings."),
    ],
    4: [
        action("Show Radio Status", "tlp-stat -r"),
        *profiles("Wi-Fi Power Saving", "WIFI_PWR", "WLAN", options=["on", "off"]),
        setting("Disable Wake on LAN", "WOL_DISABLE", "Ethernet", options=["Y", "N"]),
        *[setting(label, key, "Startup", help_="Space-separated radios: bluetooth, nfc, wifi, wwan. Empty disables this action; nil inherits defaults/drop-ins.")
          for label, key in (("Enable on Boot", "DEVICES_TO_ENABLE_ON_STARTUP"), ("Disable on Boot", "DEVICES_TO_DISABLE_ON_STARTUP"))],
        *profiles("Enable Radios", "DEVICES_TO_ENABLE", "Profiles", help_="Space-separated radios: bluetooth, nfc, wifi, wwan."),
        *profiles("Disable Radios", "DEVICES_TO_DISABLE", "Profiles", help_="Space-separated radios: bluetooth, nfc, wifi, wwan."),
        ConfigItem(label="Disable Unused Radios", key="menu_idle_radios", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Profiles",
                   extended_help="Disables only radios not currently connected/in use: bluetooth, nfc, wifi, wwan."),
        *[setting(name, f"DEVICES_TO_DISABLE_ON_{suffix}_NOT_IN_USE", "Profiles", parent="menu_idle_radios")
          for name, suffix in (("Performance", "PRF"), ("Balanced", "BAL"), ("Power Saver", "SAV"))],
        *[setting(label, f"DEVICES_TO_{verb}_ON_{event}", "Network Events",
                  help_="Requires tlp-rdw and NetworkManager. Space-separated radios: bluetooth, nfc, wifi, wwan.")
          for label, verb, event in (
              ("Disable on LAN Connect", "DISABLE", "LAN_CONNECT"),
              ("Enable on LAN Disconnect", "ENABLE", "LAN_DISCONNECT"),
              ("Disable on Wi-Fi Connect", "DISABLE", "WIFI_CONNECT"),
              ("Enable on Wi-Fi Disconnect", "ENABLE", "WIFI_DISCONNECT"),
              ("Disable on WWAN Connect", "DISABLE", "WWAN_CONNECT"),
              ("Enable on WWAN Disconnect", "ENABLE", "WWAN_DISCONNECT"),
          )],
    ],
    5: [
        action("Show USB Capabilities", "tlp-stat -u"),
        setting("USB Autosuspend", "USB_AUTOSUSPEND", "Global", options=["0", "1"]),
        setting("USB Denylist", "USB_DENYLIST", "Global", help_="Space-separated vendor:product IDs from USB diagnostics."),
        setting("USB Allowlist", "USB_ALLOWLIST", "Global", help_="Space-separated vendor:product IDs; overrides exclusions."),
        *[setting(label, f"USB_EXCLUDE_{suffix}", "Exclusions", options=["0", "1"])
          for label, suffix in (("Exclude Audio", "AUDIO"), ("Exclude Bluetooth", "BTUSB"), ("Exclude Phones", "PHONE"),
                                ("Exclude Printers", "PRINTER"), ("Exclude WWAN", "WWAN"))],
    ],
    6: [
        action("Show PCIe Capabilities", "tlp-stat -e"),
        *profiles("PCIe ASPM Policy", "PCIE_ASPM", "ASPM", options=choices("module/pcie_aspm/parameters/policy"),
                  help_="Available policies come from the kernel. default uses firmware policy; performance disables ASPM."),
        *profiles("PCIe Runtime PM", "RUNTIME_PM", "Runtime", options=["on", "auto"]),
        setting("Device Denylist", "RUNTIME_PM_DENYLIST", "Overrides", help_="Space-separated PCI addresses from PCIe diagnostics."),
        setting("Driver Denylist", "RUNTIME_PM_DRIVER_DENYLIST", "Overrides"),
        setting("Force Enable Devices", "RUNTIME_PM_ENABLE", "Overrides"),
        setting("Force Disable Devices", "RUNTIME_PM_DISABLE", "Overrides"),
    ],
    7: [
        *profiles("Audio Idle Timeout (sec)", "SOUND_POWER_SAVE", "Timeout", help_="0 disables; supported HDA/AC97 codecs only."),
        setting("Controller Power Saving", "SOUND_POWER_SAVE_CONTROLLER", "Hardware", options=["Y", "N"]),
    ],
    8: [
        action("Show Battery Capabilities", "tlp-stat -b"),
        *[item for battery in ("BAT0", "BAT1") for item in (
            ConfigItem(label=f"Battery {battery} (TLP)", key=f"menu_{battery.lower()}", scope="DEFAULT", type_="menu", default=None,
                       is_parent=True, group="Thresholds",
                       extended_help="These are TLP battery identifiers, not assumed physical positions or sysfs names. "
                       "Use Battery Capabilities to determine supported batteries and threshold ranges. "
                       "For stop-only hardware use start=0; nil leaves thresholds unconfigured."),
            setting("Start Threshold %", f"START_CHARGE_THRESH_{battery}", "Thresholds", parent=f"menu_{battery.lower()}"),
            setting("Stop Threshold %", f"STOP_CHARGE_THRESH_{battery}", "Thresholds", parent=f"menu_{battery.lower()}"),
        )],
        setting("Restore on Battery", "RESTORE_THRESHOLDS_ON_BAT", "Thresholds", options=["0", "1"],
                help_="Restore configured thresholds when unplugging, after temporary fullcharge/chargeonce changes."),
    ],
    9: [
        ConfigItem(label="Prefer Power Saver", key="preset_power_saver", scope="DEFAULT", type_="preset", default=None,
                   group="Profile Selection", preset_payload={"TLP_PROFILE_AC": "SAV", "TLP_PROFILE_BAT": "SAV", "TLP_AUTO_SWITCH": "1"},
                   extended_help="Selects your Power Saver settings on AC and battery, without imposing hardware-specific tuning. Save and apply to activate."),
        ConfigItem(label="Prefer Performance", key="preset_performance", scope="DEFAULT", type_="preset", default=None,
                   group="Profile Selection", preset_payload={"TLP_PROFILE_AC": "PRF", "TLP_PROFILE_BAT": "PRF", "TLP_AUTO_SWITCH": "1"},
                   extended_help="Selects your Performance settings on AC and battery. Save and apply to activate."),
    ],
}

if __name__ == "__main__":
    router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    try:
        sys.exit(subprocess.run([sys.executable, str(router), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    except OSError as error:
        print(f"Unable to launch TLP configurator: {error}", file=sys.stderr)
        sys.exit(1)
