#!/usr/bin/env bash
# ==============================================================================
# ARCH LINUX MASTER ORCHESTRATOR WRAPPER
# ==============================================================================
# Bleeding-edge Arch bootstrap wrapper.
# Installs only missing dependencies, then hands off to the Python orchestrator.
# ==============================================================================
set -Eeuo pipefail
shopt -s inherit_errexit nullglob

SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
readonly SCRIPT_DIR
readonly ORCHESTRATOR_PY="${SCRIPT_DIR}/orchestrator.py"
readonly NETWORK_SCRIPT="${SCRIPT_DIR}/scripts/003_network_connect.sh"

declare -g RED="" GREEN="" YELLOW="" BLUE="" BOLD="" RESET=""
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\e[1;31m'
    GREEN=$'\e[1;32m'
    YELLOW=$'\e[1;33m'
    BLUE=$'\e[1;34m'
    BOLD=$'\e[1m'
    RESET=$'\e[0m'
fi

log() {
    local level="$1"
    local msg="$2"
    local color=""
    case "$level" in
        INFO)    color="$BLUE" ;;
        SUCCESS) color="$GREEN" ;;
        WARN)    color="$YELLOW" ;;
        ERROR)   color="$RED" ;;
        RUN)     color="$BOLD" ;;
    esac
    printf "%s[%s]%s %s\n" "${color}" "${level}" "${RESET}" "${msg}"
}

wrapper_error() {
    local rc="$1" line="$2" command="$3"
    log ERROR "Wrapper failed at line ${line} (command: ${command}, exit ${rc})."
    exit "$rc"
}
trap 'wrapper_error "$?" "$LINENO" "$BASH_COMMAND"' ERR

bootstrap_packages() {
    local line
    if [[ -f "$ORCHESTRATOR_PY" ]] && line="$(grep -m1 '^# DUSKY_BOOTSTRAP_PACKAGES:' "$ORCHESTRATOR_PY" 2>/dev/null)"; then
        local -a pkgs=()
        read -r -a pkgs <<< "${line#*:}"
        if (( ${#pkgs[@]} > 0 )); then
            printf '%s\n' "${pkgs[@]}"
            return 0
        fi
    fi
    printf '%s\n' python python-textual python-rich git
}

check_internet() {
    local url
    local -a urls=("https://archlinux.org" "https://geo.mirror.pkgbuild.com")
    if command -v curl >/dev/null 2>&1; then
        for url in "${urls[@]}"; do
            if curl -fsS --connect-timeout 2 --max-time 3 "$url" >/dev/null 2>&1; then
                return 0
            fi
        done
    elif command -v wget >/dev/null 2>&1; then
        for url in "${urls[@]}"; do
            if wget -q --tries=1 --timeout=3 -O /dev/null "$url" >/dev/null 2>&1; then
                return 0
            fi
        done
    else
        return 2
    fi
    return 1
}

require_internet() {
    (( network_verified )) && return 0
    local attempt=1 probe_status
    local max_attempts=5
    while (( attempt <= max_attempts )); do
        if check_internet; then
            network_verified=1
            log SUCCESS "Internet connection verified."
            return 0
        else
            probe_status=$?
            if (( probe_status == 2 )); then
                log WARN "No HTTP probe tool is installed; network operations will report connectivity errors."
                return 0
            fi
        fi
        if (( attempt == 1 )); then
            log INFO "Waiting for network connectivity to initialize..."
        fi
        sleep 1
        ((attempt++))
    done

    log WARN "No active internet connection detected after initial probe."
    if [[ -x "$NETWORK_SCRIPT" ]]; then
        log RUN "Launching network configuration script..."
        "$NETWORK_SCRIPT" || true
        if check_internet; then
            network_verified=1
            log SUCCESS "Internet connection established."
            return 0
        fi
    fi

    log ERROR "Internet is required for the orchestration pipeline."
    exit 1
}

python_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14, 7) else 1)' >/dev/null 2>&1
}

choose_python() {
    local candidate
    if [[ -x /usr/bin/python ]] && python_ok /usr/bin/python; then
        printf "/usr/bin/python\n"
        return 0
    fi

    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done

    return 1
}

pkg_installed() {
    pacman -Qq "$1" >/dev/null 2>&1
}

