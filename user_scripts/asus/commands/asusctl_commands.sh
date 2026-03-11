#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# asusctl Forensic Data & CLI Surface Extractor v3.0
# Purpose: Deep CLI syntax mapping and state extraction for TUI generation.
# -----------------------------------------------------------------------------

# Enforce strict execution for reliability
set -euo pipefail

# Utilize native timestamp formatting (no subshells required)
printf -v TIMESTAMP '%(%Y%m%d_%H%M%S)T' -1
readonly SCRIPT_DIR="${PWD}"
readonly LOG_FILE="${SCRIPT_DIR}/asusctl_full_dump_${TIMESTAMP}.log"

# Redirect stdout and stderr to both terminal and log file in the current directory
exec > >(tee -i "${LOG_FILE}") 2>&1

echo "==================================================================="
echo " Starting Deep asusctl Forensic Extraction: ${TIMESTAMP} "
echo " Logging directly to: ${LOG_FILE}"
echo "==================================================================="

# --- Helper Functions ---
run_state_probe() {
    local desc="$1"
    shift
    echo -e "\n[+] STATE PROBE: ${desc}"
    echo "-------------------------------------------------------------------"
    echo "\$ $*"
    if ! "$@"; then
        echo "    -> WARNING: Command failed or unsupported by hardware."
    fi
    echo "-------------------------------------------------------------------"
}

run_help_probe() {
    local cmd_str="$1"
    echo -e "\n[?] CLI MAP: ${cmd_str}"
    echo "-------------------------------------------------------------------"
    echo "\$ ${cmd_str} --help"
    if ! ${cmd_str} --help 2>&1; then
         echo "    -> WARNING: Help menu extraction failed."
    fi
    echo "-------------------------------------------------------------------"
}

# ===================================================================
# PHASE 1: CLI SYNTAX & FEATURE MAPPING
# Extracting all possible flags and subcommands for TUI generation.
# ===================================================================

run_help_probe "asusctl"

# Base level commands
BASE_COMMANDS=("aura" "leds" "profile" "fan-curve" "anime" "slash" "scsi" "armoury" "backlight" "battery" "info")
for cmd in "${BASE_COMMANDS[@]}"; do
    run_help_probe "asusctl ${cmd}"
done

# Deep Dive: Aura Power Zones
AURA_POWER_ZONES=("keyboard" "logo" "lightbar" "lid" "rear-glow" "ally")
run_help_probe "asusctl aura power"
for zone in "${AURA_POWER_ZONES[@]}"; do
    run_help_probe "asusctl aura power ${zone}"
done
run_help_probe "asusctl aura power-tuf"

# Deep Dive: Aura Effects (Dynamically Extracted)
echo -e "\n[*] Dynamically extracting Aura Effects..."
mapfile -t AURA_EFFECTS < <(asusctl aura effect --help 2>/dev/null | awk '/Commands:/{flag=1; next} flag && /^  [a-z]/{print $1}' || true)
if [[ ${#AURA_EFFECTS[@]} -gt 0 ]]; then
    for eff in "${AURA_EFFECTS[@]}"; do
        run_help_probe "asusctl aura effect ${eff}"
    done
else
    echo "    -> WARNING: Could not parse Aura effects dynamically."
fi

# Deep Dive: Anime Matrix Commands
ANIME_CMDS=("image" "pixel-image" "gif" "pixel-gif" "set-builtins")
for acmd in "${ANIME_CMDS[@]}"; do
    run_help_probe "asusctl anime ${acmd}"
done

# Deep Dive: Profile Commands
PROFILE_CMDS=("next" "list" "get" "set")
for pcmd in "${PROFILE_CMDS[@]}"; do
    run_help_probe "asusctl profile ${pcmd}"
done

# Deep Dive: Battery Commands
BATTERY_CMDS=("limit" "oneshot" "info")
for bcmd in "${BATTERY_CMDS[@]}"; do
    run_help_probe "asusctl battery ${bcmd}"
done

# Deep Dive: Leds Commands
LEDS_CMDS=("set" "get" "next" "prev")
for lcmd in "${LEDS_CMDS[@]}"; do
    run_help_probe "asusctl leds ${lcmd}"
done

# Deep Dive: Armoury Commands
ARMOURY_CMDS=("set" "get" "list")
for arcmd in "${ARMOURY_CMDS[@]}"; do
    run_help_probe "asusctl armoury ${arcmd}"
done

# ===================================================================
# PHASE 2: HARDWARE STATE EXTRACTION
# Capturing active configuration values.
# ===================================================================

run_state_probe "System Info" asusctl info
run_state_probe "Battery Limit State" asusctl battery info
run_state_probe "Keyboard Brightness State" asusctl leds get
run_state_probe "Available Profiles" asusctl profile list
run_state_probe "Current Active Profile" asusctl profile get

# Fan Curves Dynamic Extraction
mapfile -t PROFILES < <(asusctl profile list 2>/dev/null || true)
run_state_probe "Enabled Fan Profiles" asusctl fan-curve --get-enabled

echo -e "\n[+] STATE PROBE: Fan Curves Per Profile"
echo "-------------------------------------------------------------------"
if [[ ${#PROFILES[@]} -gt 0 ]]; then
    for prof in "${PROFILES[@]}"; do
        echo "\$ asusctl fan-curve --mod-profile \"${prof}\""
        asusctl fan-curve --mod-profile "${prof}" || echo "    -> Failed to read curve for ${prof}"
    done
else
    echo "    -> WARNING: No profiles found to query fan curves."
fi
echo "-------------------------------------------------------------------"

# Armoury Attributes Dynamic Extraction
run_state_probe "Armoury Attribute List" asusctl armoury list
echo -e "\n[+] STATE PROBE: Individual Armoury Attributes"
echo "-------------------------------------------------------------------"
if mapfile -t ATTRIBUTES < <(asusctl armoury list 2>/dev/null | grep -E '^[[:alnum:]_]+:$' | tr -d ':' || true); then
    for attr in "${ATTRIBUTES[@]}"; do
        echo "\$ asusctl armoury get ${attr}"
        asusctl armoury get "${attr}" || echo "    -> Failed to read ${attr}"
    done
else
    echo "    -> WARNING: Could not parse armoury attributes."
fi
echo "-------------------------------------------------------------------"

# Unsupported/Edge Case State Probes
run_state_probe "Slash Lighting State" asusctl slash --list
run_state_probe "SCSI Animations State" asusctl scsi --list

echo -e "\n==================================================================="
echo " Deep Extraction Complete. "
echo " Data securely logged to: ${LOG_FILE}"
echo "==================================================================="
