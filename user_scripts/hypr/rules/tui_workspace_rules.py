#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MASTER CONFIGURATION SCHEMA (WORKSPACE RULES)
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "lua"
TARGET_FILE = "~/.config/hypr/edit_here/source/workspace_rules.lua"
APP_TITLE = "Hyprland Workspace Rules"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "1. Layout & Tiling",
    "2. Master & Scrolling",
    "3. Focus & Navigation"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: LAYOUT & TILING
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Enable Smart Gaps",
            key="enable_smart_gaps",
            scope="DEFAULT",        # Root-level Lua variable
            type_="bool",
            default=False,
            group="Global Workspace Rules",
            extended_help="**Smart Gaps**\n\nRemoves gaps and borders when exactly one tiled window is on screen, or when a window is in fullscreen/maximized state. Replicates the popular 'smartgaps' feature."
        ),
        ConfigItem(
            label="Persistent Workspaces (1-10)",
            key="enforce_persistent_1_to_10",
            scope="DEFAULT",        # Root-level Lua variable
            type_="bool",
            default=False,
            group="Global Workspace Rules",
            extended_help="**Persistent Workspaces**\n\nKeep workspaces 1-10 always alive, even when all their windows are closed. Useful for status bars that display a fixed set of workspace indicators."
        ),
        ConfigItem(
            label="Global Default Layout",
            key="layout",
            scope="general",
            type_="cycle",
            default="dwindle",
            options=["dwindle", "master", "scrolling"],
            group="General Layout",
            extended_help="**Layout Override**\n\nSets the global default tiling layout for workspaces without specific overrides."
        ),
        
        # Dwindle Settings Menu
        ConfigItem(
            label="Dwindle Layout Options",
            key="dwindle_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Dwindle Layout Settings"
        ),
        ConfigItem(
            label="Preserve Split",
            key="preserve_split",
            scope="dwindle",
            type_="bool",
            default=True,
            parent_ref="dwindle_menu_id",
            extended_help="**Preserve Split**\n\nKeep the split direction when toggling. KEEP THIS TRUE or the 'toggle split' keybind will not behave as expected."
        ),
        ConfigItem(
            label="Smart Split",
            key="smart_split",
            scope="dwindle",
            type_="bool",
            default=False,
            parent_ref="dwindle_menu_id",
            extended_help="**Smart Split**\n\nIf true, splits based on window dimensions rather than count."
        ),
        ConfigItem(
            label="Smart Resizing",
            key="smart_resizing",
            scope="dwindle",
            type_="bool",
            default=True,
            parent_ref="dwindle_menu_id",
            extended_help="**Smart Resizing**\n\nResize the side that is smaller rather than both sides."
        ),
        ConfigItem(
            label="Force Split Direction",
            key="force_split",
            scope="dwindle",
            type_="int",
            default=0,
            options=[0, 1, 2],
            parent_ref="dwindle_menu_id",
            extended_help="**Force Split**\n\n0 = Follow last window direction\n1 = Always right/down\n2 = Always left/up"
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: MASTER & SCROLLING LAYOUTS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Master Layout Options",
            key="master_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Master Layout Settings"
        ),
        ConfigItem(
            label="New Window Status",
            key="new_status",
            scope="master",
            type_="cycle",
            default="slave",
            options=["slave", "master", "inherit"],
            parent_ref="master_menu_id",
            extended_help="**New Status**\n\nWhere new windows go.\n- `slave`: Go to the slave stack (default)\n- `master`: New windows always become the master\n- `inherit`: Inherit status of the focused window"
        ),
        ConfigItem(
            label="New Windows on Top",
            key="new_on_top",
            scope="master",
            type_="bool",
            default=False,
            parent_ref="master_menu_id",
            extended_help="**New on Top**\n\nInsert new slave windows at the TOP of the stack instead of the bottom."
        ),
        ConfigItem(
            label="Master Size Fraction",
            key="mfact",
            scope="master",
            type_="float",
            default=0.55,
            min_val=0.10,
            max_val=0.90,
            step=0.05,
            parent_ref="master_menu_id",
            extended_help="**Master Fraction (mfact)**\n\nThe fraction of the screen the master pane occupies."
        ),
        ConfigItem(
            label="Orientation",
            key="orientation",
            scope="master",
            type_="cycle",
            default="left",
            options=["left", "right", "top", "bottom", "center"],
            parent_ref="master_menu_id",
            extended_help="**Orientation**\n\nControls which side of the screen the master pane occupies. Use 'center' for a centered master with stacks on both sides."
        ),
        ConfigItem(
            label="Allow Small Split",
            key="allow_small_split",
            scope="master",
            type_="bool",
            default=False,
            parent_ref="master_menu_id",
            extended_help="**Allow Small Split**\n\nAllow adding extra master windows in horizontal-split style when there are multiple masters."
        ),
        ConfigItem(
            label="Special Workspace Scale",
            key="special_scale_factor",
            scope="master",
            type_="float",
            default=0.8,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            parent_ref="master_menu_id",
            extended_help="**Special Scale Factor**\n\nScale factor for windows on special (scratchpad) workspaces when using the master layout."
        ),

        ConfigItem(
            label="Scrolling Layout Options",
            key="scrolling_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Scrolling Layout Settings"
        ),
        ConfigItem(
            label="Fullscreen on Single Column",
            key="fullscreen_on_one_column",
            scope="scrolling",
            type_="bool",
            default=False,
            parent_ref="scrolling_menu_id",
            extended_help="**Fullscreen on One Column**\n\nWhen a workspace has only one column, treat that column as fullscreen (window fills the monitor)."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: FOCUS, BINDS & NAVIGATION
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Focus & Miscellaneous",
            key="misc_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="System Behavior"
        ),
        ConfigItem(
            label="Close Empty Special Workspaces",
            key="close_special_on_empty",
            scope="misc",
            type_="bool",
            default=True,
            parent_ref="misc_menu_id",
            extended_help="**Close Special on Empty**\n\nAuto-close a special workspace (scratchpad) when the last window in it is closed."
        ),
        ConfigItem(
            label="Focus on Activate Request",
            key="focus_on_activate",
            scope="misc",
            type_="bool",
            default=False,
            parent_ref="misc_menu_id",
            extended_help="**Focus on Activate**\n\nAutomatically focus a window that requests activation (e.g., urgency hint or xdg_activation). Can steal focus unexpectedly if True."
        ),
        ConfigItem(
            label="Focus Under Fullscreen Rule",
            key="on_focus_under_fullscreen",
            scope="misc",
            type_="int",
            default=0,
            options=[0, 1, 2],
            parent_ref="misc_menu_id",
            extended_help="**Focus Under Fullscreen**\n\nBehaviour when a window is focused while another is fullscreen.\n0 = Do nothing (new window stays behind)\n1 = New window takes over (unfullscreens current)\n2 = Swap (unfullscreen current, fullscreen the new one)"
        ),

        ConfigItem(
            label="Navigation & Binds",
            key="binds_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="System Behavior"
        ),
        ConfigItem(
            label="Workspace Back and Forth",
            key="workspace_back_and_forth",
            scope="binds",
            type_="bool",
            default=False,
            parent_ref="binds_menu_id",
            extended_help="**Back and Forth**\n\nRe-dispatching to the active workspace switches back to the previously active one."
        ),
        ConfigItem(
            label="Allow Workspace Cycles",
            key="allow_workspace_cycles",
            scope="binds",
            type_="bool",
            default=False,
            parent_ref="binds_menu_id",
            extended_help="**Allow Cycles**\n\nCycling past workspace 1 wraps to the highest-numbered, and vice versa."
        ),
        ConfigItem(
            label="Workspace Center On",
            key="workspace_center_on",
            scope="binds",
            type_="int",
            default=0,
            options=[0, 1, 2],
            parent_ref="binds_menu_id",
            extended_help="**Workspace Center On**\n\nCursor behavior on switch:\n0 = Cursor stays in place\n1 = Moves to center of new window\n2 = Moves to center of monitor"
        ),
        ConfigItem(
            label="Hide Special on Change",
            key="hide_special_on_workspace_change",
            scope="binds",
            type_="bool",
            default=False,
            parent_ref="binds_menu_id",
            extended_help="**Hide Special on Change**\n\nHide open special workspaces (scratchpads) when you switch to a different normal workspace."
        ),
        ConfigItem(
            label="Movefocus Cycles Fullscreen",
            key="movefocus_cycles_fullscreen",
            scope="binds",
            type_="bool",
            default=True,
            parent_ref="binds_menu_id",
            extended_help="**Movefocus Cycles Fullscreen**\n\nAllow 'movefocus' to wrap around into/out of fullscreen windows."
        ),
        ConfigItem(
            label="Monitor Edge Fallback",
            key="window_direction_monitor_fallback",
            scope="binds",
            type_="bool",
            default=True,
            parent_ref="binds_menu_id",
            extended_help="**Monitor Fallback**\n\nMoving a window past the edge of a monitor moves it to the adjacent monitor."
        ),
        
        # Factory Reset
        ConfigItem(
            label="Factory Reset Everything",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles & Actions",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Factory Reset**\n\nReverts all workspace, layout, and behavior rules back to their default state."
        ),
    ]
}