main() {
    if [[ ! -f "$ORCHESTRATOR_PY" ]]; then
        log ERROR "Cannot find Python orchestrator at: $ORCHESTRATOR_PY"
        exit 1
    fi

    unset -v \
        LD_PRELOAD LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_ORIGIN_PATH \
        LD_PROFILE LD_SHOW_AUXV LD_USE_LOAD_BIAS PYTHONSTARTUP PYTHONHOME \
        PYTHONPATH PERL5LIB RUBYLIB NODE_OPTIONS 2>/dev/null || true

    local offline=0 info_only=0 arg
    for arg in "$@"; do
        case "$arg" in
            --offline) offline=1 ;;
            --help|-h|--version|--doctor|--list|--list-once|--list-scripts|--dry-run|--explain|--reset|--forget-once|--forget-once=*)
                info_only=1 ;;
        esac
    done
    if (( info_only )); then
        local info_python
        if ! info_python="$(choose_python)"; then
            log ERROR "Python 3.14.7+ is required for this command."
            exit 1
        fi
        launch_python "$info_python" "$@"
    fi

    local -a sudo_cmd=()
    if (( EUID != 0 )); then
        sudo_cmd=(sudo)
    fi

    local -a bootstrap_pkgs=()
    mapfile -t bootstrap_pkgs < <(bootstrap_packages)

    local -a missing_pkgs=()
    local pkg
    for pkg in "${bootstrap_pkgs[@]}"; do
        pkg_installed "$pkg" || missing_pkgs+=("$pkg")
    done

    if (( EUID == 0 )) && ! pkg_installed sudo; then
        missing_pkgs+=("sudo")
    fi

    if (( ${#missing_pkgs[@]} > 0 )); then
        if (( offline )); then
            log ERROR "Missing packages in offline mode: ${missing_pkgs[*]}"
            exit 1
        fi
        require_internet

        if (( ${#sudo_cmd[@]} > 0 )); then
            if ! command -v sudo >/dev/null 2>&1; then
                log ERROR "sudo is required to bootstrap dependencies."
                exit 1
            fi
            log INFO "Administrative privileges required to install missing dependencies."
            if ! sudo -v; then
                log ERROR "Sudo authentication failed. Cannot install dependencies."
                exit 1
            fi
        fi

        if [[ -e /var/lib/pacman/db.lck ]]; then
            log ERROR "Pacman lock exists at /var/lib/pacman/db.lck. Resolve it before retrying."
            exit 1
        fi

        log RUN "Installing missing packages: ${missing_pkgs[*]}"
        "${sudo_cmd[@]}" pacman -Syu --needed --noconfirm "${missing_pkgs[@]}"

        log SUCCESS "All dependencies satisfied."
    else
        log SUCCESS "All dependencies already satisfied."
    fi

    local PYTHON_BIN
    if ! PYTHON_BIN="$(choose_python)"; then
        log ERROR "Python 3.14.7+ interpreter not found after dependency bootstrap."
        exit 1
    fi

    if ! "$PYTHON_BIN" -c 'import textual, rich, tomllib; from importlib.metadata import version; import re, sys; sys.exit(tuple(map(int, re.findall(r"\d+", version("textual"))[:3])) < (8, 2, 8))' >/dev/null 2>&1; then
        if (( offline )); then
            log ERROR "Python dependencies are unusable in offline mode."
            exit 1
        fi
        log WARN "Python runtime imports failed. Reinstalling dependency packages..."
        if (( ${#sudo_cmd[@]} > 0 )); then
            sudo -v
        fi
        require_internet
        "${sudo_cmd[@]}" pacman -Syu --noconfirm python-textual python-rich
        if ! "$PYTHON_BIN" -c 'import textual, rich, tomllib; from importlib.metadata import version; import re, sys; sys.exit(tuple(map(int, re.findall(r"\d+", version("textual"))[:3])) < (8, 2, 8))' >/dev/null 2>&1; then
            log ERROR "Python dependencies are still unusable."
            exit 1
        fi
    fi

    if (( ! offline )); then
        require_internet
    fi
    launch_python "$PYTHON_BIN" "$@"
}

launch_python() {
    local PYTHON_BIN="$1"
    shift
    local has_allow_root=0
    local arg
    for arg in "$@"; do
        if [[ "$arg" == "--allow-root" ]]; then
            has_allow_root=1
            break
        fi
    done

    if (( EUID == 0 )) && [[ -n "${SUDO_USER:-}" ]] && (( has_allow_root == 0 )); then
        log INFO "Dropping privileges to ${SUDO_USER}..."

        local passwd_entry target_home target_shell
        if ! passwd_entry="$(getent passwd "$SUDO_USER")"; then
            log ERROR "Cannot resolve user account: $SUDO_USER"
            exit 1
        fi
        IFS=: read -r _ _ _ _ _ target_home target_shell <<< "$passwd_entry"
        [[ -n "$target_home" ]] || { log ERROR "User has no home directory: $SUDO_USER"; exit 1; }
        target_shell="${target_shell:-/bin/bash}"

        cd "$SCRIPT_DIR"
        exec sudo -u "$SUDO_USER" -- env \
            HOME="$target_home" \
            USER="$SUDO_USER" \
            LOGNAME="$SUDO_USER" \
            SHELL="$target_shell" \
            PYTHONUNBUFFERED=1 \
            PYTHONUTF8=1 \
            PYTHONDONTWRITEBYTECODE=1 \
            "$PYTHON_BIN" "$ORCHESTRATOR_PY" "$@"
    fi

    log RUN "Launching Dusky Orchestrator..."
    exec env \
        PYTHONUNBUFFERED=1 \
        PYTHONUTF8=1 \
        PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON_BIN" "$ORCHESTRATOR_PY" "$@"
}

declare -g network_verified=0
main "$@"
