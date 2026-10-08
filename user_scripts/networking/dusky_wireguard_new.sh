#!/usr/bin/env bash
# ==============================================================================
# dusky_wireguard_new.sh — interactive wizard for a new wg-quick tunnel
#
# Runs as the desktop user; exactly ONE sudo call writes the file. The root shell
# sets umask 077 before open() and uses noclobber, so the config is born
# root:root 0600 (no world/wheel-readable window as with tee → chown → chmod) and
# can never overwrite an existing tunnel, even one this user cannot see.
# ==============================================================================
set -euo pipefail
shopt -s inherit_errexit

readonly GRN=$'\e[1;32m' YLW=$'\e[1;33m' RED=$'\e[1;31m' BLU=$'\e[1;34m' CYN=$'\e[1;36m' DIM=$'\e[2m' RST=$'\e[0m'
ok()     { printf '%s[OK]%s    %s\n' "$GRN" "$RST" "$1"; }
info()   { printf '%s[INFO]%s  %s\n' "$BLU" "$RST" "$1"; }
warn()   { printf '%s[WARN]%s  %s\n' "$YLW" "$RST" "$1"; }
err()    { printf '%s[ERR]%s   %s\n' "$RED" "$RST" "$1" >&2; }
header() { printf '\n%s══ %s ══%s\n\n' "$BLU" "$1" "$RST"; }
# ask VAR "prompt" [default]
ask()    { printf '%s%s%s ' "$CYN" "$2" "$RST"; read -r "$1"; [[ -n ${!1} || -z ${3-} ]] || printf -v "$1" '%s' "$3"; }
pause()  { if [[ -t 0 ]]; then read -rp $'\nPress Enter to close...'; fi; }

[[ -x /usr/bin/wg ]] || { err "wireguard-tools missing — run dusky_wireguard_setup.sh"; pause; exit 1; }

header "Dusky WireGuard — New Tunnel"
info "Written to /etc/wireguard/<name>.conf as root:root 0600; the private key never touches a user path."

# ── Interface name: wg-quick's own rule, bounded by IFNAMSIZ (15) ────────────
while :; do
    ask IFACE "Interface name (e.g. wg0, work):"
    if [[ ! $IFACE =~ ^[a-zA-Z0-9_=+.-]{1,15}$ ]]; then
        warn "1-15 chars of [a-zA-Z0-9_=+.-] (Linux IFNAMSIZ limit)"
    elif [[ -e /etc/wireguard/$IFACE.conf ]]; then
        warn "/etc/wireguard/$IFACE.conf already exists"
    else
        break
    fi
done
readonly DEST="/etc/wireguard/$IFACE.conf"

PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(wg pubkey <<<"$PRIVATE_KEY")   # bash ≥5.1 feeds small here-strings via a pipe
ok "Keypair generated (memory only)"
printf '  %sPublic key:%s %s\n\n' "$DIM" "$RST" "$PUBLIC_KEY"

while :; do
    ask IFACE_ADDR "Tunnel address(es) (e.g. 10.0.0.2/24):"
    [[ $IFACE_ADDR == */* ]] && break
    warn "CIDR required (address/prefix)"
done
ask DNS "DNS server(s) (blank = none, e.g. 1.1.1.1):"
if [[ -n $DNS ]] && ! command -v resolvconf >/dev/null; then
    warn "wg-quick needs resolvconf for DNS=; install: sudo pacman -S systemd-resolvconf"
fi

header "Peer (server)"
while :; do
    ask PEER_PUBKEY "Peer public key:"
    [[ $PEER_PUBKEY =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$ ]] && break
    warn "Not a WireGuard public key (44-char base64 of 32 bytes)"
done
ask PEER_ENDPOINT "Peer endpoint host:port (blank = none, e.g. vpn.example.com:51820):"
ask ALLOWED_IPS "Allowed IPs [0.0.0.0/0, ::/0]:" "0.0.0.0/0, ::/0"
while :; do
    ask KEEPALIVE "Persistent keepalive seconds (blank = off, e.g. 25):"
    [[ -z $KEEPALIVE ]] || { [[ $KEEPALIVE =~ ^[0-9]+$ ]] && (( KEEPALIVE >= 1 && KEEPALIVE <= 65535 )); } && break
    warn "1-65535 or blank"
done

printf -v CONFIG '[Interface]\nPrivateKey = %s\nAddress = %s\n' "$PRIVATE_KEY" "$IFACE_ADDR"
[[ -z $DNS ]] || printf -v CONFIG '%sDNS = %s\n' "$CONFIG" "$DNS"
printf -v CONFIG '%s\n[Peer]\nPublicKey = %s\n' "$CONFIG" "$PEER_PUBKEY"
[[ -z $PEER_ENDPOINT ]] || printf -v CONFIG '%sEndpoint = %s\n' "$CONFIG" "$PEER_ENDPOINT"
printf -v CONFIG '%sAllowedIPs = %s\n' "$CONFIG" "$ALLOWED_IPS"
[[ -z $KEEPALIVE ]] || printf -v CONFIG '%sPersistentKeepalive = %s\n' "$CONFIG" "$KEEPALIVE"

header "Review"
printf '%s%s%s\n\n' "$DIM" "${CONFIG/"$PRIVATE_KEY"/<hidden>}" "$RST"
ask CONFIRM "Write $DEST? [y/N]:"
if [[ $CONFIRM != [Yy]* ]]; then
    warn "Aborted — nothing written"
    pause
    exit 0
fi

if printf '%s\n' "$CONFIG" | sudo -- /usr/bin/bash -c 'umask 077; set -C; exec cat >"$1"' _ "$DEST"; then
    unset PRIVATE_KEY CONFIG
    ok "Written: $DEST (root:root 0600)"
    printf '  %sPublic key:%s %s\n\n' "$DIM" "$RST" "$PUBLIC_KEY"
    info "Reload the Dusky Control Center (Ctrl+R) to see the tunnel."
else
    err "Write failed (file exists or sudo denied) — nothing changed"
    exit 1
fi
pause
