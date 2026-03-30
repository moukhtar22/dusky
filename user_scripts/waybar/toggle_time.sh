#!/usr/bin/env bash
# ==============================================================================
# Script:     waybar-time.sh
# Purpose:    Manages Waybar time formats (12h <-> 24h) for horizontal and 
#             vertical layouts. Supports toggling and explicit state flags.
# Architect:  Optimized for Arch/Wayland (Stateless, High-Performance)
# ==============================================================================

set -Eeuo pipefail

# --- Configuration ---
readonly WAYBAR_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/waybar"
readonly STATE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings"
readonly STATE_FILE="${STATE_DIR}/time_format"

# --- Formatting ---
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly YELLOW=$'\033[0;33m'
readonly RED=$'\033[0;31m'
readonly RESET=$'\033[0m'

die() {
    printf "\n%s[✖] %s%s\n" "${RED}" "$1" "${RESET}" >&2
    exit "${2:-1}"
}

# Ensure required directories exist
[[ -d "${WAYBAR_DIR}" ]] || die "Waybar directory missing: ${WAYBAR_DIR}"
mkdir -p "${STATE_DIR}"

# --- Argument Parsing ---
action="toggle"
case "${1:-}" in
    --12) action="12" ;;
    --24) action="24" ;;
    "")   action="toggle" ;;
    -h|--help) 
        printf "Usage: %s [--12 | --24]\n" "${0##*/}"
        printf "Omitting flags will toggle the current state.\n"
        exit 0 
        ;;
    *) die "Unknown argument: $1. Valid flags: --12, --24." ;;
esac

# --- State Resolution ---
# true  == 24-hour format
# false == 12-hour format
current_state="false"
[[ -f "${STATE_FILE}" ]] && current_state=$(<"${STATE_FILE}")

target_state=""
format_name=""

if [[ "${action}" == "12" ]]; then
    target_state="false"
elif [[ "${action}" == "24" ]]; then
    target_state="true"
else
    # Toggle logic
    [[ "${current_state}" == "true" ]] && target_state="false" || target_state="true"
fi

# --- Early Exit Optimization ---
if [[ "${current_state}" == "${target_state}" && "${action}" != "toggle" ]]; then
    format_name=$([[ "${target_state}" == "true" ]] && echo "24-hour" || echo "12-hour")
    printf "%s[i] Waybar is already set to %s format. No changes made.%s\n" "${YELLOW}" "${format_name}" "${RESET}"
    exit 0
fi

# --- Payload Definition ---
if [[ "${target_state}" == "false" ]]; then
    format_name="12-hour (AM/PM)"
    sed_horiz='s/{:%H:%M}/{:%I:%M %p}/g'
    sed_vert='s/{:%H\\n%M}/{:%I\\n%M}/g'
else
    format_name="24-hour"
    sed_horiz='s/{:%I:%M %p}/{:%H:%M}/g'
    sed_vert='s/{:%I\\n%M}/{:%H\\n%M}/g'
fi

printf "%s[i] Applying %s format to Waybar configurations...%s\n" "${BLUE}" "${format_name}" "${RESET}"

# --- Single-Pass Execution ---
find "${WAYBAR_DIR}" -type f -name "config.jsonc" -exec \
    sed -i -e "${sed_horiz}" -e "${sed_vert}" {} + || die "Sed replacement failed."

# Update state file atomically
printf "%s\n" "${target_state}" > "${STATE_FILE}"

# --- Reload Waybar ---
if pgrep -x waybar > /dev/null; then
    pkill -SIGUSR2 -x waybar || die "Failed to send SIGUSR2 to Waybar."
    printf "%s[✔] Configurations patched and Waybar reloaded.%s\n" "${GREEN}" "${RESET}"
else
    printf "%s[✔] Configurations patched. (Waybar is not currently running).%s\n" "${GREEN}" "${RESET}"
fi
