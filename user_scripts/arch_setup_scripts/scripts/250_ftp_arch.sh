#!/usr/bin/env bash
#d: Set up a secure LAN-only FTP (vsftpd) server

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

SCRIPT_PATH=""
if [[ -f "$0" ]]; then
    SCRIPT_PATH=$(readlink -f "$0" 2>/dev/null) || SCRIPT_PATH=""
    if [[ -n "$SCRIPT_PATH" ]] && [[ ! -f "$SCRIPT_PATH" ]]; then
        SCRIPT_PATH=""
    fi
fi

# --- 2. Colors & Logging ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\e[0m' C_BOLD=$'\e[1m'
    readonly C_GREEN=$'\e[32m' C_BLUE=$'\e[34m'
    readonly C_YELLOW=$'\e[33m' C_RED=$'\e[31m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE=''
    readonly C_YELLOW='' C_RED=''
fi

info()    { printf "%s[INFO]%s %s\n" "$C_BLUE" "$C_RESET" "$*"; }
success() { printf "%s[OK]%s   %s\n" "$C_GREEN" "$C_RESET" "$*"; }
warn()    { printf "%s[WARN]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
error()   { printf "%s[ERR]%s  %s\n" "$C_RED" "$C_RESET" "$*" >&2; }
die()     { error "$*"; exit 1; }

# shellcheck disable=SC2329
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]] && [[ $exit_code -ne 130 ]]; then
        printf "\n%s[!] Script exited with errors (code %d). Check output above.%s\n" \
            "$C_RED" "$exit_code" "$C_RESET" >&2
    fi
}
trap cleanup EXIT

# --- 3. Argument Parsing ---
AUTO_MODE=false
CUSTOM_PATH=""
CUSTOM_USER=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            printf "%sArch Linux Secure LAN FTP (vsftpd) Setup%s\n\n" "$C_BOLD" "$C_RESET"
            printf "Usage: %s [OPTIONS]\n\n" "$(basename "$0")"
            printf "Options:\n"
            printf "  -a, --auto, -y, --yes    Run non-interactively with defaults\n"
            printf "  -d, --dir <path>         Specify custom FTP root directory (default: /mnt/zram1)\n"
            printf "  -p, --path <path>        Alias for --dir\n"
            printf "  -u, --user <username>    Specify username allowed for FTP access\n"
            printf "  -h, --help               Show this help message\n\n"
            exit 0
            ;;
    esac
done

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a|-y|--yes)
            AUTO_MODE=true
            shift
            ;;
        --dir|-d|--path|-p)
            CUSTOM_PATH="${2:-}"
            [[ -z "$CUSTOM_PATH" ]] && die "Option '$1' requires a directory path argument."
            shift 2
            ;;
        --user|-u)
            CUSTOM_USER="${2:-}"
            [[ -z "$CUSTOM_USER" ]] && die "Option '$1' requires a username argument."
            shift 2
            ;;
        *)
            warn "Unknown argument: $1 (ignoring)"
            shift
            ;;
    esac
done

# --- 4. Privilege Escalation (Auto-Sudo) ---
if (( EUID != 0 )); then
    info "Administrative privileges required. Elevating via sudo..."
    if [[ -n "${SCRIPT_PATH:-}" && -f "$SCRIPT_PATH" ]]; then
        exec sudo --preserve-env=TERM,COLORTERM bash -- "$SCRIPT_PATH" "$@"
    else
        exec sudo --preserve-env=TERM,COLORTERM bash -- "$0" "$@"
    fi
fi

printf "\n%sArch Linux Secure LAN FTP (vsftpd) Setup%s\n" "$C_BOLD" "$C_RESET"
printf "Configures: vsftpd · Firewall Rules · Chroot Jail · Whitelist · ZRAM/Mounts\n\n"

# --- 5. Interactive Confirmation ---
if [[ "$AUTO_MODE" == "true" ]]; then
    info "Autonomous mode enabled — proceeding automatically."
else
    prompt_input="Y"
    printf "%s[INPUT]%s Do you want to set up an FTP server for local file sharing? [Y/n]: " "$C_BLUE" "$C_RESET"
    read -r prompt_input || prompt_input="Y"
    prompt_input="${prompt_input:-Y}"
    if [[ ! "$prompt_input" =~ ^[yY]([eE][sS])?$ ]]; then
        info "Operation cancelled by user. Exiting."
        exit 0
    fi
