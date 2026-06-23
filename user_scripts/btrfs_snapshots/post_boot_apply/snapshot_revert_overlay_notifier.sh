#!/usr/bin/env bash
# Arch Linux | Btrfs Snapshot GUI Notifier (XDG Autostart)
# Highly reliable, layout-agnostic overlayfs detection.

set -Eeuo pipefail

echo "[INFO] Installing Btrfs Snapshot Notifier..."

# 1. Ensure libnotify is installed (provides notify-send)
if ! command -v notify-send >/dev/null 2>&1; then
    echo "[INFO] Installing libnotify dependency..."
    sudo pacman -S --needed --noconfirm libnotify
fi

# 2. Create the payload script
NOTIFIER_SCRIPT="/usr/local/bin/btrfs-overlay-notifier"
sudo tee "$NOTIFIER_SCRIPT" >/dev/null << 'EOF'
#!/usr/bin/env bash

# Exit immediately if the root filesystem is NOT an overlay
if ! findmnt -n -t overlay -M / >/dev/null 2>&1; then
    exit 0
fi

# Extract the exact snapshot path from the kernel command line for UX purposes
SNAP_PATH=$(cat /proc/cmdline | tr ' ' '\n' | grep '^rootflags=' | grep -o 'subvol=[^,]*' | grep 'snapshot' | tail -n 1 | cut -d= -f2)
[[ -z "$SNAP_PATH" ]] && SNAP_PATH="Unknown Snapshot"

# DBUS Race Condition Fix: Wait for the notification daemon to come online.
# We ping the freedesktop Notifications DBUS interface once per second (max 30s).
for i in {1..30}; do
    if dbus-send --session --dest=org.freedesktop.Notifications \
                 --type=method_call --print-reply \
                 /org/freedesktop/Notifications \
                 org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Send the critical desktop notification
notify-send \
    --urgency=critical \
    --icon=drive-harddisk-system \
    --app-name="System Restore" \
    "Live Snapshot Mode Active" \
    "You are booted into a read-only snapshot via OverlayFS.\n\nSnapshot Path: <b>${SNAP_PATH}</b>\n\nChanges made during this session will NOT be saved permanently."
EOF

sudo chmod 0755 "$NOTIFIER_SCRIPT"

# 3. Create the universally supported XDG Autostart entry
sudo mkdir -p /etc/xdg/autostart
sudo tee /etc/xdg/autostart/btrfs-overlay-notifier.desktop >/dev/null << EOF
[Desktop Entry]
Type=Application
Name=Btrfs Overlay Notifier
Comment=Alerts the user if booted into a read-only Btrfs snapshot
Exec=$NOTIFIER_SCRIPT
Terminal=false
Categories=Utility;System;
NoDisplay=true
EOF

echo "[INFO] Success! The notifier is installed."
echo "[INFO] It will now securely monitor your boots and trigger a GUI notification if an overlayfs root is detected."
