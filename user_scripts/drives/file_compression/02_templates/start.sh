#!/usr/bin/env bash
# Copy beside actions.sh in a game directory; configure with local.config.
GAME_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")") || exit 1
export GAME_DIR
# shellcheck disable=SC1091
source "$GAME_DIR/actions.sh" || exit 1
[[ ${TERMINAL_OUTPUT:-1} != 0 ]] || exec &>/dev/null
mount_lock --nonblock || exit 1

mounted=0
cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP
    if [[ $mounted == 1 && ${UNMOUNT:-1} == 1 ]]; then
        cd -- "$GAME_DIR" || exit 1
        dwarfs-unmount || { (( status != 0 )) || status=1; }
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
if [[ ${EXTRACT:-0} == 1 ]]; then
    dwarfs-extract || exit 1
else
    already_mounted=0
    is_mounted "$GAME_ROOT" && already_mounted=1
    if dwarfs-mount; then
        if [[ $already_mounted == 0 ]] && is_mounted "$GAME_ROOT"; then mounted=1; fi
    else
        # A failed unmount must never be followed by extraction into that mount.
        dwarfs-unmount || exit 1
        dwarfs-extract || exit 1
    fi
fi

CMD=()
wine_game=0
if [[ $(declare -p CUSTOM_CMD 2>/dev/null) == 'declare -a '* ]]; then
    CMD=("${CUSTOM_CMD[@]}")
elif [[ -n ${CUSTOM_CMD:-} ]]; then
    parse_words "$CUSTOM_CMD" || exit 1
    CMD=("${WORDS[@]}")
elif [[ -f $GAME_ROOT/steamclient_loader_x64.exe ]]; then
    exe=$GAME_ROOT/steamclient_loader_x64.exe
    wine_game=1
else
    exe=
    IFS= read -r -d '' exe < <(find "$GAME_ROOT" -maxdepth 3 -type f -name '*.x86_64' -executable -print0 -quit)
    if [[ -n $exe ]]; then
        CMD=("$exe")
    else
        IFS= read -r -d '' exe < <(find "$GAME_ROOT" -maxdepth 3 -type f -iname '*.exe' -print0 -quit)
        [[ -n $exe ]] || { log_error "No executable in $GAME_ROOT; set CUSTOM_CMD in local.config."; exit 1; }
        wine_game=1
    fi
fi
if [[ $wine_game == 1 ]]; then
    SYSWINE=${SYSWINE:-wine}
    wine_path=$(command -v "$SYSWINE") || { log_error "Wine executable missing: $SYSWINE"; exit 1; }
    SYSWINE=$wine_path
    [[ $SYSWINE != */* ]] || SYSWINE=$(realpath -e -- "$SYSWINE") || exit 1
    WINEPREFIX=${WINEPREFIX:-$JC_DIRECTORY/wine-prefix-ew}
    export WINEDEBUG=${WINEDEBUG:-fixme-all}
    export WINEDLLOVERRIDES=${WINEDLLOVERRIDES:-'winemenubuilder.exe=d;mshtml=d;d3d9,d3d10core,d3d11,dxgi=n;d3d12,d3d12core=n'}
    # Wine initializes a missing prefix itself. Keep the absolute executable path.
    CMD=("$SYSWINE" "$exe")
    export ISOLATION_TYPE=wine
else
    export ISOLATION_TYPE=${ISOLATION_TYPE:-native}
fi
if [[ -n ${WINEPREFIX:-} || ${ISOLATION_TYPE:-native} == wine ]]; then
    WINEPREFIX=$(game_path "${WINEPREFIX:-$JC_DIRECTORY/wine-prefix-ew}") || exit 1
    export WINEPREFIX
fi
(( ${#CMD[@]} )) || { log_error 'CUSTOM_CMD is empty.'; exit 1; }
CMD+=("$@")
cd -- "$GAME_ROOT" || exit 1
RUN=("${CMD[@]}")
if [[ ${ISOLATE:-0} == 1 ]]; then
    RUN=(bash "$GAME_DIR/actions.sh" bwrap-run_in_sandbox "${RUN[@]}")
fi
if [[ -n ${ENV:-} ]]; then
    # ENV is trusted shell setup from the user's configuration; arguments remain
    # positional parameters rather than being interpolated into shell code.
    RUN=(bash -c "$ENV"$'\nexec "$@"' bash "${RUN[@]}")
fi
if [[ ${GAMESCOPE:-0} == 1 ]]; then
    run_managed gamescope-run_embedded "${RUN[@]}"
else
    run_managed "${RUN[@]}"
fi
