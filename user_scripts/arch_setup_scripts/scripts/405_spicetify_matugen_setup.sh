#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 405_spicetify_matugen_setup.sh
# Description: "Golden State" Spicetify Setup.
#              - Resurrection: Detects & fixes deleted/phantom installs.
#              - Warm-Up: Uses 'timeout' to safely generate 'offline.bnk'.
#              - Auto-Heals: Segfaults, Version Mismatches, and Permissions.
# -----------------------------------------------------------------------------

# Strict Mode
set -Eeuo pipefail

# --- Configuration ---
readonly REQUIRED_BASH_MAJOR=5
readonly REQUIRED_BASH_MINOR=3
readonly SPICETIFY_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spicetify"
readonly SPOTIFY_PREFS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/spotify"
readonly SPOTIFY_AUR_KEY="931FF8E79F0876134EDDBDCCA87FF9DF48BF1C90"

# --- Visual Feedback ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m'
    readonly C_INFO=$'\033[1;34m'    # Blue
    readonly C_SUCCESS=$'\033[1;32m' # Green
    readonly C_WARN=$'\033[1;33m'    # Yellow
    readonly C_ERR=$'\033[1;31m'     # Red
    readonly C_MAG=$'\033[1;35m'     # Magenta
else
    readonly C_RESET='' C_INFO='' C_SUCCESS='' C_WARN='' C_ERR='' C_MAG=''
fi

log_info()    { printf '%s[INFO]%s %s\n' "${C_INFO}" "${C_RESET}" "$*"; }
log_success() { printf '%s[OK]%s %s\n' "${C_SUCCESS}" "${C_RESET}" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "${C_WARN}" "${C_RESET}" "$*" >&2; }
log_heal()    { printf '%s[HEAL]%s %s\n' "${C_MAG}" "${C_RESET}" "$*"; }
die()         { printf '%s[FATAL]%s %s\n' "${C_ERR}" "${C_RESET}" "$*" >&2; exit 1; }

# --- 1. Guard Rails & Dependencies ---
check_system() {
    if [[ $EUID -eq 0 ]]; then die "Do not run as root."; fi
    
    if ((BASH_VERSINFO[0] < REQUIRED_BASH_MAJOR)) || \
       ((BASH_VERSINFO[0] == REQUIRED_BASH_MAJOR && BASH_VERSINFO[1] < REQUIRED_BASH_MINOR)); then
        die "Bash 5.3+ required. Current: ${BASH_VERSION}"
    fi

    local missing=()
    for cmd in git curl sudo pkill timeout; do
        if ! command -v "$cmd" &>/dev/null; then missing+=("$cmd"); fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then die "Missing dependencies: ${missing[*]}"; fi
}

# --- 2. Smart Install (Phantom Detection) ---
ensure_packages_updated() {
    log_info "Checking Spotify installation integrity..."
    
    local helper
    if command -v paru &>/dev/null; then helper="paru"
    elif command -v yay &>/dev/null; then helper="yay"
    else die "No AUR helper found."; fi

    # GPG Key (Fixed: Removed bad fallback)
    if ! gpg --list-keys "$SPOTIFY_AUR_KEY" &>/dev/null; then
        log_info "Importing Spotify GPG key..."
        if ! gpg --keyserver keyserver.ubuntu.com --recv-keys "$SPOTIFY_AUR_KEY"; then
             log_warn "GPG import failed. Installation might fail if key is missing."
        fi
    fi

    # PHANTOM CHECK: Package installed but directory gone?
    local phantom_install=false
    if pacman -Qi spotify &>/dev/null; then
        if [[ ! -d "/opt/spotify" && ! -d "/usr/share/spotify" ]]; then
            phantom_install=true
        fi
    fi

    if [[ "$phantom_install" == "true" ]]; then
        log_heal "Phantom install detected. Forcing Reinstall..."
        "$helper" -S --noconfirm spotify spicetify-cli
    else
        "$helper" -S --needed --noconfirm spotify spicetify-cli
    fi
    
    log_success "Packages are current."
}

# --- 3. Path & Permissions ---
fix_spotify_permissions() {
    log_info "Locating Spotify..."
    
    local spotify_path
    spotify_path=${| 
        if [[ -d "/opt/spotify" ]]; then REPLY="/opt/spotify"
        elif [[ -d "$HOME/.local/share/spotify-launcher/install/usr/share/spotify" ]]; then
            REPLY="$HOME/.local/share/spotify-launcher/install/usr/share/spotify"
        elif [[ -d "/usr/share/spotify" ]]; then REPLY="/usr/share/spotify"
        else REPLY=""; fi
    }

    if [[ -z "$spotify_path" ]]; then die "Could not locate Spotify directory."; fi

    if [[ -w "$spotify_path" && -w "$spotify_path/Apps" ]]; then
        log_success "Permissions OK ($spotify_path)."
    else
        log_warn "Fixing permissions for $spotify_path..."
        if sudo chmod a+wr "${spotify_path}" && \
           sudo chmod -R a+wr "${spotify_path}/Apps"; then
            log_success "Permissions granted."
        else
            die "Failed to grant permissions."
        fi
    fi
    REPLY="$spotify_path"
}

