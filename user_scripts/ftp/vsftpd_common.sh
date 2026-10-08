#!/usr/bin/env bash
# Shared by the setup and directory changer; source this file.
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

validate_root() {
    [[ $1 == /* && $1 != *$'\n'* && $1 != *$'\r'* ]] ||
        die 'FTP root must be an absolute path without line breaks.'
}

# Require a live service and a successful FTP greeting, rather than merely a
# listener on port 21. Support existing IPv4 and IPv6 configurations.
verify_service() {
    python3 - <<'CHECK'
import ftplib
from pathlib import Path
import subprocess
import time

config = dict(line.split('=', 1) for line in
              Path('/etc/vsftpd.conf').read_text().splitlines()
              if line and not line.startswith('#') and '=' in line)
ipv6 = config.get('listen_ipv6', 'NO') == 'YES'
address = config.get('listen_address6' if ipv6 else 'listen_address',
                     '::1' if ipv6 else '127.0.0.1')
if address in ('0.0.0.0', '::'):
    address = '::1' if ipv6 else '127.0.0.1'
port = int(config.get('listen_port', '21'))
deadline = time.monotonic() + 5
last_error = 'no response'
while time.monotonic() < deadline:
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(address, port, timeout=1)
        subprocess.run(['systemctl', 'is-active', '--quiet', 'vsftpd.service'],
                       check=True)
        break
    except (OSError, EOFError, ftplib.Error, subprocess.CalledProcessError) as error:
        last_error = str(error)
        time.sleep(0.1)
else:
    raise SystemExit(f'vsftpd did not become ready: {last_error}; '
                     'check journalctl -u vsftpd.service -e')
CHECK
}

lock_update() {
    # Hold the lock before any package, directory, firewall, or config mutation.
    exec {update_lock}>/run/lock/vsftpd-config.lock
    flock --nonblocking "$update_lock" || die 'Another vsftpd configuration update is running.'
}

# Stage in /etc for atomic file replacement. Retain originals until verification.
begin_update() {
    update_dir=$(mktemp -d /etc/.vsftpd-update.XXXXXXXX)
    update_files=("$@")
    update_pending=false
    restart_attempted=false
    service_was_active=false
    socket_was_active=false
    socket_was_enabled=false
    socket_changed=false
    trap finish_update EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    systemctl is-active --quiet vsftpd.service && service_was_active=true
    for file in "${update_files[@]}"; do
        if [[ -e /etc/$file || -L /etc/$file ]]; then
            cp -a -- "/etc/$file" "$update_dir/$file.old"
        fi
    done
}

finish_update() {
    local status=$? file restored=true
    trap - EXIT INT TERM
    if [[ $update_pending == true ]]; then
        warn 'Restoring previous vsftpd configuration after failure.'
        for file in "${update_files[@]}"; do
            if [[ -e $update_dir/$file.old || -L $update_dir/$file.old ]]; then
                cp -a -- "$update_dir/$file.old" "$update_dir/$file.restore" &&
                    mv -f -- "$update_dir/$file.restore" "/etc/$file" || restored=false
            else
                rm -f -- "/etc/$file" || restored=false
            fi
        done
        if [[ $restored == false ]]; then
            warn "Restore failed; recovery files remain in $update_dir."
            exit 1
        fi
        if [[ $restart_attempted == true ]]; then
            if [[ $service_was_active == true ]]; then
                if ! systemctl restart vsftpd.service || ! verify_service; then
                    warn 'Previous service could not be restored to readiness.'
                fi
            else
                systemctl stop vsftpd.service || warn 'Could not stop failed service.'
            fi
        fi
        if [[ $socket_changed == true ]]; then
            if [[ $socket_was_enabled == true ]]; then
                systemctl enable vsftpd.socket || warn 'Could not restore socket enablement.'
            fi
            if [[ $socket_was_active == true ]]; then
                systemctl start vsftpd.socket || warn 'Could not restore socket activation.'
            fi
        fi
    fi
    rm -rf -- "$update_dir" || warn "Could not remove $update_dir."
    exit "$status"
}
