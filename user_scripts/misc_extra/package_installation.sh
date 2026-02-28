#!/usr/bin/env bash
# This script installs ALL PACKAGES, you can inspect this script manually to remove/add anything you might want.
# --------------------------------------------------------------------------
# Arch Linux / Hyprland / UWSM - Elite System Installer (v3.1 - Smart Fallback)
# --------------------------------------------------------------------------

# Group 1: dusky_update
pkgs_productivity=(
  "wl-clip-persist"

  # nemo
  "nemo" "nemo-fileroller" "file-roller" "gvfs" "gvfs-smb" "gvfs-mtp" "gvfs-gphoto2" "gvfs-google" "gvfs-nfs" "gvfs-afc" "gvfs-dnssd" "ffmpegthumbnailer" "webp-pixbuf-loader" "poppler-glib" "libgsf" "gnome-epub-thumbnailer" "resvg" "nemo-python" "nemo-compare" "meld" "nemo-media-columns" "nemo-audio-tab" "nemo-image-converter" "nemo-emblems" "nemo-repairer" "nemo-share" "python-gobject" "dconf-editor" "xreader" "gst-libav" "gst-plugins-good" "nemo-fileroller" "nemo-audio-tab" "nemo-compare" "nemo-pastebin"
# "nemo-terminal"
)

# --------------------------------------------------------------------------
# --- 2. ENGINE (Optimized) ---
# --------------------------------------------------------------------------

# 1. Root Check
if [[ $EUID -ne 0 ]]; then
  printf "Elevating privileges...\n"
  exec sudo "$0" "$@"
fi

# 2. Safety & Aesthetics
set -u
set -o pipefail

BOLD=$(tput bold)
GREEN=$(tput setaf 2)
YELLOW=$(tput setaf 3)
RED=$(tput setaf 1)
CYAN=$(tput setaf 6)
RESET=$(tput sgr0)

# 3. Core Logic
install_group() {
  local group_name="$1"
  shift
  local pkgs=("$@")

  [[ ${#pkgs[@]} -eq 0 ]] && return

  printf "\n${BOLD}${CYAN}:: Processing Group: %s${RESET}\n" "$group_name"

  # STRATEGY A: Batch Install
  if pacman -S --needed --noconfirm "${pkgs[@]}"; then
    printf "${GREEN} [OK] Batch installation successful.${RESET}\n"
    return 0
  fi

  # STRATEGY B: Fallback Individual Install (Smart)
  printf "\n${YELLOW} [!] Batch transaction failed. Retrying individually...${RESET}\n"

  local fail_count=0

  for pkg in "${pkgs[@]}"; do
    # Try 1: Auto-install (Silent)
    # If this works, it means there was no conflict for THIS specific package.
    if pacman -S --needed --noconfirm "$pkg" >/dev/null 2>&1; then
      printf "  ${GREEN}[+] Installed:${RESET} %s\n" "$pkg"

    # Try 2: Interactive (Verbose)
    # If Auto failed, it's likely a conflict (e.g., tldr vs tealdeer).
    # We run without --noconfirm so you can intervene.
    else
      printf "  ${YELLOW}[?] Intervention Needed:${RESET} %s\n" "$pkg"
      if pacman -S --needed "$pkg"; then
        printf "  ${GREEN}[+] Installed (Manual):${RESET} %s\n" "$pkg"
      else
        printf "  ${RED}[X] Not Found / Failed:${RESET} %s\n" "$pkg"
        ((fail_count++))
      fi
    fi
  done

  if [[ $fail_count -gt 0 ]]; then
    printf "${YELLOW} [!] Group completed with %d failures.${RESET}\n" "$fail_count"
  else
    printf "${GREEN} [OK] Recovery successful. All packages installed.${RESET}\n"
  fi
}

# --- 3. EXECUTION ---

printf "${BOLD}:: Initializing Arch Keyring...${RESET}\n"
pacman-key --init
pacman-key --populate archlinux

printf "\n${BOLD}:: Full System Upgrade...${RESET}\n"
sleep 0.1 || printf "${YELLOW}[!] Upgrade skipped or failed.${RESET}\n"

# Execute Groups
install_group "Graphics & Drivers" "${pkgs_graphics[@]}"
install_group "Hyprland Core" "${pkgs_hyprland[@]}"
install_group "GUI Appearance" "${pkgs_appearance[@]}"
install_group "Desktop Experience" "${pkgs_desktop[@]}"
install_group "Audio & Bluetooth" "${pkgs_audio[@]}"
install_group "Filesystem Tools" "${pkgs_filesystem[@]}"
install_group "Networking" "${pkgs_network[@]}"
install_group "Terminal & CLI" "${pkgs_terminal[@]}"
install_group "Development" "${pkgs_dev[@]}"
install_group "Multimedia" "${pkgs_multimedia[@]}"
install_group "System Admin" "${pkgs_sysadmin[@]}"
install_group "Gnome Utilities" "${pkgs_gnome[@]}"
install_group "dusky_update" "${pkgs_productivity[@]}"

printf "\n${BOLD}${GREEN}:: INSTALLATION COMPLETE ::${RESET}\n"
printf "Packages Installed.\n"
