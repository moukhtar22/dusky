#!/usr/bin/env bash
# ==============================================================================
# dusky_wireguard_setup.sh — one-time system prep for the Dusky CC WireGuard page
#   0. wireguard-tools installed
#   1. /etc/wireguard            root:wheel 0750 (wheel may list, never read keys)
#   2. /etc/wireguard/**/*.conf  root:root  0600
#   3. /etc/sudoers.d/dusky-wireguard — NOPASSWD wg-quick / wg show for %wheel,
#      validated BEFORE it becomes live and installed by atomic rename.
# ==============================================================================
set -euo pipefail
shopt -s inherit_errexit nullglob globstar

# One escalation for the whole run (previously ~10 separate sudo invocations).
(( EUID == 0 )) || exec sudo -- "$BASH" "$(realpath -- "${BASH_SOURCE[0]}")" "$@"

readonly GRN=$'\e[1;32m' RED=$'\e[1;31m' BLU=$'\e[1;34m' RST=$'\e[0m'
readonly SUDOERS=/etc/sudoers.d/dusky-wireguard

ok()   { printf '%s[OK]%s    %s\n' "$GRN" "$RST" "$1"; }
info() { printf '%s[INFO]%s  %s\n' "$BLU" "$RST" "$1"; }
err()  { printf '%s[ERR]%s   %s\n' "$RED" "$RST" "$1" >&2; }

# ── 0. wireguard-tools ────────────────────────────────────────────────────────
if [[ -x /usr/bin/wg ]]; then
    ok "wireguard-tools present ($(wg --version))"
else
    info "Installing wireguard-tools ..."
    pacman -S --needed --noconfirm wireguard-tools || { err "pacman failed"; exit 1; }
    ok "wireguard-tools installed"
fi

# ── 1. Directory: create-or-fix owner/mode in a single call ──────────────────
install -d -o root -g wheel -m 0750 /etc/wireguard
ok "/etc/wireguard → root:wheel 0750"

# ── 2. Existing configs (incl. subdirectories used by path-based wg-quick up) ─
confs=(/etc/wireguard/**/*.conf)
if (( ${#confs[@]} )); then
    chown root:root -- "${confs[@]}"
    chmod 0600 -- "${confs[@]}"
    ok "Secured ${#confs[@]} config(s) → root:root 0600"
else
    info "No existing configs"
fi

# ── 3. Sudoers: stage → validate → atomic rename ─────────────────────────────
# sudo ignores sudoers.d entries containing '.', so the staged file is never live.
tmp=$(mktemp /etc/sudoers.d/.dusky-wireguard.XXXXXX)
trap 'rm -f -- "$tmp"' EXIT
cat >"$tmp" <<'EOF'
# Dusky WireGuard CC integration — wheel members manage tunnels without a password
%wheel ALL=(root) NOPASSWD: /usr/bin/systemctl ^start wg-quick@[A-Za-z0-9_=+.-]+([.]service)?$
%wheel ALL=(root) NOPASSWD: /usr/bin/systemctl ^stop wg-quick@[A-Za-z0-9_=+.-]+([.]service)?$
%wheel ALL=(root) NOPASSWD: /usr/bin/systemctl ^restart wg-quick@[A-Za-z0-9_=+.-]+([.]service)?$
%wheel ALL=(root) NOPASSWD: /usr/bin/wg-quick ^up /etc/wireguard/[A-Za-z0-9_=+./-]+[.]conf$
%wheel ALL=(root) NOPASSWD: /usr/bin/wg-quick ^down /etc/wireguard/[A-Za-z0-9_=+./-]+[.]conf$
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg show
%wheel ALL=(ALL) NOPASSWD: /usr/bin/wg show *
EOF
chmod 0440 "$tmp"
if visudo -cqf "$tmp"; then
    mv -f -- "$tmp" "$SUDOERS"
    ok "$SUDOERS installed and validated"
else
    err "sudoers syntax check failed — live configuration left untouched"
    exit 1
fi

printf '\n%s══ Setup complete ══%s\n' "$GRN" "$RST"
printf '  /etc/wireguard/  root:wheel 0750\n  *.conf           root:root  0600\n  %s  NOPASSWD wg-quick / wg show\n\n' "$SUDOERS"
info "Add tunnels via Dusky Control Center → WireGuard"
if [[ -t 0 ]]; then read -rp $'\nPress Enter to close...'; fi
