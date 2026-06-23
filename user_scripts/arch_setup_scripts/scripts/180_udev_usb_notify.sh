#!/usr/bin/env bash
# Strict modern setup for USB notifications (Systemd 260+)
set -euo pipefail

readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly NC=$'\033[0m'

log_info()    { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_success() { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
log_error()   { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

if [[ $EUID -ne 0 ]]; then
    log_info "Elevating to root..."
    exec sudo bash "$0" "$@"
fi

if [[ -z "${SUDO_USER:-}" ]]; then
    log_error "Cannot determine original user. Run without sudo."
    exit 1
fi

readonly USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
readonly SOURCE_SCRIPT="${USER_HOME}/user_scripts/external/usb_sound.sh"
readonly TARGET_BIN="/usr/local/bin/usb_sound.sh"
readonly UDEV_RULE_FILE="/etc/udev/rules.d/90-usb-sound.rules"

readonly UDEV_RULE_CONTENT='ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/local/bin/usb_sound.sh connect"
ACTION=="remove", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", RUN+="/usr/local/bin/usb_sound.sh disconnect"'

if [[ ! -f "$SOURCE_SCRIPT" ]]; then
    log_error "Source script not found: $SOURCE_SCRIPT"
    exit 1
fi

# Step 1: Physical installation with strict ownership
log_info "Installing payload to system binaries..."
install -m 755 -o root -g root "$SOURCE_SCRIPT" "$TARGET_BIN"
log_success "Installed to $TARGET_BIN"

# Step 2: Udev rules
log_info "Deploying udev rules..."
printf '%s\n' "$UDEV_RULE_CONTENT" > "$UDEV_RULE_FILE"
log_success "Udev rules deployed to $UDEV_RULE_FILE"

# Step 3: Daemon reload
log_info "Reloading systemd-udevd state..."
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add || true
log_success "Udev subsystem reloaded and active for future hotplug events"

log_success "Setup complete. System is configured."
