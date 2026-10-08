#!/usr/bin/env bash
#d: Apply comprehensive desktop theming (GTK 3/4, Qt 5/6, KDE, Icons, Fonts) to root GUI apps

set -euo pipefail

# 1. Ensure we are root
if [[ $EUID -ne 0 ]]; then
   echo "Escalating to root..."
   exec sudo "$0" "$@"
fi

# 2. Get the Real User and Home Directory
REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" ]]; then
    echo "Error: Run this via sudo from your normal user."
    exit 1
fi

REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
ROOT_HOME=$(getent passwd 0 | cut -d: -f6)
echo "================================================================="
echo "Syncing universal desktop themes from: $REAL_HOME ($REAL_USER) -> $ROOT_HOME"
echo "================================================================="

# 3. Prepare Target Directories in root's home
mkdir -p "$ROOT_HOME/.config"
mkdir -p "$ROOT_HOME/.local/share"

# 4. Helper function to replace targets with symlinks
link_item() {
    local src="$1"
    local dst="$2"
    local desc="$3"

    # Themes can be generated after setup. Keep that directory linked even
    # before the first Matugen run, so root can resolve the GTK3 theme wrappers.
    if [[ -e "$src" || -L "$src" || "${4:-}" == deferred ]]; then
        rm -rf "$dst"
        ln -sfnT "$src" "$dst"
        echo -e " \e[32m✔\e[0m Linked $desc ($src -> $dst)"
    else
        echo -e " \e[33m-\e[0m Skipping $desc (Not present at $src)"
    fi
}

# 5. Sync .config Directories & Files (GTK, Qt, KDE, Kvantum, Fontconfig, dconf, xsettingsd)
echo -e "\n\e[1m1. Processing ~/.config Components:\e[0m"
CONFIG_ITEMS=(
    "gtk-3.0"
    "gtk-4.0"
    "gtk-2.0"
    "qt5ct"
    "qt6ct"
    "kdeglobals"
    "Kvantum"
    "fontconfig"
    "xsettingsd"
    "dconf"
)

for item in "${CONFIG_ITEMS[@]}"; do
    link_item "$REAL_HOME/.config/$item" "$ROOT_HOME/.config/$item" "$item"
done

# 6. Sync .local/share Components (Icons, Color Schemes, GtkSourceView, Themes)
echo -e "\n\e[1m2. Processing ~/.local/share Components:\e[0m"
DATA_ITEMS=(
    "icons"
    "themes"
    "color-schemes"
    "gtksourceview-3.0"
    "gtksourceview-4"
    "gtksourceview-5"
)

for item in "${DATA_ITEMS[@]}"; do
    if [[ "$item" == themes ]]; then
        link_item "$REAL_HOME/.local/share/$item" "$ROOT_HOME/.local/share/$item" "local/share/$item" deferred
    else
        link_item "$REAL_HOME/.local/share/$item" "$ROOT_HOME/.local/share/$item" "local/share/$item"
    fi
done

# 7. Sync Root-Level Legacy Paths
echo -e "\n\e[1m3. Processing Root-Level Legacy Paths:\e[0m"
link_item "$REAL_HOME/.icons" "$ROOT_HOME/.icons" "~/.icons"
link_item "$REAL_HOME/.themes" "$ROOT_HOME/.themes" "~/.themes"
link_item "$REAL_HOME/.gtkrc-2.0" "$ROOT_HOME/.gtkrc-2.0" "~/.gtkrc-2.0"

echo -e "\n\e[32m=================================================================\e[0m"
echo -e "\e[32m✔ Success. Root GUI application theming is completely synchronized.\e[0m"
echo -e "\e[32m=================================================================\e[0m\n"
