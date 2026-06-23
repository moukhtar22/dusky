#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  tlp-toggle — Native TLP power profile manager for Wayland
# ---------------------------------------------------------------------------

set -euo pipefail

# -- Bash version gate (5.1+) --
if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
    printf 'Fatal: Bash 5.1+ required\n' >&2
    exit 1
fi

# -- Configuration --
readonly STATE_FILE="$HOME/.config/dusky/settings/tlp_state"
readonly STATE_DIR="${STATE_FILE%/*}"
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/tlp_toggle.lock"
readonly -a PROFILES=('power-saver' 'balanced' 'performance')

# -- UI Mappings --
declare -rA ICON_NERDFONT=(
    [performance]=$'\U000f04c5'    # 󰓅
    [balanced]=$'\U000f007e'       # 󰖳
    [power-saver]=$'\U000f0327'    # 󰌧
    [unknown]='?'
)

declare -rA ICON_NOTIFY=(
    [performance]='battery-full-charged-symbolic'
    [balanced]='battery-good-symbolic'
    [power-saver]='battery-caution-symbolic'
    [unknown]='dialog-warning'
)

declare -rA LABEL=(
    [performance]='Performance'
    [balanced]='Balanced'
    [power-saver]='Power Saver'
)

declare -rA CSS_CLASS=(
    [performance]='performance'
    [balanced]='balanced'
    [power-saver]='power-saver'
)

# -- Logging --
err() { printf '\033[31m[ERR]\033[0m %s\n' "$*" >&2; exit 1; }

# -- Initialization & Locking --
mkdir -p "$STATE_DIR"

# Concurrency lock (RAM-backed, atomic)
exec 200>"$LOCK_FILE"
if ! flock -n 200; then exit 0; fi

# Initialize state file if missing
if [[ ! -f "$STATE_FILE" ]]; then
    echo "balanced" > "$STATE_FILE"
fi
CURRENT_STATE=$(<"$STATE_FILE")

# -- Core Functions --
send_notification() {
    local profile="$1"
    if command -v notify-send >/dev/null 2>&1; then
        local pretty="${LABEL[$profile]:-$profile}"
        local icon="${ICON_NOTIFY[$profile]:-${ICON_NOTIFY[unknown]}}"
        notify-send \
            --app-name="TLP Manager" \
            --urgency="low" \
            --icon="$icon" \
            --hint=string:x-canonical-private-synchronous:power-profile \
            "Power Profile" \
            "Active: ${pretty}" &
        disown
    fi
}

send_error_notification() {
    local profile="$1"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send \
            --app-name="TLP Manager" \
            --urgency="critical" \
            --icon="dialog-error" \
            "Power Profile Error" \
            "Failed to switch to ${profile}. Check sudoers." &
        disown
    fi
}

set_state() {
    local target="$1"
    
    # Validate profile against known PROFILES array
    local valid=0
    for p in "${PROFILES[@]}"; do
        if [[ "$p" == "$target" ]]; then valid=1; break; fi
    done
    [[ $valid -eq 1 ]] || err "Unknown profile: $target"

    # Idempotency Guard
    if [[ "$CURRENT_STATE" == "$target" ]]; then
        exit 0 
    fi

    # -n forces non-interactive mode. It fails instantly if sudo requires a password.
    if sudo -n tlp "$target" >/dev/null 2>&1; then
        # Atomic file write
        local tmp_file="${STATE_FILE}.tmp"
        echo "$target" > "$tmp_file"
        mv "$tmp_file" "$STATE_FILE"
        
        send_notification "$target"
        exit 0
    else
        send_error_notification "$target"
        err "Failed to execute 'sudo tlp $target'. Verify NOPASSWD in sudoers."
    fi
}

toggle_state() {
    local direction="${1:-forward}"
    local idx=-1
    local count=${#PROFILES[@]}
    
    # Locate current index
    for i in "${!PROFILES[@]}"; do
        if [[ "${PROFILES[$i]}" == "$CURRENT_STATE" ]]; then
            idx=$i
            break
        fi
    done
    
    # Self-healing fallback if state file was corrupted
    if [[ $idx -eq -1 ]]; then
        set_state "balanced"
    fi
    
    # Array math for cycling
    local next_idx
    if [[ "$direction" == "reverse" ]]; then
        next_idx=$(( (idx - 1 + count) % count ))
    else
        next_idx=$(( (idx + 1) % count ))
    fi
    
    set_state "${PROFILES[$next_idx]}"
}

show_help() {
    cat <<EOF
tlp-toggle — Native TLP power profile manager for Wayland

USAGE
    tlp-toggle toggle              Cycle forward (power-saver → balanced → performance)
    tlp-toggle toggle --reverse    Cycle backward (performance → balanced → power-saver)
    tlp-toggle performance         Set performance profile
    tlp-toggle balanced            Set balanced profile
    tlp-toggle power-saver         Set power-saver profile
    tlp-toggle status              Print raw text state (for GTK app)
    tlp-toggle status --json       Print Waybar compatible JSON payload
    tlp-toggle -h | --help         Show this help
EOF
}

# -- Routing --
ACTION="${1:-}"
SUBFLAG="${2:-}"

# Dependency Check
if ! command -v tlp >/dev/null 2>&1; then
    err "TLP command not found. Please ensure tlp is installed."
fi

case "$ACTION" in
    toggle|-c|--cycle)
        if [[ "$SUBFLAG" == "--reverse" || "$SUBFLAG" == "-r" ]]; then
            toggle_state "reverse"
        else
            toggle_state "forward"
        fi
        ;;
    performance|balanced|power-saver)
        set_state "$ACTION"
        ;;
    status)
        if [[ "$SUBFLAG" == "--json" ]]; then
            local icon="${ICON_NERDFONT[$CURRENT_STATE]:-${ICON_NERDFONT[unknown]}}"
            local label="${LABEL[$CURRENT_STATE]:-$CURRENT_STATE}"
            local css="${CSS_CLASS[$CURRENT_STATE]:-unknown}"
            printf '{"text":"%s %s","alt":"%s","class":"%s","tooltip":"Power profile: %s"}\n' \
                "$icon" "$label" "$CURRENT_STATE" "$css" "$label"
        else
            echo "$CURRENT_STATE"
        fi
        ;;
    -h|--help|help)
        show_help
        ;;
    *)
        # Default behavior: run cycle if no arguments provided
        if [[ -z "$ACTION" ]]; then
            toggle_state "forward"
        else
            err "Unknown command: $ACTION"
        fi
        ;;
esac
