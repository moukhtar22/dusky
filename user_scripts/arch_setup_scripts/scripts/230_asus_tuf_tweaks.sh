#!/usr/bin/env bash
# Manages Asus TUF specific hardware profiles via Lua config injection
# ==============================================================================
# ARCH LINUX / HYPRLAND / UWSM - HARDWARE CONFIGURATION TWEAKER
# ==============================================================================
# Architecture: Zero-Corruption Atomic Writes, Symlink Safe, Block Injection
# Purpose: Manage config differences for the developer's ASUS TUF F15 vs Generic hardware.
#          - User 'dusk': Prompts to inject developer-specific hardware keys.
#          - Other Users: Autonomously removes hardware-specific configurations.
# Author:  Elite DevOps Architect
# Shell:   Bash 5.0+
# ==============================================================================

set -euo pipefail

# --- GLOBAL ARRAYS ------------------------------------------------------------
declare -a TARGET_FILES
declare -a TARGET_CONTENTS

readonly MARKER_START="-- === START ASUS TUF CONFIG ==="
readonly MARKER_END="-- === END ASUS TUF CONFIG ==="

# --- THE CONFIGURATION DSL ----------------------------------------------------
# Usage: config_block "/path/to/file.lua" << 'EOF'
config_block() {
    local file="$1"
    local content
    content=$(cat)
    TARGET_FILES+=("$file")
    TARGET_CONTENTS+=("$content")
}

# ==============================================================================
# EDIT HERE: HARDWARE CONFIGURATION RULES
# ==============================================================================
setup_rules() {
    local HYPR_DIR="$HOME/.config/hypr"
    local MAIN_DIR="$HYPR_DIR/source"
    local OVERLAY_DIR="$HYPR_DIR/edit_here/source"

    config_block "$OVERLAY_DIR/keybinds.lua" << 'EOF'
hl.bind(
    "ALT + 7",
    hl.dsp.exec_cmd("hyprctl keyword monitor eDP-1,1920x1080@48,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 0"),
    { description = "Set Refresh rate to 48Hz Asus Tuf", locked = true }
)

hl.bind(
    "ALT + 8",
    hl.dsp.exec_cmd("hyprctl keyword monitor eDP-1,1920x1080@144,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 1"),
    { description = "Set Refresh rate to 144Hz Asus Tuf", locked = true }
)

hl.bind(
    "XF86Launch3",
    hl.dsp.exec_cmd(terminal .. " --class asusctl.sh -e sudo " .. dusky_scripts .. "asus/asusctl.sh"),
    { description = "ASUS Control", locked = true }
)
EOF

    # Add more blocks as needed:
    # config_block "$OVERLAY_DIR/monitors.lua" << 'EOF'
    # ...
    # EOF
}

# --- UTILITIES & ANSI ---------------------------------------------------------
readonly BOLD=$'\033[1m'
readonly GREEN=$'\033[32m'
readonly BLUE=$'\033[34m'
readonly YELLOW=$'\033[33m'
readonly RED=$'\033[31m'
readonly RESET=$'\033[0m'

log_info()    { printf "${BLUE}[INFO]${RESET} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${RESET}   %s\n" "$1"; }
log_skip()    { printf "${YELLOW}[SKIP]${RESET} %s\n" "$1"; }
log_err()     { printf "${RED}[ERR]${RESET}  %s\n" "$1" >&2; }

declare -a TEMP_FILES_TO_CLEANUP

cleanup_traps() {
    for tmp in "${TEMP_FILES_TO_CLEANUP[@]:-}"; do
        [[ -f "$tmp" ]] && rm -f "$tmp"
    done
}

trap cleanup_traps EXIT HUP INT TERM