# --- 4. The Warm Up (Simplified) ---
warm_up_spotify() {
    log_info "Warming up Spotify (10s) to generate 'offline.bnk'..."
    
    pkill -u "$EUID" -x spotify || true
    
    # Run for 10s then kill
    timeout -k 5s 10s spotify >/dev/null 2>&1 || true
    
    # Check if successful (Optional verification)
    if [[ -f "$HOME/.cache/spotify/offline.bnk" ]]; then
        log_success "Warm up successful (offline.bnk generated)."
    else
        log_info "Warm up complete (offline.bnk check skipped)."
    fi
}

# --- 5. Configuration ---
prepare_assets() {
    log_info "Downloading assets..."
    local mk_dir="${SPICETIFY_CONFIG_DIR}/CustomApps/marketplace"
    if [[ ! -d "$mk_dir" ]]; then
        curl -fsSL "https://raw.githubusercontent.com/spicetify/spicetify-marketplace/main/resources/install.sh" | bash
    fi

    local ext_dir="${SPICETIFY_CONFIG_DIR}/Extensions"
    mkdir -p "$ext_dir"
    if [[ ! -f "$ext_dir/adblock.js" ]]; then
        curl -fsSL "https://raw.githubusercontent.com/rxri/spicetify-extensions/main/adblock/adblock.js" -o "$ext_dir/adblock.js"
    fi

    local comfy_dir="${SPICETIFY_CONFIG_DIR}/Themes/Comfy"
    mkdir -p "$(dirname "$comfy_dir")"
    if [[ ! -d "$comfy_dir" ]]; then
        git clone --depth 1 https://github.com/Comfy-Themes/Spicetify "$comfy_dir"
    elif [[ -d "$comfy_dir/.git" ]]; then
        if [[ -L "$comfy_dir/color.ini" ]]; then
             log_info "Matugen detected. Preserving theme colors."
        else
             git -C "$comfy_dir" pull --ff-only || true
        fi
    fi
}

configure_spicetify() {
    local install_path="$1"
    log_info "Configuring Spicetify..."
    
    if [[ ! -f "$SPICETIFY_CONFIG_DIR/config-xpui.ini" ]]; then
        spicetify >/dev/null 2>&1 || true
    fi

    spicetify config \
        spotify_path "${install_path}" \
        prefs_path "${SPOTIFY_PREFS_DIR}/prefs" \
        current_theme Comfy \
        color_scheme Comfy \
        inject_css 1 \
        replace_colors 1 \
        overwrite_assets 1 \
        extensions adblock.js > /dev/null
}

# --- 6. The Kill Switch ---
kill_spotify_hard() {
    if pgrep -u "$EUID" -x spotify >/dev/null; then
        log_info "Closing Spotify..."
        pkill -u "$EUID" -x spotify || true
        
        local retries=50
        while ((retries > 0)); do
            if ! pgrep -u "$EUID" -x spotify >/dev/null; then return 0; fi
            sleep 0.1
            ((retries--))
        done
        
        log_warn "Forcing shutdown..."
        pkill -9 -u "$EUID" -x spotify || true
    fi
}

# --- 7. The Nuclear Heal ---
nuke_cache_and_heal() {
    log_heal "Performing NUCLEAR cleanup..."
    kill_spotify_hard
    
    rm -f "${SPICETIFY_CONFIG_DIR}/backup.json"
    
    local helper
    if command -v paru &>/dev/null; then helper="paru"
    elif command -v yay &>/dev/null; then helper="yay"
    else die "No AUR helper found for reinstall."; fi
    
    log_heal "Reinstalling binary..."
    "$helper" -S --noconfirm spotify

    # Fixed: Independent deletions
    rm -rf "$HOME/.cache/spotify"
    rm -rf "$HOME/.config/spotify/Users" 
    rm -rf "$HOME/.config/spotify/GPUCache"
    log_success "Cache nuked."

    local path="$1"
    if [[ -d "${path}" ]]; then
        sudo chmod a+wr "${path}"
        sudo chmod -R a+wr "${path}/Apps"
    fi
    
    warm_up_spotify
}

apply_changes() {
    local install_path="$1"
    
    log_info "Applying patches..."
    kill_spotify_hard

    # Attempt 1
    if spicetify backup apply enable-devtools; then
        log_success "Spicetify applied successfully."
        return 0
    fi

    # Attempt 2: Nuclear Heal
    log_heal "Patch failed. Initiating Nuclear Protocol."
    nuke_cache_and_heal "$install_path"
    
    # CRITICAL FIX: Re-verify path and RE-CONFIGURE Spicetify
    fix_spotify_permissions # Update REPLY
    install_path="$REPLY"
    configure_spicetify "$install_path"

    log_heal "Retrying injection..."
    if spicetify backup apply enable-devtools; then
        log_success "System healed and patched."
    else
        die "Spicetify failed. Please check logs."
    fi
}

# --- Main ---
main() {
    check_system
    ensure_packages_updated
    
    local detected_path
    fix_spotify_permissions
    detected_path="$REPLY"

    # Ensure clean slate
    kill_spotify_hard
    
    # Warm up to generate offline.bnk
    warm_up_spotify

    prepare_assets
    configure_spicetify "$detected_path"
    apply_changes "$detected_path"

    echo ""
    log_success "Setup Complete."
    
    if ! pgrep -u "$EUID" -x spotify >/dev/null; then
        nohup spotify >/dev/null 2>&1 &
    fi
}

main "$@"
