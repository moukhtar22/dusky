#!/usr/bin/env bash
# DUSKY_INTERACTIVE=true
# Requires: bash 5.0+, iwd (iwctl), systemd, coreutils, iproute2
# Target: Arch Linux Live ISO environment

set -Eeuo pipefail

export LC_ALL=C

if [[ -t 1 ]]; then
    readonly C_RESET=$'\e[0m'
    readonly C_RED=$'\e[1;31m'
    readonly C_GREEN=$'\e[1;32m'
    readonly C_YELLOW=$'\e[1;33m'
    readonly C_CYAN=$'\e[1;36m'
else
    readonly C_RESET=""
    readonly C_RED=""
    readonly C_GREEN=""
    readonly C_YELLOW=""
    readonly C_CYAN=""
fi

cleanup() {
    stty echo 2>/dev/null || true
    echo -e "\n${C_YELLOW}[*] Script interrupted. Exiting cleanly.${C_RESET}"
    exit 130
}
trap cleanup SIGINT SIGTERM

log_info()    { echo -e "${C_CYAN}[i] ${1}${C_RESET}"; }
log_success() { echo -e "${C_GREEN}[✓] ${1}${C_RESET}"; }
log_warn()    { echo -e "${C_YELLOW}[!] ${1}${C_RESET}"; }
log_error()   { echo -e "${C_RED}[X] ${1}${C_RESET}"; }

fail_and_exit() {
    log_error "Critical failure: No active internet connection established."
    log_warn "This installation script requires an active route to the internet in Online mode."
    exit 1
}

