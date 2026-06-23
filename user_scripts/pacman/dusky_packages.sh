#!/usr/bin/env bash
# =============================================================================
# Dusky Package Atlas (Platinum Edition - Revision 19 - Centered Apex)
# Architecture: Translated 1:1 from ZSH to universally compliant Bash.
#               Fully supports CLI fallback pipeline, Wayland wl-copy integration,
#               and a dedicated --desktop flag for GUI application launchers.
# =============================================================================

# DRY Header Helper
_pkg_header() {
    printf "\n\e[34m::\e[0m \e[1m%s\e[0m (Top %s)\n" "$1" "$2"
    printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
    printf "\e[38;5;242mDATE              SIZE  PACKAGE\e[0m\n"
    printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
}

# The Interactive FZF Engine
_pkg_interactive() {
    local init_mode="${1:-date_desc}"
    local init_target="${2:-all}"
    export LC_ALL=C

    # F1 Help Menu Payload
    export DUSKY_PKG_HELP='clear; printf "\n\n  \033[1;38;5;81m󰏖 Dusky Package Atlas - Keyboard Shortcuts\033[0m\n  \033[38;5;238m──────────────────────────────────────────────\033[0m\n  \033[1;33m[CTRL-S]\033[0m  Sort by Largest Package Size (Hogs)\n  \033[1;33m[ALT-S]\033[0m   Sort by Smallest Package Size (Tiny)\n  \033[1;33m[CTRL-D]\033[0m  Sort by Newest Install Date\n  \033[1;33m[CTRL-R]\033[0m  Reset to Default Alphabetical Order\n  \033[1;33m[ALT-C]\033[0m   Copy Package Details to Clipboard\n  \033[1;33m[F1]\033[0m      Show this Help Menu\n  \033[1;33m[ESC]\033[0m     Exit Interactive Atlas\n  \033[1;33m[ENTER]\033[0m   Select Package and Copy/Output\n\n  \033[38;5;242mPress any key to return...\033[0m"; read -rsn1'

    # Compile Live List Generator
    export DUSKY_PKG_LIST='
export LC_ALL=C
mode="$1"
target="$2"

case "$mode" in
    size_desc) sort_args=(-t"|" -k4 -nr) ;;
    size_asc)  sort_args=(-t"|" -k4 -n) ;;
    date_desc) sort_args=(-t"|" -k3 -nr) ;;
    date_asc)  sort_args=(-t"|" -k3 -n) ;;
    alpha|*)   sort_args=(-t"|" -k1) ;;
esac

fetch_data() {
    if [ "$target" = "explicit" ]; then
        pacman -Qeq | expac --timefmt=%s "%n|%v|%l|%m|%d" - 2>/dev/null
    else
        expac --timefmt=%s "%n|%v|%l|%m|%d" 2>/dev/null
    fi
}

fetch_data | sort "${sort_args[@]}" | awk -F"|" '\''
    {
        name = $1; ver = $2; date = $3; size = $4; desc = $5;
        for(i=6; i<=NF; i++) desc = desc "|" $i
        if (length(desc) == 0) desc = "<No description provided>"
        
        size_mb = size / 1048576
        if (size_mb >= 1024) { size_fmt = sprintf("%.2f GiB", size_mb/1024) }
        else if (size_mb >= 1) { size_fmt = sprintf("%.2f MiB", size_mb) }
        else { size_fmt = sprintf("%.2f KiB", size / 1024) }
        
        date_fmt = strftime("%m/%d", date)

        disp_name = (length(name) > 27) ? substr(name, 1, 24) "..." : name
        disp_ver  = (length(ver) > 11)  ? substr(ver, 1, 8) "..."  : ver
        
        visual_str = sprintf("\033[1;38;5;39m%-27s\033[0m \033[38;5;238m│\033[0m \033[38;5;220m%-11s\033[0m \033[38;5;238m│\033[0m \033[38;5;114m%-5s\033[0m \033[38;5;238m│\033[0m \033[38;5;208m%10s\033[0m", disp_name, disp_ver, date_fmt, size_fmt)
        
        pad = sprintf("%150s", "")
        printf "%s|%s%s%s %s\n", name, visual_str, pad, name, desc
    }
'\''
'

    # Compile Preview Script
    export DUSKY_PKG_PREVIEW='
export LC_ALL=C
pkg="$1"

