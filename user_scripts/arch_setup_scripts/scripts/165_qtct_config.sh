#!/usr/bin/env bash
#d: Enforce Qt appearance settings (qt5ct/qt6ct)

set -euo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------------------
# 2. Logging & Presentation
# ------------------------------------------------------------------------------
declare -r RESET=$'\033[0m'
declare -r BOLD=$'\033[1m'
declare -r GREEN=$'\033[32m'
declare -r BLUE=$'\033[34m'
declare -r RED=$'\033[31m'

log_info() { printf "${BLUE}${BOLD}[INFO]${RESET} %s\n" "$1"; }
log_success() { printf "${GREEN}${BOLD}[OK]${RESET} %s\n" "$1"; }
log_err() { printf "${RED}${BOLD}[ERROR]${RESET} %s\n" "$1" >&2; }

# ------------------------------------------------------------------------------
# 3. Cleanup Trap
# ------------------------------------------------------------------------------
# Ensures no temporary files are left behind, keeping the system clean.
cleanup() {
    if [[ -n "${TEMP_FILE:-}" ]] && [[ -f "$TEMP_FILE" ]]; then
        rm -f "$TEMP_FILE"
    fi
}
trap cleanup EXIT ERR

# ------------------------------------------------------------------------------
# 4. Core Logic
# ------------------------------------------------------------------------------
readonly CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

update_qt_config() {
    local app_name="$1"       # e.g., qt5ct
    local conf_file="$2"      # Full path to config
    local dialog_val="$3"     # default or xdgdesktopportal

    log_info "Processing configuration for ${BOLD}${app_name}${RESET}..."

    # Ensure config and colors directories exist
    local config_dir="$CONFIG_HOME/$app_name"
    local colors_dir="$config_dir/colors"
    mkdir -p "$config_dir" "$colors_dir"

    # Publish a complete palette; do not point apps at Matugen's in-place writes.
    # Bootstrap can use the shipped seed before initial generation, and adds
    # Qt6's Accent role to complete 21-role installation palettes. It still
    # rejects malformed input before enabling custom_palette.
    python3 "$HOME/user_scripts/theme_matugen/global/qt_colors.py" "$app_name" --bootstrap

    # Retain an existing widget style; a color setup must not replace its layout.
    local widget_style=Fusion configured_style
    if [[ -f "$conf_file" ]]; then
        configured_style=$(awk '
            /^[[:space:]]*\[/ { appearance = ($0 ~ /^[[:space:]]*\[Appearance\][[:space:]]*$/) }
            appearance && /^[[:space:]]*style[[:space:]]*=/ {
                sub(/^[^=]*=[[:space:]]*/, ""); print; exit
            }
        ' "$conf_file")
        [[ -z "$configured_style" ]] || widget_style="$configured_style"
    fi

    # Keep the temporary file on the destination filesystem for atomic rename.
    TEMP_FILE=$(mktemp "$config_dir/.qtct-config.XXXXXXXX")

    # --------------------------------------------------------------------------
    # STEP A: Generate the enforced header
    # Dynamically expand $HOME for the current user executing the setup script.
    # Qt configuration files require absolute paths and do not expand shell variables.
    # --------------------------------------------------------------------------
    {
        printf "[Appearance]\n"
        printf "color_scheme_path=%s/colors/matugen.conf\n" "$config_dir"
        printf "custom_palette=true\n"
        printf "icon_theme=Papirus-Dark\n"
        printf "standard_dialogs=%s\n" "$dialog_val"
        printf "style=%s\n\n" "$widget_style"
    } > "$TEMP_FILE"

    # --------------------------------------------------------------------------
    # STEP B: Filter existing file or supply defaults
    # If the file exists, preserve Fonts and Interface sections.
    # If fresh, write sane default fonts and interface rules.
    # --------------------------------------------------------------------------
    if [[ -f "$conf_file" && -s "$conf_file" ]]; then
        awk '
            BEGIN { 
                # Keys to strip from the old file to avoid duplication
                keys["style"]=1
                keys["custom_palette"]=1
                keys["icon_theme"]=1
                keys["standard_dialogs"]=1
                keys["color_scheme_path"]=1
            }

            # Collect unmanaged Appearance entries into the new header first.
            NR == FNR {
                if ($0 ~ /^[[:space:]]*\[/) {
                    appearance = ($0 ~ /^[[:space:]]*\[Appearance\][[:space:]]*$/)
                    next
                }
                if (appearance && $0 !~ /^[[:space:]]*$/) {
                    split($0, map, "=")
                    key = map[1]
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
                    if (!(key in keys)) extra[++count] = $0
                }
                next
            }
            FNR == 1 {
                for (i = 1; i <= count; i++) print extra[i]
                print ""
                appearance = 0
            }
            /^[[:space:]]*\[/ {
                appearance = ($0 ~ /^[[:space:]]*\[Appearance\][[:space:]]*$/)
            }
            !appearance { print }
        ' "$conf_file" "$conf_file" >> "$TEMP_FILE"
    else
        log_info "File $conf_file did not exist. Populating with initial defaults."
        if [[ "$app_name" == "qt5ct" ]]; then
            cat << 'EOF' >> "$TEMP_FILE"
[Fonts]
fixed="JetBrainsMono Nerd Font Mono,12,-1,5,50,0,0,0,0,0"
general="Atkinson Hyperlegible,12,-1,5,50,0,0,0,0,0"

[Interface]
activate_item_on_single_click=1
buttonbox_layout=0
cursor_flash_time=1000
dialog_buttons_have_icons=1
double_click_interval=400
gui_effects=@Invalid()
keyboard_scheme=2
menus_have_icons=true
show_shortcuts_in_context_menus=true
stylesheets=@Invalid()
toolbutton_style=4
underline_shortcut=1
wheel_scroll_lines=3

[Troubleshooting]
force_raster_widgets=1
ignored_applications=@Invalid()
EOF
        else
            cat << 'EOF' >> "$TEMP_FILE"
[Fonts]
fixed="JetBrainsMono Nerd Font Mono,12,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
general="Atkinson Hyperlegible,12,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"

[Interface]
activate_item_on_single_click=1
buttonbox_layout=0
cursor_flash_time=1000
dialog_buttons_have_icons=1
double_click_interval=400
gui_effects=@Invalid()
keyboard_scheme=2
menus_have_icons=true
show_shortcuts_in_context_menus=true
stylesheets=@Invalid()
toolbutton_style=4
underline_shortcut=1
wheel_scroll_lines=3

[Troubleshooting]
force_raster_widgets=1
ignored_applications=@Invalid()
EOF
        fi
    fi

    # --------------------------------------------------------------------------
    # STEP C: Atomic Apply
    # Move temp file to actual file. No backup files (.bak) created.
    # --------------------------------------------------------------------------
    mv "$TEMP_FILE" "$conf_file"
    TEMP_FILE=""
    log_success "Updated $conf_file"
}

# ------------------------------------------------------------------------------
# 5. Execution
# ------------------------------------------------------------------------------

# Define paths
QT5_CONF="$CONFIG_HOME/qt5ct/qt5ct.conf"
QT6_CONF="$CONFIG_HOME/qt6ct/qt6ct.conf"

# Update Qt5 Config
# Requirements: standard_dialogs=default, qt5ct-colors.conf
update_qt_config "qt5ct" "$QT5_CONF" "default"

# Update Qt6 Config
# Requirements: standard_dialogs=xdgdesktopportal, qt6ct-colors.conf
update_qt_config "qt6ct" "$QT6_CONF" "xdgdesktopportal"

log_success "Qt configuration sync complete."
