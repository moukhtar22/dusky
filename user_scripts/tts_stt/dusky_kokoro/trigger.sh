#!/usr/bin/env bash
# =============================================================================
#  trigger.sh - Dusky Kokoro TTS desktop hook  (clipboard / selection -> daemon)
#  Arch Linux, Wayland (wl-clipboard), Bash 5.3+
#
#  Bind this script to a hotkey. It reads the clipboard (or the primary
#  selection with --primary), makes sure the daemon is reachable (systemd
#  socket activation, systemd-run, or a detached process - in that order) and
#  submits the text over the Unix domain socket. The daemon answers with a
#  structured acknowledgement, so every failure is reported precisely.
#
#  Style note: no "parameter-expansion-with-braces" is used on purpose; unset
#  environment defaults are resolved with printenv so the script stays safe
#  under "set -u" and embeddable in template literals.
# =============================================================================
set -Eeuo pipefail

# --- locations (override with environment variables) --------------------------
DUSKY_HOME=$(printenv DUSKY_HOME || true)
if [[ -z "$DUSKY_HOME" ]]; then
    install_path="${XDG_CONFIG_HOME:-$HOME/.config}/dusky-kokoro/install-path"
    if [[ -r "$install_path" ]]; then IFS= read -r DUSKY_HOME < "$install_path" || true; fi
    [[ -n "$DUSKY_HOME" ]] || DUSKY_HOME="$HOME/contained_apps/uv/dusky_kokoro"
fi
VENV_PY="$DUSKY_HOME/.venv/bin/python"
MAIN_PY="$DUSKY_HOME/dusky_main.py"

RUNTIME_DIR=$(printenv XDG_RUNTIME_DIR || true)
[[ -n "$RUNTIME_DIR" ]] || RUNTIME_DIR="/run/user/$(id -u)"

SOCKET_PATH=$(printenv DUSKY_SOCKET || true)
CONFIG_FILE="${DUSKY_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/dusky-kokoro/config.toml}"

STANDALONE_LOG="$RUNTIME_DIR/dusky-kokoro/daemon.log"
SOCKET_UNIT="dusky_kokoro.socket"
SERVICE_UNIT="dusky_kokoro.service"
ADHOC_UNIT="dusky_kokoro_adhoc.service"
START_TIMEOUT=45          # seconds to wait for a cold daemon (model download excluded)
REQUEST_TIMEOUT=120       # seconds to wait for the daemon's acknowledgement

# --- request state -------------------------------------------------------------
ACTION="speak"
SOURCE="clipboard"        # clipboard | primary | text | file | stdin
SOURCE_TEXT=""
SOURCE_FILE=""
MODE=""                   # "" (daemon default) | interrupt | enqueue
VOICE=""
SPEED=""
LANG_CODE=""
WAIT=0
NOTIFY=1
LEVEL="DEBUG"

# --- helpers -------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

die() {
    printf 'trigger.sh: %s\n' "$*" >&2
    exit 1
}

notify() {  # summary body urgency
    [[ "$NOTIFY" == 1 ]] || return 0
    have notify-send || return 0
    notify-send -a "Dusky Kokoro" -u "$3" -t 2500 \
        -h string:x-canonical-private-synchronous:dusky-kokoro "$1" "$2" >/dev/null 2>&1 || true
}

# control client: stdlib only, isolated interpreter, no site-packages => ~30 ms
ctl() { "$VENV_PY" -I -S "$MAIN_PY" "$@"; }
# full interpreter (numpy / onnxruntime available): diagnostics only
ctl_full() { "$VENV_PY" "$MAIN_PY" "$@"; }

kv_get() {  # kv_get "<kv output>" key
    printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n 1
}

systemd_user_ok() { have systemctl && systemctl --user show-environment >/dev/null 2>&1; }
socket_unit_present() { systemctl --user cat "$SOCKET_UNIT" >/dev/null 2>&1; }

require_install() {
    [[ -x "$VENV_PY" && -f "$MAIN_PY" ]] || die "Dusky Kokoro is not installed at $DUSKY_HOME (run kokoro_installer.sh)"
}

wait_ready() {
    local deadline=$(( SECONDS + START_TIMEOUT ))
    while (( SECONDS < deadline )); do
        if ctl ping --timeout 5 >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.2
    done
    notify "Kokoro TTS" "Daemon did not start within $START_TIMEOUT s (see trigger.sh --logs)" critical
    die "daemon did not become ready within $START_TIMEOUT s (trigger.sh --logs)"
}