left_str=":: Package Details: $pkg"
left_len=${#left_str}

count_str=""
right_len=0
if [[ -n "$FZF_POS" && -n "$FZF_MATCH_COUNT" ]]; then
    count_str="[${FZF_POS}/${FZF_MATCH_COUNT}]"
    right_len=${#count_str}
elif [[ -n "$FZF_MATCH_COUNT" ]]; then
    count_str="[${FZF_MATCH_COUNT}/${FZF_TOTAL_COUNT}]"
    right_len=${#count_str}
fi

cols=${FZF_PREVIEW_COLUMNS:-80}
pad_len=$(( cols - left_len - right_len - 1 ))
(( pad_len < 1 )) && pad_len=1
pad=$(printf "%*s" "$pad_len" "")

hr=$(printf "%*s" "$cols" "" | sed "s/ /─/g")

printf "\033[1;38;5;81m:: \033[1;37mPackage Details: \033[1;32m%s\033[0m%s\033[1;38;5;242m%s\033[0m\n\033[38;5;238m%s\033[0m\n" "$pkg" "$pad" "$count_str" "$hr"

repo=$(pacman -Si "$pkg" 2>/dev/null | awk -F":" '\''/^Repository/ {sub(/^[ \t]+/, "", $2); print $2; exit}'\'')
[[ -z "$repo" ]] && repo="Local/AUR"

pacman -Qi "$pkg" 2>/dev/null | awk -F":" -v repo="$repo" '\''
    BEGIN {
        print_kv("Repository", repo, repo == "Local/AUR" ? "203" : "213")
    }
    function print_kv(key, val, color) {
        printf "\033[1;38;5;39m%-18s\033[0m: \033[38;5;%sm%s\033[0m\n", key, color, val
    }
    {
        key = $1; sub(/[ \t]+$/, "", key)
        val = $2; for(i=3; i<=NF; i++) val = val ":" $i
        sub(/^[ \t]+/, "", val)
    }
    key == "Version"        { print_kv("Version", val, "220") }
    key == "Description"    { print_kv("Description", val, "253") }
    key == "Architecture"   { print_kv("Architecture", val, "141") }
    key == "URL"            { print_kv("URL", val, "114") }
    key == "Depends On"     { print_kv("Dependencies", val, val == "None" ? "242" : "250") }
    key == "Required By"    { print_kv("Required By", val, val == "None" ? "242" : "203") }
    key == "Conflicts With" { print_kv("Conflicts", val, val == "None" ? "242" : "196") }
    key == "Replaces"       { print_kv("Replaces", val, val == "None" ? "242" : "208") }
    key == "Packager"       { print_kv("Packager", val, "242") }
    key == "Build Date"     { print_kv("Built", val, "250") }
    key == "Install Date"   { print_kv("Installed", val, "250") }
    key == "Install Reason" { print_kv("Reason", val, "203") }
'\''

printf "\n\033[1;38;5;81m:: \033[1;37mSystem Integration\033[0m\n\033[38;5;238m%s\033[0m\n" "$hr"

pacman -Ql "$pkg" 2>/dev/null | awk '\''
    BEGIN { bins=""; srvs=""; apps="" }
    {
        path = substr($0, length($1) + 2)
        
        if (path ~ /^\/usr\/(local\/)?s?bin\/[^\/]+$/) 
            bins = bins "  \033[38;5;114m" path "\033[0m\n"
        else if (path ~ /^(\/usr\/lib|\/etc)\/systemd\/(system|user)\/.*\.(service|timer|socket|path|mount|conf|target|device)$/) 
            srvs = srvs "  \033[38;5;203m" path "\033[0m\n"
        else if (path ~ /^\/usr\/share\/applications\/[^\/]+\.desktop$/) 
            apps = apps "  \033[38;5;39m" path "\033[0m\n"
    }
    END {
        if (bins != "") { printf "\033[1;33m󰘚 Binaries:\033[0m\n%s", bins } 
        else { printf "\033[1;33m󰘚 Binaries:\033[0m \033[38;5;242m(None)\033[0m\n" }
        
        if (srvs != "") { printf "\n\033[1;35m󰒓 Systemd Units:\033[0m\n%s", srvs } 
        else { printf "\n\033[1;35m󰒓 Systemd Units:\033[0m \033[38;5;242m(None)\033[0m\n" }
        
        if (apps != "") { printf "\n\033[1;36m󰀻 Desktop Entries:\033[0m\n%s", apps } 
        else { printf "\n\033[1;36m󰀻 Desktop Entries:\033[0m \033[38;5;242m(None)\033[0m\n" }
    }
'\''
'

    # Wayland Clipboard Payload
    export DUSKY_PKG_COPY='
export LC_ALL=C
pkg="$1"
bash -c "$DUSKY_PKG_PREVIEW" _ "$pkg" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2})?)?[mGK]//g" | wl-copy
'

    local prompt_str="   Search Packages ❯ "
    case "$init_mode" in
        size_desc) prompt_str="   Hogs (Largest) ❯ " ;;
        size_asc)  prompt_str="   Tiny (Smallest) ❯ " ;;
        date_desc) prompt_str="   Newest (Date) ❯ " ;;
    esac

    # Perfectly Centered Header Strings (Math verified against layout boundaries: 27 | 11 | 5 | 10)
    local visual_header=$(printf "\033[1;37m          PACKAGE          \033[0m \033[38;5;238m│\033[0m \033[38;5;242m  VERSION  \033[0m \033[38;5;238m│\033[0m \033[38;5;242m DATE\033[0m \033[38;5;238m│\033[0m \033[38;5;242m   SIZE   \033[0m")

    local fzf_choice
    fzf_choice=$(bash -c "$DUSKY_PKG_LIST" _ "$init_mode" "$init_target" | fzf --ansi \
        --delimiter='\|' \
        --with-nth=2 \
        --tiebreak=begin,length \
        --no-hscroll \
        --ellipsis='' \
        --highlight-line \
        --prompt="$prompt_str" \
        --pointer="" \
        --marker="✓" \
        --layout=reverse \
        --border=rounded \
        --border-label=" 󰏖 Dusky Package Atlas [F1: Help] " \
        --border-label-pos=3 \
        --info=hidden \
        --header="$visual_header" \
        --header-first \
        --bind="ctrl-s:reload-sync(bash -c \"\$DUSKY_PKG_LIST\" _ size_desc $init_target)+change-prompt(   Hogs (Largest) ❯ )" \
        --bind="alt-s:reload-sync(bash -c \"\$DUSKY_PKG_LIST\" _ size_asc $init_target)+change-prompt(   Tiny (Smallest) ❯ )" \
        --bind="ctrl-d:reload-sync(bash -c \"\$DUSKY_PKG_LIST\" _ date_desc $init_target)+change-prompt(   Newest (Date) ❯ )" \
        --bind="ctrl-r:reload-sync(bash -c \"\$DUSKY_PKG_LIST\" _ alpha $init_target)+change-prompt(   Search Packages ❯ )" \
        --bind="f1:execute(bash -c \"\$DUSKY_PKG_HELP\")" \
        --bind="alt-c:execute-silent(bash -c \"\$DUSKY_PKG_COPY\" _ {1})+change-prompt(   Copied Info! ❯ )" \
        --bind="result:refresh-preview" \
        --color="bg+:#1e1e2e,bg:#11111b,spinner:#f5e0dc" \
        --color="fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,info:#cba6f7" \
        --color="pointer:#a6e3a1,marker:#f5e0dc,prompt:#cba6f7" \
        --color="hl:#f38ba8,hl+:#f38ba8,border:#585b70,label:#a6e3a1" \
        --preview='bash -c "$DUSKY_PKG_PREVIEW" _ {1}' \
        --preview-window="right,50%,border-left,wrap")

    # Environment Cleanup
    unset DUSKY_PKG_LIST
    unset DUSKY_PKG_PREVIEW
    unset DUSKY_PKG_COPY
    unset DUSKY_PKG_HELP

    # Action Router
    if [[ -n "$fzf_choice" ]]; then
        local target_pkg="${fzf_choice%%|*}"
        
        # 1. Output to standard stdout so shell piping works seamlessly if run in a terminal
        printf "%s\n" "$target_pkg"

        # 2. Quietly copy to Wayland clipboard as a convenience
        if command -v wl-copy >/dev/null 2>&1; then
            printf "%s" "$target_pkg" | wl-copy
        fi

        # 3. If launched from the .desktop GUI Entry, give visual feedback before the terminal shatters
        if (( IS_DESKTOP_ENTRY )); then
            printf "\n\e[1;32m✔ Success!\e[0m Copied package \e[1;39m'%s'\e[0m to clipboard.\n" "$target_pkg" >&2
            sleep 1.5
        fi
    fi
}