check_connectivity() {
    if ping -n -c 2 -W 2 1.1.1.1 >/dev/null 2>&1 || \
       ping -n -c 2 -W 2 8.8.8.8 >/dev/null 2>&1; then

        if command -v curl >/dev/null 2>&1; then
            if curl -s --connect-timeout 5 --max-time 5 http://ping.archlinux.org/ 2>/dev/null | grep -E -q "NetworkManager is online|captive portal detection"; then
                return 0
            fi
        fi

        if timeout 5 ping -n -c 1 -W 2 archlinux.org >/dev/null 2>&1 || \
           timeout 5 ping -n -c 1 -W 2 google.com >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

flush_dns() {
    if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        resolvectl flush-caches 2>/dev/null || true
    fi
    return 0
}

ensure_resolv_conf() {
    if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        if [[ -f /run/systemd/resolve/stub-resolv.conf ]] && [[ ! -L /etc/resolv.conf || $(readlink /etc/resolv.conf 2>/dev/null) != *"/run/systemd/resolve/stub-resolv.conf"* ]]; then
            ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf 2>/dev/null || true
        fi
    fi
    return 0
}

check_eth_carrier() {
    local dev=$1
    ip link set dev "$dev" up 2>/dev/null || true
    for ((i = 0; i < 10; i++)); do
        if [[ -f "/sys/class/net/$dev/carrier" ]] && [[ $(cat "/sys/class/net/$dev/carrier" 2>/dev/null) -eq 1 ]]; then
            return 0
        fi
        if ip link show dev "$dev" 2>/dev/null | grep -q "LOWER_UP"; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

get_eth_dev() {
    ip link show | awk -F': ' '/^[0-9]+: e/{print $2; exit}'
}

get_wifi_dev() {
    local iface
    for iface in /sys/class/net/*; do
        if [[ -d "$iface/wireless" ]]; then
            basename "$iface"
            return 0
        fi
    done
    iwctl device list 2>/dev/null | awk 'NR>4 {
        gsub(/\x1b\[[0-9;]*[a-zA-Z]/, "", $0)
        if ($1 != "" && $1 != "Name") { print $1; exit }
    }'
    return 0
}

get_wifi_devs() {
    local iface
    shopt -s nullglob
    for iface in /sys/class/net/*; do
        if [[ -d "$iface/wireless" ]] || [[ -d "$iface/phy80211" ]]; then
            basename "$iface"
        fi
    done
    shopt -u nullglob
    return 0
}

wait_scan_done() {
    local wifi_dev=$1
    local scanning
    for ((i = 0; i < 12; i++)); do
        scanning=$(iwctl station "$wifi_dev" show 2>/dev/null | awk '/Scanning/ {print $2; exit}')
        if [[ "$scanning" == "no" ]]; then
            break
        fi
        sleep 0.3
    done
    return 0
}

wait_associated() {
    local wifi_dev=$1
    local st
    for ((i = 0; i < 20; i++)); do
        st=$(iwctl station "$wifi_dev" show 2>/dev/null | awk '/State/ {print $2; exit}')
        if [[ "$st" == "connected" ]]; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

valid_passphrase() {
    local pass=$1
    [[ -z "$pass" ]] && return 0
    if (( ${#pass} >= 8 && ${#pass} <= 63 )); then
        return 0
    fi
    if (( ${#pass} == 64 )) && [[ "$pass" =~ ^[0-9a-fA-F]{64}$ ]]; then
        return 0
    fi
    return 1
}

scan_networks() {
    local wifi_dev=$1
    iwctl station "$wifi_dev" get-networks 2>/dev/null | awk 'NR>4 {
        line = $0
        gsub(/\x1b\[[0-9;]*[a-zA-Z]/, "", line)
        if (match(line, / (open|psk|8021x)[ \t]+(\*+|-?[0-9]+)[ \t]*$/)) {
            ssid = substr(line, 1, RSTART-1)
            sub(/^[ \t>]+/, "", ssid)
            sub(/[ \t]+$/, "", ssid)
            if (ssid != "") print ssid
        }
    }' | sort -u
    return 0
}

log_info "Initializing Live ISO Network Connect Wizard..."

log_info "Verifying current internet routing..."
if check_connectivity; then
    log_success "System is already connected to the internet."
    exit 0
fi

log_warn "No internet routing detected."

ensure_resolv_conf

if ! systemctl is-active --quiet iwd; then
    log_info "Starting wireless daemon (iwd)..."
    systemctl start iwd 2>/dev/null || true
    sleep 1
fi

log_info "Attempting automatic DHCP on wired interfaces..."
for eth_auto in /sys/class/net/e*; do
    eth_auto=$(basename "$eth_auto")
    if check_eth_carrier "$eth_auto"; then
        log_info "Carrier on $eth_auto. Requesting lease..."
        ip link set dev "$eth_auto" up 2>/dev/null || true
        dhcpcd "$eth_auto" >/dev/null 2>&1 || true
        sleep 3
        if check_connectivity; then
            flush_dns
            log_success "LAN connected autonomous ($eth_auto)."
            exit 0
        fi
    fi
done

if [[ ! -t 0 ]]; then
    log_info "Non-interactive environment: checking for wireless auto-connect to known networks..."
    mapfile -t auto_wifi_devs < <(get_wifi_devs)
    if [[ ${#auto_wifi_devs[@]} -gt 0 ]]; then
        auto_wifi="${auto_wifi_devs[0]}"
        ip link set dev "$auto_wifi" up 2>/dev/null || true
        if wait_associated "$auto_wifi"; then
            log_info "Wireless auto-connected on $auto_wifi. Requesting lease..."
            dhcpcd "$auto_wifi" >/dev/null 2>&1 || true
            sleep 3
            if check_connectivity; then
                flush_dns
                log_success "Internet connected autonomously via known Wi-Fi ($auto_wifi)."
                exit 0
            fi
        fi
    fi
    log_warn "Non-interactive environment: attempting LAN DHCP lease..."
    eth_dev=$(get_eth_dev)
    if [[ -n "$eth_dev" ]] && check_eth_carrier "$eth_dev"; then
        log_info "LAN cable connected to $eth_dev. Requesting lease..."
        dhcpcd "$eth_dev" >/dev/null 2>&1 || true
        sleep 5
        if check_connectivity; then
            log_success "LAN connected autonomous."
            exit 0
        fi
    fi
    log_error "Interactive connection requires a TTY."
    fail_and_exit
fi

while true; do
    PS3=$(echo -e "\n${C_CYAN}Select connection interface or option: ${C_RESET}")
    select conn_method in "LAN (Wired)" "Wi-Fi" "Re-check Connection" "Abort"; do
    case $conn_method in
        "LAN (Wired)")
            mapfile -t wired_cands < <(ip link show | awk -F': ' '/^[0-9]+: e/{print $2}')
            target_eth=""
            for cand in "${wired_cands[@]}"; do
                if check_eth_carrier "$cand"; then
                    target_eth="$cand"
                    break
                fi
            done

            if [[ -z "$target_eth" ]]; then
                if [[ ${#wired_cands[@]} -eq 0 ]]; then
                    log_error "No physical Ethernet interface detected."
                else
                    log_error "No carrier detected on any wired interface. Check physical cable."
                fi
                break
            fi

            log_info "Carrier active on $target_eth. Requesting DHCP lease..."
            ip link set dev "$target_eth" up 2>/dev/null || true
            if dhcpcd -k "$target_eth" >/dev/null 2>&1 || true; dhcpcd "$target_eth" >/dev/null 2>&1; then
                sleep 3
                if check_connectivity; then
                    flush_dns
                    log_success "LAN connected and internet routed ($target_eth)."
                    exit 0
                else
                    log_error "Carrier on $target_eth, but no internet access (Check DNS/Gateway)."
                fi
            else
                log_error "Failed to obtain DHCP lease on $target_eth."
            fi
            break
            ;;

        "Wi-Fi")
            mapfile -t wifi_devs < <(get_wifi_devs)
            if [[ ${#wifi_devs[@]} -eq 0 ]]; then
                fallback_dev=$(get_wifi_dev)
                if [[ -n "$fallback_dev" ]]; then
                    wifi_devs=("$fallback_dev")
                fi
            fi

            if [[ ${#wifi_devs[@]} -eq 0 ]]; then
                log_error "No Wi-Fi interface detected on this system."
                break
            fi

            wifi_dev="${wifi_devs[0]}"
            if [[ ${#wifi_devs[@]} -gt 1 ]]; then
                PS3=$(echo -e "\n${C_CYAN}Select Wi-Fi interface: ${C_RESET}")
                select wdev in "${wifi_devs[@]}"; do
                    if [[ -n "$wdev" ]]; then
                        wifi_dev="$wdev"
                        break
                    fi
                    log_warn "Invalid selection."
                done
            fi

            log_info "Wi-Fi interface detected: $wifi_dev"

            if command -v rfkill >/dev/null 2>&1; then
                rfkill unblock wifi wlan 2>/dev/null || true
            fi

            ip link set dev "$wifi_dev" up 2>/dev/null || true
            iwctl device "$wifi_dev" set-property Powered on 2>/dev/null || true

            log_info "Scanning for 802.11 networks using iwctl..."
            iwctl station "$wifi_dev" scan >/dev/null 2>&1 || true
            wait_scan_done "$wifi_dev"

            mapfile -t networks < <(scan_networks "$wifi_dev" || true)

            if [[ ${#networks[@]} -eq 0 ]]; then
                log_warn "No networks found on initial scan. Retrying scan..."
                iwctl station "$wifi_dev" scan >/dev/null 2>&1 || true
                wait_scan_done "$wifi_dev"
                mapfile -t networks < <(scan_networks "$wifi_dev" || true)

                if [[ ${#networks[@]} -eq 0 ]]; then
                    log_error "No broadcasting 802.11 networks found in range."
                    break
                fi
            fi

            log_info "Discovered ${#networks[@]} networks."
            PS3=$(echo -e "\n${C_CYAN}Select target SSID or option: ${C_RESET}")

            select ssid in "${networks[@]}" "[Rescan Networks]" "[Hidden SSID - Enter Manually]" "[Back to Main Menu]"; do
                if [[ -z "$ssid" ]]; then
                    log_warn "Invalid selection."
                    continue
                fi
                if [[ "$ssid" == "[Back to Main Menu]" ]]; then
                    break 2
                fi
                if [[ "$ssid" == "[Rescan Networks]" ]]; then
                    break
                fi
                hidden=0
                if [[ "$ssid" == "[Hidden SSID - Enter Manually]" ]]; then
                    read -r -p "Enter hidden SSID: " ssid || true
                    if [[ -z "$ssid" ]]; then
                        log_warn "SSID cannot be empty."
                        continue
                    fi
                    hidden=1
                fi
                    echo ""
                    read -r -s -p "Enter WPA passphrase for '$ssid' (leave empty if open): " pass || true
                    echo -e "\n"

                    if ! valid_passphrase "$pass"; then
                        log_warn "WPA passphrases must be 8-63 ASCII characters (or 64 hex digits)."
                        continue
                    fi

                    log_info "Connecting station to '$ssid'..."

                    if (( hidden == 1 )); then
                        if [[ -n "$pass" ]]; then
                            iwctl --passphrase "$pass" --dont-ask station "$wifi_dev" connect-hidden "$ssid" || { log_error "iwctl connect-hidden failed."; break; }
                        else
                            iwctl --dont-ask station "$wifi_dev" connect-hidden "$ssid" || { log_error "iwctl connect-hidden failed."; break; }
                        fi
                    elif [[ -n "$pass" ]]; then
                        iwctl --passphrase "$pass" --dont-ask station "$wifi_dev" connect "$ssid" || { log_error "iwctl connect failed."; break; }
                    else
                        iwctl --dont-ask station "$wifi_dev" connect "$ssid" || { log_error "iwctl connect failed."; break; }
                    fi

                    log_info "Waiting for Wi-Fi association..."

                    if ! wait_associated "$wifi_dev"; then
                        log_error "Wi-Fi association/authentication failed for '$ssid'. Check credentials or signal."
                        continue
                    fi

                    log_info "Wi-Fi authenticated. Requesting IP lease..."
                    dhcpcd -k "$wifi_dev" >/dev/null 2>&1 || true
                    dhcpcd "$wifi_dev" >/dev/null 2>&1 || true

                    connected=0
                    for ((c=1; c<=15; c++)); do
                        if check_connectivity; then
                            connected=1
                            break
                        fi
                        sleep 1
                    done

                    if [[ $connected -eq 1 ]]; then
                        flush_dns
                        log_success "Connected and internet routed."
                        exit 0
                    else
                        log_error "Connected to SSID but failed to route packets (No internet)."
                    fi
                    break
            done
            break
            ;;

        "Re-check Connection")
            log_info "Verifying current internet routing..."
            if check_connectivity; then
                flush_dns
                log_success "System is already connected to the internet."
                exit 0
            else
                log_warn "Still no internet connectivity detected."
            fi
            break
            ;;

        "Abort")
            fail_and_exit
            ;;

        *)
            log_warn "Invalid selection. Please choose an option from the menu."
            ;;
    esac
    done
done
