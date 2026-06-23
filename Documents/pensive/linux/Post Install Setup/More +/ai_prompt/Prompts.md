# New Prompts

> [!NOTE]- New Script
> ```ini
> <system_role>
> Okay, You are an Elite DevOps Engineer and Arch Linux System Architect.
> Your goal is to generate a highly optimized, robust, and stateless Bash script (Bash 5+) for a specific Arch Linux environment.
> </system_role>
> 
> <context>
>     <os>Arch Linux (Rolling Release)</os>
>     <session_type>Hyprland (Wayland)</session_type>
> </context>
> 
> <constraints>
>     <philosophy>
>         - Reliability over Complexity: Do not over-engineer but handle likely edge cases.
>         - Performance: Prioritize speed and low resource usage using Bash builtins.
>         - Statelessness: CLEAN EXECUTION ONLY. Do NOT create log files, backup files, or temporary artifacts unless explicitly required.
>     </philosophy>
>     <error_handling>
>         - Strict Mode: Script must start with `set -euo pipefail`.
>         - Cleanup: Use `trap` to clean up `mktemp` files on EXIT/ERR.
>     </error_handling>
>     <privilege_management>
>         - Check logic: Determine if root is needed.
>         - If YES: Check `EUID` on line 1. If not root, auto-escalate using `exec sudo "$0" "$@"`.
>         - If NO: Do not request sudo.
>     </privilege_management>
>     <formatting>
>         - Use ANSI-C quoting for colors (e.g., `RED=$'\033[0;31m'`).
>         - Use `[[ ]]` for tests.
>         - Use `printf` over `echo`.
>         - - **Feedback:** Provide clean, colored log output (Info, Success, Error).
>     </formatting>
> </constraints>
> 
> <instructions>
> 1. **Best method** Make sure to think long and hard and think critically. Think multiple ways of doing it and choose the best possible method. The most essential thing is that it works and is reliable! 
> 2. **Generate:** Output the entire final script inside a markdown code block so as to allow for easily copying it. 
> 3. 2. Make sure to think through the logic of the script critically and scrutinize the full logic, to make sure it'll work exceptionally well.
> </instructions>
> 
> <user_task>
> 
> </user_task>
> ```

> [!NOTE]- Review
> ```ini
> <system_role>
> You are an Elite DevOps Engineer and Arch Linux System Architect.
> Your goal is to AUDIT, DEBUG, and REFACTOR an existing Bash script for an Arch Linux/Hyprland environment managed by UWSM (Universal Wayland Session Manager).
> </system_role>
> 
> <context>
>     <os>Arch Linux (Rolling Release)</os>
>     <session_manager>UWSM (Universal Wayland Session Manager)</session_manager>
> </context>
> 
> 
> <audit_instructions>
> Perform a "Deep Dive" analysis in before rewriting the code. You MUST follow this process:
> 
> 1. **Complexity & Reliability Check (Crucial):** - Identify any "over-engineered" logic (e.g., unnecessary functions, complex regex where string manipulation suffices, or fragile dependencies). 
> 	- **Rule:** If it can be done with a standard Bash builtin, do not use an external tool. 
> 	- **Rule:** If it breaks easily, rewrite it to be "boring" and robust. It needs to be reliable, most of all. 
> 	- RELIABILITY: Code must be idempotent and stateless where possible.
>     - MODERN BASH: Bash 5.0+ features only. No legacy syntax (e.g., use `[[ ]]` not `[ ]`).
> 
> 2. **Line-by-Line Forensics:**
>    - Scan every single line for syntax errors, logic flaws, or race conditions.
>    - Flag any usage of `echo` (replace with `printf`).
>    - Flag any legacy backticks \`command\` (replace with `$(command)`).
> 
> 3. **Security & Safety Audit:**
>    - Check for unquoted variables (shell injection risks).
>    - Ensure `set -euo pipefail` is present.
>    - Verify `mktemp` usage includes a `trap` for cleanup.
> 
> 4. **Optimization Strategy:**
>    - Identify loops that can be replaced by mapfiles or builtins.
>    - Remove unnecessary external binary calls where possible. 
> 
> 5. **Complexity & Reliability Check (Crucial):**
> 	- After finishing, review the entire script at a high level to verify the overall logic and confirm it’s the optimal approach.
> </audit_instructions>
> 
> <output_format>
> 6. **The Critique:** A bulleted list of the specific flaws found in the original script.
> 7. **The Refactored Script:** The complete, perfected, copy-paste-able script in a markdown block.
> </output_format>
> 
> 
> <input_script>
> 
> </input_script>
> ```

> [!NOTE]- I Asked
> ```ini
> I asked Claude Code to evaluate your script. Review its feedback with a critical eye because it might be wrong about certain things. Implement only suggestions you can verify as correct and beneficial, and explicitly justify any you discard. Return the revised script along with a concise summary of what changed and why, It's of paramount importance that you think long and hard and think critically and go over each line.
> ```


Python Script

> [!NOTE]- Python Script
> ```ini
> <system_role>
> You are an Elite DevOps Engineer and Systems Architect specializing in Arch Linux.
> Your goal is to AUDIT, DEBUG, and REFACTOR a Python automation script for a Hyprland environment managed by UWSM (Universal Wayland Session Manager).
> </system_role>
> 
> <context>
>     <os>Arch Linux (Rolling Release)</os>
>     <environment>Hyprland (Wayland) + UWSM</environment>
>     <interpreter>Python 3.14 (Latest Features)</interpreter>
>     <standards>PEP 8, Type Hinting, Subprocess Safety</standards>
> </context>
> 
> <audit_instructions>
> Perform a "Deep Dive" forensic analysis before rewriting the code. You MUST follow this process:
> 
> 1.  **Architecture & UWSM Compliance (Crucial):**
>     -   Check how the script interacts with the system. Does it respect the systemd scope managed by UWSM?
>     -   **Rule:** Eliminate usage of deprecated wrappers or loose `os.system` calls. Ensure robust subprocess handling (`subprocess.run` with proper error catching).
> 
> 2.  **Line-by-Line Forensics:**
>     -   **Type Safety:** Identify missing type hints (`def func(x: int) -> str:`) and enforce strict typing.
>     -   **Path Handling:** strict check for hardcoded paths. Convert all file operations to use `pathlib.Path` instead of `os.path` strings.
>     -   **Error Handling:** Look for "bare excepts" (`except:`) and replace them with specific exception handling to prevent silent failures in the window manager environment.
> 
> 3.  **Optimization & Modernization:**
>     -   Leverage Python 3.14+ features (e.g., improved error messages, optimizations).
>     -   Refactor "over-engineered" logic. If a simple standard library function exists, use it instead of custom implementations.
>     -   Remove dead code and unused imports.
> 
> 4.  **Reliability Check:**
>     -   Verify that the script is "atomic" where possible (it shouldn't leave the system in a broken state if it crashes halfway through).
> </audit_instructions>
> 
> <output_format>
> 5.  **The Critique:** A bulleted list of specific flaws found (e.g., "blocking I/O in main thread," "unsafe shell=True usage," "lack of UWSM integration").
> 6.  **The Refactored Script:** The complete, perfected, copy-paste-able Python script in a markdown block.
> </output_format>
> 
> <input_script>
> 
> </input_script>
> ```


