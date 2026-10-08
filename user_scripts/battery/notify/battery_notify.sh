#!/usr/bin/env bash
# Event-driven battery notifications for the Hyprland user session.
# Requires Bash 5.3+, systemd 262+, and UPower (including DisplayDevice).
set -uo pipefail
# UPower's human-readable field names and decimal separator must be stable.
export LC_ALL=C

##########################
# CONFIGURATION — EDIT ME
##########################
readonly BATTERY_DEVICE="${BATTERY_DEVICE:-}"
readonly BATTERY_FULL_THRESHOLD="${BATTERY_FULL_THRESHOLD:-100}"
readonly BATTERY_LOW_THRESHOLD="${BATTERY_LOW_THRESHOLD:-20}"
readonly BATTERY_CRITICAL_THRESHOLD="${BATTERY_CRITICAL_THRESHOLD:-10}"
readonly BATTERY_UNPLUG_THRESHOLD="${BATTERY_UNPLUG_THRESHOLD:-100}"

readonly REPEAT_FULL_MIN="${REPEAT_FULL_MIN:-999}"
readonly REPEAT_LOW_MIN="${REPEAT_LOW_MIN:-3}"
readonly REPEAT_CRITICAL_MIN="${REPEAT_CRITICAL_MIN:-1}"
readonly SUSPEND_GRACE_SEC="${SUSPEND_GRACE_SEC:-60}"
readonly SAFETY_POLL_INTERVAL="${SAFETY_POLL_INTERVAL:-60}"
readonly DO_SUSPEND="${DO_SUSPEND:-true}"

readonly MSG_CRITICAL="${MSG_CRITICAL:-Suspending system!}"
readonly SOUND_LOW="${SOUND_LOW:-/usr/share/sounds/freedesktop/stereo/complete.oga}"
readonly SOUND_CRITICAL="${SOUND_CRITICAL:-/usr/share/sounds/freedesktop/stereo/suspend-error.oga}"
readonly SOUND_PLUG="${SOUND_PLUG:-/usr/share/sounds/freedesktop/stereo/device-added.oga}"
readonly SOUND_UNPLUG="${SOUND_UNPLUG:-/usr/share/sounds/freedesktop/stereo/device-removed.oga}"

readonly MAX_RETRIES=5
readonly BATTERY_PATH="${BATTERY_DEVICE:-/org/freedesktop/UPower/devices/DisplayDevice}"

MON_FD=-1
MONITOR_PID=""
LAST_ON_BATTERY=""
LAST_FULL_NOTIFY=-1
LAST_LOW_NOTIFY=-1
LAST_CRITICAL_NOTIFY=-1
SUSPEND_DEADLINE=-1
READ_FAILED=false
HAS_NOTIFY=false
SOUND_PLAYER=""

log() { printf '[%(%Y-%m-%d %H:%M:%S)T] [battery_notify] %s\n' -1 "$*" >&2; }

