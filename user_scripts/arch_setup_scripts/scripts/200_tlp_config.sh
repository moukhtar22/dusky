#!/usr/bin/env bash
# configures /etc/tlp.conf for ASUS TUF F15 (personal, dusk)
# -----------------------------------------------------------------------------
# Script: 200_tlp_config.sh
# Description: Conditionally configures /etc/tlp.conf for ASUS TUF F15.
#              Includes auto-installation, backup logic, and strict state detection.
# Author: Elite DevOps (Arch/Hyprland)
# Dependencies: pacman, systemd, bash 5.3+
# -----------------------------------------------------------------------------

# 1. Strict Safety & Error Handling
set -euo pipefail

# 2. Configuration Content
# This variable holds the EXACT content to be written to /etc/tlp.conf.
mapfile -d '' TLP_CONFIG_CONTENT <<'EOF'
# tlp 1.10
# Do not use, this is custom configured for dusk's FX507ZE asus tuf f15 laptop

TLP_ENABLE=1
TLP_AUTO_SWITCH=1
TLP_PROFILE_AC=BAL
TLP_PROFILE_BAT=SAV
DISK_IDLE_SECS_ON_AC=0
DISK_IDLE_SECS_ON_BAT=2
MAX_LOST_WORK_SECS_ON_AC=30
MAX_LOST_WORK_SECS_ON_BAT=300
CPU_SCALING_GOVERNOR_ON_AC=performance
CPU_SCALING_GOVERNOR_ON_BAT=powersave
CPU_SCALING_GOVERNOR_ON_SAV=powersave
CPU_ENERGY_PERF_POLICY_ON_AC=performance
CPU_ENERGY_PERF_POLICY_ON_BAT=balance_power
CPU_ENERGY_PERF_POLICY_ON_SAV=power
CPU_MIN_PERF_ON_AC=0
CPU_MAX_PERF_ON_AC=100
CPU_MIN_PERF_ON_BAT=0
CPU_MAX_PERF_ON_BAT=50
CPU_MIN_PERF_ON_SAV=0
CPU_MAX_PERF_ON_SAV=30
CPU_BOOST_ON_AC=1
CPU_BOOST_ON_BAT=0
CPU_BOOST_ON_SAV=0
CPU_HWP_DYN_BOOST_ON_AC=1
CPU_HWP_DYN_BOOST_ON_BAT=0
CPU_HWP_DYN_BOOST_ON_SAV=0
PLATFORM_PROFILE_ON_AC=performance
PLATFORM_PROFILE_ON_BAT=balanced
PLATFORM_PROFILE_ON_SAV=quiet
MEM_SLEEP_ON_AC=s2idle
MEM_SLEEP_ON_BAT=s2idle
DISK_DEVICES="nvme-INTEL_SSDPEKNU512GZ_BTKA151410KY512A nvme-Samsung_SSD_980_1TB_S649NL0T857112D"
DISK_IOSCHED="none none"
AHCI_RUNTIME_PM_ON_AC=auto
AHCI_RUNTIME_PM_ON_BAT=auto
AHCI_RUNTIME_PM_TIMEOUT=10
INTEL_GPU_MIN_FREQ_ON_AC=100
INTEL_GPU_MIN_FREQ_ON_BAT=100
INTEL_GPU_MIN_FREQ_ON_SAV=100
INTEL_GPU_MAX_FREQ_ON_AC=1200
INTEL_GPU_MAX_FREQ_ON_BAT=200
INTEL_GPU_MAX_FREQ_ON_SAV=200
INTEL_GPU_BOOST_FREQ_ON_AC=1400
INTEL_GPU_BOOST_FREQ_ON_BAT=400
INTEL_GPU_BOOST_FREQ_ON_SAV=300
WIFI_PWR_ON_AC=off
WIFI_PWR_ON_BAT=on
SOUND_POWER_SAVE_CONTROLLER=Y
PCIE_ASPM_ON_AC=powersupersave
PCIE_ASPM_ON_BAT=powersupersave
PCIE_ASPM_ON_SAV=powersupersave
RUNTIME_PM_ON_AC=auto
RUNTIME_PM_ON_BAT=auto
USB_AUTOSUSPEND=1
DEVICES_TO_DISABLE_ON_BAT="bluetooth"
START_CHARGE_THRESH_BAT1=70
STOP_CHARGE_THRESH_BAT1=75
EOF

