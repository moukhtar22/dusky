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
HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"

update_qt_config() {
    local app_name="$1"       # e.g., qt5ct
    local conf_file="$2"      # Full path to config
    local dialog_val="$3"     # default or xdgdesktopportal
    local colors_file="$4"    # filename of the colors conf

    log_info "Processing configuration for ${BOLD}${app_name}${RESET}..."

    # Ensure config and colors directories exist
    local config_dir="$HOME/.config/$app_name"
    local colors_dir="$config_dir/colors"
    mkdir -p "$config_dir" "$colors_dir"

    # Pre-link matugen colors if generated colors already exist
    local gen_colors="$HOME/.config/matugen/generated/$colors_file"
    if [[ -f "$gen_colors" ]]; then
        ln -nfs "$gen_colors" "$colors_dir/matugen.conf"
    fi

    # Create a temporary file for atomic writing
    TEMP_FILE=$(mktemp)

    # --------------------------------------------------------------------------
    # STEP A: Generate the enforced header
    # Dynamically expand $HOME for the current user executing the setup script.
    # Qt configuration files require absolute paths and do not expand shell variables.
    # --------------------------------------------------------------------------
    {
        printf "[Appearance]\n"
        printf "color_scheme_path=%s/.config/%s/colors/matugen.conf\n" "$HOME" "$app_name"
        printf "custom_palette=true\n"
        printf "icon_theme=Papirus-Dark\n"
        printf "standard_dialogs=%s\n" "$dialog_val"
        printf "style=Fusion\n\n"
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

            # Skip the specific [Appearance] section header
            /^\[Appearance\]/ { next }

            # Check if line matches "key=value" format
            /=/ {
                split($0, map, "=")
                key = map[1]
                # If this key is one we are managing, skip it (we wrote it at the top)
                if (key in keys) { next }
            }

            # Print everything else (Fonts, Interface, other Appearance keys)
            { print }
        ' "$conf_file" >> "$TEMP_FILE"
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
    log_success "Updated $conf_file"
}

# ------------------------------------------------------------------------------
# 5. Execution
# ------------------------------------------------------------------------------

# Define paths
QT5_CONF="$HOME/.config/qt5ct/qt5ct.conf"
QT6_CONF="$HOME/.config/qt6ct/qt6ct.conf"

# Update Qt5 Config
# Requirements: standard_dialogs=default, qt5ct-colors.conf
update_qt_config "qt5ct" "$QT5_CONF" "default" "qt5ct-colors.conf"

# Update Qt6 Config
# Requirements: standard_dialogs=xdgdesktopportal, qt6ct-colors.conf
update_qt_config "qt6ct" "$QT6_CONF" "xdgdesktopportal" "qt6ct-colors.conf"

log_success "Qt configuration sync complete."