ensure_daemon() {
    if ctl ping --timeout 3 >/dev/null 2>&1; then
        return 0
    fi
    if systemd_user_ok && socket_unit_present; then
        systemctl --user reset-failed "$SERVICE_UNIT" >/dev/null 2>&1 || true
        systemctl --user start "$SOCKET_UNIT" >/dev/null 2>&1 || true
        systemctl --user start "$SERVICE_UNIT" >/dev/null 2>&1 || true
    elif systemd_user_ok && have systemd-run; then
        # No units installed: run a transient, journald-logged service.
        systemctl --user reset-failed "$ADHOC_UNIT" >/dev/null 2>&1 || true
        local -a socket_args=()
        [[ -n "$SOCKET_PATH" ]] && socket_args=(--socket "$SOCKET_PATH")
        systemd-run --user --quiet --collect --unit "$ADHOC_UNIT" \
            --property=KillMode=mixed --property=TimeoutStopSec=10 \
            "$VENV_PY" "$MAIN_PY" --config "$CONFIG_FILE" "${socket_args[@]}" daemon >/dev/null 2>&1 || true
    else
        # Last resort: fully detached process with a log file.
        mkdir -p "$(dirname "$STANDALONE_LOG")"
        setsid -f "$VENV_PY" "$MAIN_PY" daemon >>"$STANDALONE_LOG" 2>&1 </dev/null
    fi
    wait_ready
}

read_selection() {  # clipboard | primary  -> stdout (empty when there is no text)
    local sel="$1" wl_display flag="" types
    wl_display=$(printenv WAYLAND_DISPLAY || true)
    if [[ -n "$wl_display" ]] && have wl-paste; then
        [[ "$sel" == primary ]] && flag="--primary"
        types=$(wl-paste $flag --list-types 2>/dev/null || true)
        if ! grep -qiE '^(text/|utf8_string|string$|text$)' <<<"$types"; then
            return 0
        fi
        wl-paste $flag --no-newline --type text 2>/dev/null || true
        return 0
    fi
    die "Wayland clipboard unavailable (wl-paste and WAYLAND_DISPLAY are required)"
}

show_help() {
    cat <<'HELP'
Dusky Kokoro · read aloud

  Usage  dusky-kokoro [INPUT] [OPTIONS]
  Default input: clipboard

Input
  FILE / --file PATH   Read TXT, Markdown, PDF or EPUB
  --text "..."         Read text directly
  --primary            Read selected text
  -                    Read piped input

Speech
  --queue / --interrupt  Queue text or replace current speech
  --voice SPEC           Voice name or name:weight blend
  --speed X              Generation speed, 0.5–2.0
  --lang CODE            Language override, e.g. en-us
  --wait                 Wait for completion and report errors
  --no-notify            Disable desktop notifications

Control
  --stop / --pause       Stop, or toggle pause
  --status / --ping      Show status or check the daemon
  --reload / --restart   Apply config or restart
  --unload / --kill      Release the model or stop the daemon

Inspect
  --voices              List voices
  --doctor / --synth     Check setup or test synthesis
  --logs / --debug       Follow normal or debug logs
  --loglevel LEVEL       Change runtime logging
  -h, --help             Show this help

  Example  dusky-kokoro --file book.epub
HELP
}

# --- argument parsing ------------------------------------------------------------
while (( $# > 0 )); do
    case "$1" in
        -h|--help)      show_help; exit 0 ;;
        --primary)      SOURCE="primary" ;;
        --text)         (( $# >= 2 )) || die "--text needs an argument"; SOURCE="text"; SOURCE_TEXT="$2"; shift ;;
        --file)         (( $# >= 2 )) || die "--file needs an argument"; SOURCE="file"; SOURCE_FILE="$2"; shift ;;
        -)              SOURCE="stdin" ;;
        --queue)        MODE="enqueue" ;;
        --interrupt)    MODE="interrupt" ;;
        --voice)        (( $# >= 2 )) || die "--voice needs an argument"; VOICE="$2"; shift ;;
        --speed)        (( $# >= 2 )) || die "--speed needs an argument"; SPEED="$2"; shift ;;
        --lang)         (( $# >= 2 )) || die "--lang needs an argument"; LANG_CODE="$2"; shift ;;
        --wait)         WAIT=1 ;;
        --no-notify)    NOTIFY=0 ;;
        --stop|--pause|--status|--ping|--unload|--reload|--kill|--restart|--debug|--logs|--voices|--doctor)
                        ACTION="$1" ;;
        --synth)        ACTION="--doctor-synth" ;;
        --loglevel)     (( $# >= 2 )) || die "--loglevel needs an argument"; ACTION="--loglevel"; LEVEL="$2"; shift ;;
        --*)            die "unknown option: $1 (see --help)" ;;
        *)
            if [[ -f "$1" ]]; then
                SOURCE="file"
                SOURCE_FILE="$1"
            else
                die "unexpected argument: $1 (use --text to pass text, --file for files, or see --help)"
            fi
            ;;
    esac
    shift
