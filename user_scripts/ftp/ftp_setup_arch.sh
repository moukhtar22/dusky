#!/usr/bin/env bash
# Configure local-user FTP with passive transfers and subnet-scoped firewall rules.
set -euo pipefail
script_path=$(realpath -- "${BASH_SOURCE[0]}")
# shellcheck source-path=SCRIPTDIR
# shellcheck source=vsftpd_common.sh
source "${script_path%/*}/vsftpd_common.sh"
original_args=("$@")
auto=false
ftp_root=''
ftp_user=''
interface=''

while (( $# )); do
    case $1 in
        -h|--help)
            printf 'Usage: %s [--auto] [--dir PATH] [--user USER] [--interface IFACE]\n' "${0##*/}"
            printf '  -a, --auto, -y, --yes   Use defaults without prompts\n'
            printf '  -d, --dir, -p, --path   FTP root (default: /mnt/zram1)\n'
            printf '  -u, --user              Existing local user (default: invoking user)\n'
            printf '  -i, --interface         Override automatic LAN interface selection\n'
            exit 0 ;;
        -a|--auto|-y|--yes) auto=true; shift ;;
        -d|--dir|-p|--path|-u|--user|-i|--interface)
            (( $# >= 2 )) && [[ -n $2 && $2 != -* ]] || die "Option $1 requires a value."
            case $1 in
                -d|--dir|-p|--path) ftp_root=$2 ;;
                -u|--user) ftp_user=$2 ;;
                -i|--interface) interface=$2 ;;
            esac
            shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
if (( EUID != 0 )); then
    exec sudo -- bash -- "$script_path" "${original_args[@]}"
fi
if [[ $auto == false ]]; then
    read -rp 'Set up an FTP server for local file sharing? [Y/n]: ' answer || die 'No confirmation received.'
    case ${answer,,} in ''|y|yes) ;; *) info 'Cancelled.'; exit 0 ;; esac
fi
ftp_user=${ftp_user:-${SUDO_USER:-}}
if [[ -z $ftp_user || $ftp_user == root ]]; then
    [[ $auto == false ]] || die 'Specify a regular user with --user when running directly as root.'
    read -rp 'Username to allow FTP access: ' ftp_user || die 'No username received.'
fi
[[ -n $ftp_user && $ftp_user != -* && $ftp_user != *:* && $ftp_user != *$'\n'* ]] || die 'Invalid username.'
passwd_entry=$(getent passwd "$ftp_user") || die "User '$ftp_user' does not exist."
IFS=: read -r account_name _ account_uid account_gid _ _ account_shell <<< "$passwd_entry"
[[ $account_uid != 0 ]] || die 'The FTP account must be a regular user.'
ftp_user=$account_name
grep -Fxq -- "$account_shell" /etc/shells || die "User shell '$account_shell' is not listed in /etc/shells (required by vsftpd PAM)."
if [[ -f /etc/ftpusers ]] && grep -Fxq -- "$ftp_user" /etc/ftpusers; then
    die "User '$ftp_user' is denied by /etc/ftpusers."
fi
if [[ -z $ftp_root ]]; then
    if [[ $auto == false ]]; then
        read -rp 'FTP root directory [/mnt/zram1]: ' ftp_root || die 'No directory received.'
    fi
    ftp_root=${ftp_root:-/mnt/zram1}
fi
validate_root "$ftp_root"
ftp_root=$(realpath -m -- "$ftp_root")
validate_root "$ftp_root"
lock_update

# Derive the subnet from the selected live address, not an unrelated route.
# Prefer the lowest-metric default route and exclude VPN/container interfaces.
# Explicit --interface supports bridges and unusual interface names.
network=$(python3 - "$interface" <<'NETWORK'
import ipaddress
import json
import subprocess
import sys

def ip(*args):
    return json.loads(subprocess.check_output(['ip', '-j', '-4', *args], text=True))

requested = sys.argv[1]
routes = sorted(ip('route', 'show', 'default'), key=lambda r: r.get('metric', 0))
preferred = [r.get('dev') for r in routes]
addresses = ip('-d', 'address', 'show')
addresses.sort(key=lambda a: preferred.index(a['ifname']) if a['ifname'] in preferred else len(preferred))
excluded_names = ('lo', 'wg', 'tun', 'tap', 'tailscale', 'CloudflareWARP',
                  'warp', 'docker', 'waydroid', 'virbr', 'br-', 'veth', 'zt')
excluded_kinds = {'wireguard', 'tun', 'veth', 'vxlan', 'geneve', 'gre', 'gretap', 'ipip', 'sit'}
for link in addresses:
    name = link['ifname']
    if requested:
        if name != requested:
            continue
    elif name.startswith(excluded_names) or link.get('linkinfo', {}).get('info_kind') in excluded_kinds:
        continue
    if 'UP' not in link.get('flags', []):
        continue
    for addr in link.get('addr_info', []):
        if addr.get('scope') != 'global' or addr.get('valid_life_time') == 0:
            continue
        host = ipaddress.IPv4Interface(f"{addr['local']}/{addr['prefixlen']}")
        print(name, host.ip, host.network)
        sys.exit(0)
raise SystemExit('No active LAN IPv4 address found; connect the LAN or specify --interface.')
NETWORK
) || die 'LAN detection failed.'
read -r interface lan_ip subnet <<< "$network"
info "User: $ftp_user; root: $ftp_root; LAN: $interface ($lan_ip, $subnet)"
if ! pacman -Q vsftpd >/dev/null 2>&1; then
    info 'Installing vsftpd from the configured repositories/cache.'
    pacman -S --needed --noconfirm vsftpd
