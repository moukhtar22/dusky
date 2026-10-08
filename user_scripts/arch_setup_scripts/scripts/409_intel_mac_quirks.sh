#!/usr/bin/env bash
#d: Apply Intel Mac hardware quirks (Wi-Fi, keyboard, sensors)

# -----------------------------------------------------------------------------
# Script: 409_intel_mac_quirks.sh
# Purpose: Intel Macs (2012-2020) are ordinary x86_64 PCs -- Arch and dusky
#          boot on them out of the box -- but three areas want tweaks:
#
#            1. Broadcom Wi-Fi (14e4:43xx, common in 2012-2015 models)
#               -> broadcom-wl-dkms from [extra] (wl driver), or the b43
#                  driver with linux-firmware for the legacy chips
#            2. Apple keyboard fn-mode: /etc/modprobe.d/hid_apple.conf with
#               fnmode=2 so F1-F12 act as function keys by default
#            3. lm_sensors + applesmc so `sensors` reports Mac fan/temp data
#
#          No-ops politely on non-Apple hardware (checks the DMI vendor).
#
# Flags:   --auto     no prompts (still hardware-gated)
#          --no-wifi  skip the Broadcom driver installation
# -----------------------------------------------------------------------------

set -euo pipefail

AUTO_MODE=0
NO_WIFI=0
for arg in "$@"; do
    case "$arg" in
        --auto)    AUTO_MODE=1 ;;
        --no-wifi) NO_WIFI=1 ;;
        *)
            printf "[ERR ] Unknown argument '%s' (supported: --auto, --no-wifi)\n" "$arg" >&2
            exit 2
            ;;
    esac
done

# --- logging + privilege escalation (381 pattern) -------------------------------
log_info()    { printf "[\033[0;34mINFO\033[0m] %s\n" "$1"; }
log_success() { printf "[\033[0;32m OK \033[0m] %s\n" "$1"; }
log_warn()    { printf "[\033[0;33mWARN\033[0m] %s\n" "$1"; }
log_err()     { printf "[\033[0;31mERR \033[0m] %s\n" "$1"; }

if [[ $EUID -ne 0 ]]; then
    printf "[\033[0;33mINFO\033[0m] Escalating permissions to root...\n"
    exec sudo "$0" "$@"
fi

HID_APPLE_CONF="/etc/modprobe.d/hid_apple.conf"

confirm() {
    [[ "$AUTO_MODE" -eq 1 ]] && return 0
    local choice
    read -rp "$1 [y/N] " choice || true
    [[ "${choice:-N}" =~ ^[yY] ]]
}

# --- hardware gates ---------------------------------------------------------------
is_apple_hardware() {
    local vendor
    vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null) || true
    [[ "$vendor" == "Apple Inc." || "$vendor" == "Apple Computer, Inc." ]]
}

has_broadcom_wifi() {
    # Broadcom wireless: vendor 14e4, class 0280 (network controller)
    command -v lspci &>/dev/null && lspci -d 14e4::0280 2>/dev/null | grep -q .
}

# --- Broadcom Wi-Fi -----------------------------------------------------------------
install_broadcom_wifi() {
    has_broadcom_wifi || { log_info "No Broadcom Wi-Fi detected; skipping."; return; }

    local chip
    chip=$(lspci -d 14e4::0280 2>/dev/null | head -n1)
    log_info "Broadcom Wi-Fi found: ${chip}"

    # b43-supported legacy chips use linux-firmware, no extra driver needed.
    if grep -qE 'BCM43(31|60)' <<<"$chip"; then
        log_info "Legacy b43 chip: ensuring linux-firmware is installed."
        pacman -S --needed --noconfirm linux-firmware
        log_warn "If Wi-Fi does not appear, check: https://wiki.archlinux.org/title/broadcom_wireless"
        return
    fi

    # Everything else (4360/4352/...) -> wl driver. broadcom-wl-dkms lives in
    # [extra] now; it needs the matching kernel headers present.
    log_info "Installing broadcom-wl-dkms (needs kernel headers present)."
    pacman -S --needed --noconfirm broadcom-wl-dkms
    log_warn "If the wl module blacklists conflict, follow the b43/wl table on the Arch wiki."
}

# --- Apple keyboard -------------------------------------------------------------------
configure_hid_apple() {
    modinfo hid_apple &>/dev/null || { log_info "hid_apple module not present; skipping."; return; }

    if [[ -f "$HID_APPLE_CONF" ]] && grep -q 'fnmode' "$HID_APPLE_CONF"; then
        log_success "hid_apple already configured."
        return
    fi

    mkdir -p /etc/modprobe.d
    printf '%s\n' \
        "# Managed by dusky 409_intel_mac_quirks.sh" \
        "# F1-F12 behave as function keys; hold Fn for media keys." \
        "options hid_apple fnmode=2" > "$HID_APPLE_CONF"
    log_success "Wrote ${HID_APPLE_CONF} (fnmode=2)."
    log_info "Apply immediately for this session:  echo 2 | sudo tee /sys/module/hid_apple/parameters/fnmode"
    log_info "EU keyboards: add 'options hid_apple iso_layout=0' if ~ and section are swapped."
}

# --- sensors ------------------------------------------------------------------------------
install_sensors() {
    pacman -S --needed --noconfirm lm_sensors
    if command -v sensors &>/dev/null && ! sensors 2>/dev/null | grep -q applesmc; then
        log_info "Running sensors-detect so applesmc (fans/temps) gets picked up..."
        sensors-detect --auto &>/dev/null || log_warn "sensors-detect failed; run it manually."
    fi
    if command -v sensors &>/dev/null && sensors 2>/dev/null | grep -q applesmc; then
        log_success "applesmc sensors reporting."
    else
        log_warn "applesmc not detected (desktop Macs often lack it)."
    fi
}

# --- main ------------------------------------------------------------------------------------
main() {
    if ! is_apple_hardware; then
        log_info "Not Apple hardware (DMI vendor: $(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || echo 'unknown')); skipping."
        exit 0
    fi
    log_info "Intel Mac detected."
    log_info "Scope note: Apple Silicon (M1-M4) needs the Asahi/ARM port -- out of scope here."
    confirm "  Apply Intel Mac quirks?" || { log_warn "Skipping."; exit 0; }

    if [[ "$NO_WIFI" -eq 0 ]]; then
        install_broadcom_wifi
    fi
    configure_hid_apple
    install_sensors

    echo ""
    log_success "Intel Mac quirks applied. Reboot to load Wi-Fi/keyboard changes."
    log_info "Further reading: https://wiki.archlinux.org/title/Mac"
}

main "$@"