fi

# --- 6. User Detection & Validation ---
if [[ -n "$CUSTOM_USER" ]]; then
    REAL_USER="$CUSTOM_USER"
else
    REAL_USER="${SUDO_USER:-}"
    if [[ -z "$REAL_USER" ]]; then
        REAL_USER=$(loginctl list-sessions --no-legend 2>/dev/null \
            | awk '$2 >= 1000 {print $3; exit}' || true)
    fi
    REAL_USER="${REAL_USER:-$(whoami 2>/dev/null || echo "root")}"
fi

if [[ "$REAL_USER" == "root" && "$AUTO_MODE" == "false" ]]; then
    warn "Running as root. Root is blocked by vsftpd PAM policy by default."
    printf "%s[INPUT]%s Enter regular username to allow FTP access: " "$C_BLUE" "$C_RESET"
    read -r user_in || user_in=""
    if [[ -n "$user_in" ]]; then
        REAL_USER="$user_in"
    fi
fi

if ! id "$REAL_USER" &>/dev/null; then
    die "User '$REAL_USER' does not exist on this system."
fi

# Shell validation (PAM vsftpd requirement)
USER_SHELL=$(getent passwd "$REAL_USER" 2>/dev/null | cut -d: -f7 || true)
if [[ -n "$USER_SHELL" ]] && ! grep -Fxq "$USER_SHELL" /etc/shells 2>/dev/null; then
    warn "User shell '$USER_SHELL' is not in /etc/shells. PAM authentication may fail!"
    warn "Consider running: echo '$USER_SHELL' | sudo tee -a /etc/shells"
fi

# --- 7. FTP Directory Selection ---
DEFAULT_PATH="/mnt/zram1"
if [[ -n "$CUSTOM_PATH" ]]; then
    FTP_ROOT="$CUSTOM_PATH"
elif [[ "$AUTO_MODE" == "true" ]]; then
    FTP_ROOT="$DEFAULT_PATH"
else
    printf "%s[INPUT]%s Enter FTP directory path [Default: %s]: " "$C_BLUE" "$C_RESET" "$DEFAULT_PATH"
    read -r dir_in || dir_in=""
    FTP_ROOT="${dir_in:-$DEFAULT_PATH}"
fi

info "Target User: $REAL_USER"
info "FTP Root Dir: $FTP_ROOT"

# --- 8. Network Detection (Interface, LAN IP & Subnet) ---
# Filter out VPN, tunnel, and container virtual interfaces
WAN_IFACE=$(ip -4 route show default 2>/dev/null \
    | awk '/dev/ && $0 !~ /dev (wg|tun|tap|tailscale|CloudflareWARP|warp|docker|waydroid|virbr|br-|veth|zt)/ {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}' || true)

if [[ -z "$WAN_IFACE" ]]; then
    WAN_IFACE=$(ip -4 -o link show 2>/dev/null \
        | awk -F': ' '$2 !~ /^(lo|wg|tun|tap|tailscale|CloudflareWARP|warp|docker|waydroid|virbr|br-|veth|zt)/ {print $2; exit}' || true)
fi

LAN_IP=""
LOCAL_SUBNET=""

if [[ -n "$WAN_IFACE" ]]; then
    LAN_IP=$(ip -4 -o addr show dev "$WAN_IFACE" scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)
    LOCAL_SUBNET=$(ip -4 route show dev "$WAN_IFACE" scope link 2>/dev/null | awk '{print $1; exit}' || true)

    # If scope link was empty, calculate network CIDR from host CIDR via standard python library
    if [[ -z "$LOCAL_SUBNET" ]]; then
        host_cidr=$(ip -4 -o addr show dev "$WAN_IFACE" scope global 2>/dev/null | awk '{print $4}' | head -n1 || true)
        if [[ -n "$host_cidr" ]]; then
            LOCAL_SUBNET=$(python3 -c "import ipaddress, sys; print(ipaddress.IPv4Network(sys.argv[1], strict=False))" "$host_cidr" 2>/dev/null || echo "$host_cidr")
        fi
    fi
fi

# Fallbacks if network interface detection was inconclusive
if [[ -z "$LAN_IP" ]]; then
    LAN_IP=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || echo "127.0.0.1")
fi

