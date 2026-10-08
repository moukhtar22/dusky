#!/usr/bin/env bash
#d: Install the Bibata cursor theme

set -Eeuo pipefail
shopt -s inherit_errexit
umask 022

# 2. Configuration
readonly THEME_NAME="Bibata-Modern-Classic"
readonly CURSOR_SIZE=18
readonly REPO_URL="https://github.com/ful1e5/Bibata_Cursor"
# HOME is guaranteed by login/PAM/systemd on Arch (pam_env(8), systemd.exec(5)
# SetLoginEnvironment). Fail fast instead of guessing a home directory.
: "${HOME:?HOME must be set}"
readonly USER_HOME="${HOME}"
readonly XDG_DATA_HOME="${XDG_DATA_HOME:-${USER_HOME}/.local/share}"
readonly ICON_DIR="${XDG_DATA_HOME}/icons"
readonly THEME_PATH="${ICON_DIR}/${THEME_NAME}"

# Network settings (split: tiny version probe vs multi-MB download)
readonly CURL_CONNECT_TIMEOUT=10
readonly CURL_VERSION_TIMEOUT=15
readonly CURL_DOWNLOAD_TIMEOUT=120
readonly CURL_RETRIES=3
readonly CURL_RETRY_DELAY=2
# Shared hardened curl base. --retry-all-errors is only safe with curl-managed
# -o <file> (it truncates before retry); never use it with a pipe to tar.
readonly -a CURL_COMMON=(
    --location
    --proto '=https'
    --proto-redir '=https'
    --tlsv1.2
    --max-redirs 5
    --connect-timeout "${CURL_CONNECT_TIMEOUT}"
    --retry "${CURL_RETRIES}"
    --retry-all-errors
    --retry-delay "${CURL_RETRY_DELAY}"
    --retry-max-time 60
)

# 3. Colors (Safe & Compact)
if [[ -z ${NO_COLOR-} ]] && [[ -n ${TERM-} ]] && [[ -t 1 ]] && command -v tput &>/dev/null && [[ $(tput colors 2>/dev/null || echo -1) -ge 8 ]]; then
    BLUE=$(tput setaf 4 2>/dev/null || true)
    GREEN=$(tput setaf 2 2>/dev/null || true)
    YELLOW=$(tput setaf 3 2>/dev/null || true)
    RED=$(tput setaf 1 2>/dev/null || true)
    RESET=$(tput sgr0 2>/dev/null || true)
else
    BLUE='' GREEN='' YELLOW='' RED='' RESET=''
fi

# 4. Logging Helpers
log_info()    { printf -- "%s[INFO]%s %s\n" "${BLUE}" "${RESET}" "$*"; }
log_success() { printf -- "%s[OK]%s %s\n" "${GREEN}" "${RESET}" "$*"; }
log_warn()    { printf -- "%s[WARN]%s %s\n" "${YELLOW}" "${RESET}" "$*" >&2; }
log_error()   { printf -- "%s[ERROR]%s %s\n" "${RED}" "${RESET}" "$*" >&2; }

die() { log_error "$*"; exit 1; }

# Friendly diagnostics for unexpected failures (requires -E/errtrace above).
# This never fires for handled errors (if-conditions, ||/&& lists, hyprctl
# fallback), only for genuine bugs that errexit would otherwise kill silently.
trap 'log_error "Unexpected failure (exit $?) at line ${LINENO}: ${BASH_COMMAND}"' ERR

usage() {
    printf -- 'Usage: %s [-h|--help]\n' "${0##*/}"
    printf -- 'Install the Bibata cursor theme (%s) to ~/.local/share/icons.\n' "${THEME_NAME}"
}

# 5. Functions
check_dependencies() {
    local cmd
    for cmd in curl tar mktemp; do
        command -v "$cmd" &>/dev/null || die "Missing dependency: $cmd"
    done
}