startup_checks() {
    local cmd var val max errors=0
    for cmd in upower busctl; do
        command -v "$cmd" >/dev/null || { log "Missing command: $cmd"; ((errors++)); }
    done
    if [[ $DO_SUSPEND == true ]]; then
        command -v systemctl >/dev/null || { log 'Missing command: systemctl'; ((errors++)); }
    elif [[ $DO_SUSPEND != false ]]; then
        log 'DO_SUSPEND must be true or false'; ((errors++))
    fi
    command -v notify-send >/dev/null && HAS_NOTIFY=true
    if command -v pw-play >/dev/null; then SOUND_PLAYER=pw-play
    elif command -v paplay >/dev/null; then SOUND_PLAYER=paplay
    fi
    for var in BATTERY_FULL_THRESHOLD BATTERY_LOW_THRESHOLD BATTERY_CRITICAL_THRESHOLD \
               BATTERY_UNPLUG_THRESHOLD REPEAT_FULL_MIN REPEAT_LOW_MIN REPEAT_CRITICAL_MIN \
               SUSPEND_GRACE_SEC SAFETY_POLL_INTERVAL; do
        val=${!var}
        # Bound length before arithmetic to avoid overflow; leading zeros are decimal.
        if [[ ! $val =~ ^[0-9]{1,4}$ ]]; then
            log "Invalid $var='$val': expected up to four decimal digits"
            ((errors++)); continue
        fi
        case $var in
            BATTERY_*_THRESHOLD) max=100 ;;
            REPEAT_*_MIN) max=1440 ;;
            SUSPEND_GRACE_SEC) max=3600 ;;
            SAFETY_POLL_INTERVAL) max=600 ;;
        esac
        if (( 10#$val > max )); then
            log "$var must be 0..$max"; ((errors++))
        fi
    done
    (( errors == 0 )) || return 1
    if (( 10#$BATTERY_CRITICAL_THRESHOLD >= 10#$BATTERY_LOW_THRESHOLD ||
          10#$BATTERY_LOW_THRESHOLD >= 10#$BATTERY_FULL_THRESHOLD )); then
        log 'Thresholds must satisfy CRITICAL < LOW < FULL'; return 1
    fi
    if (( 10#$SAFETY_POLL_INTERVAL < 5 || 10#$REPEAT_FULL_MIN < 1 ||
          10#$REPEAT_LOW_MIN < 1 || 10#$REPEAT_CRITICAL_MIN < 1 )); then
        log 'Poll interval must be at least 5s; repeat intervals at least 1 minute'; return 1
    fi
}

get_icon() {
    local percentage=$1 state=$2 icon
    if (( percentage <= 10 )); then icon=battery-empty
    elif (( percentage <= 20 )); then icon=battery-caution
    elif (( percentage <= 40 )); then icon=battery-low
    elif (( percentage <= 80 )); then icon=battery-good
    else icon=battery-full
    fi
    [[ $state == Charging ]] && icon+=-charging
    printf '%s' "$icon"
}

fn_notify() {
    local urgency=$1 title=$2 body=$3 icon=$4 sound=$5 err timeout=5000
    [[ $urgency == critical ]] && timeout=0
    if [[ $HAS_NOTIFY == true ]]; then
        if ! err=$(notify-send --app-name='Battery Monitor' --urgency="$urgency" \
            --hint=string:x-canonical-private-synchronous:battery-status \
            --expire-time="$timeout" --icon="$icon" -- "$title" "$body" 2>&1); then
            log "notify-send failed: $err"
        fi
    else
        log "Notification: [$urgency] $title - $body"
    fi
    if [[ -n $SOUND_PLAYER && $sound != disabled && -r $sound ]]; then
        "$SOUND_PLAYER" -- "$sound" >/dev/null 2>&1 &
    fi
}

# Return 2 for a confirmed absent battery, 1 for unavailable/invalid data.
# DisplayDevice is UPower's documented composite battery/UPS; do not reaggregate it.
read_battery() {
    local info key value state="" percentage="" present="" supply="" kind="" power
    info=$(upower --show-info "$BATTERY_PATH" 2>/dev/null) || return 1
    while IFS= read -r value; do
        if [[ $value =~ ^[[:space:]]*(battery|ups)[[:space:]]*$ ]]; then
            kind=${BASH_REMATCH[1]}
        elif [[ $value =~ ^[[:space:]]*([^:]+):[[:space:]]*(.*[^[:space:]])[[:space:]]*$ ]]; then
            key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
            case $key in
                state) state=$value ;;
                percentage) percentage=$value ;;
                present) present=$value ;;
                'power supply') supply=$value ;;
            esac
        fi
    done <<< "$info"
    [[ $present == no ]] && return 2
    [[ $present == yes && $supply == yes && -n $kind ]] || return 1
    [[ $percentage =~ ^([0-9]{1,3})(\.[0-9]+)?%$ ]] || return 1
    percentage=$((10#${BASH_REMATCH[1]}))
    (( percentage <= 100 )) || return 1
    case $state in
        discharging|pending-discharge) state=Discharging ;;
        charging|pending-charge) state=Charging ;;
        fully-charged) state=Full ;;
        empty) state=Empty ;;
        *) return 1 ;;
    esac
    power=$(busctl --system get-property org.freedesktop.UPower /org/freedesktop/UPower \
        org.freedesktop.UPower OnBattery 2>/dev/null) || return 1
    case $power in
        'b true') power=true ;;
        'b false') power=false ;;
        *) return 1 ;;
    esac
    printf '%s;%s;%s\n' "$state" "$percentage" "$power"
}

