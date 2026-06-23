#!/usr/bin/env bash
# ==============================================================================
# Arch Linux: Universal Wayland SSO Master (Platinum Edition v2)
# Target: Hyprland, UWSM, Greetd, Tuigreet, GNOME Keyring, Udiskie
# Kernel: 7.0.10+ | Systemd: 260+ | Bash: 5.3.9+
# ==============================================================================

set -euo pipefail

# --- 1. Pre-Flight Help Scanner (No Root Required) ---
show_help() {
    cat << EOF
Usage: ${0##*/} [OPTIONS]

A fully automated, zero-touch deployment script for Wayland Single Sign-On (SSO) 
on Arch Linux. Orchestrates Greetd, UWSM, Hyprland, and GNOME Keyring seamlessly.

Options:
  -m, --mode <mode>   Set the deployment mode. Available modes:
                      auto        : (Default) Auto-detects encryption. Deploys 'luks' if encrypted, 'unencrypted' if not.
                      unencrypted : Forces password prompt at Tuigreet. Unlocks Keyring automatically.
                      luks        : Forces autologin bypass. Injects LUKS boot password into Keyring from kernel.
                      autologin   : Forces autologin on unencrypted drive. (WARNING: Keyring will start locked).
  -h, --help          Show this help message and exit.

Example:
  sudo ./${0##*/} --mode auto
EOF
}

for arg in "$@"; do
    if [[ "$arg" == "-h" || "$arg" == "--help" ]]; then
        show_help
        exit 0
    fi
done

# --- 2. Privilege Escalation (Preserving Arguments) ---
if [[ "${EUID}" -ne 0 ]]; then
    echo "CRITICAL: This script requires root privileges. Elevating..."
    exec sudo "$0" "$@"
fi

# --- 3. Hardened CLI Argument Parsing ---
MODE="auto"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -m|--mode) 
            # Safely check if the next argument exists and doesn't start with '-'
            if [[ -n "${2:-}" && ! "$2" =~ ^- ]]; then
                MODE="$2"
                shift
            else
                echo "FATAL: --mode requires a valid argument (auto, unencrypted, luks, autologin)."
                exit 1
            fi
            ;;
        *) 
            echo "FATAL: Unknown parameter passed: $1"
            show_help
            exit 1 
            ;;
    esac
    shift
done

if [[ "$MODE" != "auto" && "$MODE" != "unencrypted" && "$MODE" != "luks" && "$MODE" != "autologin" ]]; then
    echo "FATAL: Invalid mode '$MODE'. Use 'auto', 'unencrypted', 'luks', or 'autologin'."
    exit 1
fi

# --- 4. Environment Validation ---
REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" ]] || [[ "$REAL_USER" == "root" ]]; then
    REAL_USER=$(awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}' /etc/passwd)
fi

if [[ -z "$REAL_USER" ]]; then
    echo "FATAL: Could not determine a valid non-root user. Aborting."
    exit 1
fi

USER_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
echo "Targeting user configuration for: $REAL_USER"

# --- 5. Topology & Encryption Auto-Detection ---
# Safely strips BTRFS subvolume tags (e.g., [/@]) to prevent lsblk parsing crashes
is_root_encrypted() {
    local root_dev raw_dev
    root_dev=$(findmnt -n -o SOURCE /) || return 1
    raw_dev=$(echo "$root_dev" | cut -d'[' -f1)
    lsblk -s -no TYPE "$raw_dev" 2>/dev/null | grep -q "^crypt$"
}

echo "=> Analyzing root filesystem topology..."
if [[ "$MODE" == "auto" ]]; then
    if is_root_encrypted; then
        echo "   [+] LUKS encryption detected. Selecting 'luks' mode (Zero-Touch SSO)."
        ACTIVE_MODE="luks"
    else
        echo "   [-] No root encryption detected. Selecting 'unencrypted' mode (Tuigreet prompt)."
        ACTIVE_MODE="unencrypted"
    fi
else
    ACTIVE_MODE="$MODE"
    if [[ "$ACTIVE_MODE" == "luks" ]] && ! is_root_encrypted; then
        echo "FATAL: LUKS mode explicitly requested, but root partition is not encrypted. Aborting."
        exit 1
    fi
fi

# --- 6. Core Dependency Enforcement ---
echo "Verifying core Wayland and SSO dependencies..."
pacman -S --needed --noconfirm greetd greetd-tuigreet uwsm udiskie libsecret gnome-keyring

