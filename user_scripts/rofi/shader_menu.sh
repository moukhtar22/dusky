#!/usr/bin/env bash
#
# Hyprshade Selector - Interactive shader picker with live preview
# Powered natively by hyprctl & Lua eval (Hyprland 0.55+ Compatible)
# Requires: rofi, hyprctl, flock
#

set -o errexit
set -o nounset
set -o pipefail

declare -rA ICONS=(
    [active]=""
    [inactive]=""
    [off]=""
    [shader]=""
)

declare -ra ROFI_CMD=(
    rofi
    -dmenu
    -i
    -markup-rows
    -no-custom
    -no-sort
    -theme-str 'window { width: 400px; }'
)

declare -a SHADERS=()
declare -a PREVIEW_PIDS=()

declare ORIGINAL_SHADER="off"
declare PREVIEW_SHADER="off"
declare SEARCH_QUERY=""
declare TMP_DIR=""
declare LOCK_FILE=""
declare TOKEN_FILE=""

declare -i CURRENT_IDX=0
declare -i MAX_IDX=0
declare -i CLEANUP_NEEDED=1
declare -i REQUEST_SEQ=0

trim() {
    local str="${1-}"
    str="${str#"${str%%[![:space:]]*}"}"
    str="${str%"${str##*[![:space:]]}"}"
    printf '%s' "$str"
}

escape_pango() {
    local str="${1-}"
    str=${str//&/&amp;}
    str=${str//</&lt;}
    str=${str//>/&gt;}
    printf '%s' "$str"
}

err() {
    printf 'Error: %s\n' "$*" >&2
}

check_dependencies() {
    local -a missing=()
    local cmd

    for cmd in rofi hyprctl flock; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done

    if ((${#missing[@]} > 0)); then
        err "Missing required commands: ${missing[*]}"
        exit 1
    fi
}

write_latest_token() {
    local token="$1"
    local tmp_file="${TOKEN_FILE}.tmp.${BASHPID}.${RANDOM}"

    printf '%s\n' "$token" >"$tmp_file"
    mv -f -- "$tmp_file" "$TOKEN_FILE"
}

apply_shader_sync() {
    local shader="${1:-off}"

    if [[ "$shader" == "off" ]]; then
        # Turn off in Hyprland 0.55+ via Lua Eval
        hyprctl eval 'hl.config({ decoration = { screen_shader = "" } })' >/dev/null 2>&1 || true
    else
        # Resolve full path natively for both .glsl and .frag
        local shader_path=""
        local dir
        local ext
        for dir in "$HOME/.config/hypr/shaders" "/usr/share/hyprshade/shaders"; do
            for ext in glsl frag; do
                if [[ -f "$dir/$shader.$ext" ]]; then
                    shader_path="$dir/$shader.$ext"
                    break 2
                fi
            done
        done

        if [[ -n "$shader_path" ]]; then
            # Apply in Hyprland 0.55+ via Lua Eval
            hyprctl eval "hl.config({ decoration = { screen_shader = \"$shader_path\" } })" >/dev/null 2>&1 || true
        else
            err "Could not resolve path for shader: $shader"
        fi
    fi
}

queue_preview() {
    local shader="$1"
    local token

    ((++REQUEST_SEQ))
    token=$REQUEST_SEQ
    write_latest_token "$token"

    (
        exec {__lock_fd}>"$LOCK_FILE"
        flock "$__lock_fd"

        __latest=$(<"$TOKEN_FILE") || exit 0
        [[ "$__latest" == "$token" ]] || exit 0

        apply_shader_sync "$shader" >/dev/null 2>&1 || exit 0
    ) &

    PREVIEW_PIDS+=("$!")
}

apply_serialized() {
    local shader="$1"
    local lock_fd

    ((++REQUEST_SEQ))
    write_latest_token "$REQUEST_SEQ"

    exec {lock_fd}>"$LOCK_FILE"
    flock "$lock_fd"
    apply_shader_sync "$shader"
    exec {lock_fd}>&-
}

reap_preview_jobs() {
    local pid

    for pid in "${PREVIEW_PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    PREVIEW_PIDS=()
}

cleanup() {
    local status=$?

    if ((CLEANUP_NEEDED)) && [[ -n "$LOCK_FILE" && -n "$TOKEN_FILE" ]]; then
        apply_serialized "$ORIGINAL_SHADER" >/dev/null 2>&1 || true
        CLEANUP_NEEDED=0
    fi

    reap_preview_jobs

    if [[ -n "$TMP_DIR" ]]; then
        rm -rf -- "$TMP_DIR"
    fi

    return "$status"
}

init() {
    local current_path
    local shader_file
    local name
    local i
    local dir
    local ext
    local -A seen=([off]=1)

    check_dependencies

    umask 077
    TMP_DIR=$(mktemp -d -t hyprshade-selector.XXXXXXXX)
    LOCK_FILE="$TMP_DIR/apply.lock"
    TOKEN_FILE="$TMP_DIR/latest.token"

    : >"$LOCK_FILE"
    write_latest_token 0

    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    # Get current shader purely through hyprctl JSON (Bypassing hyprshade bugs)
    current_path=$(hyprctl getoption decoration:screen_shader -j 2>/dev/null | grep '"str"' | cut -d'"' -f4 || true)
    
    if [[ -z "$current_path" || "$current_path" == "[[EMPTY]]" ]]; then
        ORIGINAL_SHADER="off"
    else
        ORIGINAL_SHADER=$(basename "$current_path")
        ORIGINAL_SHADER="${ORIGINAL_SHADER%.*}"
    fi
    [[ -z "$ORIGINAL_SHADER" ]] && ORIGINAL_SHADER="off"

    SHADERS=("off")

    # Native directory scan for shaders (Supporting both .glsl and .frag)
    for dir in "$HOME/.config/hypr/shaders" "/usr/share/hyprshade/shaders"; do
        [[ -d "$dir" ]] || continue
        for ext in glsl frag; do
            for shader_file in "$dir/"*."$ext"; do
                [[ -f "$shader_file" ]] || continue
                name=$(basename "$shader_file")
                name="${name%.*}"
                [[ -z "$name" || "$name" == "off" ]] && continue
                [[ -n "${seen[$name]+_}" ]] && continue
                seen["$name"]=1
                SHADERS+=("$name")
            done
        done
    done

    CURRENT_IDX=0
    for i in "${!SHADERS[@]}"; do
        if [[ "${SHADERS[i]}" == "$ORIGINAL_SHADER" ]]; then
            CURRENT_IDX=$i
            break
        fi
    done

    MAX_IDX=$((${#SHADERS[@]} - 1))
    PREVIEW_SHADER="$ORIGINAL_SHADER"
}

build_menu() {
    local -n menu_ref=$1
    local -n active_ref=$2

    menu_ref=()
    active_ref=-1

    local i
    local item
    local icon
    local display_name
    local prefix
    local suffix

    for i in "${!SHADERS[@]}"; do
        item="${SHADERS[i]}"
        prefix=""
        suffix=""

        if [[ "$item" == "$PREVIEW_SHADER" ]]; then
            active_ref=$i
            prefix="<b>"
            suffix=" (Active)</b>"
            if [[ "$item" == "off" ]]; then
                icon="${ICONS[off]}"
            else
                icon="${ICONS[active]}"
            fi
        else
            if [[ "$item" == "off" ]]; then
                icon="${ICONS[inactive]}"
            else
                icon="${ICONS[shader]}"
            fi
        fi

        if [[ "$item" == "off" ]]; then
            display_name="Turn Off"
        else
            display_name=$(escape_pango "$item")
        fi

        icon=$(escape_pango "$icon")

        if [[ -n "$icon" ]]; then
            menu_ref+=("${prefix}${icon}  ${display_name}${suffix}")
        else
            menu_ref+=("${prefix}${display_name}${suffix}")
        fi
    done
}

notify_applied() {
    local shader="$1"
    local msg="$shader"

    [[ "$msg" == "off" ]] && msg="Off"

    if command -v notify-send >/dev/null 2>&1; then
        notify-send -i video-display "Hyprshade" "Applied: $msg" >/dev/null 2>&1 || true
    fi
}

main_loop() {
    local -a menu_lines=()
    local -a rofi_flags=()
    local -i active_row_index=-1
    local -i exit_code=0
    local raw_output
    local selection
    local returned_query
    local target

    while true; do
        build_menu menu_lines active_row_index

        rofi_flags=(
            -p "Shader Preview"
            -format "i|f"
        )

        if ((active_row_index >= 0)); then
            rofi_flags+=(-a "$active_row_index")
        fi

        if [[ -n "$SEARCH_QUERY" ]]; then
            rofi_flags+=(-filter "$SEARCH_QUERY")
        else
            rofi_flags+=(
                -selected-row "$CURRENT_IDX"
                -kb-custom-1 "Down"
                -kb-custom-2 "Up"
                -kb-row-down ""
                -kb-row-up ""
            )
        fi

        set +o errexit
        raw_output=$(
            printf '%s\n' "${menu_lines[@]}" |
                "${ROFI_CMD[@]}" "${rofi_flags[@]}" 2>/dev/null
        )
        exit_code=$?
        set -o errexit

        selection="${raw_output%%|*}"
        returned_query=""
        [[ "$raw_output" == *"|"* ]] && returned_query="${raw_output#*|}"

        case "$exit_code" in
            0)
                if [[ "$selection" =~ ^[0-9]+$ ]] && ((selection >= 0 && selection <= MAX_IDX)); then
                    target="${SHADERS[selection]}"
                else
                    target="$PREVIEW_SHADER"
                fi

                if ! apply_serialized "$target"; then
                    err "Failed to apply shader: $target"
                    exit 1
                fi

                PREVIEW_SHADER="$target"
                CLEANUP_NEEDED=0
                notify_applied "$target"
                exit 0
                ;;

            10)
                if [[ -n "$returned_query" ]]; then
                    SEARCH_QUERY="$returned_query"
                    continue
                fi

                SEARCH_QUERY=""
                CURRENT_IDX=$(((CURRENT_IDX + 1) % (MAX_IDX + 1)))
                PREVIEW_SHADER="${SHADERS[CURRENT_IDX]}"
                queue_preview "$PREVIEW_SHADER"
                ;;

            11)
                if [[ -n "$returned_query" ]]; then
                    SEARCH_QUERY="$returned_query"
                    continue
                fi

                SEARCH_QUERY=""
                CURRENT_IDX=$(((CURRENT_IDX - 1 + MAX_IDX + 1) % (MAX_IDX + 1)))
                PREVIEW_SHADER="${SHADERS[CURRENT_IDX]}"
                queue_preview "$PREVIEW_SHADER"
                ;;

            1)
                if ! apply_serialized "$ORIGINAL_SHADER"; then
                    err "Failed to restore original shader: $ORIGINAL_SHADER"
                    exit 1
                fi

                CLEANUP_NEEDED=0
                exit 0
                ;;

            *)
                err "Rofi exited with unexpected code: $exit_code"

                if ! apply_serialized "$ORIGINAL_SHADER"; then
                    err "Failed to restore original shader: $ORIGINAL_SHADER"
                fi

                CLEANUP_NEEDED=0
                exit 1
                ;;
        esac
    done
}

main() {
    init
    main_loop
}

main "$@"
