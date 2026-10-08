#!/usr/bin/env bash
#d: Connect to Wi-Fi and set up networking

set -Eeuo pipefail

# Standardize environment for predictable parsing
export LC_ALL=C

# ANSI Colors for UI
readonly C_RESET='\e[0m'
readonly C_RED='\e[1;31m'
readonly C_GREEN='\e[1;32m'
readonly C_YELLOW='\e[1;33m'
readonly C_CYAN='\e[1;36m'

# ==============================================================================
# Helper Functions
# ==============================================================================

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
    log_warn "This orchestration script requires an active route to the internet."
    log_warn "Please resolve your network issues and rerun the pipeline."
    exit 1
}

wait_for_nm() {
    if ! systemctl is-active --quiet NetworkManager; then
        log_warn "NetworkManager service is not active. Attempting to start it..."
        sudo systemctl start NetworkManager 2>/dev/null || true
        sleep 2
        if ! systemctl is-active --quiet NetworkManager; then
            log_error "NetworkManager service is not running and could not be started."
            exit 1
        fi
    fi

    # Wait up to 10 seconds for DBus and NM to fully initialize interfaces
    local attempt=1
    while ! nmcli -g RUNNING general 2>/dev/null | grep -q "running"; do
        if (( attempt > 10 )); then
            log_error "NetworkManager failed to reach 'running' state."
            exit 1
        fi
        sleep 1
        ((attempt++))
    done
}

flush_dns_caches() {
    log_info "Flushing local DNS caches to clear negative/stale records..."
    
    # Per dnsmasq(8) manual: SIGHUP clears the cache and re-loads hosts
    if systemctl is-active --quiet dnsmasq; then
        sudo systemctl kill -s HUP dnsmasq 2>/dev/null || sudo pkill -HUP dnsmasq 2>/dev/null || true
        log_info " -> Flushed dnsmasq cache."
    fi
    
    if systemctl is-active --quiet systemd-resolved; then
        sudo resolvectl flush-caches 2>/dev/null || sudo systemd-resolve --flush-caches 2>/dev/null || true
        log_info " -> Flushed systemd-resolved cache."
    fi
    
    # Allow DBus and network stack 2 seconds to settle routes post-flush
    sleep 2
}

