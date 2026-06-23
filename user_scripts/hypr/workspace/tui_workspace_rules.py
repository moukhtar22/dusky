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
APP_TITLE = "Workspace Rules"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# =============================================================================
# 3. TABS DEFINITION (STRICTLY ONE WORD)
# =============================================================================
TABS = [
    "General",
    "Workspaces",
    "Dwindle",
    "Master",
    "Scrolling",
    "Presets"
]

# =============================================================================
# 4. DYNAMIC WORKSPACE GENERATOR (TAB 1)
# =============================================================================
# We dynamically build the workspace 1-10 folders to keep the schema clean
# while giving you absolute per-workspace granularity over Section 1 in the Lua!
WORKSPACE_ITEMS = []
for i in range(1, 11):
    WORKSPACE_ITEMS.extend([
        ConfigItem(
            label=f"Workspace {i} Settings",
            key=f"ws_{i}_menu",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=False,
            group="Workspaces"
        ),
        ConfigItem(
            label="Layout",
            key="layout",
            scope=f"workspace_rule/{i}",
            type_="cycle",
            default="dwindle",
            options=["dwindle", "master", "scrolling", "monocle"],
            parent_ref=f"ws_{i}_menu",
            extended_help=f"**Workspace {i} Layout**\n\nForces a specific layout for this workspace. Monocle will make windows take up the entire available space."
        ),
        ConfigItem(
            label="Persistent",
            key="persistent",
            scope=f"workspace_rule/{i}",
            type_="bool",
            default=False,
            parent_ref=f"ws_{i}_menu",
            extended_help=f"**Workspace {i} Persistence**\n\nKeeps this workspace alive even when all windows inside it are closed."
        )
    ])


# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: GENERAL & BEHAVIOR
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Global Default Layout",
            key="layout",
            scope="general",
            type_="cycle",
            default="dwindle",
            options=["dwindle", "master", "scrolling", "monocle"],
            group="Layout",
            extended_help="**Layout Override**\n\nSets the global default tiling layout for workspaces without specific overrides."
        ),
        
        # --- FOCUS & BEHAVIOR FOLDER ---
        ConfigItem(
            label="Focus & Miscellaneous",
            key="misc_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Behavior"
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
            default=True,
            parent_ref="misc_menu_id",
            extended_help="**Focus on Activate**\n\nAutomatically focus a window that requests activation (e.g., urgency hint or xdg_activation)."
        ),
        ConfigItem(
            label="Focus Under Fullscreen Rule",
            key="on_focus_under_fullscreen",
            scope="misc",
            type_="int",
            default=2,
            options=[0, 1, 2],
            parent_ref="misc_menu_id",
            extended_help="**Focus Under Fullscreen**\n\nBehaviour when a window is focused while another is fullscreen.\n0 = Do nothing (new window stays behind)\n1 = New window takes over (unfullscreens current)\n2 = Swap (unfullscreen current, fullscreen the new one)"
        ),

        # --- NAVIGATION FOLDER ---
        ConfigItem(
            label="Navigation & Binds",
            key="binds_menu_id",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=False,
            group="Behavior"
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
    ],

    # -------------------------------------------------------------------------
    # TAB 1: WORKSPACES (Granular Settings Injected Dynamically)
    # -------------------------------------------------------------------------
    1: WORKSPACE_ITEMS,

    # -------------------------------------------------------------------------
    # TAB 2: DWINDLE LAYOUT
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Force Split Direction",
            key="force_split",
            scope="dwindle",
            type_="int",
            default=0,
            options=[0, 1, 2],
            group="Dwindle",
            extended_help="**Force Split**\n\n0 = Split follows mouse (last window direction)\n1 = Always split right/down\n2 = Always split left/up"
        ),
        ConfigItem(
            label="Preserve Split",
            key="preserve_split",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle",
            extended_help="**Preserve Split**\n\nIf enabled, the split (side/top) will not change regardless of what happens to the container. *Required for togglesplit dispatcher to work correctly.*"
        ),
        ConfigItem(
            label="Smart Split",
            key="smart_split",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle",
            extended_help="**Smart Split**\n\nIf enabled, allows a more precise control over the window split direction based on the cursor's position within conceptual triangles."
        ),
        ConfigItem(
            label="Smart Resizing",
            key="smart_resizing",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle",
            extended_help="**Smart Resizing**\n\nIf enabled, resizing direction will be determined by the mouse's position on the window (nearest to which corner). Else, it relies purely on tiling position."
        ),
        ConfigItem(
            label="Permanent Direction Override",
            key="permanent_direction_override",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle",
            extended_help="**Permanent Direction Override**\n\nIf enabled, makes a preselected direction persist until turned off or a non-direction is specified."
        ),
        ConfigItem(
            label="Special Workspace Scale",
            key="special_scale_factor",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            group="Dwindle",
            extended_help="**Special Scale Factor**\n\nScale factor for windows located on special workspaces (scratchpads)."
        ),
        ConfigItem(
            label="Split Width Multiplier",
            key="split_width_multiplier",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.5,
            max_val=3.0,
            step=0.1,
            group="Dwindle",
            extended_help="**Split Width Multiplier**\n\nUseful for ultrawide monitors where a window's width remains greater than its height even after multiple splits."
        ),
        ConfigItem(
            label="Use Active For Splits",
            key="use_active_for_splits",
            scope="dwindle",
            type_="bool",
            default=True,
            group="Dwindle",
            extended_help="**Use Active For Splits**\n\nWhether to prefer the active window or the mouse position when calculating splits."
        ),
        ConfigItem(
            label="Default Split Ratio",
            key="default_split_ratio",
            scope="dwindle",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.9,
            step=0.1,
            group="Dwindle",
            extended_help="**Default Split Ratio**\n\nThe ratio on window open. 1.0 means an even 50/50 split."
        ),
        ConfigItem(
            label="Split Bias",
            key="split_bias",
            scope="dwindle",
            type_="int",
            default=0,
            options=[0, 1],
            group="Dwindle",
            extended_help="**Split Bias**\n\nSpecifies which window receives the split ratio.\n0 = Directional (the top or left window)\n1 = The current active window"
        ),
        ConfigItem(
            label="Precise Mouse Move",
            key="precise_mouse_move",
            scope="dwindle",
            type_="bool",
            default=False,
            group="Dwindle",
            extended_help="**Precise Mouse Move**\n\nWhen using the bindm 'movewindow' dispatcher, this will drop the window more precisely depending on the exact mouse coordinates."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: MASTER LAYOUT
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="New Window Status",
            key="new_status",
            scope="master",
            type_="cycle",
            default="slave",
            options=["slave", "master", "inherit"],
            group="Master",
            extended_help="**New Status**\n\n- `slave`: Go to the slave stack (default)\n- `master`: New windows always become the master\n- `inherit`: Inherit status of the focused window"
        ),
        ConfigItem(
            label="New Windows on Top",
            key="new_on_top",
            scope="master",
            type_="bool",
            default=False,
            group="Master",
            extended_help="**New on Top**\n\nInsert new windows at the TOP of the stack instead of the bottom."
        ),
        ConfigItem(
            label="New on Active",
            key="new_on_active",
            scope="master",
            type_="cycle",
            default="none",
            options=["none", "before", "after"],
            group="Master",
            extended_help="**New on Active**\n\nPlace new windows relative to the currently focused window (`before` or `after`). If `none`, behaves according to 'New Windows on Top'."
        ),
        ConfigItem(
            label="Orientation",
            key="orientation",
            scope="master",
            type_="cycle",
            default="left",
            options=["left", "right", "top", "bottom", "center"],
            group="Master",
            extended_help="**Orientation**\n\nControls which side the master pane occupies. 'center' uses a central master with stacks on both left and right sides."
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
            group="Master",
            extended_help="**Master Fraction (mfact)**\n\nThe percentage fraction (0.0 - 1.0) of the screen the master pane occupies."
        ),
        ConfigItem(
            label="Allow Small Split",
            key="allow_small_split",
            scope="master",
            type_="bool",
            default=False,
            group="Master",
            extended_help="**Allow Small Split**\n\nAllow adding extra master windows in horizontal-split style when there are multiple masters."
        ),
        ConfigItem(
            label="Slave Count For Center",
            key="slave_count_for_center_master",
            scope="master",
            type_="int",
            default=2,
            min_val=0,
            max_val=10,
            step=1,
            group="Master",
            extended_help="**Slave Count For Center**\n\nWhen using orientation=center, the master window is only centered when at least this many slave windows are open. Set to 0 to always center."
        ),
        ConfigItem(
            label="Center Master Fallback",
            key="center_master_fallback",
            scope="master",
            type_="cycle",
            default="left",
            options=["left", "right", "top", "bottom"],
            group="Master",
            extended_help="**Center Master Fallback**\n\nThe orientation to use when the slave count is lower than the required threshold for centering."
        ),
        ConfigItem(
            label="Smart Resizing",
            key="smart_resizing",
            scope="master",
            type_="bool",
            default=True,
            group="Master",
            extended_help="**Smart Resizing**\n\nResizing direction determined by nearest corner to mouse position."
        ),
        ConfigItem(
            label="Drop at Cursor",
            key="drop_at_cursor",
            scope="master",
            type_="bool",
            default=True,
            group="Master",
            extended_help="**Drop at Cursor**\n\nDragging and dropping windows puts them at the exact cursor position rather than the ends of the stack."
        ),
        ConfigItem(
            label="Always Keep Position",
            key="always_keep_position",
            scope="master",
            type_="bool",
            default=False,
            group="Master",
            extended_help="**Always Keep Position**\n\nKeeps the master window locked in its configured position even when there are absolutely no slave windows open."
        ),
        ConfigItem(
            label="Special Workspace Scale",
            key="special_scale_factor",
            scope="master",
            type_="float",
            default=1.0,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            group="Master",
            extended_help="**Special Scale Factor**\n\nScale factor for windows on special (scratchpad) workspaces when using the master layout."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: SCROLLING LAYOUT
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Direction",
            key="direction",
            scope="scrolling",
            type_="cycle",
            default="right",
            options=["left", "right", "up", "down"],
            group="Scrolling",
            extended_help="**Direction**\n\nThe direction in which new windows appear and the entire tape layout scrolls."
        ),
        ConfigItem(
            label="Fullscreen on Single Column",
            key="fullscreen_on_one_column",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling",
            extended_help="**Fullscreen on Single Column**\n\nWhen a workspace has only one column, treat that column as fullscreen (window fills the entire monitor)."
        ),
        ConfigItem(
            label="Column Width",
            key="column_width",
            scope="scrolling",
            type_="float",
            default=0.5,
            min_val=0.1,
            max_val=1.0,
            step=0.1,
            group="Scrolling",
            extended_help="**Column Width**\n\nThe default width of a new column (percentage of monitor 0.1 - 1.0)."
        ),
        ConfigItem(
            label="Focus Fit Method",
            key="focus_fit_method",
            scope="scrolling",
            type_="int",
            default=1,
            options=[0, 1],
            group="Scrolling",
            extended_help="**Focus Fit Method**\n\nWhen a column is focused, how should it be brought into view?\n0 = Center it completely\n1 = Fit it onto the screen"
        ),
        ConfigItem(
            label="Follow Focus",
            key="follow_focus",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling",
            extended_help="**Follow Focus**\n\nWhen a window is focused via other means, the layout automatically scrolls to bring it into view."
        ),
        ConfigItem(
            label="Follow Min Visible",
            key="follow_min_visible",
            scope="scrolling",
            type_="float",
            default=0.4,
            min_val=0.0,
            max_val=1.0,
            step=0.1,
            group="Scrolling",
            extended_help="**Follow Min Visible**\n\nRequire at least this fraction (0.0 - 1.0) of a window to be visible for focus to follow automatically."
        ),
        ConfigItem(
            label="Explicit Column Widths",
            key="explicit_column_widths",
            scope="scrolling",
            type_="string",
            default="0.333, 0.5, 0.667, 1.0",
            group="Scrolling",
            extended_help="**Explicit Column Widths**\n\nA comma-separated list of preconfigured width breakpoints used when resizing columns."
        ),
        ConfigItem(
            label="Wrap Focus",
            key="wrap_focus",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling",
            extended_help="**Wrap Focus**\n\nAllows the focus dispatcher to wrap around from the end of the tape back to the beginning."
        ),
        ConfigItem(
            label="Wrap Swap Column",
            key="wrap_swapcol",
            scope="scrolling",
            type_="bool",
            default=True,
            group="Scrolling",
            extended_help="**Wrap Swap Column**\n\nAllows swapping a column at the very left to wrap around and be placed at the very right of the tape."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: PRESETS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Factory Reset Everything",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Actions",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Factory Reset**\n\nReverts all workspace, layout, and behavior rules back to their default state."
        ),
    ]
}