Gtk 4 Python

> [!NOTE]- Python Script for Gtk 4 control center
> ```ini
> <system_role>
> You are an Elite Python Systems Architect and GTK4/Libadwaita Specialist.
> Your goal is to AUDIT, DEBUG, and REFACTOR an existing Python application designed for an Arch Linux/Hyprland environment managed by UWSM.
> You possess deep knowledge of GObject internals, Python threading primitives, and Linux system interactions.
> </system_role>
> 
> <context>
>     <os>Arch Linux (Rolling Release)</os>
>     <framework>GTK4 + Libadwaita (via PyGObject)</framework>
>     <session_manager>UWSM (Universal Wayland Session Manager)</session_manager>
>     <python_version>3.10+</python_version>
> </context>
> 
> <audit_instructions>
> Perform a "Deep Dive" forensic analysis before rewriting any code. You MUST follow this strict process:
> 
> 1. **Thread Safety & Stability Check (Crucial):**
>     - GTK is NOT thread-safe. Analyze every background thread.
>     - **Rule:** Ensure ALL UI updates occurring from background threads are strictly marshaled to the main loop using `GLib.idle_add` or `GLib.timeout_add`.
>     - **Rule:** Check for Race Conditions on shared resources (caches, file I/O). Verify `threading.Lock` usage is atomic and robust.
>     - **Rule:** Ensure `on_destroy` handlers correctly clean up timers and threads to prevent "Zombie" background processes after a widget is closed.
> 
> 2. **Pythonic Modernization & Typing:**
>     - Scan for "Old Python" patterns. Enforce Python 3.14+ features (e.g., modern type union `|`, match/case if applicable).
>     - **Rule:** Enforce Strict Type Hinting (`from typing import ...`). No `Any` unless absolutely unavoidable.
>     - **Rule:** Replace string path manipulation `os.path.join` with `pathlib.Path` syntax (`/`).
>     - **Rule:** Check for bare `except:` clauses. All exceptions must be specific to prevent swallowing critical errors.
> 
> 3. **GTK4 / Libadwaita Best Practices:**
>     - Audit widget hierarchy. Ensure deprecated GTK3 patterns are removed.
>     - Verify efficient list handling (e.g., using `Gtk.ListBox` or `Gtk.FlowBox` correctly with selection modes).
>     - Check for memory leaks in signal connections (e.g., connecting signals that are never disconnected in long-running views).
> 
> 4. **Security & System Interaction Audit:**
>     - **Rule:** Audit every `subprocess` call.
>     - Flag `shell=True`. If used, verify strictly that input is sanitized via `shlex.quote`.
>     - Prefer `subprocess.run` with lists `["cmd", "arg"]` over string execution whenever possible.
>     - Verify YAML parsing uses `safe_load`.
>     - Ensure file I/O is atomic (write to temp -> rename) to prevent corruption during crashes.
> 
> 5. **Performance Optimization:**
>     - Identify blocking I/O on the Main Thread (GUI Freeze risk). Move all file reads/subprocess calls to background threads.
>     - Check for redundant I/O (e.g., reading the same config file multiple times per second). Suggest caching strategies.
> 
> 6. **The "Boring Code" Principle:**
>     - If a clever one-liner is hard to read or debug, refactor it into explicit, robust logic. Reliability > Cleverness.
> </audit_instructions>
> 
> <output_format>
> 7. **The Critique:** A bulleted list of specific flaws (Logic, Threading, Typing, or Style) found in the input.
> 8. **The Refactored Code:** The complete, optimized, production-ready Python code in a markdown block.
> </output_format>
> 
> <input_script>
> 
> </input_script>
> ```

Libadwaita GTK 4 Prompt