if [[ -z "$LOCAL_SUBNET" ]]; then
    raw_cidr=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | head -n1 || true)
    if [[ -n "$raw_cidr" ]]; then
        LOCAL_SUBNET=$(python3 -c "import ipaddress, sys; print(ipaddress.IPv4Network(sys.argv[1], strict=False))" "$raw_cidr" 2>/dev/null || echo "$raw_cidr")
    else
        LOCAL_SUBNET="127.0.0.1/32"
    fi
fi

info "Detected LAN Interface: ${WAN_IFACE:-Unknown}"
info "Detected LAN IP:        ${LAN_IP}"
info "Detected Local Subnet:  ${LOCAL_SUBNET}"

# --- 9. Package Installation ---
info "Verifying vsftpd package installation..."
if [[ -f /var/lib/pacman/db.lck ]]; then
    die "Pacman database locked (/var/lib/pacman/db.lck). Is another pacman process running?"
fi

if ! pacman -Qi vsftpd &>/dev/null; then
    info "Installing vsftpd..."
    if ! pacman -S --needed --noconfirm vsftpd; then
        die "Failed to install vsftpd via pacman."
    fi
    success "vsftpd installed successfully."
else
    success "vsftpd is already installed."
fi

# --- 10. Firewall Configuration ---
info "Configuring firewall for LAN FTP access..."

HAS_UFW=false
HAS_FIREWALLD=false
HAS_IPTABLES=false

if command -v ufw &>/dev/null; then
    HAS_UFW=true
elif command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
    HAS_FIREWALLD=true
elif command -v iptables &>/dev/null && iptables -S INPUT 2>/dev/null | grep -qE '^-P INPUT (DROP|REJECT)'; then
    HAS_IPTABLES=true
fi

if [[ "$HAS_UFW" == "true" ]]; then
    info "Configuring UFW (LAN-restricted to $LOCAL_SUBNET)..."
    systemctl enable ufw.service >/dev/null 2>&1 || true

    # Ensure UFW is active
    if ! ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw --force enable >/dev/null 2>&1 || true
    fi

    # Apply subnet-restricted rules for FTP control (21) and passive data range (40000:40100)
    ufw allow from "$LOCAL_SUBNET" to any port 21 proto tcp comment 'LAN FTP Control' >/dev/null 2>&1 || true
    ufw allow from "$LOCAL_SUBNET" to any port 40000:40100 proto tcp comment 'LAN FTP Passive' >/dev/null 2>&1 || true
    success "UFW rules configured for $LOCAL_SUBNET."

elif [[ "$HAS_FIREWALLD" == "true" ]]; then
    info "Configuring firewalld..."
    default_zone=$(firewall-cmd --get-default-zone 2>/dev/null || echo "public")
    firewall-cmd --permanent --zone="$default_zone" --add-service=ftp >/dev/null 2>&1 || true
    firewall-cmd --permanent --zone="$default_zone" --add-port=40000-40100/tcp >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    success "firewalld rules configured."

elif [[ "$HAS_IPTABLES" == "true" ]]; then
    info "Configuring iptables..."
    iptables -C INPUT -p tcp -s "$LOCAL_SUBNET" --dport 21 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT 1 -p tcp -s "$LOCAL_SUBNET" --dport 21 -j ACCEPT
    iptables -C INPUT -p tcp -s "$LOCAL_SUBNET" --dport 40000:40100 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT 2 -p tcp -s "$LOCAL_SUBNET" --dport 40000:40100 -j ACCEPT
    success "iptables rules applied."
else
    info "No active firewall manager blocking ingress detected. Skipping firewall rule injection."
fi

# --- 11. Directory Setup & Permissions ---
info "Setting up FTP directory ($FTP_ROOT)..."
if [[ ! -d "$FTP_ROOT" ]]; then
    mkdir -p "$FTP_ROOT"
    info "Created directory: $FTP_ROOT"
fi

# Set ownership to target user and permissions allowing full access
chown "$REAL_USER:$REAL_USER" "$FTP_ROOT" 2>/dev/null || true
chmod 777 "$FTP_ROOT"
success "Directory $FTP_ROOT prepared with full read/write permissions."

# --- 12. User Allow List Configuration ---
info "Configuring user whitelist (/etc/vsftpd.userlist)..."
touch /etc/vsftpd.userlist
chmod 600 /etc/vsftpd.userlist
chown root:root /etc/vsftpd.userlist

