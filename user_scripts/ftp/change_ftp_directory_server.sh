#!/usr/bin/env bash
# Update the FTP root without interpreting path characters as sed expressions.
set -euo pipefail
script_path=$(realpath -- "${BASH_SOURCE[0]}")
# shellcheck source-path=SCRIPTDIR
# shellcheck source=vsftpd_common.sh
source "${script_path%/*}/vsftpd_common.sh"
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf 'Usage: %s [ABSOLUTE_DIRECTORY]\n' "${0##*/}"
    exit 0
fi
(( $# <= 1 )) || die 'Expected at most one directory argument.'
if (( EUID != 0 )); then
    exec sudo -- bash -- "$script_path" "$@"
fi
[[ -f /etc/vsftpd.conf ]] || die '/etc/vsftpd.conf is missing; run FTP setup first.'
new_root=${1:-}
if [[ -z $new_root ]]; then
    grep '^local_root=' /etc/vsftpd.conf || true
    read -rp 'New FTP root directory: ' new_root || die 'No directory received.'
fi
validate_root "$new_root"
new_root=$(realpath -e -- "$new_root")
validate_root "$new_root"
[[ -d $new_root ]] || die "Directory '$new_root' does not exist."
lock_update
# vsftpd silently falls back to the account home if local_root is inaccessible.
# Check the configured allowlist before accepting a root that would do that.
python3 - "$new_root" <<'ACCESS'
from pathlib import Path
import pwd
import subprocess
import sys

config = dict(line.split('=', 1) for line in
              Path('/etc/vsftpd.conf').read_text().splitlines()
              if line and not line.startswith('#') and '=' in line)
if config.get('userlist_enable', 'NO') == 'YES' and config.get('userlist_deny', 'YES') == 'NO':
    userlist = Path(config.get('userlist_file', '/etc/vsftpd.user_list'))
    for user in userlist.read_text().splitlines():
        if not user or user.startswith('#'):
            continue
        try:
            account = pwd.getpwnam(user)
        except KeyError:
            continue
        user = config.get('guest_username', 'ftp') if config.get('guest_enable', 'NO') == 'YES' else account.pw_name
        root = sys.argv[1]
        token = config.get('user_sub_token', '')
        if token:
            root = root.replace(token, account.pw_name)
        result = subprocess.run(['sudo', '-u', user, '--', 'bash', '-c',
                                 '[[ -r $1 && -x $1 ]]', 'bash', root])
        if result.returncode:
            raise SystemExit(f"FTP user {account.pw_name!r} cannot read/traverse {root!r}.")
ACCESS
begin_update vsftpd.conf
cp -aL -- /etc/vsftpd.conf "$update_dir/vsftpd.conf"
python3 - "$update_dir/vsftpd.conf" "$new_root" <<'CONFIG'
from pathlib import Path
import sys
path = Path(sys.argv[1])
lines = path.read_bytes().split(b'\n')
# Remove all active duplicates; preserve comments and unrelated settings.
lines = [line for line in lines if not line.startswith(b'local_root=')]
if lines and lines[-1] == b'':
    lines.pop()
lines.append(b'local_root=' + sys.argv[2].encode())
path.write_bytes(b'\n'.join(lines) + b'\n')
CONFIG
update_pending=true
mv -f -- "$update_dir/vsftpd.conf" /etc/vsftpd.conf
restart_attempted=true
systemctl restart vsftpd.service
verify_service
update_pending=false
info "FTP is running with root: $new_root"
