#!/usr/bin/env bash
# Auto-detects system keyboard layout and patches Hyprland Lua config.
# Target System: Arch Linux / Hyprland (0.55+) / UWSM
# Author: Elite DevOps
# -----------------------------------------------------------------------------------------

set -euo pipefail

# --- Configuration ---
readonly TARGET_CONF="${HOME}/.config/hypr/edit_here/source/input.lua"
readonly DEFAULT_LAYOUT="us"

# --- Styling (TTY-aware) ---
# Only use colors if connected to a terminal, otherwise keep logs clean for piping
if [[ -t 2 && "${TERM:-dumb}" != "dumb" ]]; then
    readonly BOLD=$'\033[1m' GREEN=$'\033[0;32m' BLUE=$'\033[0;34m'
    readonly YELLOW=$'\033[0;33m' RED=$'\033[0;31m' NC=$'\033[0m'
else
    readonly BOLD="" GREEN="" BLUE="" YELLOW="" RED="" NC=""
fi

# --- Global State for Cleanup ---
_temp_file=""

# --- Cleanup Trap ---
cleanup() {
    # If a temp file exists and hasn't been moved yet, delete it
    if [[ -n "${_temp_file}" && -f "${_temp_file}" ]]; then
        rm -f "${_temp_file}"
    fi
}
trap cleanup EXIT INT TERM

# --- Logging (all to stderr) ---
log_info() { printf '%s[INFO]%s %s\n' "${BLUE}" "${NC}" "$*" >&2; }
log_ok()   { printf '%s[OK]%s %s\n' "${GREEN}" "${NC}" "$*" >&2; }
log_warn() { printf '%s[WARN]%s %s\n' "${YELLOW}" "${NC}" "$*" >&2; }
log_err()  { printf '%s[ERROR]%s %s\n' "${RED}" "${NC}" "$*" >&2; }

# --- Privilege Check ---
if (( EUID == 0 )); then
    log_err "Do not run as root. This modifies user config in \$HOME."
    exit 1
fi

# --- Map VC Keymaps to XKB Layouts ---
map_vc_to_xkb() {
    local vc_map="${1:-}"
    
    # Sanitize input: allow only alphanumerics, hyphens, underscores
    vc_map="${vc_map//[^a-zA-Z0-9_-]/}"

    # Default fallback if empty
    [[ -z "${vc_map}" ]] && { echo "${DEFAULT_LAYOUT}"; return; }

    # Bash 4.0+ lowercase conversion
    case "${vc_map,,}" in
        br-abnt2)               echo "br" ;;
        uk)                     echo "gb" ;;
        us|us-*|dvorak|colemak) echo "us" ;;
        de|de-*)                echo "de" ;;
        fr|fr-*)                echo "fr" ;;
        es|es-*)                echo "es" ;;
        it|it-*)                echo "it" ;;
        cz|cz-*)                echo "cz" ;;
        pt|pt-*)                echo "pt" ;;
        sg|sg-*)                echo "ch" ;; # Swiss German
        ru|ru-*)                echo "ru" ;;
        pl|pl-*)                echo "pl" ;;
        jp|jp-*)                echo "jp" ;;
        fi|fi-*)                echo "fi" ;;
        dk|dk-*)                echo "dk" ;;
        no|no-*)                echo "no" ;;
        se|se-*)                echo "se" ;;
        *)
            # Heuristic: grab the first two chars (e.g. 'be'lgian -> 'be')
            local code="${vc_map:0:2}"
            echo "${code:-${DEFAULT_LAYOUT}}"
            ;;
    esac
}

# --- Detect Keyboard Layout ---
detect_layout() {
    log_info "Detecting system keyboard configuration..."

    if ! command -v localectl &>/dev/null; then
        log_warn "localectl not found. Using default."
        echo "${DEFAULT_LAYOUT}"
        return
    fi

    local localectl_out
    localectl_out=$(localectl status 2>/dev/null) || localectl_out=""

    # 1. Try X11 Layout (Preferred for Wayland/XKB)
    # Regex improvement: Use [[:space:]]+ to match one OR MORE spaces
    if [[ "${localectl_out}" =~ X11[[:space:]]+Layout:[[:space:]]*([^[:space:]]+) ]]; then
        local x11="${BASH_REMATCH[1]}"
        if [[ -n "${x11}" && "${x11}" != "(unset)" && "${x11}" != "n/a" ]]; then
            echo "${x11}"
            return
        fi
    fi

    log_warn "X11 Layout unset. Falling back to VC Keymap..."

    # 2. Fallback to Virtual Console keymap
    if [[ "${localectl_out}" =~ VC[[:space:]]+Keymap:[[:space:]]*([^[:space:]]+) ]]; then
        local vc="${BASH_REMATCH[1]}"
        if [[ -n "${vc}" && "${vc}" != "(unset)" && "${vc}" != "n/a" ]]; then
            map_vc_to_xkb "${vc}"
            return
        fi
    fi

    log_warn "No layout detected. Using default: '${DEFAULT_LAYOUT}'"
    echo "${DEFAULT_LAYOUT}"
}

# --- Apply Configuration Patch ---
apply_patch() {
    local layout="${1:-}"

    # Validate layout string (lowercase letters and commas only)
    # Allows 'us' or 'us,ru'
    if [[ ! "${layout}" =~ ^[a-z]+(,[a-z]+)*$ ]]; then
        log_err "Invalid layout detected: '${layout}'. Aborting."
        exit 1
    fi

    if [[ ! -f "${TARGET_CONF}" ]]; then
        log_err "Config not found: ${TARGET_CONF}"
        log_err "Please ensure the path is correct."
        exit 1
    fi

    # Check if the file actually has the key we want to replace
    # Anchor to start of line to avoid comments, allows for whitespace
    if ! grep -E -q '^[[:space:]]*kb_layout[[:space:]]*=' "${TARGET_CONF}"; then
        log_err "No active 'kb_layout' key found in config. Cannot patch."
        exit 1
    fi

    log_info "Patching ${BOLD}${TARGET_CONF}${NC} → kb_layout = ${BOLD}\"${layout}\"${NC}"

    # --- Atomic Write Logic ---
    local target_dir
    target_dir=$(dirname "${TARGET_CONF}")
    
    # 1. Create temp file in the SAME DIRECTORY to allow atomic rename
    _temp_file=$(mktemp "${target_dir}/.tmp.input.XXXXXX")

    # 2. Preserve original file permissions (if possible)
    chmod --reference="${TARGET_CONF}" "${_temp_file}" 2>/dev/null || true

    # 3. Perform substitution for Lua Syntax
    # Group 1: Captures leading whitespace
    # Group 2: Captures the trailing comma and comments after the quoted value
    if ! sed -E "s|^([[:space:]]*)kb_layout[[:space:]]*=[[:space:]]*[\"'][^\"']*[\"'](.*)|\1kb_layout = \"${layout}\"\2|" \
             "${TARGET_CONF}" > "${_temp_file}"; then
        log_err "sed processing failed."
        exit 1
    fi

    # 4. Atomic Swap
    if ! mv -f "${_temp_file}" "${TARGET_CONF}"; then
        log_err "Failed to write config file (mv failed)."
        exit 1
    fi

    # 5. Clear global var so trap doesn't delete the successfully moved file
    _temp_file=""
    
    log_ok "Configuration updated successfully."
}

# --- Main ---
main() {
    local layout
    layout=$(detect_layout)
    
    # Ensure layout is never empty
    : "${layout:=${DEFAULT_LAYOUT}}"
    
    apply_patch "${layout}"
}

main
