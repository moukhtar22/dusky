#!/usr/bin/env bash
# ==============================================================================
# Migrates/Moves legacy Hyprland .conf files to a safe backup directory.
#              Designed to run before generating the new Hyprland 0.55+ .lua files.
#              - Strictly targets only .conf files (ignores .lua).
#              - Moves (does not copy) files so the directory is clean.
#              - Idempotent: safe to run repeatedly by an updater script.
#              - Maintains relative directory structure to prevent name collisions.
# ==============================================================================

set -euo pipefail

# --- ANSI Color Codes ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[0;33m'
readonly BLUE=$'\033[0;34m'
readonly RESET=$'\033[0m'

# --- Paths ---
readonly HYPR_DIR="${HOME}/.config/hypr"
readonly EDIT_DIR="${HYPR_DIR}/edit_here"
readonly SOURCE_DIR="${EDIT_DIR}/source"
readonly BACKUP_DIR="${HOME}/Documents/dusky_backups/hyprland_conf"

# --- Helper Functions ---
log_info()    { printf '%s[INFO]%s %s\n'    "${BLUE}"   "${RESET}" "${1:-}"; }
log_success() { printf '%s[OK]%s   %s\n'    "${GREEN}"  "${RESET}" "${1:-}"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "${YELLOW}" "${RESET}" "${1:-}"; }
log_error()   { printf '%s[ERR]%s  %s\n'    "${RED}"    "${RESET}" "${1:-}" >&2; }

# ------------------------------------------------------------------------------
# Pre-flight Checks
# ------------------------------------------------------------------------------
if [[ "${EUID}" -eq 0 ]]; then
    log_error "This script must NOT be run as root."
    exit 1
fi

log_info "Initializing legacy .conf file migration..."

# Ensure the root backup directory exists
if [[ ! -d "${BACKUP_DIR}" ]]; then
    log_info "Creating backup directory: ${BACKUP_DIR}"
    mkdir -p -- "${BACKUP_DIR}"
fi

# Enable nullglob: if no *.conf files exist in source/, it cleanly expands to nothing
# instead of passing a literal string "*.conf" to the loop.
shopt -s nullglob

# Explicitly define the targeted files / globs as requested
readonly -a TARGET_FILES=(
#    "${HYPR_DIR}/hyprland.conf"
    "${EDIT_DIR}/hyprland.conf"
    "${SOURCE_DIR}/"*.conf
)

# Disable nullglob now that we have populated our safe array
shopt -u nullglob

# ------------------------------------------------------------------------------
# Main Logic: Move and Backup
# ------------------------------------------------------------------------------
moved_count=0
failed_count=0

for file_path in "${TARGET_FILES[@]}"; do
    # 1. Idempotency Check: If the file is already moved/missing, skip silently.
    if [[ ! -f "${file_path}" ]]; then
        continue
    fi

    # 2. Hard Safeguard: Absolutely DO NOT touch .lua files.
    if [[ "${file_path}" == *.lua ]]; then
        log_warn "Safeguard triggered: Ignored .lua file at ${file_path}"
        continue
    fi

    # 3. Double-check requirement: Ensure it is strictly a .conf file.
    if [[ "${file_path}" != *.conf ]]; then
        continue
    fi

    # 4. Resolve destination path while maintaining relative directory structure
    #    to prevent name collisions between identically named files in different dirs.
    #    Example: ~/.config/hypr/edit_here/hyprland.conf -> edit_here/hyprland.conf
    rel_path="${file_path#${HYPR_DIR}/}"
    dest_path="${BACKUP_DIR}/${rel_path}"

    # 5. Guard against silently clobbering an existing backup.
    #    If a backup already exists, append a timestamp so the active folder 
    #    is still successfully cleaned without destroying historical backups.
    if [[ -e "${dest_path}" ]]; then
        # Bash 5.0+ built-in timestamp generation
        printf -v timestamp '%(%Y%m%d_%H%M%S)T' -1
        dest_path="${dest_path}_${timestamp}"
        log_warn "Backup collision detected. Redirecting to: ${dest_path}"
    fi

    # Determine the destination directory based on the final dest_path
    dest_dir="${dest_path%/*}"

    # Ensure the nested destination directory structure exists before moving
    mkdir -p -- "${dest_dir}"

    # 6. Move the file
    log_info "Moving: ${rel_path} ..."
    if mv -- "${file_path}" "${dest_path}"; then
        log_success "  -> Backed up to: ${dest_path}"
        ((++moved_count))
    else
        log_error "  -> Failed to move ${file_path}"
        ((++failed_count))
    fi
done

# ------------------------------------------------------------------------------
# Completion status
# ------------------------------------------------------------------------------
printf '\n'
if [[ ${failed_count} -gt 0 ]]; then
    log_error "Migration completed with ${failed_count} error(s). ${moved_count} file(s) moved successfully."
    exit 1
elif [[ ${moved_count} -eq 0 ]]; then
    log_success "No legacy .conf files found. (Already migrated!)"
else
    log_success "Successfully safely moved ${moved_count} legacy .conf file(s)."
    log_info "Backup location: ${BACKUP_DIR}"
fi

exit 0