is_critical() {
    [[ $3 == true && ( $1 == Discharging || $1 == Empty ) ]] &&
        (( $2 <= 10#$BATTERY_CRITICAL_THRESHOLD ))
}

do_suspend() {
    # Preserve the intended critical-battery inhibitor override, without prompting.
    # systemctl returns when the request is enqueued, not after resume.
    systemctl --no-ask-password --check-inhibitors=no suspend
}

process_battery_event() {
    local state=$1 percentage=$2 on_battery=$3 now=$4 message reading state2 perc2 power2
    if [[ -n $LAST_ON_BATTERY && $on_battery != "$LAST_ON_BATTERY" ]]; then
        if [[ $on_battery == true ]]; then
            if (( percentage <= 10#$BATTERY_UNPLUG_THRESHOLD )); then
                fn_notify normal 'Power Disconnected' "$percentage% — On Battery" battery-ac-adapter "$SOUND_UNPLUG"
            fi
        else
            fn_notify normal 'Power Connected' "$percentage% — External Power" "$(get_icon "$percentage" "$state")" "$SOUND_PLUG"
        fi
    fi
    if [[ $on_battery == false ]] && { [[ $state == Full ]] || (( percentage >= 10#$BATTERY_FULL_THRESHOLD )); }; then
        if (( LAST_FULL_NOTIFY < 0 || now - LAST_FULL_NOTIFY >= 10#$REPEAT_FULL_MIN * 60 )); then
            fn_notify normal 'Battery Charged' "$percentage% — Charged" battery-full-charged "$SOUND_PLUG"
            LAST_FULL_NOTIFY=$now
        fi
    else
        LAST_FULL_NOTIFY=-1
    fi
    if [[ $on_battery == true && ( $state == Discharging || $state == Empty ) ]] &&
        (( percentage <= 10#$BATTERY_LOW_THRESHOLD && percentage > 10#$BATTERY_CRITICAL_THRESHOLD )); then
        if (( LAST_LOW_NOTIFY < 0 || now - LAST_LOW_NOTIFY >= 10#$REPEAT_LOW_MIN * 60 )); then
            fn_notify normal 'Battery Low' "$percentage% — Low Battery" battery-caution "$SOUND_LOW"
            LAST_LOW_NOTIFY=$now
        fi
    else
        LAST_LOW_NOTIFY=-1
    fi
    if is_critical "$state" "$percentage" "$on_battery"; then
        if [[ $DO_SUSPEND == true ]]; then
            (( SUSPEND_DEADLINE < 0 )) && SUSPEND_DEADLINE=$((now + 10#$SUSPEND_GRACE_SEC))
            message="$percentage% — $MSG_CRITICAL (in $(( SUSPEND_DEADLINE > now ? SUSPEND_DEADLINE - now : 0 ))s)"
        else
            message="$percentage% — Auto-suspend disabled"
        fi
        if (( LAST_CRITICAL_NOTIFY < 0 || now - LAST_CRITICAL_NOTIFY >= 10#$REPEAT_CRITICAL_MIN * 60 )); then
            fn_notify critical 'Battery Critical' "$message" battery-empty "$SOUND_CRITICAL"
            LAST_CRITICAL_NOTIFY=$now
        fi
        if [[ $DO_SUSPEND == true ]] && (( now >= SUSPEND_DEADLINE )); then
            # Require a fresh, confirmed critical reading immediately before suspend.
            if reading=$(read_battery); then
                IFS=';' read -r state2 perc2 power2 <<< "$reading"
                if is_critical "$state2" "$perc2" "$power2"; then
                    log "Critical $perc2% — requesting suspend"
                    if do_suspend; then log 'Suspend request accepted'; else log 'Suspend request failed'; fi
                else
                    log "Suspend cancelled: $state2 $perc2%, on-battery=$power2"
                    SUSPEND_DEADLINE=-1
                fi
            else
                log 'Suspend deferred: battery recheck unavailable'
            fi
            if (( SUSPEND_DEADLINE >= 0 )); then
                # CLOCK_MONOTONIC excludes sleep: this also grants time after resume.
                SUSPEND_DEADLINE=$((BASH_MONOSECONDS + (10#$SUSPEND_GRACE_SEC > 5 ? 10#$SUSPEND_GRACE_SEC : 5)))
            fi
        fi
    else
        SUSPEND_DEADLINE=-1
        LAST_CRITICAL_NOTIFY=-1
    fi
    LAST_ON_BATTERY=$on_battery
}

stop_monitor() {
    if (( MON_FD >= 0 )); then
        { exec {MON_FD}<&-; } 2>/dev/null
        MON_FD=-1
    fi
    if [[ -n $MONITOR_PID ]]; then
        kill "$MONITOR_PID" 2>/dev/null || true
        wait "$MONITOR_PID" 2>/dev/null || true
        MONITOR_PID=""
    fi
}

start_monitor() {
    local input_fd
    coproc UPMON { exec upower --monitor; }
    MONITOR_PID=$!
    if [[ -n ${UPMON[0]:-} ]]; then
        exec {MON_FD}<&"${UPMON[0]}"
        input_fd=${UPMON[1]}
        exec {input_fd}>&-
        log "Monitor started PID=$MONITOR_PID"
    else
        log 'UPower monitor failed to start'; return 1
    fi
}

sample_battery() {
    local reading state percentage power
    if reading=$(read_battery); then
        [[ $READ_FAILED == true ]] && log 'Battery readings recovered'
        READ_FAILED=false
        IFS=';' read -r state percentage power <<< "$reading"
        process_battery_event "$state" "$percentage" "$power" "$BASH_MONOSECONDS"
    else
        [[ $READ_FAILED == false ]] && log 'Battery reading unavailable; waiting for recovery'
        READ_FAILED=true
        # Avoid a busy loop if a critical deadline expires while UPower is unavailable.
        if (( SUSPEND_DEADLINE >= 0 && BASH_MONOSECONDS >= SUSPEND_DEADLINE )); then
            SUSPEND_DEADLINE=$((BASH_MONOSECONDS + 5))
        fi
    fi
}

main() {
    local reading state percentage power rc=1 retry next_poll wait_sec count spec last interval deadline
    startup_checks || { log 'Startup checks failed'; return 2; }
    trap stop_monitor EXIT
    trap 'exit 0' TERM INT HUP
    for ((retry=1; retry<=MAX_RETRIES; retry++)); do
        if reading=$(read_battery); then rc=0; break; else rc=$?; fi
        (( retry < MAX_RETRIES )) && sleep 2
    done
    if (( rc == 2 )); then log 'No battery present; exiting'; return 0; fi
    if (( rc != 0 )); then log 'Unable to read battery after retries'; return 1; fi
    IFS=';' read -r state percentage power <<< "$reading"
    log "Initial: $state $percentage%, on-battery=$power ($BATTERY_PATH)"
    start_monitor || return 1
    process_battery_event "$state" "$percentage" "$power" "$BASH_MONOSECONDS"
    next_poll=$((BASH_MONOSECONDS + 10#$SAFETY_POLL_INTERVAL))
    while true; do
        wait_sec=$((next_poll - BASH_MONOSECONDS))
        if (( SUSPEND_DEADLINE >= 0 && SUSPEND_DEADLINE - BASH_MONOSECONDS < wait_sec )); then
            wait_sec=$((SUSPEND_DEADLINE - BASH_MONOSECONDS))
        fi
        if [[ $READ_FAILED == false ]]; then
            for spec in LAST_FULL_NOTIFY:REPEAT_FULL_MIN LAST_LOW_NOTIFY:REPEAT_LOW_MIN LAST_CRITICAL_NOTIFY:REPEAT_CRITICAL_MIN; do
                last=${spec%:*} interval=${spec#*:}
                if (( ${!last} >= 0 )); then
                    deadline=$(( ${!last} + 10#${!interval} * 60 - BASH_MONOSECONDS ))
                    (( deadline < wait_sec )) && wait_sec=$deadline
                fi
            done
        fi
        (( wait_sec < 1 )) && wait_sec=1
        if IFS= read -r -t "$wait_sec" -u "$MON_FD"; then
            # Coalesce bursts and limit queries to five per second during a storm.
            sleep 0.2
            # Bound draining so a busy event stream cannot starve deadlines.
            for ((count=0; count<64; count++)); do
                IFS= read -r -t 0.01 -u "$MON_FD" || break
            done
        else
            rc=$?
            if (( rc <= 128 )); then
                log 'UPower monitor exited; restarting'
                stop_monitor
                sleep 1
                start_monitor || return 1
            fi
        fi
        sample_battery
        if (( BASH_MONOSECONDS >= next_poll )); then
            next_poll=$((BASH_MONOSECONDS + 10#$SAFETY_POLL_INTERVAL))
        fi
    done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
