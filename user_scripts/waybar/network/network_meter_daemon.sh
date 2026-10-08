#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC2154
export LC_ALL=C
for _b in sleep stat rm; do
    if [[ -f "/usr/lib/bash/$_b" ]]; then
        enable -f "/usr/lib/bash/$_b" "$_b" 2>/dev/null || true
    fi
done
unset _b
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/${UID:-$(id -u)}}"
STATE_DIR="$RUNTIME/waybar-net"
STATE_FILE="$STATE_DIR/state"
STATE_EXT_FILE="$STATE_DIR/state_ext"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"
SESSION_FILE="$STATE_DIR/conn_session"
: "${STATE_DIR:?empty}"
command mkdir -p "$STATE_DIR"
printf '%s\n' "$$" > "$PID_FILE"
cleanup() {
    local cur=""
    read -r cur < "$PID_FILE" 2>/dev/null || true
    if [[ "$cur" == "$$" ]]; then
        rm -f "$PID_FILE" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap ':' USR1
find_active_iface() {
    local -n _iface_out=$1
    local iface dest
    while read -r iface dest _; do
        if [[ "$dest" == "00000000" ]]; then
            if [[ -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
                _iface_out="$iface"
                return 0
            fi
        fi
    done < /proc/net/route
    local path if_name state
    for path in /sys/class/net/*; do
        if_name="${path##*/}"
        [[ "$if_name" == "lo" ]] && continue
        [[ -e "$path/device" ]] || continue
        [[ -r "$path/operstate" ]] || continue
        [[ -r "$path/statistics/rx_bytes" ]] || continue
        read -r state < "$path/operstate" 2>/dev/null || continue
        if [[ "$state" == "up" ]]; then
            _iface_out="$if_name"
            return 0
        fi
    done
    for path in /sys/class/net/*; do
        if_name="${path##*/}"
        [[ "$if_name" == "lo" ]] && continue
        [[ -e "$path/device" ]] || continue
        [[ -r "$path/operstate" ]] || continue
        [[ -r "$path/statistics/rx_bytes" ]] || continue
        read -r state < "$path/operstate" 2>/dev/null || continue
        if [[ "$state" == "unknown" ]]; then
            _iface_out="$if_name"
            return 0
        fi
    done
    _iface_out=""
    return 1
}
format_speed() {
    local -n _unit=$1 _tx=$2 _rx=$3 _class=$4
    local rx_d=$5 tx_d=$6
    local max=$(( rx_d > tx_d ? rx_d : tx_d ))
    if (( max >= 1038090240 )); then
        local tx_x10=$(( (tx_d * 10 + 536870912) / 1073741824 ))
        local rx_x10=$(( (rx_d * 10 + 536870912) / 1073741824 ))
        if (( tx_x10 < 100 )); then _tx="$((tx_x10 / 10)).$((tx_x10 % 10))"; else _tx="$(( (tx_d + 536870912) / 1073741824 ))"; fi
        if (( rx_x10 < 100 )); then _rx="$((rx_x10 / 10)).$((rx_x10 % 10))"; else _rx="$(( (rx_d + 536870912) / 1073741824 ))"; fi
        _unit="GB"
        _class="network-gb"
    elif (( max >= 1013760 )); then
        local tx_x10=$(( (tx_d * 10 + 524288) / 1048576 ))
        local rx_x10=$(( (rx_d * 10 + 524288) / 1048576 ))
        if (( tx_x10 < 100 )); then _tx="$((tx_x10 / 10)).$((tx_x10 % 10))"; else _tx="$(( (tx_d + 524288) / 1048576 ))"; fi
        if (( rx_x10 < 100 )); then _rx="$((rx_x10 / 10)).$((rx_x10 % 10))"; else _rx="$(( (rx_d + 524288) / 1048576 ))"; fi
        _unit="MB"
        _class="network-mb"
    else
        _tx=$(( (tx_d + 512) / 1024 ))
        _rx=$(( (rx_d + 512) / 1024 ))
        _unit="KB"
        _class="network-kb"
    fi
}
format_single_rate() {
    local -n _res=$1
    local rate=$2
    if (( rate >= 1038090240 )); then
        local x10=$(( (rate * 10 + 536870912) / 1073741824 ))
        if (( x10 < 100 )); then _res="$((x10 / 10)).$((x10 % 10))G"; else _res="$(( (rate + 536870912) / 1073741824 ))G"; fi
    elif (( rate >= 1013760 )); then
        local x10=$(( (rate * 10 + 524288) / 1048576 ))
        if (( x10 < 100 )); then _res="$((x10 / 10)).$((x10 % 10))M"; else _res="$(( (rate + 524288) / 1048576 ))M"; fi
    else
        local kb=$(( (rate + 512) / 1024 ))
        _res="${kb}K"
    fi
}
format_data_bytes() {
    local -n _res=$1
    local -n _unit_out=$2
    local bytes=$3
    local mb=$(( (bytes + 524288) / 1048576 ))
    if (( mb > 9999 )); then
        local gb_x10=$(( (bytes * 10 + 536870912) / 1073741824 ))
        if (( gb_x10 < 1000 )); then
            _res="$(( gb_x10 / 10 )).$(( gb_x10 % 10 ))"
            _unit_out="GB"
        elif (( gb_x10 < 10000 )); then
            _res="$(( (bytes + 536870912) / 1073741824 ))"
            _unit_out="GB"
        else
            local tb_x10=$(( (bytes * 10 + 549755813888) / 1099511627776 ))
            if (( tb_x10 < 100 )); then
                _res="$(( tb_x10 / 10 )).$(( tb_x10 % 10 ))"
            else
                _res="$(( (bytes + 549755813888) / 1099511627776 ))"
            fi
            _unit_out="TB"
        fi
    else
        _res="$mb"
        _unit_out="MB"
    fi
}
check_heartbeat() {
    local -n _hb_time=$1
    local now=$2
    local -A STAT
    if stat -A STAT "$HEARTBEAT_FILE" 2>/dev/null; then
        _hb_time="${STAT[mtime]}"
        return 0
    fi
    local mtime
    if mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null); then
        [[ "$mtime" =~ ^[0-9]+$ ]] && _hb_time="$mtime" || _hb_time="$now"
    else
        _hb_time="$now"
    fi
}
rx_prev=0
tx_prev=0
prev_sample_us=0
initialized=0
iface=""
current_iface=""
iface_counter=0
hb_counter=2
hb_time=0
session_rx=0
session_tx=0
conn_rx_start=-1
conn_tx_start=-1
last_carrier_up=-1
if [[ -r "$SESSION_FILE" ]]; then
    read -r _s_if _s_cr _s_rx _s_tx < "$SESSION_FILE" 2>/dev/null || true
    if [[ -n "${_s_if:-}" && "${_s_rx:-}" =~ ^[0-9]+$ && "${_s_tx:-}" =~ ^[0-9]+$ ]]; then
        conn_rx_start="$_s_rx"
        conn_tx_start="$_s_tx"
        last_carrier_up="${_s_cr:--1}"
        iface="$_s_if"
    fi
