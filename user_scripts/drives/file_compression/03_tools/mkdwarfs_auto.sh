#!/usr/bin/env bash
# Profile-aware front end; all compression logic lives in actions.sh.
set -euo pipefail
TOOLS_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
usage() { printf 'Usage: %s SOURCE [-l LEVEL] [--profile NAME] [--reproducible] [--par2] [--output IMAGE]\n' "$0"; }
SOURCE=
OUTPUT=
CLI_OVERRIDES=()
while (( $# )); do
    case $1 in
        -h|--help) usage; exit 0;;
        -l|--level|--profile|--output)
            (( $# >= 2 )) || { usage >&2; exit 1; }
            case $1 in
                -l|--level) CLI_OVERRIDES+=("DWARFS_LEVEL=$2");;
                --profile) CLI_OVERRIDES+=("DWARFS_PROFILE=$2");;
                --output) OUTPUT=$2;;
            esac
            shift 2;;
        -l[0-9]) CLI_OVERRIDES+=("DWARFS_LEVEL=${1#-l}"); shift;;
        --reproducible) CLI_OVERRIDES+=(DWARFS_REPRODUCIBLE=1); shift;;
        --par2) CLI_OVERRIDES+=(DWARFS_PAR2=1); shift;;
        --) shift; (( $# == 1 )) && [[ -z $SOURCE ]] || { usage >&2; exit 1; }; SOURCE=$1; shift;;
        -*) printf 'Unknown option: %s\n' "$1" >&2; exit 1;;
        *) [[ -z $SOURCE ]] || { usage >&2; exit 1; }; SOURCE=$1; shift;;
    esac
done
[[ -n $SOURCE ]] || { usage >&2; exit 1; }
SOURCE=$(realpath -e -- "$SOURCE")
export DWARFS_PROFILES_DIR=${DWARFS_PROFILES_DIR:-$TOOLS_DIR/profiles}
if [[ -d $SOURCE/files/game-root ]]; then
    export GAME_DIR=$SOURCE
    OUTPUT=${OUTPUT:-$SOURCE/files/game-root.dwarfs}
    SOURCE=$SOURCE/files/game-root
else
    OUTPUT=${OUTPUT:-$SOURCE.dwarfs}
fi
# Load configured defaults first, then apply explicit command-line choices.
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../01_universal_actions/actions.sh
source "$TOOLS_DIR/../01_universal_actions/actions.sh"
(( ${#CLI_OVERRIDES[@]} == 0 )) || export "${CLI_OVERRIDES[@]}"
run_managed dwarfs-compress "$SOURCE" "$OUTPUT"