get_latest_version() {
    local url
    url=$(curl -sS "${CURL_COMMON[@]}" --fail-with-body \
        --max-time "${CURL_VERSION_TIMEOUT}" \
        -o /dev/null -w '%{url_effective}' \
        "${REPO_URL}/releases/latest") || return 1

    # Strip any query/fragment/trailing slash the redirect may carry.
    url="${url%%\?*}"
    url="${url%%\#*}"
    url="${url%/}"

    local version="${url##*/}"

    # Full anchor: tags are vMAJOR.MINOR.PATCH with optional pre-release suffix.
    if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$ ]]; then
        return 1
    fi
    printf -- '%s' "$version"
}

update_legacy_index() {
    # This ensures apps that don't respect Hyprland env vars (like some GTK2/X11 apps)
    # still see the correct cursor.
    [[ "${THEME_NAME}" =~ ^[A-Za-z0-9._-]+$ ]] || die "Refusing to write index.theme: bad THEME_NAME"
    local default_dir="${ICON_DIR}/default"
    mkdir -p -- "$default_dir" || die "Cannot create ${default_dir}"
    # Remove a pre-planted symlink so we never follow it, then write atomically.
    if [[ -L "${default_dir}/index.theme" ]]; then
        rm -f -- "${default_dir}/index.theme" || die "Cannot remove stale index.theme symlink"
    fi
    local tmp_theme
    tmp_theme=$(mktemp -- "${default_dir}/.index.theme.XXXXXX") || die "Cannot create temp index.theme"
    printf -- '[Icon Theme]\nName=Default\nComment=Default Cursor Theme\nInherits=%s\n' \
        "${THEME_NAME}" > "$tmp_theme" || die "Cannot write index.theme"
    chmod 0644 -- "$tmp_theme" || die "Cannot chmod index.theme"
    mv -- "$tmp_theme" "${default_dir}/index.theme" || die "Cannot install index.theme"
    log_info "Updated legacy index.theme fallback."
}

# Staging dir for atomic install (cleaned on any exit).
STAGING_DIR=""
cleanup_staging() {
    if [[ -n "${STAGING_DIR:-}" && -d "${STAGING_DIR}" ]]; then
        rm -rf -- "${STAGING_DIR}"
    fi
    STAGING_DIR=""
}
trap cleanup_staging EXIT INT TERM