fi
while :; do
    printf -v now '%(%s)T' -1
    if (( ++hb_counter >= 3 )); then
        hb_counter=0
        check_heartbeat hb_time "$now"
    fi
    if (( now - hb_time > 10 )); then
        initialized=0
        sleep 600 &
        _sleep_pid=$!
        wait "$_sleep_pid" || true
        kill "$_sleep_pid" 2>/dev/null || true
        wait "$_sleep_pid" 2>/dev/null || true
        hb_counter=10
        continue
    fi
    if (( ++iface_counter >= 5 )) || [[ -z "$iface" ]] || [[ ! -r "/sys/class/net/$iface/statistics/rx_bytes" ]]; then
        iface_counter=0
        find_active_iface current_iface || current_iface=""
    else
        current_iface="$iface"
    fi
    if [[ -z "$current_iface" ]]; then
        printf '%s\n' "- - - network-disconnected" > "$STATE_FILE"
        printf '%s %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s\n' \
            "- -" "- -" "- -" "0" "0" "0" "0" "0" "0" \
            "network-disconnected" "none" "MB" "MB" "MB" "MB" "MB" "GB" > "$STATE_EXT_FILE"
        rx_prev=0; tx_prev=0; prev_sample_us=0; initialized=0; iface=""
        session_rx=0; session_tx=0; conn_rx_start=-1; conn_tx_start=-1; last_carrier_up=-1
        rm -f "$SESSION_FILE" 2>/dev/null || true
        sleep 3 || true
        continue
    fi
    sample_us="${EPOCHREALTIME/./}"
    carrier_up=-1
    if [[ -r "/sys/class/net/$current_iface/carrier_up_count" ]]; then
        read -r carrier_up < "/sys/class/net/$current_iface/carrier_up_count" 2>/dev/null || carrier_up=-1
    fi
    if [[ "$current_iface" != "$iface" ]] || (( last_carrier_up != -1 && carrier_up != -1 && carrier_up != last_carrier_up )); then
        iface="$current_iface"
        initialized=0
        conn_rx_start=-1
        conn_tx_start=-1
        session_rx=0
        session_tx=0
        last_carrier_up=$carrier_up
        rm -f "$SESSION_FILE" 2>/dev/null || true
    fi
    if (( last_carrier_up == -1 && carrier_up != -1 )); then
        last_carrier_up=$carrier_up
    fi
    read -r rx_now < "/sys/class/net/$iface/statistics/rx_bytes" 2>/dev/null || rx_now=0
    read -r tx_now < "/sys/class/net/$iface/statistics/tx_bytes" 2>/dev/null || tx_now=0
    [[ "$rx_now" =~ ^[0-9]+$ ]] || rx_now=0
    [[ "$tx_now" =~ ^[0-9]+$ ]] || tx_now=0
    if (( initialized == 0 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        prev_sample_us=$sample_us
        initialized=1
        sleep 1 || true
        continue
    fi
    dt_us=$(( sample_us - prev_sample_us ))
    if (( dt_us < 400000 || dt_us > 2500000 )); then
        rx_prev=$rx_now
        tx_prev=$tx_now
        prev_sample_us=$sample_us
        sleep 1 || true
        continue
    fi
    rx_delta=$(( rx_now - rx_prev ))
    tx_delta=$(( tx_now - tx_prev ))
    if (( rx_delta < 0 )); then rx_delta=$rx_now; fi
    if (( tx_delta < 0 )); then tx_delta=$tx_now; fi
    rx_prev=$rx_now
    tx_prev=$tx_now
    prev_sample_us=$sample_us
    rx_rate=$(( (rx_delta * 1000000 + dt_us / 2) / dt_us ))
    tx_rate=$(( (tx_delta * 1000000 + dt_us / 2) / dt_us ))
    total_rate=$(( rx_rate + tx_rate ))

    if (( conn_rx_start == -1 )); then
        conn_rx_start=$rx_now
        conn_tx_start=$tx_now
        printf '%s %s %s %s\n' "$iface" "$carrier_up" "$conn_rx_start" "$conn_tx_start" > "$SESSION_FILE" 2>/dev/null || true
    fi
    session_rx=$(( rx_now - conn_rx_start ))
    session_tx=$(( tx_now - conn_tx_start ))
    if (( session_rx < 0 )); then session_rx=0; conn_rx_start=$rx_now; fi
    if (( session_tx < 0 )); then session_tx=0; conn_tx_start=$tx_now; fi
    session_total=$(( session_rx + session_tx ))
    boot_rx=$rx_now
    boot_tx=$tx_now
    boot_total=$(( rx_now + tx_now ))

    format_speed unit tx_fmt rx_fmt class "$rx_rate" "$tx_rate"
    format_single_rate rx_speed_fmt "$rx_rate"
    format_single_rate tx_speed_fmt "$tx_rate"
    format_single_rate total_speed_fmt "$total_rate"
    format_data_bytes session_rx_fmt s_rx_u "$session_rx"
    format_data_bytes session_tx_fmt s_tx_u "$session_tx"
    format_data_bytes session_total_fmt s_tot_u "$session_total"
    format_data_bytes boot_rx_fmt b_rx_u "$boot_rx"
    format_data_bytes boot_tx_fmt b_tx_u "$boot_tx"
    format_data_bytes boot_total_fmt b_tot_u "$boot_total"

    # shellcheck disable=SC2154
    printf '%s %s %s %s\n' "$unit" "$tx_fmt" "$rx_fmt" "$class" > "$STATE_FILE"
    printf '%s %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s\n' \
        "$rx_speed_fmt" "$tx_speed_fmt" "$total_speed_fmt" \
        "$session_rx_fmt" "$session_tx_fmt" "$session_total_fmt" \
        "$boot_rx_fmt" "$boot_tx_fmt" "$boot_total_fmt" \
        "$class" "$iface" \
        "$s_rx_u" "$s_tx_u" "$s_tot_u" \
        "$b_rx_u" "$b_tx_u" "$b_tot_u" > "$STATE_DIR/state_ext.tmp" 2>/dev/null && \
        mv -f "$STATE_DIR/state_ext.tmp" "$STATE_EXT_FILE" 2>/dev/null || true

    end_time="${EPOCHREALTIME/./}"
    sleep_us=$(( 1000000 - (end_time - sample_us) ))
    if (( sleep_us <= 0 )); then
        :
    elif (( sleep_us >= 1000000 )); then
        sleep 1 || true
    else
        printf -v sleep_sec "0.%06d" "$sleep_us"
        sleep "$sleep_sec" || true
    fi
done