main() {
    if ! command -v expac >/dev/null 2>&1; then
        printf "\n\e[31m✖ Error:\e[0m 'expac' is not installed.\n" >&2
        printf "  Please install it first: \e[36msudo pacman -S expac\e[0m\n\n" >&2
        sleep 4; exit 1
    fi
    if ! command -v fzf >/dev/null 2>&1; then
        printf "\n\e[31m✖ Error:\e[0m 'fzf' is not installed.\n" >&2
        printf "  Please install it first: \e[36msudo pacman -S fzf\e[0m\n\n" >&2
        sleep 4; exit 1
    fi

    local target="all"
    local metric=""
    declare -i count=-1
    declare -i show_help=0
    export IS_DESKTOP_ENTRY=0

    for arg in "$@"; do
        arg_lower="${arg,,}"
        case "$arg_lower" in
            --desktop) IS_DESKTOP_ENTRY=1 ;;
            help|-h|--help) show_help=1 ;;
            explicit|user) target="explicit" ;;
            all) target="all" ;;
            hogs|size|big|fat|massive|huge|giant) metric="size_desc" ;;
            tiny|small|micro|mini|little) metric="size_asc" ;;
            new|recent|latest) metric="date_desc" ;;
            old|ancient) metric="date_asc" ;;
            *)
                if [[ "$arg" =~ ^[1-9][0-9]*$ ]]; then
                    count="$arg"
                else
                    printf "\n\e[31m✖ Error:\e[0m Unknown argument: '\e[33m%s\e[0m'\n\n" "$arg" >&2
                    sleep 3; exit 1
                fi
                ;;
        esac
    done

    # 4. Help Menu Overlay
    if (( show_help )); then
        printf "\n\e[34m::\e[0m \e[1mpkg\e[0m — Advanced Package Query Tool\n"
        printf "\e[38;5;238m------------------------------------------------------------\e[0m\n"
        printf "\e[32mUsage:\e[0m pkg [target] [metric] [count]\n"
        printf "       \e[38;5;242m(Arguments can be provided in ANY order)\e[0m\n"
        printf "       \e[38;5;14mOmitting [count] launches the Interactive FZF Atlas.\e[0m\n\n"
        
        printf "\e[1mTargets:\e[0m\n"
        printf "  \e[36mall\e[0m                  - System-wide packages (Default)\n"
        printf "  \e[36mexplicit\e[0m, \e[36muser\e[0m       - Only explicitly installed packages\n\n"
        
        printf "\e[1mMetrics:\e[0m\n"
        printf "  \e[36mhogs\e[0m, \e[36mbig\e[0m, \e[36mmassive\e[0m... - Sort by size descending (largest first)\n"
        printf "  \e[36mtiny\e[0m, \e[36msmall\e[0m, \e[36mmicro\e[0m... - Sort by size ascending (smallest first)\n"
        printf "  \e[36mnew\e[0m, \e[36mrecent\e[0m, \e[36mlatest\e[0m  - Sort by installation date (newest first)\n"
        printf "  \e[36mold\e[0m, \e[36mancient\e[0m          - Sort by installation date (oldest first)\n\n"
        
        printf "\e[1mExamples:\e[0m\n"
        printf "  \e[33mpkg\e[0m                  # Launch Interactive FZF Package Atlas\n"
        printf "  \e[33mpkg explicit massive\e[0m # Launch Atlas showing explicitly installed Hogs\n"
        printf "  \e[33mpkg 50 size\e[0m          # Print classic CLI list of Top 50 largest packages\n\n"
        exit 0
    fi

    # 5. Execution Router: Launch Interactive Atlas if no count is provided
    if (( count == -1 )); then
        local init_mode="${metric:-date_desc}"
        _pkg_interactive "$init_mode" "$target"
        exit 0
    fi

    # 6. Fallback: Classic CLI List Execution (if count > 0)
    metric="${metric:-size_desc}"
    declare -a expac_args=(--timefmt='%s' '%l|%m|%n')
    declare -a sort_cmd
    local title_metric=""

    case "$metric" in
        size_desc) title_metric="Largest"; sort_cmd=(sort -t '|' -k2 -rn) ;;
        size_asc)  title_metric="Smallest"; sort_cmd=(sort -t '|' -k2 -n) ;;
        date_desc) title_metric="Newest"; sort_cmd=(sort -t '|' -k1 -rn) ;;
        date_asc)  title_metric="Oldest";   sort_cmd=(sort -t '|' -k1 -n) ;;
        alpha|*)   title_metric="Alpha"; sort_cmd=(sort -t '|' -k3) ;;
    esac

    local title_full=""
    if [[ "$target" == "explicit" ]]; then
        title_full="${title_metric} Explicitly Installed Packages"
    else
        title_full="${title_metric} Installed Packages (Overall)"
    fi

    _pkg_header "$title_full" "$count"
    
    local awk_color='{ printf "\033[38;5;246m%-15s\033[0m \033[38;5;220m%10s\033[0m  \033[1;38;5;39m%s\033[0m\n", strftime("%m/%d", $1), $2, $3 }'

    if [[ "$target" == "explicit" ]]; then
        pacman -Qeq | expac "${expac_args[@]}" - 2>/dev/null | "${sort_cmd[@]}" | head -n "$count" | numfmt --to=iec-i --suffix=B --field=2 --delimiter='|' --padding=8 | awk -F '|' "$awk_color"
    else
        expac "${expac_args[@]}" 2>/dev/null | "${sort_cmd[@]}" | head -n "$count" | numfmt --to=iec-i --suffix=B --field=2 --delimiter='|' --padding=8 | awk -F '|' "$awk_color"
    fi

    printf "\n"
}

main "$@"