> [!NOTE]- CSS GTK 4  
> ```ini
> <system_role>
> You are an Elite GNOME Application Architect and GTK4 Theming Expert.
> Your goal is to AUDIT, DEBUG, and REFACTOR a CSS stylesheet for a Libadwaita (GTK 4) application.
> </system_role>
> 
> <context>
>     <framework>GTK 4 + Libadwaita (Adw)</framework>
>     <standards>GNOME Human Interface Guidelines (HIG)</standards>
>     <constraints>GTK CSS Parser (Not a web browser engine)</constraints>
> </context>
> 
> <audit_instructions>
> Perform a "Deep Dive" analysis of the theming logic. You MUST follow this process:
> 
> 1.  **System Integration & Color Logic (Crucial):**
>     -   Identify any **hardcoded hex/rgb values** (e.g., `#ffffff`, `#3584e4`).
>     -   **Rule:** You MUST replace hardcoded colors with Libadwaita **Named Colors** (e.g., `@window_bg_color`, `@accent_color`, `@error_color`) to ensure native Light/Dark mode compatibility and High Contrast support.
> 
> 2.  **GTK-Specific Forensics:**
>     -   Scan for unsupported web-only properties (e.g., `float`, `position: absolute`, complex `grid` inside CSS) which do not work in GTK4 CSS (layout is handled by UI definitions/Blueprints, not CSS).
>     -   Verify correct usage of GTK Nodes (e.g., `window.messagedialog`, `headerbar`, `button.suggested-action`) vs generic classes.
>     -   Check for deprecated GTK3 syntax (e.g., incorrect pseudo-elements or Gadgets).
> 
> 3.  **Selector Efficiency & Specificity:**
>     -   Remove "Specificty Wars." GTK CSS node matching is strict; deep nesting often breaks widget states (hover, backdrop, active).
>     -   Ensure the styling respects the window state (e.g., correct styling for `:backdrop` when the window loses focus).
> 
> 4.  **Visual Polish:**
>     -   Verify that margins, padding, and border-radius align with the modern Adwaita aesthetic (rounded corners, distinct separation of content).
> </audit_instructions>
> 
> <output_format>
> 5.  **The Critique:** A bulleted list of flaws found (e.g., broken dark mode due to hardcoded colors, usage of invalid web properties).
> 6.  **The Refactored Stylesheet:** The complete, perfected, copy-paste-able CSS code in a markdown block, using correct Libadwaita named colors.
> </output_format>
> 
> <input_css>
> 
> </input_css>
> ```



























---
---

# Old Prompts

> [!NOTE]- New Script
> ```ini
>  # Role & Objective
> 
> Act as an Elite DevOps Engineer and Arch Linux System Architect. Your task is to write a highly optimized, robust, and modern Bash script (Bash 5+) for an Arch Linux environment running Hyprland and UWSM.
> 
> 
> # Constraints & Environment
> 
> 1. **OS:** Arch Linux (Rolling).
> 
> 2. **Session:** Hyprland (Wayland).
> 
> 3. **Manager:** UWSM (Universal Wayland Session Manager). *Crucial: Respect UWSM environment variables and systemd scoping.*
> 
> 4. **Complexity:** Keep it straightforward and performant. Do not over-engineer, but handle likely edge cases.
> 
> 5. **Clean:** Make sure it doesnt creat a log file or backup file i want this to be done cleanly. 
> 
> 
> # Coding Standards (Strict)
> 
> - **Safety:** Use `set -euo pipefail` for strict error handling.
> 
> - **Cleanup:** Use `trap` to handle cleanup on EXIT/ERR signals if temporary files or states are modified.
> 
> - **Modern Bash:** Use `[[ ]]` over `[ ]`, `printf` over `echo`, and purely builtin commands where possible to save forks.
> 
> - **Feedback:** Provide clean, colored log output (Info, Success, Error).
> 
> 
> # Process
> 
> 1. **Code:** Generate the script.
> 
> 2. Make sure to think through the logic of the scirpt critically, to make sure it'll work. 
> 
> 
> # Sudo/Privilege Strategy
> 
> - **If Root IS Needed:** The script must check for root privileges immediately at the very start (Line 1 logic).
> 
>   - If the user is not root, the script should either: a) explicitly prompt/re-execute itself with `sudo`, or b) exit with a clear error message instructions to run with sudo. 
> ```

> [!NOTE]-  Review
> ```ini
> As an Elite DevOps Engineer and Systems Architect specializing in Arch Linux, and the Hyprland Window Manager with Universal Wayland Session Manager. You're a Linux enthusiast, who's been using Linux for as long it's been around, You know everything about bash scripting and it's quirks and you're a master Linux user Who knows every aspect of Arch Linux. Evaluate, generate, debug, and optimize Bash scripts specifically for the Arch/Hyprland/UWSM ecosystem. You leverage modern Bash 5+ features for performance and efficiency. You keep upto date with all the latest improvements in how to bash script and use Linux.
> 
> You're tasked with taking a look at this script file and evaluating it for any errors and bad code. think long and hard.
> 
> go at every line in excruciating detail to check for errors. and then provide the most optimized and perfected script in full to be copy and pasted for testing.
> 
> Dont over engineer, just make sure it's reliable. 
> ```

> [!NOTE]- I Asked
> ```ini
> i asked chatgpt to evaluvate your script, what do you think of it's feedback? if it made any good points, make sure to impliment those into our script.  it might be wrong, so make sure to think critically.  
> ```



---
---

> [!NOTE]- INI schema
> ```python
> #!/usr/bin/env python3
> """
> ===============================================================================
> DUSKY TUI: MASTER CONFIGURATION SCHEMA (INI PARADIGM)
> ===============================================================================
> 
> TARGET MAPPING VISUALIZATION:
> How `scope` and `key` instruct the engine to mutate the target INI file.
> (e.g., pacman.conf, makepkg.conf, mako/config, etc.)
> 
> ===============================================================================
> [ SCENARIO A: Standard Section Variables ]
> ===============================================================================
>   [options]                      <-- scope="options"
>   HoldPkg = pacman glibc         <-- key="HoldPkg"
> 
> ===============================================================================
> [ SCENARIO B: Root/Global Variables ]
> ===============================================================================
>   log_level = debug              <-- scope="DEFAULT", key="log_level"
> 
> ===============================================================================
> [ SCENARIO C: Valueless Flags (Arch Linux style) ]
> ===============================================================================
>   Color                          <-- scope="DEFAULT", key="Color", type_="bool"
>   ILoveCandy                     <-- scope="options", key="ILoveCandy", type_="bool"
> 
> STRICT RULES FOR SCHEMA GENERATION (CRITICAL - DO NOT VIOLATE):
> 
> 1. UID (Unique Identifier) Rule:
>    - If a variable sits at the root of the target file (no section), set scope="DEFAULT".
>    - If scope is defined, the UID is `scope.key` (e.g., "options.HoldPkg").
>    - If scope is "DEFAULT", the UID is just the `key` (e.g., "log_level").
>    - You MUST use the exact UID when using `parent_ref` or `preset_payload`.
> 
> 2. The Naming Imperative (STRICTLY ONE WORD):
>    - TABS & GROUPS: Every `group` string and `Tab` string MUST be EXACTLY ONE WORD.
>    - LABELS (SETTINGS): The `label` for each ConfigItem MUST be as succinct as humanly 
>      possible, ideally ONE WORD (e.g., use "Logging" instead of "Enable System Logging").
> 
> 3. The Theme File Axiom (CRITICAL):
>    - The `THEME_FILE` path must be exactly preserved (`~/.config/matugen/generated/dusky_tui.json`).
>    - This file MUST ALWAYS exist at this exact path. Do not alter the string.
> 
> 4. Contiguous Grouping Rule (Do NOT interleave):
>    - Items with the same `group` string MUST be placed immediately next to each other. 
>    - Items with a `parent_ref` MUST be placed immediately beneath their parent in a 
>      single, unbroken block. Do not break the visual tree.
> 
> 5. Structural Restrictions & Hybrid Folders (CRITICAL):
>    - "preset" and "action" items are PURE UI constructs. They DO NOT write to the 
>      target file. Their `key` is just an internal ID.
>    - HYBRID FOLDERS: You can turn ANY standard item (e.g., "bool", "cycle", "int") into
>      an expandable drop-down menu by setting `is_parent=True`. This allows the parent 
>      header itself to hold a changeable backend value (e.g., a Master Toggle Switch).
>    - Folders can only be ONE level deep. DO NOT nest a folder inside another folder.
> 
> 6. Available Types (`type_`):
>    - "bool"   : Toggles instantly (True/False). Maps to true/false OR valueless flags.
>    - "int"    : Numeric integer (supports min_val, max_val, step).
>    - "float"  : Numeric decimal (supports min_val, max_val, step).
>    - "string" : Text input (Use options=[] to provide a multiple-choice dropdown).
>    - "cycle"  : Instant left/right cycling through an `options` list of strings.
>    - "picker" : Opens a searchable fullscreen modal from an `options` list.
>    - "color"  : Hex, RGB, HSL, or Matugen theme variables.
>    - "menu"   : A pure visual folder with no value (requires `is_parent=True`, `default=None`).
>    - "action" : Triggers a shell command (put the exact shell command string in `default=`).
>    - "preset" : Applies multiple values at once (requires `preset_payload`, `default=None`).
> 
> 7. Preset Payload Strict Application (Nuclear Reset):
>    - Presets apply a STRICT state snapshot. If you omit a key from a `preset_payload`, 
>      the application will forcibly revert that omitted key back to its `default` value 
>      when the preset is applied. Define ALL necessary keys in the payload.
> 
> 8. Multi-File & Multi-Engine Overrides (NEW):
>    - By default, all items use the root `ENGINE_TYPE` and `TARGET_FILE`.
>    - You can route individual items to completely different files or engines using 
>      `target_file_override="~/.config/other.conf"` and `engine_type_override="systemd"`.
>    - Supported engines: "ini", "lua", "systemd", "hyprlang", "trackpad", "monitor".
> 
> 9. Validation, Warnings, & Modals (NEW):
>    - `confirm_message`: Requires a Yes/No dialog confirmation BEFORE mutating the value.
>    - `warning_msg`: Adds a ⚠️ marker in the UI and a warning block to the help panel.
>    - `popup_message`: Shows an 'OK' alert dialog AFTER the value is successfully applied.
> ===============================================================================
> """
> 
> from python.frontend.core_types import ConfigItem
> 
> # =============================================================================
> # 1. CORE APPLICATION ROUTING (REQUIRED)
> # =============================================================================
> ENGINE_TYPE = "ini"                        # STRICTLY: "ini"
> TARGET_FILE = "~/.config/mako/config"      # Standard INI target path
> APP_TITLE = "Dusky Config"                 # Displayed in the TUI border
> 
> # =============================================================================
> # 2. UI & ENVIRONMENT BEHAVIOR
> # =============================================================================
> DEFAULT_MODE = "auto"                      # "auto" (instant save) | "batch" (Ctrl+S required)
> 
> # STRICT REQUIREMENT: This exact path must always exist. Do not change.
> THEME_FILE = "~/.config/matugen/generated/dusky_tui.json" 
> 
> ENABLE_USER_PRESETS = True                 # Allows users to save/delete profiles dynamically
> USER_PRESETS_TAB = "Profiles"              # Must exactly match a ONE-WORD tab name below
> 
> # OPTIONAL: Displays a popup when the TUI is first launched
> GLOBAL_POPUP = {
>     "title": "Configuration Notice",
>     "message": "Proceed with caution. Modifying these values affects system behavior.",
>     "level": "info",           # "info" | "warning" | "danger" | "success"
>     "require_confirm": False,  # If True, shows Cancel/Confirm buttons
>     "cancel_quits": False      # If True, clicking Cancel exits the TUI
> }
> 
> # OPTIONAL: Displays persistent warning/info boxes at the top of specific tabs
> TAB_NOTICES = {
>     # e.g., 2 is the index of the "Profiles" tab
>     2: {"level": "warning", "message": "Applying profiles may immediately overwrite active settings."}
> }
> 
> # =============================================================================
> # 3. TABS DEFINITION
> # STRICT RULE: Keep tabs ONE WORD ONLY to prevent UI clutter and wrapping.
> # =============================================================================
> 
> TABS = [
>     "Core",
>     "Visuals",
>     "Profiles"
> ]
> 
> # =============================================================================
> # 4. SCHEMA DEFINITION
> # STRICT RULE: Keep `label` as close to ONE WORD as possible.
> # =============================================================================
> 
> SCHEMA = {
>     # -------------------------------------------------------------------------
>     # TAB 0: CORE (Infrastructure & Standard Types)
>     # -------------------------------------------------------------------------
>     0: [
>         ConfigItem(
>             label="Logging",
>             key="log_level",
>             scope="DEFAULT",       # UID = "log_level"
>             type_="cycle",
>             default="info",
>             options=["debug", "info", "warning", "error"],
>             group="System",        # STRICT: ONE WORD ONLY
>             extended_help="**Diagnostic Logging**\n\nSets the verbosity of the daemon's internal logs. `debug` provides the highest detail, while `error` only reports critical failures."
>         ),
>         ConfigItem(
>             label="Concurrency",
>             key="max_threads",
>             scope="DEFAULT",       # UID = "max_threads"
>             type_="int",
>             default=4,
>             min_val=1,
>             max_val=32,
>             step=1,
>             group="System",
>             extended_help="**Thread Allocation**\n\nDefines the maximum number of simultaneous threads allocated for background processing."
>         ),
>         ConfigItem(
>             label="Color",         # Example of an Arch Linux pacman.conf valueless flag
>             key="Color",
>             scope="options",       # UID = "options.Color"
>             type_="bool",
>             default=True,
>             group="Pacman",
>             extended_help="**Color Output**\n\nA valueless boolean flag. When enabled, inserts `Color` into the INI file without an equals sign to force colorized terminal output."
>         ),
>     ],
> 
>     # -------------------------------------------------------------------------
>     # TAB 1: VISUALS (UI Components & Hybrid Menu Folders)
>     # -------------------------------------------------------------------------
>     1: [
>         ConfigItem(
>             label="Background",
>             key="background-color",
>             scope="DEFAULT",       # UID = "background-color"
>             type_="color",
>             default="#1e1e2e",
>             group="Theme",
>             extended_help="**Base Background Color**\n\nDetermines the core background fill for UI elements. Accepts standard Hex codes."
>         ),
>         
>         # --- HYBRID FOLDER IMPLEMENTATION ---
>         # 1. The Parent (Holds a real backend "bool" value, acts as a folder)
>         ConfigItem(
>             label="Typography",
>             key="custom_fonts", 
>             scope="appearance",       # UID = "appearance.custom_fonts"
>             type_="bool",             # Real boolean value
>             default=False,
>             is_parent=True,           # Flags this as an expandable folder
>             expanded=False,
>             group="Fonts",
>             extended_help="**Typography Override**\n\nMaster switch for font enforcement. When ON, applies the nested typography values."
>         ),
>         # 2. Child Item A (Contiguous with parent)
>         ConfigItem(
>             label="Family",
>             key="font",
>             scope="appearance",       # UID = "appearance.font"
>             type_="picker",        
>             default="JetBrains Mono",
>             options=["JetBrains Mono", "Fira Code", "Roboto", "Inter"],
>             hints=["Monospace", "Ligatures", "Sans", "Sans"], 
>             parent_ref="appearance.custom_fonts",  # Links to Hybrid Parent UID
>             extended_help="**Interface Font Family**\n\nSelects the core typeface used across the system UI panels."
>         ),
>         # 3. Child Item B (Contiguous with parent)
>         ConfigItem(
>             label="Size",
>             key="font_size",
>             scope="appearance",       # UID = "appearance.font_size"
>             type_="float",        
>             default=10.0,
>             min_val=6.0,
>             max_val=24.0,
>             step=0.5,
>             parent_ref="appearance.custom_fonts",
>             extended_help="**Base Font Size**\n\nThe root cryptographic scale parameter measured in points (pt)."
>         ),
>     ],
> 
>     # -------------------------------------------------------------------------
>     # TAB 2: PROFILES (Advanced Controls & State Synchronization)
>     # -------------------------------------------------------------------------
>     2: [
>         ConfigItem(
>             label="Purge",
>             key="action_clear_cache", 
>             scope="DEFAULT",          # UID = "action_clear_cache"
>             type_="action",
>             default="rm -rf ~/.cache/mako/* && echo 'Cache Cleared'",
>             group="Maintenance",
>             warning_msg="This will permanently delete volatile cache files.",
>             extended_help="**Cache Purge**\n\nImmediately deletes volatile application cache files via shell command."
>         ),
>         ConfigItem(
>             label="Performance",
>             key="preset_performance",     
>             scope="DEFAULT",          # UID = "preset_performance"
>             type_="preset",
>             default=None,
>             group="Orchestrator",
>             preset_payload={
>                 "log_level": "error",      
>                 "appearance.custom_fonts": False,            
>                 "max_threads": 32  
>             },
>             extended_help="**Performance Preset**\n\nInstantly overrides multiple settings to maximize system responsiveness. Any omitted settings are forcibly reverted to default."
>         ),
>         ConfigItem(
>             label="Reset",
>             key="preset_factory_reset",
>             scope="DEFAULT",          # UID = "preset_factory_reset"
>             type_="preset",
>             default=None,
>             group="Orchestrator",
>             confirm_message="Are you sure you want to perform a full factory reset? All unsaved customizations will be lost.",
>             preset_payload={
>                 "__ALL_DEFAULTS__": True
>             },
>             extended_help="**Factory Reset**\n\nReverts every single configuration item across all tabs back to its originally programmed default state."
>         ),
>     ]
> }
> 
> # =============================================================================
> # QUICK-REFERENCE CHEAT SHEET (BULLETPROOF EDITION)
> # =============================================================================
> # Copy/Paste this block when building new items to ensure correct kwargs.
> #
> # ConfigItem(
> #     label          = "ShortName",        # STRICT: Keep as succinct/close to one word as possible.
> #     key            = "backend_key",
> #     scope          = "DEFAULT",          # "backend_section" or "DEFAULT" for root/valueless keys.
> #     type_          = "bool",             # STANDARD: bool | int | float | string | color
> #                                          # MODALS: cycle | picker 
> #                                          # PURE UI: action | preset | menu
> #     default        = None,               # Native Python type MUST match type_ (e.g., True, 10, "text"). 
> #                                          # -> For 'action', default is the shell command string. 
> #                                          # -> For 'menu' & 'preset', default MUST be None.
> #     options        = [],                 # Required for 'cycle'/'picker'. Triggers Hybrid Menu for int/float/string/color.
> #     hints          = [],                 # Subtitles for 'picker' modals (length must match options list).
> #     preset_payload = {},                 # Dict of {"scope.key": target_value}. Unlisted keys revert to default.
> #     min_val        = None,               # Numeric lower bound (int/float).
> #     max_val        = None,               # Numeric upper bound (int/float).
> #     step           = None,               # Numeric step size for arrow key adjustments.
> #     group          = "OneWord",          # STRICT: UI header string MUST BE ONE WORD.
> #     extended_help  = "Markdown String",  # Explain what the setting does for the user's '?' panel.
> #     is_parent      = False,              # Set True to make this item an expandable folder (Works on ANY type!).
> #     parent_ref     = None,               # Nested UI link. MUST exactly match parent's UID format: "scope.key" (or "key" if DEFAULT).
> #     expanded       = False,              # Default UI state for parent folders (Starts open or closed).
> #
> #     # --- ADVANCED VALIDATION & OVERRIDES ---
> #     warning_msg    = None,               # Adds a ⚠️ marker and displays this warning in the help panel.
> #     popup_message  = None,               # Shows an 'OK' alert dialog AFTER the value is applied.
> #     confirm_message= None,               # Requires Yes/No dialog confirmation BEFORE mutation.
> #     target_file_override = None,         # Reroutes this specific item to a different file path.
> #     engine_type_override = None,         # Changes the parsing engine for this item (e.g., "systemd", "ini").
> # )
> ```


> [!NOTE]- Lua Schema
> ```python
> #!/usr/bin/env python3
> """
> ===============================================================================
> DUSKY TUI: MASTER CONFIGURATION SCHEMA
> ===============================================================================
> 
> TARGET MAPPING VISUALIZATION:
> How `scope` and `key` tell the engine exactly what to edit in the target file.
> The mapping behavior changes depending on your declared ENGINE_TYPE.
> 
> ===============================================================================
> [ SCENARIO A: If ENGINE_TYPE = "ini" ]
> ===============================================================================
>   [theme.colors]                 <-- scope="theme.colors" (Deep nesting supported)
>   active_border = #ff89b4fa      <-- key="active_border"
> 
> ===============================================================================
> [ SCENARIO B: If ENGINE_TYPE = "lua" (Hyprland AST) ]
> ===============================================================================
>   10. STANDARD TABLES (hl.config)
>      hl.config({
>          decoration = {
>              rounding = 10          <-- scope="decoration", key="rounding"
>              blur = {
>                  enabled = true     <-- scope="decoration/blur", key="enabled"
>              }
>          }
>      })
> 
>   11. HYPRLAND WINDOW RULES (Mapped by 'name')
>      hl.window_rule({ name = "my_rule", rounding = 0 }) 
>          <-- scope="window_rule/my_rule", key="rounding"
> 
>   12. HYPRLAND WORKSPACE RULES (Mapped by 'workspace' string)
>      hl.workspace_rule({ workspace = "w[tv1]", border_size = 2 }) 
>          <-- scope="workspace_rule/w[tv1]", key="border_size"
> 
> STRICT RULES FOR SCHEMA GENERATION (CRITICAL - DO NOT VIOLATE):
> 
> 13. UID (Unique Identifier) Rule:
>    - If a variable sits at the root of the target file (no section), set scope="DEFAULT".
>    - If scope is defined, the UID is `scope.key` (e.g., "theme.border_active").
>    - If scope is "DEFAULT", the UID is just the `key` (e.g., "logging").
>    - You MUST use the exact UID when using `parent_ref` or `preset_payload`.
> 
> 14. Hyprland AST Context Rules (CRITICAL FOR LUA ENGINE):
>    - For `hl.window_rule`, the scope MUST strictly be `window_rule/<name>`. The target rule in the Lua file MUST have an explicit `name = "..."` attribute.
>    - For `hl.workspace_rule`, the scope MUST strictly be `workspace_rule/<workspace_selector>` (e.g., `scope="workspace_rule/w[tv1]s[false]"`). 
>    - NEVER inject artificial `name` keys into `hl.workspace_rule` target files, as the C++ compositor will reject them. The engine parses the workspace string natively.
> 
> 15. Contiguous Grouping Rule (Do NOT interleave):
>    - Items with the same `group` string MUST be placed immediately next to 
>      each other. The UI draws headers sequentially.
>    - Items with a `parent_ref` MUST be placed immediately beneath their parent 
>      in a single, unbroken block. Do not break the visual tree.
> 
> 16. Structural Restrictions & Hybrid Folders (CRITICAL):
>    - "preset" and "action" items are PURE UI constructs. They DO NOT write 
>      to the target file. Their `key` is just an internal ID.
>    - "menu": A PURE UI visual folder (default=None, type_="menu"). It writes nothing.
>    - HYBRID FOLDERS: You can turn ANY standard item (e.g., "bool", "cycle", "int") into
>      an expandable drop-down menu by setting `is_parent=True`. This allows the parent 
>      header itself to hold a changeable backend value (e.g., a Master Toggle Switch).
>    - Folders can only be ONE level deep. DO NOT nest a folder inside another folder.
> 
> 17. Naming Conventions (ONE-WORD HEADERS ONLY):
>    - NO MULTI-WORD HEADERS: Every `group` name and `Tab` name MUST be strictly ONE WORD 
>      and highly intuitive (e.g., use `Theme` instead of `Theming Subsystem`). This prevents 
>      terminal UI clutter and wrapping issues.
>    - NO SERIALIZED NUMBERS: Do not use numbered increments for keys or labels.
>    - USE DESCRIPTIVE KEYS: Utilize descriptive, semantic identifiers for backend keys 
>      (e.g., use `enable_dynamic_workspace_routing`).
> 
> 18. Available Types (`type_`) & Hybrid Menus:
>    - "bool"   : Toggles instantly (True/False)
>    - "int"    : Numeric integer (supports min_val, max_val, step. Use options=[] for hybrid dropdown)
>    - "float"  : Numeric decimal (supports min_val, max_val, step. Use options=[] for hybrid dropdown)
>    - "string" : Text input (Use options=[] to provide a hybrid multiple-choice dropdown)
>    - "cycle"  : Instant left/right cycling through an `options` list of strings
>    - "picker" : Opens a searchable fullscreen modal from an `options` list
>    - "color"  : Hex, RGB, HSL, or Matugen theme variables (Use options=[] for hybrid dropdown)
>    - "menu"   : A pure visual folder with no value (requires `is_parent=True`, `default=None`)
>    - "action" : Triggers a shell command (put the exact shell command string in `default=`)
>    - "preset" : Applies multiple values at once (requires `preset_payload`, `default=None`)
> 
> 19. Strict Type & Native Value Matching (CRITICAL FOR AST ENGINES):
>    - You MUST map the target configuration's native data type to the correct `type_`. 
>    - The Python data type of the `default` argument MUST strictly match the declared `type_`:
>      * "bool"   -> default=True (Python boolean, NOT string "true" or "True")
>      * "int"    -> default=10   (Python integer, NOT string "10")
>      * "float"  -> default=1.5  (Python float, NOT string "1.5")
>      * "string" -> default="Text"
>    - For "action", `default` MUST be the exact shell command string to execute.
>    - If using the `options` array, the array elements MUST match the native type.
> 
> 20. Preset Payload Strict Application (Nuclear Reset):
>    - Presets apply a STRICT state snapshot. If you omit a key from a `preset_payload`, 
>      the application will forcibly revert that omitted key back to its `default` value 
>      when the preset is applied. Define ALL necessary keys in the payload.
> 
> 21. Documentation & Help Text (extended_help):
>    - Every item MUST include clear, detailed `extended_help`.
>    - Explain exactly what the item does and how the specific values affect the system.
>    - Write it so users who have absolutely no idea what the setting does can easily configure it.
> 
> 22. The Theme File Axiom (CRITICAL REQUIREMENT):
>    - The `THEME_FILE` path must be EXACTLY preserved as `~/.config/matugen/generated/dusky_tui.json`.
>    - This file MUST ALWAYS exist at this exact path. Do not alter, abbreviate, or omit this string under any circumstances.
> 
> 23. Multi-File & Multi-Engine Overrides (NEW):
>    - By default, all items use the root `ENGINE_TYPE` and `TARGET_FILE`.
>    - You can route individual items to completely different files or engines using 
>      `target_file_override="~/.config/other.conf"` and `engine_type_override="ini"`.
>    - Supported engines: "ini", "lua", "systemd", "hyprlang", "trackpad", "monitor".
> 
> 24. Validation, Warnings, & Modals (NEW):
>    - `confirm_message`: Requires a Yes/No dialog confirmation BEFORE mutating the value.
>    - `warning_msg`: Adds a ⚠️ marker in the UI and a warning block to the help panel.
>    - `popup_message`: Shows an 'OK' alert dialog AFTER the value is successfully applied.
> ===============================================================================
> """
> 
> from python.frontend.core_types import ConfigItem
> 
> # =============================================================================
> # 1. CORE APPLICATION ROUTING (REQUIRED)
> # =============================================================================
> ENGINE_TYPE = "lua"                        # STRICTLY: "ini" or "lua"
> TARGET_FILE = "~/.config/hypr/edit_here/source/appearance.lua"   # Where the engine writes the data
> APP_TITLE = "Dusky Appearance"             # Displayed in the TUI border
> 
> # =============================================================================
> # 2. UI & ENVIRONMENT BEHAVIOR
> # =============================================================================
> DEFAULT_MODE = "auto"                      # "auto" (instant save) | "batch" (Ctrl+S required)
> 
> # STRICT REQUIREMENT: This exact path must always exist. Do not change or omit.
> THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
> 
> ENABLE_USER_PRESETS = True                 # Allows users to save/delete profiles dynamically
> USER_PRESETS_TAB = "Profiles"              # Must exactly match a ONE-WORD tab name below
> 
> # OPTIONAL: Displays a popup when the TUI is first launched
> GLOBAL_POPUP = {
>     "title": "Welcome to Dusky",
>     "message": "Adjusting Wayland attributes directly interfaces with the compositor.",
>     "level": "info",           # "info" | "warning" | "danger" | "success"
>     "require_confirm": False,  # If True, shows Cancel/Confirm buttons
>     "cancel_quits": False      # If True, clicking Cancel exits the TUI
> }
> 
> # OPTIONAL: Displays persistent warning/info boxes at the top of specific tabs
> TAB_NOTICES = {
>     # e.g., 0 is the index of the "Infrastructure" tab
>     0: {"level": "info", "message": "Core infrastructure handles system-wide compositor rules."}
> }
> 
> # =============================================================================
> # 3. TABS DEFINITION
> # Arrays in SCHEMA map directly to the index of these tabs.
> # STRICT RULE: Keep tabs ONE WORD ONLY so it doesn't clutter the top bar.
> # =============================================================================
> 
> TABS = [
>     "Infrastructure",
>     "Interface",
>     "Profiles"
> ]
> 
> # =============================================================================
> # 4. SCHEMA DEFINITION
> # =============================================================================
> 
> SCHEMA = {
>     # -------------------------------------------------------------------------
>     # TAB 0: STANDARD DATA TYPES (Infrastructure)
>     # -------------------------------------------------------------------------
>     0: [
>         ConfigItem(
>             label="Enable Comprehensive System Logging",
>             key="global_diagnostic_logging_active",
>             scope="DEFAULT",       # UID = "global_diagnostic_logging_active"
>             type_="bool",
>             default=False,
>             group="Execution",     # STRICT: ONE WORD ONLY
>             extended_help="**Diagnostic Logging**\n\nWhen enabled, this writes detailed execution pathways, state changes, and error traces to the system logs. It is highly useful for debugging backend issues, but may consume extra disk space over long periods of time. Leave off for standard daily usage."
>         ),
>         ConfigItem(
>             label="Enable Hardware Accelerated Animations",
>             key="hardware_animations_enabled",
>             scope="core",          # UID = "core.hardware_animations_enabled"
>             type_="bool",
>             default=True,
>             group="Execution",
>             warning_msg="Disabling this on high-refresh rate monitors may cause tearing.",
>             extended_help="**Hardware Animations**\n\nToggles UI hardware acceleration globally. Disabling this can save battery life and reduce GPU overhead on lower-end systems or virtual machines, but interface transitions and window movements will lose their smoothness."
>         ),
>         ConfigItem(
>             label="Global Workspace Inner Gaps",
>             key="workspace_padding_inner",
>             scope="layout",        # UID = "layout.workspace_padding_inner"
>             type_="int",
>             default=5,
>             min_val=0,
>             max_val=50,
>             step=1,
>             group="Layout",
>             extended_help="**Inner Workspace Gaps**\n\nDefines the spacing (measured in pixels) between adjacent windows within the same workspace. \n\n- Higher values create a distinct, spaced-out layout.\n- Lower values (or 0) maximize usable screen real estate for maximum productivity."
>         ),
>         ConfigItem(
>             label="Constrained Border Thickness",
>             key="locked_window_border_size",
>             scope="layout",        # UID = "layout.locked_window_border_size"
>             type_="int",
>             default=2,
>             options=[0, 2, 5, 8, 15], # Triggers hybrid dropdown suggestions in UI
>             group="Layout",
>             extended_help="**Border Thickness**\n\nSets the absolute pixel width of the borders drawn around application windows. \n\nA thicker border makes it easier to visually distinguish the currently active window, which is especially helpful in dense, multi-window tiling layouts."
>         ),
>         ConfigItem(
>             label="Authenticated User Greeting",
>             key="authentication_welcome_message",
>             scope="core",          # UID = "core.authentication_welcome_message"
>             type_="string",
>             default="Welcome back to the terminal.",
>             options=["Welcome back.", "System online.", "Awaiting input."], # Triggers hybrid dropdown
>             group="Textual",
>             extended_help="**Welcome Message**\n\nThis is the custom text string displayed in the terminal interface or lock screen upon successful user authentication. You can type any standard sentence or phrase here."
>         ),
>     ],
> 
>     # -------------------------------------------------------------------------
>     # TAB 1: UI COMPONENTS & HYBRID MENU FOLDERS (Interface)
>     # -------------------------------------------------------------------------
>     1: [
>         ConfigItem(
>             label="Primary Active Border Highlight",
>             key="focused_window_border_color",
>             scope="theme",         # UID = "theme.focused_window_border_color"
>             type_="color",
>             default="#a8c8ff",
>             group="Theme",         # STRICT: ONE WORD ONLY
>             extended_help="**Active Window Border Color**\n\nDetermines the bright highlight color surrounding the currently focused (active) window. \n\nAccepts standard Hex codes (e.g., `#ff0000`), RGB, or HSL formats. Use high-contrast colors so you never lose track of where your keyboard inputs are going."
>         ),
>         ConfigItem(
>             label="Background Inactive Border Fade",
>             key="unfocused_window_border_color",
>             scope="theme",         # UID = "theme.unfocused_window_border_color"
>             type_="color",
>             default="#414453",
>             options=["background", "surface", "primary", "error"], 
>             group="Theme",
>             extended_help="**Inactive Window Border Color**\n\nThe color applied to the borders of all windows that *do not* currently have user focus. \n\nIt is highly recommended to set this to a muted, dark, or dim color to emphasize the active window and reduce visual clutter."
>         ),
>         
>         # --- HYBRID FOLDER IMPLEMENTATION ---
>         # 1. The Parent (Holds a real backend "bool" value, but acts as a folder)
>         ConfigItem(
>             label="Enable Custom Typography Overrides",
>             key="custom_typography_override_active", 
>             scope="fonts",            # UID = "fonts.custom_typography_override_active"
>             type_="bool",             # CRITICAL: Valid data type (Not a dummy menu)
>             default=False,
>             is_parent=True,           # CRITICAL: Flags this item as an expandable folder
>             expanded=False,           # Starts collapsed
>             group="Typography",
>             extended_help="**Typography Master Switch**\n\nThis acts as the master toggle for custom fonts. \n\n- When **ON**, the system will ignore default fonts and strictly enforce the custom font family and size defined in the nested menu below.\n- When **OFF**, system defaults are restored."
>         ),
>         # 2. Child Item A (MUST be placed immediately after its parent)
>         ConfigItem(
>             label="Primary Interface Font Family",
>             key="primary_interface_font",
>             scope="fonts",            # UID = "fonts.primary_interface_font"
>             type_="picker",        
>             default="JetBrains Mono",
>             options=["JetBrains Mono", "Fira Code", "Roboto"],
>             hints=["Monospace", "Ligatures", "Sans-Serif"], 
>             parent_ref="fonts.custom_typography_override_active",  # Links to Hybrid Parent UID
>             extended_help="**Interface Font Family**\n\nSelects the core typeface used across the system UI panels, bars, and terminals. \n\n*Note:* Ensure the selected font is actually installed on your system (via your package manager) or the system will fall back to a default ugly font."
>         ),
>         # 3. Child Item B (Contiguous block of children continues)
>         ConfigItem(
>             label="Global Base Font Size",
>             key="global_base_font_size",
>             scope="fonts",            # UID = "fonts.global_base_font_size"
>             type_="float",        
>             default=11.0,
>             min_val=8.0,
>             max_val=24.0,
>             step=0.5,
>             parent_ref="fonts.custom_typography_override_active",
>             extended_help="**Base Font Size**\n\nThe root typographic scale parameter. \n\nAll UI text elements will scale proportionally relative to this base value. Measured in standard points (pt). Increase this if the text is too difficult to read on high-resolution displays."
>         ),
>     ],
> 
>     # -------------------------------------------------------------------------
>     # TAB 2: ADVANCED CONTROLS (Profiles)
>     # -------------------------------------------------------------------------
>     2: [
>         ConfigItem(
>             label="Purge Volatile System Cache",
>             key="action_purge_system_cache", 
>             scope="DEFAULT",          # UID = "action_purge_system_cache"
>             type_="action",
>             default="rm -rf ~/.cache/app_name/* && echo 'Cache Cleared'",
>             group="Maintenance",
>             extended_help="**Cache Purge Action**\n\nExecuting this action immediately deletes volatile application cache files via shell command. \n\nThis is completely safe to run and can resolve strange visual glitches or free up temporary disk space without affecting your persistent user data or settings."
>         ),
>         ConfigItem(
>             label="Deploy High-Performance Profile",
>             key="preset_deploy_performance_mode",     
>             scope="DEFAULT",          # UID = "preset_deploy_performance_mode"
>             type_="preset",
>             default=None,
>             group="System",
>             preset_payload={
>                 "core.hardware_animations_enabled": False,      
>                 "layout.workspace_padding_inner": 0,            
>                 "theme.focused_window_border_color": "#ff0000"  
>             },
>             extended_help="**High-Performance Preset**\n\nInstantly overrides multiple current settings to maximize system responsiveness for gaming or heavy workloads. \n\nApplying this will:\n1. Disable all UI animations.\n2. Remove all window padding (gaps = 0).\n3. Set the active border to stark red for high visibility.\n\n*Note: Any omitted settings are forcibly reverted to default.*"
>         ),
>         ConfigItem(
>             label="Initiate Complete Factory Reset",
>             key="preset_initiate_factory_reset",
>             scope="DEFAULT",          # UID = "preset_initiate_factory_reset"
>             type_="preset",
>             default=None,
>             group="System",
>             confirm_message="Are you absolutely sure you want to revert to factory defaults? This cannot be undone.",
>             preset_payload={
>                 "__ALL_DEFAULTS__": True
>             },
>             extended_help="**Nuclear Factory Reset**\n\nReverts every single configuration item across all tabs in this schema back to its originally programmed default state. \n\n⚠️ **WARNING:** Use with absolute caution as all your customized colors, gaps, and toggles will be wiped out instantly."
>         ),
>     ]
> }
> 
> # =============================================================================
> # QUICK-REFERENCE CHEAT SHEET (BULLETPROOF EDITION)
> # =============================================================================
> # Copy/Paste this block when building new items to ensure correct kwargs.
> #
> # ConfigItem(
> #     label          = "Display Name",
> #     key            = "backend_key",
> #     scope          = "DEFAULT",          # "backend_section", "window_rule/<name>", "workspace_rule/<workspace>" or "DEFAULT"
> #     type_          = "bool",             # STANDARD: bool | int | float | string | color
> #                                          # MODALS: cycle | picker 
> #                                          # PURE UI: action | preset | menu
> #     default        = None,               # Native Python type MUST match type_ (e.g., True, 10, "text"). 
> #                                          # -> For 'action', default is the shell command string. 
> #                                          # -> For 'menu' & 'preset', default MUST be None.
> #     options        = [],                 # Required for 'cycle'/'picker'. Triggers Hybrid Menu for int/float/string/color.
> #     hints          = [],                 # Subtitles for 'picker' modals (length must match options list)
> #     preset_payload = {},                 # Dict of {"scope.key": target_value}. Unlisted keys revert to default.
> #     min_val        = None,               # Numeric lower bound (int/float)
> #     max_val        = None,               # Numeric upper bound (int/float)
> #     step           = None,               # Numeric step size for arrow key adjustments
> #     group          = "OneWord",          # STRICT: UI header string MUST BE ONE WORD.
> #     extended_help  = "Markdown String",  # Explain what the setting does for the user's '?' panel.
> #     is_parent      = False,              # Set True to make this item an expandable folder (Works on ANY type!)
> #     parent_ref     = None,               # Nested UI link. MUST exactly match parent's UID format: "scope.key" (or "key" if DEFAULT)
> #     expanded       = False,              # Default UI state for parent folders (Starts open or closed)
> #
> #     # --- ADVANCED VALIDATION & OVERRIDES ---
> #     warning_msg    = None,               # Adds a ⚠️ marker and displays this warning in the help panel.
> #     popup_message  = None,               # Shows an 'OK' alert dialog AFTER the value is applied.
> #     confirm_message= None,               # Requires Yes/No dialog confirmation BEFORE mutation.
> #     target_file_override = None,         # Reroutes this specific item to a different file path.
> #     engine_type_override = None,         # Changes the parsing engine for this item (e.g., "systemd", "ini").
> # )
> ```


> [!NOTE]- engine router
> ```ini
> # Dusky TUI: Engine Routing Cheat Sheet
> 
> When you create a new schema, look at the target config file. Match its appearance to one of the visual examples below to know exactly what to set `ENGINE_TYPE` to.
> 
> ### 1. The `hyprlang` Engine
> 
> **When to use it:** For anything in the modern Hyprland ecosystem. It recognizes C-like braces, variables starting with `$`, and nested categories. **Target files:** `hyprland.conf`, `hypridle.conf`, `hyprpaper.conf`, `hyprlock.conf` **What the file looks like:** $mainMod = SUPER
> 
> general {
>     gaps_in = 5
>     border_size = 2
> }
> 
> ### 2. The `flatdotconfig` Engine
> 
> **When to use it:** When a file has absolutely no `[sections]` or `{braces}`, but uses a hierarchy separated by dots, and the key is separated from the value by a single space. **Target files:** `config_ui` (GPU Screen Recorder) **What the file looks like:** record.record_options.fps 60 record.container mp4 screenshot.image_quality very_high
> 
> ### 3. The `ini` Engine
> 
> **When to use it:** For classic Linux configuration files divided into `[sections]`. It safely handles keys with or without spaces around the `=` sign, as well as valueless flags. **Target files:** `/etc/pacman.conf`, `~/.config/mako/config`, `makepkg.conf` **What the file looks like:** [options] Color ParallelDownloads = 5
> 
> ### 4. The `bridged_ini` Engine
> 
> **When to use it:** For INI-style files where the system defaults are explicitly written out but _commented out_. This engine reads the commented lines so the TUI can display the default values without marking them as "[Missing]". **Target files:** `/etc/systemd/logind.conf`, `/etc/ssh/sshd_config` **What the file looks like:** [Login] #NAutoVTs=6 #ReserveVT=6 HandlePowerKey=poweroff
> 
> ### 5. The `cmdline` Engine
> 
> **When to use it:** For flat, single-line configuration files where arguments are separated by spaces. **Target files:** `/etc/kernel/cmdline`, `/etc/default/grub` (specifically the `GRUB_CMDLINE_LINUX` line). **What the file looks like:** rw root=UUID=123 quiet splash nvidia-drm.modeset=1
> 
> ### 6. The `lua` Engine
> 
> **When to use it:** When the configuration file is literally a `.lua` programming script. **Target files:** `wezterm.lua`, or users who migrated their Hyprland config to Lua. **What the file looks like:** local config = {} config.fps = 60 return config
> 
> ### 7. The `env` Engine (NEW)
> 
> **When to use it:** For Arch Linux core system files that define environment variables. These strictly forbid spaces around the `=` sign and sometimes use the `export` keyword. 
> **Target files:** `/etc/locale.conf`, `/etc/vconsole.conf`, `/etc/environment`, `~/.config/uwsm/env`
> **What the file looks like:** LANG=en_US.UTF-8
> export WAYLAND_DISPLAY=wayland-1
> 
> ### 8. The `systemd` Engine
> 
> **When to use it:** When you aren't editing a file at all, but rather want the TUI to act as a toggle switch to enable/disable background system or user services. **Target files:** None (Target keys are service names like `bluetooth.service`).
> 
> ### Missing Engines for the Future:
> 
> If you want to expand further, the only two major formats left on Arch Linux that require dedicated parsers are:
> 
> 1. **JSONC (`jsonc`):** Crucial for `~/.config/waybar/config`. Standard JSON parsers destroy comments, so a custom zero-destruction JSONC engine would be powerful.
>     
> 2. **TOML (`toml`):** Crucial for `~/.config/starship.toml` or `alacritty.toml`.
> ```