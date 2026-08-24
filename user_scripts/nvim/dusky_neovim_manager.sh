#!/usr/bin/env bash
# ==============================================================================
#  DUSKY NEOVIM MANAGER
# ==============================================================================
#  Target: Arch Linux | Bash 5.1+ | Wayland/Hyprland ecosystem
#  Purpose: Elite Neovim configuration deployer, state manager, and synchronizer.
# ==============================================================================
set -euo pipefail

# ==============================================================================
# Visual Formatting & Logging (ANSI-C Quoting)
# ==============================================================================
declare -r GREEN=$'\033[0;32m'
declare -r BLUE=$'\033[0;34m'
declare -r RED=$'\033[0;31m'
declare -r YELLOW=$'\033[0;33m'
declare -r BOLD=$'\033[1m'
declare -r RESET=$'\033[0m'

log_info()    { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$GREEN" "$RESET" "$*"; }
log_warn()    { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*"; }

# ==============================================================================
# Configuration & Globals
# ==============================================================================
readonly BACKUP_DIR="${HOME}/.local/share/nvim_backups"
readonly DUSKY_SRC="${XDG_CONFIG_HOME:-$HOME/.config}/dusky_nvim"

declare -A NVIM_PATHS=(
    [config]="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"
    [data]="${XDG_DATA_HOME:-$HOME/.local/share}/nvim"
    [state]="${XDG_STATE_HOME:-$HOME/.local/state}/nvim"
    [cache]="${XDG_CACHE_HOME:-$HOME/.cache}/nvim"
)

CURRENT_BACKUP_PATH=""
AUTONOMOUS_MODE=false
INSTALL_TARGET=""

# ==============================================================================
# Safety Helpers
# ==============================================================================
is_safe_nvim_path() {
    local p="$1"
    # Must be non-empty, not root, inside $HOME or XDG, and contain 'nvim'
    [[ -n "$p" ]] || return 1
    [[ "$p" != "/" ]] || return 1
    [[ "$p" != "$HOME" ]] || return 1
    [[ "$p" == "$HOME"/* || "$p" == "${XDG_CONFIG_HOME:-$HOME/.config}"/* || "$p" == "${XDG_DATA_HOME:-$HOME/.local/share}"/* || "$p" == "${XDG_STATE_HOME:-$HOME/.local/state}"/* || "$p" == "${XDG_CACHE_HOME:-$HOME/.cache}"/* ]] || return 1
    [[ "$p" == *nvim* ]] || return 1
    return 0
}

safe_rm() {
    local target="$1"
    if ! is_safe_nvim_path "$target"; then
        log_error "Refusing to remove unsafe path: $target"
        return 1
    fi
    if [[ -e "$target" ]]; then
        rm -rf -- "$target"
    fi
}

# ==============================================================================
# Initialization & Traps
# ==============================================================================
cleanup_on_exit() {
    local ec=$?
    if (( ec != 0 )) && [[ -n "${CURRENT_BACKUP_PATH}" && -d "${CURRENT_BACKUP_PATH}" ]]; then
        echo ""
        log_warn "Deployment interrupted. Your previous state is backed up at:"
        printf '  %s%s%s\n' "$BLUE" "$CURRENT_BACKUP_PATH" "$RESET"
    fi
    # Do NOT call exit here — EXIT trap must not recurse
    return "$ec"
}

cleanup_on_signal() {
    local sig="$1"
    local ec=$?
    if [[ -n "${CURRENT_BACKUP_PATH}" && -d "${CURRENT_BACKUP_PATH}" ]]; then
        echo ""
        log_warn "Received ${sig}. Your previous state is backed up at:"
        printf '  %s%s%s\n' "$BLUE" "$CURRENT_BACKUP_PATH" "$RESET"
    fi
    trap - INT TERM EXIT
    exit "$ec"
}

trap cleanup_on_exit EXIT
trap 'cleanup_on_signal INT' INT
trap 'cleanup_on_signal TERM' TERM

disable_traps() {
    trap - INT TERM EXIT
}

check_dependencies() {
    local missing_deps=()
    local cmd
    for cmd in git nvim timeout; do
        if ! command -v "${cmd}" &>/dev/null; then
            missing_deps+=("${cmd}")
        fi
    done
    if (( ${#missing_deps[@]} > 0 )); then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_info "Please install them before running this manager."
        disable_traps
        exit 1
    fi
}

# ==============================================================================
# CLI Argument Parsing
# ==============================================================================
print_help() {
    # Use %s format to satisfy shellcheck SC2059
    printf '%s====================================================%s\n' "$BOLD" "$RESET"
    printf '%s               Dusky Neovim Manager                 %s\n' "$BOLD" "$RESET"
    printf '%s====================================================%s\n\n' "$BOLD" "$RESET"
    printf 'Usage: %s [OPTIONS]\n\n' "$(basename "$0")"
    printf '%sOptions:%s\n' "$BOLD" "$RESET"
    printf '  %s-h, --help%s      Show this help menu and exit.\n' "$GREEN" "$RESET"
    printf '  %s-a, --auto%s      Enable autonomous (non-interactive) mode.\n' "$GREEN" "$RESET"
    printf '  %s-t, --target%s    Specify installation target (nvchad, lazyvim, astronvim, dusky).\n' "$GREEN" "$RESET"
    printf '\n'
    printf '%sDescription:%s\n' "$BOLD" "$RESET"
    printf '  When executed without flags, launches the interactive UI.\n'
    printf '  In autonomous mode (-a), the script implicitly enforces safety by backing up\n'
    printf '  the existing state and running a headless plugin sync post-installation.\n'
    printf '\n'
    printf '%sExamples:%s\n' "$BOLD" "$RESET"
    printf '  %s -a -t lazyvim\n' "$(basename "$0")"
    printf '  %s --auto --target nvchad\n\n' "$(basename "$0")"
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                print_help
                disable_traps
                exit 0
                ;;
            -a|--auto)
                AUTONOMOUS_MODE=true
                shift
                ;;
            -t|--target)
                if [[ -z "${2:-}" || "$2" == -* ]]; then
                    log_error "Target missing for $1 flag."
                    disable_traps
                    exit 1
                fi
                INSTALL_TARGET="$2"
                shift 2
                ;;
            *)
                log_error "Unknown argument: $1"
                print_help
                disable_traps
                exit 1
                ;;
        esac
    done
}

# ==============================================================================
# State Management (Backup, Wipe, Restore, Reset)
# ==============================================================================
backup_neovim_state() {
    local has_data=false key dir
    for key in "${!NVIM_PATHS[@]}"; do
        if [[ -d "${NVIM_PATHS[$key]}" ]]; then
            has_data=true
            break
        fi
    done
    if ! ${has_data}; then
        log_info "No existing Neovim directories found. Skipping backup."
        return 0
    fi

    # Use mktemp to avoid EPOCHSECONDS collision (second granularity)
    mkdir -p -- "$BACKUP_DIR"
    local tmp
    # shellcheck disable=SC2172
    tmp="$(mktemp -d "${BACKUP_DIR}/backup_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
    CURRENT_BACKUP_PATH="$tmp"

    log_info "Creating structured backup at ${CURRENT_BACKUP_PATH}..."
    local moved=0
    for key in "${!NVIM_PATHS[@]}"; do
        dir="${NVIM_PATHS[$key]}"
        if [[ -d "${dir}" ]]; then
            if ! is_safe_nvim_path "$dir"; then
                log_error "Skipping unsafe path: $dir"
                continue
            fi
            # mv is atomic on same FS; fallback to cp -a + rm if cross-FS
            if mv -- "$dir" "${CURRENT_BACKUP_PATH}/${key}" 2>/dev/null; then
                printf '  %s✓%s Backed up: %s -> %s\n' "$GREEN" "$RESET" "$dir" "$key"
                moved=$((moved+1))
            else
                # Cross-FS fallback
                if cp -a -- "$dir" "${CURRENT_BACKUP_PATH}/${key}" && safe_rm "$dir"; then
                    printf '  %s✓%s Backed up (cp): %s -> %s\n' "$GREEN" "$RESET" "$dir" "$key"
                    moved=$((moved+1))
                else
                    log_error "Failed to back up $dir"
                fi
            fi
        fi
    done
    if (( moved == 0 )); then
        log_warn "Backup created but no components were moved (race?)."
    fi
}

wipe_neovim_state() {
    local key dir
    log_warn "Surgically wiping current Neovim state..."
    for key in "${!NVIM_PATHS[@]}"; do
        dir="${NVIM_PATHS[$key]}"
        if [[ -d "${dir}" ]]; then
            if safe_rm "$dir"; then
                printf '  %s✗%s Deleted: %s\n' "$RED" "$RESET" "$dir"
            fi
        fi
    done
}

reset_neovim_state() {
    local key dir
    log_warn "Surgically wiping Neovim data, state, and cache..."
    for key in "${!NVIM_PATHS[@]}"; do
        if [[ "${key}" == "config" ]]; then
            continue # Shield the configuration directory
        fi
        dir="${NVIM_PATHS[$key]}"
        if [[ -d "${dir}" ]]; then
            if safe_rm "$dir"; then
                printf '  %s✗%s Deleted: %s\n' "$RED" "$RESET" "$dir"
            fi
        fi
    done
    log_success "State reset successfully. Configuration preserved."
}

restore_neovim_state() {
    if [[ ! -d "${BACKUP_DIR}" ]]; then
        log_warn "Backup directory does not exist: ${BACKUP_DIR}"
        return 1
    fi

    local available_backups
    # Portable: no GNU -printf, use -exec basename
    mapfile -t available_backups < <(find "${BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'backup_*' -exec basename {} \; 2>/dev/null | sort -r)

    if (( ${#available_backups[@]} == 0 )); then
        log_warn "No backups found in ${BACKUP_DIR}."
        return 1
    fi

    echo ""
    log_info "Available Backups:"

    local original_ps3="${PS3-}"
    local ps3_was_set=0; [[ -v PS3 ]] && ps3_was_set=1
    local original_columns="${COLUMNS-}"
    local col_was_set=0; [[ -v COLUMNS ]] && col_was_set=1

    PS3="${BOLD}${YELLOW}Select a backup to restore: ${RESET}"
    COLUMNS=1

    local restore_status=1
    local backup_name
    select backup_name in "${available_backups[@]}" "Cancel"; do
        if [[ "${backup_name}" == "Cancel" ]]; then
            log_info "Restore cancelled."
            restore_status=1
            break
        elif [[ -n "${backup_name}" ]]; then
            local target_backup="${BACKUP_DIR}/${backup_name}"
            if [[ ! -d "$target_backup" ]]; then
                log_error "Selected backup no longer exists: $target_backup"
                restore_status=1
                break
            fi
            log_warn "You are about to restore an older state."
            prompt_state_management

            log_info "Initiating restore from ${backup_name}..."

            local key dir restored_count=0
            for key in "${!NVIM_PATHS[@]}"; do
                dir="${NVIM_PATHS[$key]}"
                if [[ -d "${target_backup}/${key}" ]]; then
                    mkdir -p -- "$dir"
                    # Ensure target is safe before overwriting
                    if ! is_safe_nvim_path "$dir"; then
                        log_error "Refusing to restore to unsafe path: $dir"
                        continue
                    fi
                    # Remove existing if present (safe)
                    if [[ -e "$dir" ]]; then
                        safe_rm "$dir" || continue
                        mkdir -p -- "$dir"
                    fi
                    cp -a -- "${target_backup}/${key}/." "${dir}/"
                    printf '  %s✓%s Restored: %s\n' "$GREEN" "$RESET" "$dir"
                    restored_count=$((restored_count + 1))
                fi
            done

            if (( restored_count > 0 )); then
                log_success "Restore completed successfully (${restored_count} components)."
                restore_status=0
            else
                log_error "Selected backup was empty. Nothing was restored."
                restore_status=1
            fi
            break
        else
            log_error "Invalid selection."
        fi
    done

    if (( ps3_was_set )); then PS3="${original_ps3}"; else unset PS3; fi
    if (( col_was_set )); then COLUMNS="${original_columns}"; else unset COLUMNS; fi
    return "${restore_status}"
}

# ==============================================================================
# Installation Handlers
# ==============================================================================
install_nvchad() {
    log_info "Deploying NvChad..."
    if [[ -e "${NVIM_PATHS[config]}" ]]; then
        log_error "Config dir already exists: ${NVIM_PATHS[config]} (backup/wipe first)"
        return 1
    fi
    mkdir -p -- "${NVIM_PATHS[config]}"
    if ! git clone --depth 1 https://github.com/NvChad/starter "${NVIM_PATHS[config]}"; then
        log_error "git clone failed for NvChad"
        safe_rm "${NVIM_PATHS[config]}"
        return 1
    fi
    rm -rf -- "${NVIM_PATHS[config]}/.git"
    log_success "NvChad deployed."
}

install_lazyvim() {
    log_info "Deploying LazyVim..."
    if [[ -e "${NVIM_PATHS[config]}" ]]; then
        log_error "Config dir already exists: ${NVIM_PATHS[config]}"
        return 1
    fi
    mkdir -p -- "${NVIM_PATHS[config]}"
    if ! git clone --depth 1 https://github.com/LazyVim/starter "${NVIM_PATHS[config]}"; then
        log_error "git clone failed for LazyVim"
        safe_rm "${NVIM_PATHS[config]}"
        return 1
    fi
    rm -rf -- "${NVIM_PATHS[config]}/.git"
    log_success "LazyVim deployed."
}

install_astronvim() {
    log_info "Deploying AstroNvim..."
    if [[ -e "${NVIM_PATHS[config]}" ]]; then
        log_error "Config dir already exists: ${NVIM_PATHS[config]}"
        return 1
    fi
    mkdir -p -- "${NVIM_PATHS[config]}"
    if ! git clone --depth 1 https://github.com/AstroNvim/template "${NVIM_PATHS[config]}"; then
        log_error "git clone failed for AstroNvim"
        safe_rm "${NVIM_PATHS[config]}"
        return 1
    fi
    rm -rf -- "${NVIM_PATHS[config]}/.git"
    log_success "AstroNvim deployed."
}

install_dusky_nvim() {
    log_info "Deploying Dusky Neovim..."
    if [[ ! -d "${DUSKY_SRC}" ]]; then
        log_error "Dusky Neovim source not found at ${DUSKY_SRC}"
        return 1
    fi
    # Guard: source and dest must not be the same inode
    if [[ "${DUSKY_SRC}" -ef "${NVIM_PATHS[config]}" ]]; then
        log_error "Source and destination are the same: ${DUSKY_SRC}"
        return 1
    fi
    if [[ -e "${NVIM_PATHS[config]}" ]]; then
        log_error "Config dir already exists: ${NVIM_PATHS[config]} (backup/wipe first)"
        return 1
    fi
    mkdir -p -- "${NVIM_PATHS[config]}"
    if ! cp -a -- "${DUSKY_SRC}/." "${NVIM_PATHS[config]}/"; then
        log_error "Failed to copy Dusky Neovim"
        safe_rm "${NVIM_PATHS[config]}"
        return 1
    fi
    log_success "Dusky Neovim deployed precisely."
}

# ==============================================================================
# Synchronization & Interactive Logic
# ==============================================================================
execute_headless_sync() {
    log_info "Verifying network connectivity..."
    local has_net=false
    # Prefer timeout+tcp, fallback to curl, fallback to getent
    if command -v timeout &>/dev/null && timeout 5 bash -c 'exec 3<>/dev/tcp/github.com/443' &>/dev/null; then
        has_net=true
    elif command -v curl &>/dev/null && curl -Is --connect-timeout 5 https://github.com &>/dev/null; then
        has_net=true
    elif command -v getent &>/dev/null && getent hosts github.com &>/dev/null; then
        has_net=true
    fi
    if ! $has_net; then
        log_error "Cannot establish connection to GitHub. Skipping headless sync."
        return 1
    fi
    log_info "Starting Headless Sync. This may take a moment..."
    echo "--------------------------------------------------------------------------------"
    if nvim --headless "+Lazy! sync" +qa; then
        echo "--------------------------------------------------------------------------------"
        log_success "Neovim plugins synced successfully."
        return 0
    else
        echo "--------------------------------------------------------------------------------"
        log_error "Neovim exited with an error code during sync."
        return 1
    fi
}

prompt_state_management() {
    echo ""
    log_warn "Proceeding will remove your current Neovim configuration."

    local original_ps3="${PS3-}"
    local ps3_was_set=0; [[ -v PS3 ]] && ps3_was_set=1
    local original_columns="${COLUMNS-}"
    local col_was_set=0; [[ -v COLUMNS ]] && col_was_set=1

    PS3="${BOLD}${YELLOW}State Management (1-3): ${RESET}"
    COLUMNS=1

    local state_handled=false
    local opt
    select opt in "Backup existing configuration" "Wipe existing configuration (No Backup)" "Cancel"; do
        case "${REPLY}" in
            1) backup_neovim_state; state_handled=true; break ;;
            2) wipe_neovim_state; state_handled=true; break ;;
            3)
                log_info "Aborting deployment."
                disable_traps
                exit 0
                ;;
            *) log_error "Invalid option. Select 1-3." ;;
        esac
    done

    if [[ "${state_handled}" != "true" ]]; then
        log_error "Input terminated. Aborting."
        disable_traps
        exit 1
    fi

    if (( ps3_was_set )); then PS3="${original_ps3}"; else unset PS3; fi
    if (( col_was_set )); then COLUMNS="${original_columns}"; else unset COLUMNS; fi
}

prompt_reset_management() {
    echo ""
    log_warn "Proceeding will remove your Neovim data, state, and cache directories."
    log_info "Your main configuration (~/.config/nvim) will NOT be touched."

    local original_ps3="${PS3-}"
    local ps3_was_set=0; [[ -v PS3 ]] && ps3_was_set=1
    local original_columns="${COLUMNS-}"
    local col_was_set=0; [[ -v COLUMNS ]] && col_was_set=1

    PS3="${BOLD}${YELLOW}Reset State? (1-3): ${RESET}"
    COLUMNS=1

    local state_handled=false
    local opt
    local reset_status=1

    select opt in "Backup before reset (Includes config)" "Wipe state directly (No Backup)" "Cancel"; do
        case "${REPLY}" in
            1)
                backup_neovim_state
                if [[ -n "${CURRENT_BACKUP_PATH}" && -d "${CURRENT_BACKUP_PATH}/config" ]]; then
                    # Restore config that was just moved to backup
                    mkdir -p -- "${NVIM_PATHS[config]}"
                    cp -a -- "${CURRENT_BACKUP_PATH}/config/." "${NVIM_PATHS[config]}/"
                fi
                log_success "State reset successfully. Configuration preserved."
                state_handled=true
                reset_status=0
                break
                ;;
            2) reset_neovim_state; state_handled=true; reset_status=0; break ;;
            3)
                log_info "Reset cancelled."
                state_handled=true
                reset_status=1
                break
                ;;
            *) log_error "Invalid option. Select 1-3." ;;
        esac
    done

    if [[ "${state_handled}" != "true" ]]; then
        log_error "Input terminated. Aborting."
        disable_traps
        exit 1
    fi

    if (( ps3_was_set )); then PS3="${original_ps3}"; else unset PS3; fi
    if (( col_was_set )); then COLUMNS="${original_columns}"; else unset COLUMNS; fi
    return "${reset_status}"
}

prompt_headless_sync() {
    echo ""
    log_info "Do you want to run headless plugin synchronization now?"

    local original_ps3="${PS3-}"
    local ps3_was_set=0; [[ -v PS3 ]] && ps3_was_set=1
    local original_columns="${COLUMNS-}"
    local col_was_set=0; [[ -v COLUMNS ]] && col_was_set=1

    PS3="${BOLD}${YELLOW}Sync Plugins? (1-2): ${RESET}"
    COLUMNS=1

    local sync_handled=false
    local opt
    select opt in "Yes, sync plugins via Lazy.nvim" "No, skip for now"; do
        case "${REPLY}" in
            1)
                sync_handled=true
                execute_headless_sync || log_warn "Sync failed but continuing."
                break
                ;;
            2) sync_handled=true; break ;;
            *) log_error "Invalid option. Select 1-2." ;;
        esac
    done

    if [[ "${sync_handled}" != "true" ]]; then
        log_warn "Input terminated during sync prompt. Proceeding to launch."
    fi

    if (( ps3_was_set )); then PS3="${original_ps3}"; else unset PS3; fi
    if (( col_was_set )); then COLUMNS="${original_columns}"; else unset COLUMNS; fi
}

# ==============================================================================
# Main Interface
# ==============================================================================
main() {
    parse_arguments "$@"
    check_dependencies

    if [[ "${AUTONOMOUS_MODE}" == "true" ]]; then
        if [[ -z "${INSTALL_TARGET}" ]]; then
            log_error "Autonomous mode requires a target. Provide one using -t or --target."
            disable_traps
            exit 1
        fi

        log_info "Running in Autonomous Mode..."

        # Validate target BEFORE touching filesystem (prevents backup on invalid input)
        case "${INSTALL_TARGET,,}" in
            nvchad|lazyvim|astronvim|dusky) ;;
            *)
                log_error "Invalid target: ${INSTALL_TARGET}. Valid options: nvchad, lazyvim, astronvim, dusky."
                disable_traps
                exit 1
                ;;
        esac

        # Enforce Safe Defaults (Moves existing configuration out of the way securely)
        backup_neovim_state

        case "${INSTALL_TARGET,,}" in
            nvchad)    install_nvchad || { disable_traps; exit 1; } ;;
            lazyvim)   install_lazyvim || { disable_traps; exit 1; } ;;
            astronvim) install_astronvim || { disable_traps; exit 1; } ;;
            dusky)     install_dusky_nvim || { disable_traps; exit 1; } ;;
        esac

        # Sync is best-effort in autonomous mode (offline should not abort deploy)
        execute_headless_sync || log_warn "Headless sync failed — you can run :Lazy sync manually."

        disable_traps
        echo ""
        log_success "Autonomous deployment complete. You can now start Neovim."
        exit 0

    else
        # INTERACTIVE FALLBACK (Classic Flow)
        clear || true
        printf '%s====================================================%s\n' "$BOLD" "$RESET"
        printf '%s               Dusky Neovim Manager                 %s\n' "$BOLD" "$RESET"
        printf '%s====================================================%s\n\n' "$BOLD" "$RESET"

        local original_columns="${COLUMNS-}"
        local col_was_set=0; [[ -v COLUMNS ]] && col_was_set=1
        COLUMNS=1

        local action_taken=false
        local skip_sync_prompt=false
        local nvim_config

        PS3="${BOLD}${YELLOW}Select an operation (1-6): ${RESET}"
        select nvim_config in "Install NvChad" "Install LazyVim" "Install AstroNvim" "Install Dusky Neovim" "Maintenance & Utilities" "Quit"; do
            case "${REPLY}" in
                1) prompt_state_management; install_nvchad || continue; action_taken=true; break ;;
                2) prompt_state_management; install_lazyvim || continue; action_taken=true; break ;;
                3) prompt_state_management; install_astronvim || continue; action_taken=true; break ;;
                4) prompt_state_management; install_dusky_nvim || continue; action_taken=true; break ;;
                5)
                    echo ""
                    local main_ps3="${PS3}"
                    PS3="${BOLD}${YELLOW}Select a utility (1-5): ${RESET}"

                    local util
                    select util in "Backup Current Configuration" "Restore Backup" "Sync Plugins" "Reset State (Keep Config)" "Back to Main Menu"; do
                        case "${REPLY}" in
                            1)
                                echo ""
                                backup_neovim_state
                                echo ""
                                REPLY=""
                                continue
                                ;;
                            2)
                                if restore_neovim_state; then
                                    action_taken=true
                                    break 2 # Exits util select and main menu select
                                else
                                    REPLY=""
                                    continue
                                fi
                                ;;
                            3)
                                echo ""
                                if execute_headless_sync; then
                                    action_taken=true
                                fi
                                skip_sync_prompt=true
                                break 2 # Exits util select and main menu select
                                ;;
                            4)
                                if prompt_reset_management; then
                                    action_taken=true
                                    break 2 # Exits util select and main menu select
                                else
                                    REPLY=""
                                    continue
                                fi
                                ;;
                            5)
                                echo ""
                                break # Exits the Utility menu loop
                                ;;
                            *) log_error "Invalid option. Select 1-5." ;;
                        esac
                    done

                    # Restore Main Menu environment and redraw
                    PS3="${main_ps3}"
                    REPLY=""
                    continue
                    ;;
                6)
                    log_info "Exiting gracefully."
                    disable_traps
                    exit 0
                    ;;
                *) log_error "Invalid option. Select 1-6." ;;
            esac
        done

        if [[ "${action_taken}" != "true" ]]; then
            log_error "Input terminated. Exiting."
            disable_traps
            exit 1
        fi

        if (( col_was_set )); then COLUMNS="${original_columns}"; else unset COLUMNS; fi

        if [[ "${skip_sync_prompt}" != "true" ]]; then
            prompt_headless_sync
        fi

        disable_traps
        echo ""
        log_success "Operations complete. You can now start Neovim."
        exit 0
    fi
}

# Execute only when run directly, not when sourced for testing
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