if [[ "$ACTIVE_MODE" == "luks" ]]; then
    if ! pacman -Qq pam-fde-boot-pw-git &>/dev/null; then
        echo "LUKS mode: Compiling pam-fde-boot-pw-git from AUR..."
        pacman -S --needed --noconfirm base-devel git meson ninja

        BUILD_DIR="${USER_HOME}/.cache/aur-build-pam"
        rm -rf "$BUILD_DIR"
        mkdir -p "$BUILD_DIR"
        
        # POSIX compliant automatic primary group resolution
        chown -R "$REAL_USER": "$BUILD_DIR"
        
        # Demote privileges purely to compile the source code safely
        sudo -u "$REAL_USER" bash -c "
            cd '$BUILD_DIR' && \
            git clone https://aur.archlinux.org/pam-fde-boot-pw-git.git . && \
            makepkg -sc --noconfirm
        "
        # Retain root elevation to install the compiled artifact
        pacman -U --noconfirm "$BUILD_DIR"/*.pkg.tar.zst
        rm -rf "$BUILD_DIR"
    else
        echo "pam-fde-boot-pw-git is already installed."
    fi
fi

# --- 7. Architecting UWSM & Tuigreet ---
echo "Deploying Greetd, Tuigreet, and UWSM Wrappers..."

mkdir -p /usr/local/bin
cat > /usr/local/bin/wayland-session << 'EOF'
#!/usr/bin/env bash
exec uwsm start -- hyprland.desktop
EOF
chmod 0755 /usr/local/bin/wayland-session

mkdir -p /var/cache/tuigreet
if getent passwd greeter >/dev/null; then
    chown greeter:greeter /var/cache/tuigreet
fi
chmod 0755 /var/cache/tuigreet

mkdir -p /etc/greetd
if [[ "$ACTIVE_MODE" == "unencrypted" ]]; then
    # Standard Mode: Forces password prompt at Tuigreet to unlock keyring
    cat > /etc/greetd/config.toml << EOF
[terminal]
vt = 1

[default_session]
command = "tuigreet --time --remember --remember-session --cmd /usr/local/bin/wayland-session"
user = "greeter"
EOF
else
    # LUKS or Autologin Mode: Bypasses Tuigreet entirely (Autologin)
    cat > /etc/greetd/config.toml << EOF
[terminal]
vt = 1

[default_session]
command = "tuigreet --time --remember --remember-session --cmd /usr/local/bin/wayland-session"
user = "greeter"

[initial_session]
command = "/usr/local/bin/wayland-session"
user = "$REAL_USER"
EOF
fi

if getent passwd greeter >/dev/null; then
    chown -R greeter:greeter /etc/greetd
fi

# --- 8. The Platinum PAM Stack ---
echo "Configuring PAM stack for automated Keyring decryption..."
cp /etc/pam.d/greetd "/etc/pam.d/greetd.bak.$(date +%s)" 2>/dev/null || true

if [[ "$ACTIVE_MODE" == "luks" ]]; then
    cat > /etc/pam.d/greetd << 'EOF'
#%PAM-1.0
auth       required     pam_securetty.so
auth       requisite    pam_nologin.so
auth       include      system-local-login
auth       optional     pam_gnome_keyring.so
account    include      system-local-login
password   include      system-local-login

# --- SESSION PHASE ---
session    include      system-local-login
session    optional     pam_fde_boot_pw.so inject_for=gkr
session    optional     pam_gnome_keyring.so auto_start
EOF
else
    cat > /etc/pam.d/greetd << 'EOF'
#%PAM-1.0
auth       required     pam_securetty.so
auth       requisite    pam_nologin.so
auth       include      system-local-login
auth       optional     pam_gnome_keyring.so
account    include      system-local-login
password   include      system-local-login

# --- SESSION PHASE ---
session    include      system-local-login
session    optional     pam_gnome_keyring.so auto_start
EOF
fi

# --- 9. Systemd Service Overrides ---
echo "Applying Systemd overrides for Kernel Keyring inheritance..."
mkdir -p /etc/systemd/system/greetd.service.d
cat > /etc/systemd/system/greetd.service.d/keyringmode.conf << 'EOF'
[Service]
KeyringMode=inherit
EOF

# --- 10. Automating Udiskie for External Drives ---
echo "Writing udiskie YAML configuration..."
mkdir -p "${USER_HOME}/.config/udiskie"
cat > "${USER_HOME}/.config/udiskie/config.yml" << 'EOF'
program_options:
  password_prompt: ["secret-tool", "lookup", "uuid", "{id_uuid}"]
  automount: true
  notify: true
  tray: auto
EOF
# POSIX compliant automatic primary group resolution
chown -R "$REAL_USER": "${USER_HOME}/.config/udiskie"

# --- 11. Service Enablement ---
echo "Enabling boot services..."
if systemd-detect-virt -q --chroot; then
    systemctl enable greetd.service --force
else
    systemctl daemon-reload
    systemctl enable greetd.service
fi

echo "====================================================================="
echo " Deployment Complete! Active Configuration: [ ${ACTIVE_MODE^^} ]"
if [[ "$ACTIVE_MODE" == "autologin" ]]; then
    echo " WARNING: You forced autologin on an unencrypted drive."
    echo "          Your GNOME keyring will start LOCKED."
fi
echo "====================================================================="