# 3. Aesthetics & Logging
readonly C_RESET=$'\E[0m'
readonly C_GREEN=$'\E[1;32m'
readonly C_BLUE=$'\E[1;34m'
readonly C_RED=$'\E[1;31m'
readonly C_YELLOW=$'\E[1;33m'

log_info() { printf "%b[INFO]%b %s\n" "${C_BLUE}" "${C_RESET}" "$1"; }
log_success() { printf "%b[OK]%b %s\n" "${C_GREEN}" "${C_RESET}" "$1"; }
log_warn() { printf "%b[WARN]%b %s\n" "${C_YELLOW}" "${C_RESET}" "$1"; }
log_error() { printf "%b[ERROR]%b %s\n" "${C_RED}" "${C_RESET}" "$1" >&2; }

# 4. Root Privilege Check (Auto-Elevation)
if [[ "${EUID}" -ne 0 ]]; then
    log_info "Root privileges required. Elevating..."
    exec sudo "$0" "$@"
fi

# 5. Main Execution
main() {
    local -r target_file="/etc/tlp.conf"
    
    # ---------------------------------------------------------
    # A. User Interaction & Warnings
    # ---------------------------------------------------------
    echo ""
    log_warn "You are about to apply a TLP configuration tuned specifically for the:"
    log_warn "ASUS TUF F15 Gaming Laptop"
    echo ""
    printf "  %bIf you do not own this specific device, it is HIGHLY advised not to apply this.%b\n" "${C_RED}" "${C_RESET}"
    printf "  For other laptops, we recommend manually configuring %s to achieve\n" "${target_file}"
    printf "  the best battery life for your specific hardware.\n\n"

    local response
    read -r -p "Do you want to proceed with applying this configuration? [y/N] " response
    
    # Modern Bash syntax for case-insensitive string matching
    if [[ "${response,,}" != "y" && "${response,,}" != "yes" ]]; then
        log_info "Operation cancelled by user."
        exit 0
    fi

    # ---------------------------------------------------------
    # B. Dependency Installation (Idempotent)
    # ---------------------------------------------------------
    local -r pkgs=("tlp" "tlp-rdw")
    log_info "Ensuring required packages are installed: ${pkgs[*]}"
    
    if sudo pacman -S --needed --noconfirm "${pkgs[@]}"; then
        log_success "Packages are installed and up-to-date."
    else
        log_error "Failed to install required packages via pacman."
        exit 1
    fi

    # ---------------------------------------------------------
    # C. Backup Logic
    # ---------------------------------------------------------
    local -r real_user="${SUDO_USER:-${USER}}"
    local real_home
    real_home="$(getent passwd "${real_user}" | cut -d: -f6)"
    
    local -r backup_dir="${real_home}/Documents"
    local -r backup_file="${backup_dir}/tlp_backup.conf"
    local file_existed=false

    if [[ -f "${target_file}" ]]; then
        file_existed=true
        
        if [[ ! -d "${backup_dir}" ]]; then
            log_info "Creating directory ${backup_dir}..."
            mkdir -p "${backup_dir}"
            chown "${real_user}:$(id -gn "${real_user}")" "${backup_dir}"
        fi

        log_info "Backing up current config to ${backup_file}..."
        cp "${target_file}" "${backup_file}"
        chown "${real_user}:$(id -gn "${real_user}")" "${backup_file}"
        log_success "Backup verified."
    fi

    # ---------------------------------------------------------
    # D. Write Configuration
    # ---------------------------------------------------------
    if [[ "${file_existed}" == true ]]; then
        log_info "Overwriting existing file at ${target_file}..."
    else
        log_info "File did not exist. Creating new file at ${target_file}..."
    fi
    
    if printf "%s" "${TLP_CONFIG_CONTENT}" > "${target_file}"; then
        log_success "Configuration written successfully."
    else
        log_error "Failed to write to ${target_file}."
        exit 1
    fi

    # ---------------------------------------------------------
    # E. Service Management
    # ---------------------------------------------------------
    log_info "Enabling and starting required systemd services..."
    
    if systemctl enable --now tlp.service; then
         log_success "Service (tlp.service) enabled and active."
    else
         log_error "Failed to enable or start TLP services."
         exit 1
    fi

    log_info "Restarting TLP service to apply the new configuration..."
    if systemctl restart tlp.service; then
        log_success "TLP service restarted successfully."
    else
        log_error "Failed to restart TLP service."
        exit 1
    fi
    
    log_success "TLP configuration pipeline completed successfully."
}

# Run Main
main