fi

# Preserve existing shared/mounted directory metadata and all descendant modes.
if [[ ! -d $ftp_root ]]; then
    mkdir -p -- "$ftp_root"
    chown -- "$account_uid:$account_gid" "$ftp_root"
    chmod 0755 -- "$ftp_root"
fi
if ! sudo -u "$ftp_user" -- bash -c '[[ -r $1 && -w $1 && -x $1 ]]' bash "$ftp_root"; then
    die "User '$ftp_user' needs read/write/traverse access to '$ftp_root'; adjust its permissions first."
fi

# Configure only active managers. Do not activate a new firewall or hide failures.
ufw_status=''
iptables_policy=''
firewalld_active=false
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld.service; then
    firewalld_active=true
elif command -v ufw >/dev/null; then
    ufw_status=$(LC_ALL=C ufw status) || die 'Could not query UFW status.'
fi
if [[ $firewalld_active == true ]]; then
    # firewalld reports an unassigned interface as "no zone" with exit 2.
    if zone=$(LC_ALL=C firewall-cmd --get-zone-of-interface="$interface" 2>&1); then
        :
    else
        zone_status=$?
        [[ $zone_status == 2 && $zone == 'no zone' ]] || die "Could not query firewalld zone: $zone"
    fi
    if [[ -z $zone || $zone == 'no zone' ]]; then
        zone=$(firewall-cmd --get-default-zone)
    fi
    for port in 21 40000-40100; do
        rule="rule family=\"ipv4\" source address=\"$subnet\" port port=\"$port\" protocol=\"tcp\" accept"
        firewall-cmd --zone="$zone" --add-rich-rule="$rule"
        firewall-cmd --permanent --zone="$zone" --add-rich-rule="$rule"
    done
    info "firewalld rules configured for $subnet in zone $zone."
elif [[ $ufw_status == 'Status: active'* ]]; then
    ufw allow in on "$interface" from "$subnet" to any port 21 proto tcp comment 'LAN FTP Control'
    ufw allow in on "$interface" from "$subnet" to any port 40000:40100 proto tcp comment 'LAN FTP Passive'
    info "UFW rules configured for $subnet."
else
    if command -v iptables >/dev/null; then
        iptables_policy=$(iptables -w 5 -S INPUT) || die 'Could not query iptables INPUT rules.'
    fi
    if [[ $iptables_policy == *'-P INPUT DROP'* ]]; then
        for port in 21 40000:40100; do
            if ! iptables -w 5 -C INPUT -i "$interface" -s "$subnet" -p tcp --dport "$port" -j ACCEPT; then
                iptables -w 5 -I INPUT 1 -i "$interface" -s "$subnet" -p tcp --dport "$port" -j ACCEPT
            fi
        done
        warn 'iptables rules are runtime-only; persist them using your existing firewall configuration.'
    else
        warn 'No supported active firewall detected. FTP listens on all IPv4 interfaces; LAN-only access is not enforced by this script.'
    fi
fi

begin_update vsftpd.conf vsftpd.userlist
if [[ -e /etc/vsftpd.userlist ]]; then
    cp -- /etc/vsftpd.userlist "$update_dir/vsftpd.userlist"
else
    : > "$update_dir/vsftpd.userlist"
fi
if ! grep -Fxq -- "$ftp_user" "$update_dir/vsftpd.userlist"; then
    python3 - "$update_dir/vsftpd.userlist" "$ftp_user" <<'USERLIST'
from pathlib import Path
import sys
path = Path(sys.argv[1])
data = path.read_bytes()
path.write_bytes(data + (b'\n' if data and not data.endswith(b'\n') else b'')
                 + sys.argv[2].encode() + b'\n')
USERLIST
fi
cat > "$update_dir/vsftpd.conf" <<EOF
# Local-user FTP; subnet access rules are managed separately by the firewall.
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
use_localtime=YES
dirmessage_enable=YES
chroot_local_user=YES
allow_writeable_chroot=YES
local_root=$ftp_root
userlist_enable=YES
userlist_file=/etc/vsftpd.userlist
userlist_deny=NO
xferlog_enable=YES
xferlog_std_format=NO
vsftpd_log_file=/var/log/vsftpd.log
listen=YES
listen_ipv6=NO
listen_port=21
pam_service_name=vsftpd
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=40100
use_sendfile=YES
connect_from_port_20=YES
seccomp_sandbox=NO
ftpd_banner=Welcome to the Arch Linux LAN FTP service.
EOF
chmod 0600 -- "$update_dir/vsftpd.conf" "$update_dir/vsftpd.userlist"
chown root:root -- "$update_dir/vsftpd.conf" "$update_dir/vsftpd.userlist"
update_pending=true
mv -f -- "$update_dir/vsftpd.userlist" /etc/vsftpd.userlist
mv -f -- "$update_dir/vsftpd.conf" /etc/vsftpd.conf
if [[ $(systemctl show --property=LoadState --value vsftpd.socket) == loaded ]]; then
    systemctl is-active --quiet vsftpd.socket && socket_was_active=true
    systemctl is-enabled --quiet vsftpd.socket && socket_was_enabled=true
    socket_changed=true
    systemctl disable --now vsftpd.socket
fi
restart_attempted=true
systemctl restart vsftpd.service
verify_service
systemctl enable vsftpd.service
update_pending=false
printf '\nFTP is ready: ftp://%s@%s/\nRoot: %s\nPassive ports: 40000–40100/TCP\n' "$ftp_user" "$lan_ip" "$ftp_root"