check_connectivity() {
    # 1. Native NM Cached State Check
    local nm_state
    nm_state=$(nmcli -t networking connectivity 2>/dev/null || echo "unknown")
    if [[ "$nm_state" == "full" ]]; then
        return 0
    elif [[ "$nm_state" == "portal" ]]; then
        return 1 # Explicitly behind a captive portal, fail immediately
    fi

    # 2. Kernel-level Routing Check via HTTP (Strict Portal Avoidance)
    if command -v curl >/dev/null 2>&1; then
        # Test A: Arch Linux official check (Defeats redirects, supports both old and new check strings)
        if curl -s --connect-timeout 5 --max-time 5 http://ping.archlinux.org/ 2>/dev/null | grep -E -q "NetworkManager is online|captive portal detection"; then
            return 0
        fi
        
        # Test B: Global 204 No Content check (A captive portal will return 200/302, not 204)
        local http_code
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 5 http://cpcheck.gstatic.com/generate_204 2>/dev/null || echo "000")
        if [[ "$http_code" == "204" ]]; then
            return 0
        fi
    fi

    # 3. Kernel-level Routing Check via ICMP (Fallback if HTTP is blocked)
    # Ping Cloudflare/Google directly to verify Layer 3 ICMP routing
    if ping -n -c 2 -W 2 1.1.1.1 >/dev/null 2>&1 || \
       ping -n -c 2 -W 2 8.8.8.8 >/dev/null 2>&1; then
        
        # Ensure DNS is not hijacked by verifying a non-existent domain fails to resolve.
        # (Some captive portals allow ICMP/ping but hijack DNS queries to resolve everything)
        # Bound the lookup: a hung resolver (filtered/slow DNS) must not stall the script.
        if ! timeout 5 getent ahosts nonexistent-dns-test-12345.org >/dev/null 2>&1; then
            # Concurrent parallel checks for DNS routing reliability
            timeout 5 ping -n -c 1 -W 2 google.com >/dev/null 2>&1 &
            local p1=$!
            timeout 5 ping -n -c 1 -W 2 cloudflare.com >/dev/null 2>&1 &
            local p2=$!
            
            local has_internet=1
            for ((i=0; i<20; i++)); do
                if ! kill -0 "$p1" 2>/dev/null; then
                    if wait "$p1"; then
                        has_internet=0
                        break
                    fi
                fi
                if ! kill -0 "$p2" 2>/dev/null; then
                    if wait "$p2"; then
                        has_internet=0
                        break
                    fi
                fi
                if ! kill -0 "$p1" 2>/dev/null && ! kill -0 "$p2" 2>/dev/null; then
                    break
                fi
                sleep 0.1
            done
            kill "$p1" "$p2" 2>/dev/null || true
            wait "$p1" "$p2" 2>/dev/null || true
            
            if (( has_internet == 0 )); then
                return 0
            fi
        else
            # Resolver answers every name (DNS sinkhole e.g. Pi-hole, or portal hijack).
            # Verify via TLS against the real archlinux.org: a hijacked resolver cannot
            # forge a valid certificate, while a sinkhole still routes real domains.
            local tls_code
            tls_code=$(curl -s --connect-timeout 5 --max-time 5 -o /dev/null -w "%{http_code}" https://archlinux.org/ 2>/dev/null || echo "000")
            if [[ "$tls_code" == "200" ]]; then
                return 0
            fi
        fi
    fi

    # 4. Active NM check (Last resort, forces DBus block and active probing)
    if [[ "$(timeout 5 nmcli -w 4 networking connectivity check 2>/dev/null || echo "unknown")" == "full" ]]; then
        return 0
    fi

    return 1
}

check_eth_carrier() {
    local dev=$1
    ip link set dev "$dev" up 2>/dev/null || sudo -n ip link set dev "$dev" up 2>/dev/null || true
    # Allow PHY auto-negotiation to settle before trusting carrier state
    for ((i = 0; i < 10; i++)); do
        # LOWER_UP validates physical electrical carrier presence on the interface
        if ip link show dev "$dev" 2>/dev/null | grep -q "LOWER_UP"; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

get_eth_devs() {
    nmcli -g DEVICE,TYPE dev 2>/dev/null | awk -F: '$2=="ethernet"{print $1}'
}

get_active_eth_dev() {
    local devs
    mapfile -t devs < <(get_eth_devs)
    if [[ ${#devs[@]} -eq 0 ]]; then
        return 1
    fi
    for d in "${devs[@]}"; do
        if check_eth_carrier "$d"; then
            echo "$d"
            return 0
        fi
    done
    echo "${devs[0]}"
    return 0
}

get_wifi_devs() {
    nmcli -g DEVICE,TYPE dev 2>/dev/null | awk -F: '$2=="wifi"{print $1}'
}

get_active_wifi_dev() {
    local devs
    mapfile -t devs < <(get_wifi_devs)
    if [[ ${#devs[@]} -gt 0 ]]; then
        echo "${devs[0]}"
        return 0
    fi
    return 1
}

ensure_wifi_radio() {
    if ! nmcli -g WIFI radio 2>/dev/null | grep -q "enabled"; then
        log_warn "Wi-Fi radio is disabled. Attempting to power on..."
        
        if command -v rfkill >/dev/null 2>&1; then
            rfkill unblock wifi wlan 2>/dev/null || sudo -n rfkill unblock wifi wlan 2>/dev/null || sudo rfkill unblock wifi wlan 2>/dev/null || true
        fi
        
        nmcli radio wifi on 2>/dev/null || sudo -n nmcli radio wifi on 2>/dev/null || sudo nmcli radio wifi on 2>/dev/null || true
        sleep 2

        if ! nmcli -g WIFI radio 2>/dev/null | grep -q "enabled"; then
            log_error "Failed to enable Wi-Fi radio. A physical hardware switch or BIOS setting may be toggled."
            fail_and_exit
        fi
    fi
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

# ==============================================================================
# Main Execution
# ==============================================================================

log_info "Initializing Network Orchestrator Phase 0..."
wait_for_nm

log_info "Verifying current routing table and internet access..."
if check_connectivity; then
    log_success "System is already connected to the internet."
    exit 0
fi

log_info "Probing network hardware and auto-connecting interfaces..."

# 1. Ensure Wi-Fi radio is enabled so saved auto-connect profiles can associate
wifi_dev_auto=$(get_active_wifi_dev || true)
if [[ -n "$wifi_dev_auto" ]]; then
    if command -v rfkill >/dev/null 2>&1; then
        rfkill unblock wifi wlan 2>/dev/null || sudo -n rfkill unblock wifi wlan 2>/dev/null || true
    fi
    nmcli radio wifi on 2>/dev/null || true
fi

# 2. Check for physical Ethernet carrier across all available adapters
mapfile -t all_eth_devs < <(get_eth_devs)
for eth_candidate in "${all_eth_devs[@]}"; do
    if [[ -n "$eth_candidate" ]]; then
        nmcli device set "$eth_candidate" managed yes 2>/dev/null || sudo -n nmcli device set "$eth_candidate" managed yes 2>/dev/null || true
        if check_eth_carrier "$eth_candidate"; then
            log_info "Active Ethernet carrier detected on $eth_candidate. Activating connection..."
            timeout 10 nmcli device connect "$eth_candidate" >/dev/null 2>&1 || timeout 10 sudo -n nmcli device connect "$eth_candidate" >/dev/null 2>&1 || true
        fi
    fi
done

# 3. Allow network routes and DHCP leases up to 8s to settle
log_info "Waiting for network routes to settle..."
deadline=$((SECONDS + 8))
while (( SECONDS < deadline )); do
    if check_connectivity; then
        log_success "Internet connection established."
        exit 0
    fi
    sleep 0.5
done

# 4. Flush stale DNS caches and perform one final auto-check
flush_dns_caches
if check_connectivity; then
    log_success "Internet connection established."
    exit 0
fi

log_warn "No active internet connection established automatically."

# ==============================================================================
# Non-Interactive (Headless) Fallback
# ==============================================================================
if [[ ! -t 0 ]]; then
    log_error "Non-interactive environment detected. Interactive network configuration unavailable."
    fail_and_exit
fi

# ==============================================================================
# Interactive Menu (TTY Mode Only - When Genuinely Disconnected)
# ==============================================================================
while true; do
PS3=$(echo -e "\n${C_CYAN}Select connection interface or option: ${C_RESET}")

select conn_method in "LAN (Wired)" "Wi-Fi" "Re-check Connection" "Abort"; do
    case $conn_method in
        "LAN (Wired)")
            mapfile -t lan_cands < <(get_eth_devs)
            target_eth=""
            for cand in "${lan_cands[@]}"; do
                if check_eth_carrier "$cand"; then
                    target_eth="$cand"
                    break
                fi
            done

            if [[ -z "$target_eth" ]]; then
                if [[ ${#lan_cands[@]} -eq 0 ]]; then
                    log_error "No physical Ethernet interface detected on this system."
                else
                    echo -e "${C_YELLOW}[+] Please ensure your Ethernet cable is physically plugged in.${C_RESET}"
                    read -r -p "Press Enter to verify carrier state..." _ || true
                    for cand in "${lan_cands[@]}"; do
                        if check_eth_carrier "$cand"; then
                            target_eth="$cand"
                            break
                        fi
                    done
                fi
            fi

            if [[ -z "$target_eth" ]]; then
                log_error "No carrier detected on any wired interface. Check physical cable."
                break
            fi

            log_info "Carrier active on $target_eth. Requesting DHCP lease..."
            nmcli device set "$target_eth" managed yes 2>/dev/null || sudo -n nmcli device set "$target_eth" managed yes 2>/dev/null || true

            if timeout 15 nmcli device connect "$target_eth" >/dev/null 2>&1 || timeout 15 sudo -n nmcli device connect "$target_eth" >/dev/null 2>&1 || timeout 15 sudo nmcli device connect "$target_eth" >/dev/null 2>&1; then
                flush_dns_caches
                if check_connectivity; then
                    log_success "LAN connected and internet routed ($target_eth)."
                    exit 0
                else
                    log_error "Carrier on $target_eth, but no internet access (Check DNS/Gateway)."
                fi
            else
                log_error "Failed to bring up $target_eth. DHCP timeout or Layer 2 failure."
            fi
            break
            ;;

        "Wi-Fi")
            mapfile -t wifi_devs < <(get_wifi_devs)
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

            log_info "Using Wi-Fi interface: $wifi_dev"
            ensure_wifi_radio

            nmcli device set "$wifi_dev" managed yes 2>/dev/null || sudo -n nmcli device set "$wifi_dev" managed yes 2>/dev/null || true

            while true; do
            log_info "Triggering active 802.11 rescan on $wifi_dev..."
            timeout 10 nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || timeout 10 sudo -n nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || true
            sleep 3

            # Mapfile safely handles SSIDs with spaces. sort -u drops duplicated BSSIDs.
            mapfile -t networks < <(nmcli -g SSID dev wifi list ifname "$wifi_dev" 2>/dev/null | grep -v '^$' | sort -u || true)

            if [[ ${#networks[@]} -eq 0 ]]; then
                log_warn "No networks found. Retrying scan once..."
                timeout 10 nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || timeout 10 sudo -n nmcli dev wifi rescan ifname "$wifi_dev" >/dev/null 2>&1 || true
                sleep 3
                mapfile -t networks < <(nmcli -g SSID dev wifi list ifname "$wifi_dev" 2>/dev/null | grep -v '^$' | sort -u || true)

                if [[ ${#networks[@]} -eq 0 ]]; then
                    log_error "No broadcasting 802.11 networks found in range."
                    break
                fi
            fi

            log_info "Discovered ${#networks[@]} available networks."
            PS3=$(echo -e "\n${C_CYAN}Select target SSID or option: ${C_RESET}")

            select ssid in "${networks[@]}" "[Rescan Networks]" "[Hidden SSID - Enter Manually]" "[Back to Main Menu]"; do
                if [[ -z "$ssid" ]]; then
                    log_warn "Invalid selection. Enter a number from the list."
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
                    read -r -s -p "Enter WPA/WEP password for '$ssid' (leave empty if open): " pass || true
                    echo -e "\n"

                    if ! valid_passphrase "$pass"; then
                        log_warn "WPA/WPA2/WPA3 passphrases must be 8-63 ASCII characters (or 64 hex digits)."
                        continue
                    fi

                    log_info "Negotiating handshake with '$ssid'..."

                    nm_cmd=(nmcli -w 20 dev wifi connect "$ssid" ifname "$wifi_dev")
                    [[ -n "$pass" ]] && nm_cmd+=(password "$pass")
                    (( hidden == 1 )) && nm_cmd+=(hidden yes)

                    if timeout 30 "${nm_cmd[@]}" >/dev/null 2>&1 || timeout 30 sudo -n "${nm_cmd[@]}" >/dev/null 2>&1 || timeout 30 sudo "${nm_cmd[@]}" >/dev/null 2>&1; then
                        log_success "Layer 2 authentication successful."

                        active_con=$(nmcli -g GENERAL.CONNECTION dev show "$wifi_dev" 2>/dev/null | awk 'NR==1' || true)

                        if [[ -n "$active_con" ]]; then
                            nmcli con modify "$active_con" \
                                connection.autoconnect yes \
                                connection.autoconnect-priority 99 >/dev/null 2>&1 || \
                            sudo -n nmcli con modify "$active_con" \
                                connection.autoconnect yes \
                                connection.autoconnect-priority 99 >/dev/null 2>&1 || true
                            log_info "Profile '$active_con' hardened for future high-priority autoconnect."
                        fi

                        flush_dns_caches

                        if check_connectivity; then
                            log_success "Internet connectivity validated. Ready for pipeline execution."
                            exit 0
                        else
                            log_error "Connected to '$ssid', but ICMP/DNS routing failed (Possible captive portal)."
                        fi
                    else
                        log_error "Handshake failed. Invalid password, out of range, or AP rejected client."
                    fi
                    break
            done
            done
            break
            ;;

            "Re-check Connection")
                log_info "Verifying current routing table and internet access..."
                if check_connectivity; then
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
            log_warn "Invalid input. Select an option from the menu."
            ;;
    esac
    done
done
