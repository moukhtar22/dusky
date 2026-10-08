#!/usr/bin/env bash
# Live RAM/ZRAM HUD for the graphical user session. Bash 5.3+, Linux 7.3+.

# Percentages are integer values rounded down. Critical RAM bypasses recovery
# grace; an explicit right-click snooze suppresses all alerts.
THRESHOLD_RAM_CRITICAL=95
THRESHOLD_RAM_HIGH=90
THRESHOLD_ZRAM_HIGH=90
THRESHOLD_RAM_RECOVERY=80
POLL_INTERVAL=0.5
COOLDOWN_SECS=120

hud_active=false
grace_expire_time=0
snooze_expire_time=0

log() {
    printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "$*" >&2
}

read_time() {
    local uptime
    read -r uptime _ < /proc/uptime || return 1
    now=${uptime%%.*}
}

handle_snooze() {
    read_time || return
    snooze_expire_time=$((now + COOLDOWN_SECS))
    hud_active=false
    log "USER SNOOZED HUD FOR ${COOLDOWN_SECS}s"
}

read_memory() {
    local key val mem_total=0 available=-1 device type size used
    local zram_total=0 zram_used=0

    while read -r key val _; do
        case $key in
            MemTotal:) mem_total=$val ;;
            MemAvailable:) available=$val ;;
        esac
        (( mem_total > 0 && available >= 0 )) && break
    done < /proc/meminfo
    # Missing statistics must not be interpreted as a recovered system.
    (( mem_total > 0 && available >= 0 && available <= mem_total )) || return 1
    RamUsedPct=$(((mem_total - available) * 100 / mem_total))

    # Count swap slots, rather than compressed bytes or non-swap ZRAM data.
    # Re-reading the active list also handles swap devices added/removed at runtime.
    while read -r device type size used _; do
        [[ $type == partition && ${device##*/} =~ ^zram[0-9]+$ ]] || continue
        zram_total=$((zram_total + size))
        zram_used=$((zram_used + used))
    done < /proc/swaps || return 1
    ZramUsedPct=0
    (( zram_total > 0 )) && ZramUsedPct=$((zram_used * 100 / zram_total))
    return 0
}

update_hud() {
    if (( now < snooze_expire_time )); then
        return
    fi

    if [[ $hud_active == true ]] && (( RamUsedPct <= THRESHOLD_RAM_RECOVERY )); then
        # Replace the red HUD with a short-lived frame; low urgency avoids the
        # global Mako critical-timeout override. The recovery notice is separate.
        notify-send -a dusky-high-ram-alert \
            -h string:x-canonical-private-synchronous:dusky-ram-hud \
            -u low -t 1 ' ' ' '
        notify-send -a dusky-ram-recovered -u normal -t 3000 \
            'SYSTEM RECOVERED' "RAM: ${RamUsedPct}% | Memory Stabilized"
        hud_active=false
        grace_expire_time=$((now + COOLDOWN_SECS))
        log "SYSTEM RECOVERED: RAM=${RamUsedPct}% (at or below ${THRESHOLD_RAM_RECOVERY}%)"
        return
    fi

    if [[ $hud_active == false ]]; then
        (( RamUsedPct >= THRESHOLD_RAM_CRITICAL ||
            (RamUsedPct >= THRESHOLD_RAM_HIGH && ZramUsedPct >= THRESHOLD_ZRAM_HIGH) )) || return
        (( now >= grace_expire_time || RamUsedPct >= THRESHOLD_RAM_CRITICAL )) || return
    fi

    # Keep the HUD alive through the hysteresis band. Retry failed delivery on
    # the next scan rather than recording an activation that never appeared.
    if notify-send -a dusky-high-ram-alert \
        -h string:x-canonical-private-synchronous:dusky-ram-hud \
        -u critical -t 1500 'CRITICAL MEMORY LOW' \
        "RAM: ${RamUsedPct}% | ZRAM: ${ZramUsedPct}%"; then
        # A snooze may arrive while notify-send is running.
        if (( now >= snooze_expire_time )) && [[ $hud_active == false ]]; then
            hud_active=true
            log "HUD ACTIVATED: RAM=${RamUsedPct}%, ZRAM=${ZramUsedPct}%"
        fi
    fi
}

main() {
    # Optional Bash loadable builtin: no child process on idle polling scans.
    if [[ -f /usr/lib/bash/sleep ]]; then
        enable -f /usr/lib/bash/sleep sleep 2>/dev/null || :
    fi
    trap handle_snooze USR1
    log "Dusky RAM HUD started: CriticalRAM=${THRESHOLD_RAM_CRITICAL}%, HighRAM=${THRESHOLD_RAM_HIGH}%, HighZRAM=${THRESHOLD_ZRAM_HIGH}%, RecoveryRAM=${THRESHOLD_RAM_RECOVERY}%, PollInterval=${POLL_INTERVAL}s"
    while true; do
        if ! read_memory || ! read_time; then
            log 'Cannot read valid memory/time statistics; exiting for service restart.'
            return 1
        fi
        update_hud
        sleep "$POLL_INTERVAL" || return 1
    done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main
fi
