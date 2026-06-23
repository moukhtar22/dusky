-- HYPRLAND SPECIFIC VARIABLES ARE TO BE SET IN ~/.config/uwsm/env-hyprland
-- and compositor indifferent variables in ~/.config/uwsm/env

hl.config({
    -- this one environment variable is set here to fix a warning caused by hyprland on boot.
    env = {
        [[XDG_CURRENT_DESKTOP,Hyprland]]
    }
})
