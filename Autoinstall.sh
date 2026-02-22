#!/usr/bin/env bash
# ==============================================================================
#  DUSKY AUTOINSTALL SCRIPT
#  One-command installation for Dusklinux Hyprland dotfiles
# ==============================================================================

set -o errexit
set -o nounset
set -o pipefail

readonly REPO_URL="https://github.com/dusklinux/dusky.git"
readonly BARE_REPO_DIR="${HOME}/.dusky"
readonly DOTFILES_DIR="${HOME}/.dotfiles"
readonly LOG_FILE="${HOME}/dusky_install.log"

# Colors
if [[ -t 1 ]]; then
    RED=$'\e[1;31m'
    GREEN=$'\e[1;32m'
    YELLOW=$'\e[1;33m'
    BLUE=$'\e[1;34m'
    BOLD=$'\e[1m'
    RESET=$'\e[0m'
    DIM=$'\e[2m'
fi

log() {
    local level="$1"
    local msg="$2"
    local color=""
    local prefix=""

    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        STEP)    color="$BOLD" ;;
    esac

    printf "%s[%s]%s %s\n" "$color" "$level" "$RESET" "$msg"
}

header() {
    echo ""
    echo -e "${BOLD}========================================${RESET}"
    echo -e "${BOLD}  DUSKY AUTOINSTALL${RESET}"
    echo -e "${BOLD}========================================${RESET}"
    echo ""
}

cleanup() {
    local exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        log "SUCCESS" "Installation completed successfully!"
    else
        log "ERROR" "Installation failed (exit code: $exit_code)"
        log "INFO" "Check ${LOG_FILE} for details"
    fi
}
trap cleanup EXIT

check_dependencies() {
    log "INFO" "Checking dependencies..."

    local missing=()

    if ! command -v git &>/dev/null; then
        missing+=("git")
    fi

    if ! command -v sudo &>/dev/null; then
        missing+=("sudo")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log "ERROR" "Missing dependencies: ${missing[*]}"
        log "INFO" "Install them with: sudo pacman -S --needed ${missing[*]}"
        exit 1
    fi

    log "SUCCESS" "All dependencies satisfied"
}

check_network() {
    log "INFO" "Checking network connectivity..."
    if ! ping -c 1 -W 3 github.com &>/dev/null; then
        log "ERROR" "No network connection"
        exit 1
    fi
    log "SUCCESS" "Network available"
}

check_existing_installation() {
    if [[ -d "$BARE_REPO_DIR" ]] || [[ -d "$DOTFILES_DIR" ]]; then
        log "WARN" "Existing dusky installation detected"
        read -r -p "Remove and reinstall? [y/N]: " choice
        if [[ "${choice,,}" != "y" ]]; then
            log "INFO" "Aborting installation"
            exit 0
        fi
        rm -rf "$BARE_REPO_DIR" "$DOTFILES_DIR"
    fi
}

clone_repo() {
    log "INFO" "Cloning repository..."
    log "INFO" "URL: $REPO_URL"

    if ! git clone --bare "$REPO_URL" "$BARE_REPO_DIR" 2>&1 | tee -a "$LOG_FILE"; then
        log "ERROR" "Failed to clone repository"
        exit 1
    fi

    log "SUCCESS" "Repository cloned"
}

deploy_dotfiles() {
    log "INFO" "Deploying dotfiles..."

    git --git-dir="$BARE_REPO_DIR/" --work-tree="$HOME" checkout -f 2>&1 | tee -a "$LOG_FILE" || true

    log "SUCCESS" "Dotfiles deployed"
}

verify_deployment() {
    log "INFO" "Verifying deployment..."

    local required_dirs=(
        "${HOME}/.config/hypr"
        "${HOME}/user_scripts"
    )

    local missing=()
    for dir in "${required_dirs[@]}"; do
        if [[ ! -d "$dir" ]]; then
            missing+=("$dir")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log "WARN" "Some expected directories were not created:"
        printf "  %s\n" "${missing[@]}"
    else
        log "SUCCESS" "Deployment verified"
    fi
}

run_orchestra() {
    local orchestra_path="${HOME}/user_scripts/arch_setup_scripts/ORCHESTRA.sh"

    if [[ ! -f "$orchestra_path" ]]; then
        log "ERROR" "ORCHESTRA.sh not found at $orchestra_path"
        log "INFO" "Please run the installation manually"
        return 1
    fi

    echo ""
    log "STEP" "Running ORCHESTRA.sh..."
    log "INFO" "This will take 30-60 minutes. You'll be prompted during setup."
    echo ""

    read -r -p "Run ORCHESTRA.sh now? [y/N]: " choice
    if [[ "${choice,,}" != "y" ]]; then
        log "INFO" "Skipping ORCHESTRA.sh"
        log "INFO" "Run it manually with: ~/user_scripts/arch_setup_scripts/ORCHESTRA.sh"
        return 0
    fi

    chmod +x "$orchestra_path"
    bash "$orchestra_path"
}

show_summary() {
    echo ""
    echo -e "${BOLD}========================================${RESET}"
    echo -e "${BOLD}  INSTALLATION SUMMARY${RESET}"
    echo -e "${BOLD}========================================${RESET}"
    echo ""
    echo -e "  ${GREEN}✓${RESET} Repository cloned to: ${DIM}$BARE_REPO_DIR${RESET}"
    echo -e "  ${GREEN}✓${RESET} Dotfiles deployed to: ${DIM}$HOME${RESET}"
    echo -e "  ${GREEN}✓${RESET} Log file: ${DIM}$LOG_FILE${RESET}"
    echo ""
    echo -e "${BOLD}NEXT STEPS:${RESET}"
    echo "  1. Review the configuration files if desired"
    echo "  2. Run ORCHESTRA.sh to install dependencies:"
    echo "     ~/user_scripts/arch_setup_scripts/ORCHESTRA.sh"
    echo "  3. Reboot your system"
    echo ""
    echo -e "${YELLOW}NOTE:${RESET} Some errors during checkout are expected."
    echo "      They will be resolved after ORCHESTRA.sh runs."
    echo ""
}

main() {
    header

    # Redirect all output to log file as well
    exec > >(tee "$LOG_FILE")

    check_dependencies
    check_network
    check_existing_installation
    clone_repo
    deploy_dotfiles
    verify_deployment
    show_summary

    read -r -p "Run ORCHESTRA.sh to complete installation? [y/N]: " choice
    if [[ "${choice,,}" == "y" ]]; then
        run_orchestra
    fi
}

main "$@"