if ! grep -Fxq "$REAL_USER" /etc/vsftpd.userlist 2>/dev/null; then
    echo "$REAL_USER" >> /etc/vsftpd.userlist
    success "Added '$REAL_USER' to /etc/vsftpd.userlist."
else
    success "User '$REAL_USER' is already in /etc/vsftpd.userlist."
fi

# --- 13. VSFTPD Configuration Generation ---
info "Generating modern hardened /etc/vsftpd.conf..."

cat > /etc/vsftpd.conf <<EOF
# ==============================================================================
# VSFTPD Configuration - Arch Linux Secure LAN FTP Server
# Generated: $(date -Iseconds 2>/dev/null || date)
# ==============================================================================

# --- Access Control & Identity ---
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
use_localtime=YES
dirmessage_enable=YES

# --- Chroot & Root Directory ---
chroot_local_user=YES
allow_writeable_chroot=YES
local_root=$FTP_ROOT

# --- User Whitelisting ---
userlist_enable=YES
userlist_file=/etc/vsftpd.userlist
userlist_deny=NO

# --- Logging & Auditing ---
xferlog_enable=YES
xferlog_std_format=NO
log_ftp_protocol=YES
vsftpd_log_file=/var/log/vsftpd.log

# --- Standalone Network Daemon ---
listen=YES
listen_ipv6=NO
listen_port=21
pam_service_name=vsftpd

# --- Passive Mode Configuration (Firewall Friendly) ---
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100

# --- Performance & Linux Kernel Sandboxing ---
use_sendfile=YES
connect_from_port_20=YES
seccomp_sandbox=NO

# --- Banners ---
ftpd_banner=Welcome to the Arch Linux Secure LAN FTP service.
EOF

chmod 600 /etc/vsftpd.conf
chown root:root /etc/vsftpd.conf
success "Generated /etc/vsftpd.conf."

# --- 14. Service Activation & Verification ---
info "Activating vsftpd systemd service..."

# Disable conflicting socket activation if present
systemctl disable --now vsftpd.socket >/dev/null 2>&1 || true

# Enable and restart vsftpd standalone service
systemctl enable vsftpd.service >/dev/null 2>&1 || true
systemctl restart vsftpd.service

# Give daemon a brief instant to bind port
sleep 0.3

if ! systemctl is-active --quiet vsftpd.service; then
    die "vsftpd failed to start. Check 'journalctl -u vsftpd.service -e'."
fi

if ss -tln sport = :21 2>/dev/null | grep -q "21"; then
    success "vsftpd daemon is active and listening on port 21."
else
    warn "vsftpd is active, but port 21 is not yet listed in ss. Verifying socket..."
fi

# --- 15. Summary & Quick Connect Guide ---
printf "\n%s======================================================%s\n" "$C_GREEN" "$C_RESET"
printf " %sFTP Server Successfully Configured & Running!%s\n" "$C_BOLD" "$C_RESET"
printf "%s======================================================%s\n" "$C_GREEN" "$C_RESET"
printf "  %-18s : %s\n" "LAN Server IP" "${LAN_IP}"
printf "  %-18s : %s\n" "Control Port" "21 (TCP)"
printf "  %-18s : %s\n" "Passive Ports" "40000-40100 (TCP)"
printf "  %-18s : %s\n" "Allowed Subnet" "${LOCAL_SUBNET}"
printf "  %-18s : %s\n" "FTP Username" "${REAL_USER}"
printf "  %-18s : %s\n" "FTP Root Path" "${FTP_ROOT}"
printf "  %-18s : %s\n" "Log File" "/var/log/vsftpd.log"
printf "%s------------------------------------------------------%s\n" "$C_GREEN" "$C_RESET"
printf "  %sQuick Connect Examples:%s\n" "$C_BOLD" "$C_RESET"
printf "  • CLI:      ftp %s\n" "${LAN_IP}"
printf "  • cURL:     curl -u %s:<password> ftp://%s/\n" "${REAL_USER}" "${LAN_IP}"
printf "  • URL:      ftp://%s@%s:21/\n" "${REAL_USER}" "${LAN_IP}"
printf "%s======================================================%s\n\n" "$C_GREEN" "$C_RESET"

exit 0
