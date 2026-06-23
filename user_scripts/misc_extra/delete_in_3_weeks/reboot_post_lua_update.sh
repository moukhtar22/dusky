#!/bin/bash

# 1. Define the exact state file location safely
STATE_FILE="$HOME/.config/dusky/settings/reboot_post_lua"

# 2. State Check: Silent exit if already processed
if [[ -f "$STATE_FILE" ]]; then
    exit 0
fi

# 3. Action Functions
perform_reboot() {
    mkdir -p "$(dirname "$STATE_FILE")"
    touch "$STATE_FILE"
    
    echo -e "\n\e[1;32mInitiating system reboot...\e[0m\n" > /dev/tty
    systemctl reboot
    exit 0
}

cancel_reboot() {
    echo -e "\n\e[1;33mReboot cancelled. Please reboot manually later to apply Lua changes.\e[0m\n" > /dev/tty
    exit 0
}

# Ensure we have a valid terminal to interact with, otherwise fallback to stdout/stderr
if [[ -c /dev/tty ]]; then
    TERM_TARGET="/dev/tty"
else
    TERM_TARGET="/dev/stderr"
fi

# 4. Visual Notification: Force output directly to the terminal ($TERM_TARGET)
# This completely bypasses the update_dusky.sh log piping so it appears instantly.
{
    echo -e "\n\n"
    echo -e "\e[1;97;41m======================================================================\e[0m"
    echo -e "\e[1;97;41m|                                                                    |\e[0m"
    echo -e "\e[1;97;41m|               LUA CHANGES REQUIRE A RESTART                        |\e[0m"
    echo -e "\e[1;97;41m|                                                                    |\e[0m"
    echo -e "\e[1;97;41m======================================================================\e[0m"
    echo -e "\n\e[1;93mThe system will automatically reboot in \e[1;91m2 MINUTES\e[1;93m if there is no response.\e[0m\n"
} > "$TERM_TARGET"

# 5. Flush the input buffer specifically on the terminal
read -t 0.1 -n 10000 discard_input < "$TERM_TARGET" 2>/dev/null || true

# 6. Prompt User (120-second timeout)
# By redirecting both input (<) and error/prompt output (2>) to $TERM_TARGET,
# we force the script to ask the user on the screen rather than hanging in the log pipe.
if read -t 120 -p $'\e[1;36mWould you like to reboot now? [Y/n]: \e[0m' choice < "$TERM_TARGET" 2> "$TERM_TARGET"; then
    case "$choice" in
        [Nn]* )
            cancel_reboot
            ;;
        * )
            perform_reboot
            ;;
    esac
else
    exit_status=$?
    
    if [[ $exit_status -gt 128 ]]; then
        echo -e "\n\n\e[1;91m*** TIMEOUT REACHED: No response received. Auto-rebooting... ***\e[0m" > "$TERM_TARGET"
        perform_reboot
    else
        echo -e "\n\n\e[1;31mWarning: Unable to read user input (terminal closed). Proceeding with required auto-reboot...\e[0m" > "$TERM_TARGET"
        perform_reboot
    fi
fi
