#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Elite DevOps Arch/Hyprland/UWSM GPU Configurator (v2026.07-Final)
# -----------------------------------------------------------------------------
# Role:       System Architect
# Objective:  Interactive topology selection + Active Dependency Management.
# Standards:  Bash 5+, Sysfs Parsing, Atomic Writes, User Choice.
# -----------------------------------------------------------------------------

# --- 1. STRICT MODE ---
set -euo pipefail
# Enable extended globbing and nullglob (globs expand to nothing if no match)
shopt -s extglob nullglob

# --- 2. CONFIGURATION PATHS ---
readonly UWSM_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/uwsm"
readonly ENV_DIR="$UWSM_CONFIG_DIR/env.d"
readonly OUTPUT_FILE="$ENV_DIR/gpu"

# --- 3. LOGGING UTILITIES ---
readonly BOLD=$'\033[1m'
readonly BLUE=$'\033[34m'
readonly GREEN=$'\033[32m'
readonly YELLOW=$'\033[33m'
readonly RED=$'\033[31m'
readonly RESET=$'\033[0m'

log_info() { printf "%s[INFO]%s %s\n" "${BLUE}${BOLD}" "${RESET}" "$*"; }
log_ok()   { printf "%s[OK]%s %s\n" "${GREEN}${BOLD}" "${RESET}" "$*"; }
log_warn() { printf "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
log_err()  { printf "%s[ERROR]%s %s\n" "${RED}${BOLD}" "${RESET}" "$*" >&2; }

# --- 4. ARGUMENT PARSING ---
AUTO_MODE=0
for arg in "$@"; do
    if [[ "$arg" == "--auto" ]]; then
        AUTO_MODE=1
    fi
done

# --- 5. ACTIVE DEPENDENCY CHECK ---
check_deps() {
    local missing=()
    
    # CRITICAL FIX: Prevent grep hang by using array to check existence first
    local vendor_files=(/sys/class/drm/card*/device/vendor)
    
    # Check for NVIDIA specifics only if NVIDIA hardware exists
    if (( ${#vendor_files[@]} > 0 )); then
        if grep -q "0x10de" "${vendor_files[@]}"; then
            if ! command -v nvidia-smi &>/dev/null; then
                missing+=("nvidia-utils")
            fi
        fi
    fi
    
    if (( ${#missing[@]} > 0 )); then
        log_warn "Missing dependencies detected: ${missing[*]}"
        log_info "Attempting to install via pacman..."
        
        # Sudo will handle the auth. If cached, it's instant.
        if sudo pacman -S --needed --noconfirm "${missing[@]}"; then
            log_ok "Dependencies installed successfully."
        else
            log_err "Failed to install dependencies. Please install manually."
        fi
    else
        log_ok "All dependencies satisfied."
    fi
}

# --- 6. DETECTION ENGINE ---

# Global Arrays to store detected paths
INTEL_CARDS=()
AMD_CARDS=()
NVIDIA_CARDS=()

detect_topology() {
    log_info "Scanning GPU Topology via Sysfs..."
    
    # Robust Loop: Only matches card0, card1... (No connectors)
    for card_path in /sys/class/drm/card+([0-9]); do
        local vendor_file="$card_path/device/vendor"
        if [[ ! -r "$vendor_file" ]]; then continue; fi
        
        local vendor_id
        vendor_id=$(cat "$vendor_file")
        vendor_id=${vendor_id,,} # Lowercase
        
        local dev_node="/dev/dri/${card_path##*/}"
        
        case "$vendor_id" in
            "0x8086") INTEL_CARDS+=("$dev_node") ;;
            "0x1002") AMD_CARDS+=("$dev_node") ;;
            "0x10de") NVIDIA_CARDS+=("$dev_node") ;;
        esac
    done
    
    # DIAGNOSTIC: Check if any GPU was found
    if [[ ${#INTEL_CARDS[@]} -eq 0 && ${#AMD_CARDS[@]} -eq 0 && ${#NVIDIA_CARDS[@]} -eq 0 ]]; then
        log_err "No GPUs detected in /sys/class/drm. Is kernel mode setting (KMS) enabled?"
        # We allow the script to proceed to generate a minimal config, but this is suspicious.
    fi
}

is_nvidia_modern() {
    if ! command -v nvidia-smi &>/dev/null; then return 1; fi
    local cc
    cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]' || echo "0.0")
    if [[ ! "$cc" =~ ^[0-9]+\.[0-9]+$ ]]; then return 1; fi
    local major=${cc%.*}
    local minor=${cc#*.}
    
    # Modern = Turing (7.5) or newer (8.0+)
    if (( major >= 8 )); then return 0; fi
    if (( major == 7 && minor >= 5 )); then return 0; fi
    return 1
}

# --- 7. INTELLIGENT SELECTION LOGIC ---

select_mode() {
    local has_intel=0
    local has_amd=0
    local has_nvidia=0
    [[ ${#INTEL_CARDS[@]} -gt 0 ]] && has_intel=1
    [[ ${#AMD_CARDS[@]} -gt 0 ]] && has_amd=1
    [[ ${#NVIDIA_CARDS[@]} -gt 0 ]] && has_nvidia=1

    echo ""
    echo "${BOLD}--- GPU Topology Detected ---${RESET}"
    (( has_intel )) && echo "  • INTEL:  ${INTEL_CARDS[*]}"
    (( has_amd ))   && echo "  • AMD:    ${AMD_CARDS[*]}"
    (( has_nvidia )) && echo "  • NVIDIA: ${NVIDIA_CARDS[*]}"
    echo ""

    # SCENARIO 1: Single Vendor (No choice needed)
    if (( has_intel && !has_amd && !has_nvidia )); then
        SELECTED_MODE="intel_only"
        return
    elif (( !has_intel && has_amd && !has_nvidia )); then
        SELECTED_MODE="amd_only"
        return
    elif (( !has_intel && !has_amd && has_nvidia )); then
        SELECTED_MODE="nvidia_only"
        return
    fi

    # SCENARIO 2: Auto Mode Forced
    if (( AUTO_MODE == 1 )); then
        log_info "Auto Mode enabled: Defaulting to Integrated Priority (Hybrid)."
        SELECTED_MODE="hybrid"
        return
    fi

    # SCENARIO 3: Hybrid / Interactive
    echo "Select Configuration Mode:"
    echo "  1) ${GREEN}Hybrid / Power Saver${RESET} (Recommended)"
    echo "     - Integrated GPU drives Hyprland."
    echo "     - Dedicated GPU available for games via 'prime-run'."
    echo ""
    
    # Only show NVIDIA option if NVIDIA card exists
    if (( has_nvidia )); then
        echo "  2) ${RED}NVIDIA Performance${RESET} (Desktop Mode)"
        echo "     - NVIDIA drives Hyprland."
        echo "     - Higher power consumption."
        echo ""
    fi
    
    read -rp "Enter choice: " choice
    case "${choice:-1}" in
        1) SELECTED_MODE="hybrid" ;;
        2) 
            if (( has_nvidia )); then
                SELECTED_MODE="nvidia_pri"
            else
                log_warn "Invalid selection. Defaulting to Hybrid."
                SELECTED_MODE="hybrid"
            fi
            ;;
        *) SELECTED_MODE="hybrid" ;;
    esac
}

# --- 8. CONFIG GENERATOR ---

generate_config() {
    local primary_vendor=""
    local aq_device_string=""
    local sorted_devices=()

    # Determine Priority based on selection
    if [[ "$SELECTED_MODE" == "nvidia_pri" || "$SELECTED_MODE" == "nvidia_only" ]]; then
        primary_vendor="nvidia"
        sorted_devices+=("${NVIDIA_CARDS[@]}")
        sorted_devices+=("${INTEL_CARDS[@]}")
        sorted_devices+=("${AMD_CARDS[@]}")
    elif [[ "$SELECTED_MODE" == "amd_only" ]]; then
        primary_vendor="amd"
        sorted_devices+=("${AMD_CARDS[@]}")
    elif [[ "$SELECTED_MODE" == "intel_only" ]]; then
        primary_vendor="intel"
        sorted_devices+=("${INTEL_CARDS[@]}")
    else
        # Hybrid/Default: Integrated First
        sorted_devices+=("${INTEL_CARDS[@]}")
        sorted_devices+=("${AMD_CARDS[@]}")
        sorted_devices+=("${NVIDIA_CARDS[@]}")
        
        # Determine primary for VAAPI settings
        if [[ ${#INTEL_CARDS[@]} -gt 0 ]]; then primary_vendor="intel";
        elif [[ ${#AMD_CARDS[@]} -gt 0 ]]; then primary_vendor="amd";
        else primary_vendor="nvidia"; fi
    fi

    # Build AQ string
    if [[ ${#sorted_devices[@]} -gt 0 ]]; then
        aq_device_string=$(IFS=:; echo "${sorted_devices[*]}")
    fi

    # --- ATOMIC WRITE ---
    [[ ! -d "$ENV_DIR" ]] && mkdir -p "$ENV_DIR"
    local temp_file
    temp_file=$(mktemp "$ENV_DIR/.gpu.XXXXXX")
    trap 'rm -f "$temp_file"' EXIT

    {
        echo "# -----------------------------------------------------------------"
        echo "# UWSM GPU Config | Mode: ${SELECTED_MODE^^} | $(date)"
        echo "# -----------------------------------------------------------------"
        echo "export ELECTRON_OZONE_PLATFORM_HINT=auto"
        echo "export MOZ_ENABLE_WAYLAND=1"
        echo ""
        
        if [[ -n "$aq_device_string" ]]; then
            echo "# Hyprland GPU Priority (First = Compositor)"
            echo "export AQ_DRM_DEVICES=\"$aq_device_string\""
        fi
        echo ""

        # Vendor Specifics
        if [[ ${#INTEL_CARDS[@]} -gt 0 ]]; then
            echo "# --- Intel ---"
            # NOTE: Removed MESA_LOADER_DRIVER_OVERRIDE=iris
            # Modern Mesa auto-selects Iris. Hardcoding it breaks AMD hybrid setups.
            
            if [[ "$primary_vendor" == "intel" ]]; then
                echo "export LIBVA_DRIVER_NAME=iHD"
            fi
        fi

        if [[ ${#AMD_CARDS[@]} -gt 0 ]]; then
            echo "# --- AMD ---"
            if [[ "$primary_vendor" == "amd" ]]; then
                echo "export LIBVA_DRIVER_NAME=radeonsi"
            fi
        fi

        if [[ ${#NVIDIA_CARDS[@]} -gt 0 ]]; then
            if [[ "$primary_vendor" == "nvidia" ]]; then
                echo "# --- NVIDIA (Primary) ---"
                echo "export LIBVA_DRIVER_NAME=nvidia"
                echo "export GBM_BACKEND=nvidia-drm"
                echo "export __GLX_VENDOR_LIBRARY_NAME=nvidia"
                
                if is_nvidia_modern; then
                    echo "export NVD_BACKEND=direct"
                else
                    echo "# Legacy Nvidia Detected"
                    echo "# Add 'cursor { no_hardware_cursors = true }' to hyprland.conf"
                    echo "export AQ_NO_HARDWARE_CURSORS=1"
                    echo "export NVD_BACKEND=egl"
                fi
            else
                echo "# --- NVIDIA (Secondary/Hybrid) ---"
                echo "# Env vars hidden to ensure Intel/AMD handles desktop session."
            fi
        fi
    } > "$temp_file"

    chmod 644 "$temp_file"
    mv "$temp_file" "$OUTPUT_FILE"
    trap - EXIT
    log_ok "Config written to: $OUTPUT_FILE"
}

# --- 9. MAIN ---
main() {
    log_info "Starting Elite DevOps GPU Configuration..."
    check_deps
    detect_topology
    select_mode
    generate_config
    
    log_info "Previewing generated config:"
    echo "-------------------------------------"
    grep -E "AQ_DRM|LIBVA|Mode:" "$OUTPUT_FILE" || true
    echo "-------------------------------------"
    log_ok "Done. Please restart your UWSM session."
}

main "$@"