# 6. Main Execution
main() {
    case "${1-}" in
        -h|--help) usage; return 0 ;;
        '') ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac

    log_info "Starting Bibata Cursor setup..."
    check_dependencies

    # --- Version Detection ---
    log_info "Resolving latest version..."
    local latest_ver
    latest_ver=$(get_latest_version) || die "Could not detect latest version from GitHub."

    log_info "Target: ${THEME_NAME} (${latest_ver}) @ ${CURSOR_SIZE}px"

    # --- Preparation ---
    local dl_url="${REPO_URL}/releases/download/${latest_ver}/${THEME_NAME}.tar.xz"
    mkdir -p -- "${ICON_DIR}" || die "Cannot create ${ICON_DIR}"

    # Containment guard: never rm outside ICON_DIR, never rm root.
    : "${THEME_PATH:?THEME_PATH empty, aborting}"
    : "${ICON_DIR:?ICON_DIR empty, aborting}"
    case "${THEME_PATH}" in
        "${ICON_DIR}"/*) ;;
        *) die "Refusing to remove: THEME_PATH [${THEME_PATH}] outside ICON_DIR [${ICON_DIR}]" ;;
    esac
    [[ "${THEME_PATH}" != "/" ]] || die "Refusing to remove root"

    # Staging lives inside ICON_DIR on purpose: same filesystem, so the final
    # mv -T below is a true atomic rename (never a cross-device copy).
    STAGING_DIR=$(mktemp -d -- "${ICON_DIR}/.bibata-staging.XXXXXX") || die "Cannot create staging dir"
    local tmpfile="${STAGING_DIR}/dl.tar.xz"
    # --- Atomic download to file (curl-managed -o, so retries are safe) ---
    log_info "Downloading..."
    if ! curl -fS "${CURL_COMMON[@]}" \
            --speed-limit 1024 --speed-time 30 \
            --max-time "${CURL_DOWNLOAD_TIMEOUT}" \
            --remove-on-error \
            -o "$tmpfile" "$dl_url"; then
        die "Download failed (existing theme untouched)."
    fi

    # --- Extract into staging, never into the live dir ---
    log_info "Extracting..."
    if ! tar -xJ --no-same-owner --no-same-permissions --no-acls --no-selinux --no-xattrs -f "$tmpfile" -C "${STAGING_DIR}"; then
        die "Extraction failed (existing theme untouched)."
    fi
    [[ -d "${STAGING_DIR}/${THEME_NAME}/cursors" ]] || die "Archive has unexpected layout (missing ${THEME_NAME}/cursors)."

    # --- Atomic swap: old theme moved aside only after new one verifies ---
    if [[ -d "${THEME_PATH}" && ! -L "${THEME_PATH}" ]]; then
        log_warn "Removing existing installation for clean update..."
        rm -rf -- "${THEME_PATH}.bak" || die "Cannot clear backup slot"
        mv -T -- "${THEME_PATH}" "${THEME_PATH}.bak" || die "Cannot stage old theme aside"
    elif [[ -L "${THEME_PATH}" ]]; then
        rm -f -- "${THEME_PATH}" || die "Cannot remove stale theme symlink"
    elif [[ -e "${THEME_PATH}" ]]; then
        # A stray file (or other non-directory node) blocks the swap: mv
        # refuses to overwrite a non-directory with a directory.
        rm -f -- "${THEME_PATH}" || die "Cannot remove obstructing file at ${THEME_PATH}"
    fi
    if ! mv -T -- "${STAGING_DIR}/${THEME_NAME}" "${THEME_PATH}"; then
        # Best-effort rollback.
        if [[ -d "${THEME_PATH}.bak" ]]; then
            mv -T -- "${THEME_PATH}.bak" "${THEME_PATH}" || true
        fi
        die "Atomic install failed."
    fi
    rm -rf -- "${THEME_PATH}.bak" || true
    # Staging consumed (cleanup is now a no-op via empty STAGING_DIR).
    rm -rf -- "${STAGING_DIR}" || true
    STAGING_DIR=""

    [[ -d "${THEME_PATH}" ]] || die "Install verification failed."
    log_success "Installed to ${THEME_PATH}"

    # --- Configuration ---
    # Dusky owns every layer (including the legacy default/index.theme
    # fallback) once installed: hand off to it first, so a rerun of this
    # installer can never clobber Dusky state. Fully optional: skipped when
    # Dusky/python3 is absent, and any failure falls back to the standalone
    # Bibata path below (which then owns the fallback + live apply itself).
    local dusky_script="${HOME}/user_scripts/cursor/color/dusky_cursor.py"
    if [[ -d "${ICON_DIR}/Dusky/cursors" ]] \
        && [[ -f "${dusky_script}" ]] \
        && command -v python3 &>/dev/null; then
        log_info "Dusky theme detected; reconciling via dusky_cursor.py..."
        if python3 "${dusky_script}" --apply --quiet; then
            log_success "Cursor active (Dusky)."
            return 0
        else
            log_warn "dusky_cursor.py failed; falling back to standalone Bibata apply."
        fi
    fi

    # Standalone Bibata path (no Dusky installed).
    # 1. Update legacy fallback
    update_legacy_index

    # 2. Apply to Hyprland (Live). Persistence across reboots is owned by the
    # Hyprland env config (XCURSOR_THEME/XCURSOR_SIZE), not this installer.
    if command -v hyprctl &>/dev/null && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
        log_info "Applying to Hyprland..."
        if hyprctl setcursor "${THEME_NAME}" "${CURSOR_SIZE}" >/dev/null; then
            log_success "Cursor active."
        else
            log_warn "hyprctl failed to set cursor (check logs)."
        fi
    else
        log_warn "Hyprland not running/detected. Cursor installed but not active."
    fi
}

main "$@"