done

require_install

# --- actions -------------------------------------------------------------------------
case "$ACTION" in
    --stop)
        if out=$(ctl stop --format kv --timeout 5 2>/dev/null); then
            if [[ "$(kv_get "$out" was_playing)" == true ]]; then notify "Kokoro TTS" "Stopped" low; fi
        else
            echo "daemon not running - nothing to stop"
        fi
        exit 0 ;;
    --pause)
        if out=$(ctl pause --format kv --timeout 5 2>/dev/null); then
            case "$(kv_get "$out" paused)" in
                true)  notify "Kokoro TTS" "Paused" low ;;
                false) notify "Kokoro TTS" "Resumed" low ;;
                *)     notify "Kokoro TTS" "Nothing is playing" low ;;
            esac
        else
            notify "Kokoro TTS" "Daemon not running" low
        fi
        exit 0 ;;
    --status)
        if ! ctl status --format pretty --timeout 5; then
            echo "daemon: not running (it starts on demand)"
            if systemd_user_ok && socket_unit_present; then
                printf 'systemd: %s=%s %s=%s\n' "$SOCKET_UNIT" "$(systemctl --user is-active "$SOCKET_UNIT" || true)" \
                    "$SERVICE_UNIT" "$(systemctl --user is-active "$SERVICE_UNIT" || true)"
            fi
        fi
        exit 0 ;;
    --ping)     ctl ping --format pretty --timeout 5; exit $? ;;
    --unload)   ctl unload --format kv --timeout 10 && echo "model unloaded"; exit $? ;;
    --reload)   ctl reload --format pretty --timeout 10; exit $? ;;
    --loglevel) ctl loglevel "$LEVEL" --format kv --timeout 5; exit $? ;;
    --voices)   ctl_full voices; exit $? ;;
    --doctor)   ctl_full doctor; exit $? ;;
    --doctor-synth) ctl_full doctor --synth; exit $? ;;
    --kill)
        ctl shutdown --timeout 5 >/dev/null 2>&1 || true
        if systemd_user_ok; then
            systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true
            systemctl --user stop "$ADHOC_UNIT" >/dev/null 2>&1 || true
        fi
        echo "daemon stopped (the socket unit stays active: the next trigger relaunches it)"
        echo "to disable completely:  systemctl --user disable --now $SOCKET_UNIT"
        exit 0 ;;
    --restart)
        if systemd_user_ok && socket_unit_present; then
            systemctl --user reset-failed "$SERVICE_UNIT" >/dev/null 2>&1 || true
            systemctl --user restart "$SERVICE_UNIT" >/dev/null 2>&1 || true
        else
            ctl shutdown --timeout 5 >/dev/null 2>&1 || true
            if systemd_user_ok; then systemctl --user stop "$SERVICE_UNIT" "$ADHOC_UNIT" >/dev/null 2>&1 || true; fi
            sleep 0.3
            ensure_daemon
        fi
        wait_ready
        echo "daemon restarted"
        exit 0 ;;
    --debug)
        if systemd_user_ok && socket_unit_present; then
            systemctl --user set-environment DUSKY_LOG_LEVEL=DEBUG
            ctl shutdown --timeout 5 >/dev/null 2>&1 || true
            systemctl --user stop "$SERVICE_UNIT" >/dev/null 2>&1 || true
            ensure_daemon
            echo "DEBUG logging active for new daemon starts; revert with: systemctl --user unset-environment DUSKY_LOG_LEVEL"
        else
            ensure_daemon
            ctl loglevel DEBUG --timeout 5 >/dev/null
        fi
        ;&   # fall through to --logs
    --logs)
        if systemd_user_ok; then
            exec journalctl --user -f -n 200 -u "$SERVICE_UNIT" -u "$ADHOC_UNIT"
        elif [[ -f "$STANDALONE_LOG" ]]; then
            exec tail -n 200 -f "$STANDALONE_LOG"
        else
            die "no log available (daemon never started standalone and no systemd user session)"
        fi ;;
    speak) ;;
    *) die "internal error: unknown action $ACTION" ;;
