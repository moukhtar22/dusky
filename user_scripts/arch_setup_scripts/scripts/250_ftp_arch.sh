#!/usr/bin/env bash
#d: Set up a LAN FTP (vsftpd) server
set -euo pipefail
script_path=$(realpath -- "${BASH_SOURCE[0]}")
exec bash -- "${script_path%/*}/../../ftp/ftp_setup_arch.sh" "$@"