# --- CORE ATOMIC ENGINE -------------------------------------------------------
execute_atomic_swap() {
    local target_file="$1"
    local action="$2" 
    local content="${3:-}"
    local base_name
    base_name=$(basename "$target_file")

    local actual_target="${target_file}"
    if [[ -L "${target_file}" ]]; then
        actual_target=$(realpath -m "${target_file}")
    fi

    local target_dir="${actual_target%/*}"

    [[ ! -d "${target_dir}" ]] && mkdir -p "${target_dir}"
    [[ ! -f "${actual_target}" ]] && touch "${actual_target}"

    if [[ ! -w "${actual_target}" ]]; then
        log_err "Write permission denied: ${actual_target}"
        return 1
    fi

    local has_block=0
    # FIX: Use -F (Fixed Strings) and -- (End of options) to prevent the "--" in MARKER_START from breaking grep
    grep -Fq -- "$MARKER_START" "$actual_target" && has_block=1

    if [[ "$action" == "remove" && $has_block -eq 0 ]]; then
        log_skip "Generic config active (no changes needed) in: $base_name"
        return 0
    elif [[ "$action" == "inject" && $has_block -eq 1 ]]; then
        log_skip "ASUS config already active in: $base_name"
        return 0
    fi

    local temp_file
    temp_file=$(mktemp "${target_dir}/.hypr_tweak.XXXXXX") || {
        log_err "Failed to create temp file in ${target_dir}"
        return 1
    }
    TEMP_FILES_TO_CLEANUP+=("$temp_file")

    command cp -pf "${actual_target}" "${temp_file}"

    if [[ "$action" == "remove" ]]; then
        # Safely excise ALL blocks between MARKER_START and MARKER_END
        sed -i '/^'"$MARKER_START"'$/,/^'"$MARKER_END"'$/d' "$temp_file"
    elif [[ "$action" == "inject" ]]; then
        if [[ -s "${temp_file}" ]] && [[ -n "$(tail -c 1 "${temp_file}" | tr -d '\n')" ]]; then
            printf "\n" >> "${temp_file}"
        fi
        printf "\n%s\n%s\n%s\n" "$MARKER_START" "$content" "$MARKER_END" >> "$temp_file"
    fi

    sync "${temp_file}"
    command mv -f "${temp_file}" "${actual_target}"

    if [[ "$action" == "remove" ]]; then
        log_success "Purged developer ASUS configurations from: $base_name"
    else
        log_success "Injected developer ASUS configurations into: $base_name"
    fi
}

# --- ACTIONS ------------------------------------------------------------------
remove_asus_config() {
    log_info "Enforcing Generic Hardware Profile (Atomic Removal)..."
    local total=${#TARGET_FILES[@]}
    for (( i=0; i<total; i++ )); do
        execute_atomic_swap "${TARGET_FILES[$i]}" "remove" ""
    done
}

inject_asus_config() {
    log_info "Applying ASUS TUF Hardware Profile (Atomic Injection)..."
    local total=${#TARGET_FILES[@]}
    for (( i=0; i<total; i++ )); do
        execute_atomic_swap "${TARGET_FILES[$i]}" "inject" "${TARGET_CONTENTS[$i]}"
    done
}

# --- MAIN EXECUTION -----------------------------------------------------------
main() {
    setup_rules

    # 1. Override Flags (For scripts/automation)
    local flag="${1:-}"
    if [[ "$flag" == "--generic" ]] || [[ "$flag" == "--remove" ]]; then
        remove_asus_config
        exit 0
    elif [[ "$flag" == "--asus" ]] || [[ "$flag" == "--inject" ]]; then
        inject_asus_config
        exit 0
    fi

    # 2. Autonomous Mode for Standard Users
    if [[ "$USER" != "dusk" ]]; then
        printf "${BOLD}Hardware Profile Manager${RESET}\n"
        printf "Standard user detected. Autonomously adapting dotfiles for generic hardware...\n"
        remove_asus_config
        printf "\n${GREEN}Dotfiles optimized for non-ASUS hardware.${RESET}\n"
        printf "${BLUE}(Note: If you are actually using an Asus TUF, run this script with --asus)${RESET}\n"
        exit 0
    fi

    # 3. Interactive Mode exclusively for Developer (Dusk)
    clear
    printf "${BOLD}========================================${RESET}\n"
    printf "${BOLD}   Hardware Profile Manager (Dev Mode)  ${RESET}\n"
    printf "${BOLD}========================================${RESET}\n\n"
    
    printf "Welcome back, Dusk. This script manages your personal Asus TUF F15 overrides.\n"
    printf "These overlays contain your hardware-specific refresh rates, ROG keys, etc.\n\n"
    
    printf "${BLUE}Current System Check:${RESET}\n"
    printf "Are you deploying these dotfiles on your primary Asus TUF? [Y/n] "
    read -r is_asus
    is_asus=${is_asus:-y}

    if [[ "$is_asus" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        printf "\n"
        inject_asus_config
        printf "\n${GREEN}Success.${RESET} Asus TUF hardware profile is locked in.\n"
    elif [[ "$is_asus" =~ ^([nN][oO]|[nN])$ ]]; then
        printf "\n"
        remove_asus_config
        printf "\n${GREEN}Success.${RESET} Reverted to generic hardware profile.\n"
    else
        printf "\n${RED}Invalid input.${RESET} Aborting.\n"
        exit 1
    fi
}

main "$@"