esac

# --- speak ------------------------------------------------------------------------------
if [[ "$SOURCE" == "file" ]]; then
    [[ -f "$SOURCE_FILE" ]] || die "file not found: $SOURCE_FILE"
    [[ -r "$SOURCE_FILE" ]] || die "cannot read $SOURCE_FILE (permission denied)"
    ensure_daemon

    set -- speak --file "$SOURCE_FILE" --format kv --client trigger --timeout "$REQUEST_TIMEOUT"
    [[ -n "$MODE" ]] && set -- "$@" --mode "$MODE"
    [[ -n "$VOICE" ]] && set -- "$@" --voice "$VOICE"
    [[ -n "$SPEED" ]] && set -- "$@" --speed "$SPEED"
    [[ -n "$LANG_CODE" ]] && set -- "$@" --lang "$LANG_CODE"

    if [[ "$WAIT" == 1 ]]; then
        ctl "$@" --wait
        exit $?
    fi

    rc=0
    out=$(ctl "$@") || rc=$?
    if (( rc == 2 || rc == 3 )); then
        ensure_daemon
        rc=0
        out=$(ctl "$@") || rc=$?
    fi

    event=$(kv_get "$out" event)
    case "$event" in
        accepted)
            segments=$(kv_get "$out" segments)
            chars=$(kv_get "$out" chars)
            base_name=$(basename -- "$SOURCE_FILE")
            if [[ "$(kv_get "$out" engine_loaded)" == true ]]; then
                notify "Kokoro TTS" "Reading $base_name ($segments segments, $chars chars)" low
            else
                notify "Kokoro TTS" "Loading model... reading $base_name ($segments segments)" low
            fi
            ;;
        deduplicated)
            ;;
        *)
            err=$(kv_get "$out" error)
            [[ -n "$err" ]] || err="unknown error (exit code $rc)"
            notify "Kokoro TTS error" "$err" critical
            printf 'trigger.sh: %s\n' "$err" >&2
            exit 1 ;;
    esac
    exit 0
fi

text=""
case "$SOURCE" in
    clipboard|primary) text=$(read_selection "$SOURCE") ;;
    text)              text="$SOURCE_TEXT" ;;
    stdin)             text=$(cat) ;;
esac

if [[ -z "$(printf '%s' "$text" | tr -d '[:space:]')" ]]; then
    notify "Kokoro TTS" "Nothing to read: the $SOURCE contains no text" low
    exit 0
fi

ensure_daemon

set -- speak --stdin --format kv --client trigger --timeout "$REQUEST_TIMEOUT"
[[ -n "$MODE" ]] && set -- "$@" --mode "$MODE"
[[ -n "$VOICE" ]] && set -- "$@" --voice "$VOICE"
[[ -n "$SPEED" ]] && set -- "$@" --speed "$SPEED"
[[ -n "$LANG_CODE" ]] && set -- "$@" --lang "$LANG_CODE"

if [[ "$WAIT" == 1 ]]; then
    printf '%s' "$text" | ctl "$@" --wait
    exit $?
fi

rc=0
out=$(printf '%s' "$text" | ctl "$@") || rc=$?
if (( rc == 2 || rc == 3 )); then
    # The daemon exited between our ping and the request (idle-exit race): relaunch once and retry.
    ensure_daemon
    rc=0
    out=$(printf '%s' "$text" | ctl "$@") || rc=$?
fi

event=$(kv_get "$out" event)
case "$event" in
    accepted)
        segments=$(kv_get "$out" segments)
        chars=$(kv_get "$out" chars)
        if [[ "$(kv_get "$out" engine_loaded)" == true ]]; then
            notify "Kokoro TTS" "Reading $segments segment(s), $chars characters" low
        else
            notify "Kokoro TTS" "Loading model... reading $segments segment(s)" low
        fi
        ;;
    deduplicated)
        ;;
    *)
        err=$(kv_get "$out" error)
        [[ -n "$err" ]] || err="unknown error (exit code $rc)"
        notify "Kokoro TTS error" "$err" critical
        printf 'trigger.sh: %s\n' "$err" >&2
        exit 1 ;;
esac
