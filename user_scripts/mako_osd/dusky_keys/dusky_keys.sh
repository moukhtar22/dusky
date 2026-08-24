#!/usr/bin/env bash
# ==============================================================================
# DUSKY KEYS ELITE - ARCH LINUX / WAYLAND OPTIMIZED
# ==============================================================================
# Global Keystroke & Mouse Visualizer with Smart Chording, Compact Symbols,
# TOML Configuration, and Mako OSD Notification Integration.
# ==============================================================================

set -euo pipefail
shopt -s inherit_errexit

RUN_MODE="run"
case "${1:-}" in
    --reset) RUN_MODE="reset" ;;
    --setup) RUN_MODE="setup" ;;
    --config) RUN_MODE="config" ;;
    --help|-h)
        printf "Usage: %s [--setup|--reset|--config]\n" "$(basename -- "$0")"
        exit 0
        ;;
    "") ;;
    *) exit 1 ;;
esac

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║  INTERNAL CONFIGURATION                                                    ║
# ╚════════════════════════════════════════════════════════════════════════════╝

readonly APP_NAME="dusky_keys"
readonly BASE_DIR="$HOME/contained_apps/uv/$APP_NAME"
readonly VENV_DIR="$BASE_DIR/.venv"
readonly PYTHON_BIN="$VENV_DIR/bin/python"
readonly RUNNER_SCRIPT="$BASE_DIR/runner.py"
readonly PID_FILE="$BASE_DIR/$APP_NAME.pid"
readonly LOCK_FILE="$BASE_DIR/$APP_NAME.lock"
readonly MARKER_FILE="$BASE_DIR/.build_marker_v2"
readonly USER_CONFIG_DIR="$HOME/.config/dusky/settings/dusky_keys"
readonly USER_CONFIG_FILE="$USER_CONFIG_DIR/config.toml"

# Notification message when triggered before setup / permissions ready
readonly NOT_SETUP_MSG="Install Dusky Keys from the Control Center or add user to 'input' group."

# --- ANSI COLORS ---
readonly C_RED=$'\033[1;31m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_CYAN=$'\033[1;36m'
readonly C_YELLOW=$'\033[1;33m'
readonly C_RESET=$'\033[0m'

RUNNER_CHILD_PID=""
LOCK_FD=""
LOCK_HELD=false

# --- UTILITY FUNCTIONS ---

notify_user() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical -t 5000 --app-name="dusky-keys" "Dusky Keys" "$1" || true
    fi
}

acquire_lock() {
    mkdir -p "$BASE_DIR" 2>/dev/null || true
    if ! exec {LOCK_FD}> "$LOCK_FILE"; then exit 1; fi
    
    # TOGGLE LOGIC: If lock cannot be acquired, daemon is running -> shut it down.
    if ! flock -n "$LOCK_FD"; then
        printf "%b[TOGGLE]%b Dusky Keys is running. Shutting it down...\n" "${C_YELLOW}" "${C_RESET}"
        if [[ -f "$PID_FILE" ]]; then
            local pid
            pid=$(cat "$PID_FILE")
            kill -TERM "$pid" 2>/dev/null || true
            rm -f "$PID_FILE" 2>/dev/null || true
        fi
        if command -v notify-send >/dev/null 2>&1; then
            notify-send -u low -t 2000 --app-name="dusky-keys" "Dusky Keys" "Visualizer Disabled" || true
        fi
        exit 0
    fi
    LOCK_HELD=true
}

release_lock() {
    [[ "$LOCK_HELD" == true ]] || return 0
    flock -u "$LOCK_FD" 2>/dev/null || true
    exec {LOCK_FD}>&- || true
}

cleanup() {
    if [[ -n "${RUNNER_CHILD_PID:-}" ]]; then
        kill -TERM "$RUNNER_CHILD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" 2>/dev/null || true
    release_lock
}
trap cleanup EXIT INT TERM

current_session_has_input_access() {
    if id -nG | grep -qw -- input; then return 0; fi
    shopt -s nullglob
    for node in /dev/input/event*; do
        if [[ -r "$node" ]]; then shopt -u nullglob; return 0; fi
    done
    shopt -u nullglob
    return 1
}

# --- AUTO-DEPLOY CONFIGURATION ---
deploy_config() {
    mkdir -p "$USER_CONFIG_DIR" 2>/dev/null || true
    if [[ ! -f "$USER_CONFIG_FILE" ]]; then
        cat > "$USER_CONFIG_FILE" << 'TOML_EOF'
# ==============================================================================
# DUSKY KEYS CONFIGURATION
# Location: ~/.config/dusky/settings/dusky_keys/config.toml
# ==============================================================================

[display]
# Maximum number of key items/chords in the OSD notification buffer.
buffer_size = 10

# Display timeout in seconds before clearing the OSD notification.
display_timeout = 2.5

# Use compact symbols for modifier keys and special keys (❖, ⌃, ⌥, ⇧, ⇥, ⏎, ⌫, Esc).
compact_symbols = true

# Wrap key output in Pango HTML markup for Mako styling (set false if your mako shows raw <b> tags).
use_pango_markup = false

# Delimiter between sequential keystroke items in the buffer.
separator = " "

[chording]
# Group held modifier keys + target key into unified chords (e.g. ❖S or ⌃C).
enable_chording = true

# Suppress emitting pure modifier keys when pressed & held down.
# Pure modifier symbol is emitted ONLY if tapped & released alone without pressing another key.
suppress_pure_modifiers = true

[mouse]
# Enable capturing mouse button clicks (Left, Right, Middle, Back, Forward).
enable_mouse = false

# Custom mouse button symbols (used in compact mode)
left_click = "LMB"
right_click = "RMB"
middle_click = "MMB"
side_click = "Back"
extra_click = "Fwd"

[notification]
app_name = "dusky-keys"
sync_id = "dusky-keys-sync"
urgency = "low"

[symbols]
super = "❖"
ctrl = "⌃"
alt = "⌥"
shift = "⇧"
tab = "⇥"
enter = "⏎"
backspace = "⌫"
delete = "⌦"
escape = "⎋"
space = "␣"
caps_lock = "⇪"
up = "↑"
down = "↓"
left = "←"
right = "→"
page_up = "PgUp"
page_down = "PgDn"
home = "Home"
end = "End"
TOML_EOF
        printf "%b[CONFIG]%b Deployed default configuration to %s\n" "${C_GREEN}" "${C_RESET}" "$USER_CONFIG_FILE"
    fi
}

# --- MODE 1: CONFIG MODE ---
if [[ "$RUN_MODE" == "config" ]]; then
    deploy_config
    if [[ -n "${EDITOR:-}" ]]; then
        exec "$EDITOR" "$USER_CONFIG_FILE"
    else
        printf "Config file path: %s\n" "$USER_CONFIG_FILE"
    fi
    exit 0
fi

# --- MODE 2: RESET MODE ---
if [[ "$RUN_MODE" == "reset" ]]; then
    printf "%b[RESET]%b Cleaning Dusky Keys environment...\n" "${C_BLUE}" "${C_RESET}"
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -rf "$BASE_DIR"
    printf "%b[SUCCESS]%b Environment deleted.\n" "${C_GREEN}" "${C_RESET}"
    exit 0
fi

# --- INTERACTIVE DETECTION ---
[[ -t 0 ]] && INTERACTIVE=true || INTERACTIVE=false

# --- INPUT ACCESS CHECK ---
if ! current_session_has_input_access; then
    if ! $INTERACTIVE; then
        notify_user "$NOT_SETUP_MSG"
        exit 1
    fi
    printf "%b[CRITICAL]%b You are not in the 'input' group.\n" "${C_RED}" "${C_RESET}"
    printf "Run: %bsudo usermod -aG input %s%b\n" "${C_CYAN}" "$USER" "${C_RESET}"
    notify_user "Permission Denied. Run: sudo usermod -aG input $USER\nThen log out and log back in."
    exit 1
fi

acquire_lock
deploy_config

# --- DEPENDENCY & VENV SETUP ---
mkdir -p "$BASE_DIR" 2>/dev/null || true

if [[ ! -x "$PYTHON_BIN" ]]; then
    if ! $INTERACTIVE; then
        notify_user "$NOT_SETUP_MSG"
        exit 1
    fi
    printf "%b[BUILD]%b Initializing UV environment...\n" "${C_BLUE}" "${C_RESET}"
    if ! command -v uv >/dev/null 2>&1; then
        printf "%b[ERROR]%b Missing 'uv' package manager.\n" "${C_RED}" "${C_RESET}"
        exit 1
    fi
    uv venv "$VENV_DIR" --quiet
fi

if [[ ! -f "$MARKER_FILE" ]]; then
    if ! $INTERACTIVE; then
        notify_user "$NOT_SETUP_MSG"
        exit 1
    fi
    printf "%b[BUILD]%b Compiling python dependencies with native CPU flags...\n" "${C_YELLOW}" "${C_RESET}"
    export CFLAGS="-march=native -O3 -pipe -flto=auto"
    uv pip install --python "$PYTHON_BIN" --upgrade --no-binary evdev evdev
    uv pip install --python "$PYTHON_BIN" --upgrade --no-binary uvloop uvloop || true
    touch "$MARKER_FILE"
    printf "%b[SUCCESS]%b Native build complete.\n" "${C_GREEN}" "${C_RESET}"
fi

# --- GENERATE PYTHON RUNNER ---
cat > "$RUNNER_SCRIPT" << 'PYTHON_EOF'
import asyncio
import os
import signal
import sys
import tomllib
from pathlib import Path
from evdev import InputDevice, ecodes, list_devices

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

CONFIG_PATH = Path.home() / ".config" / "dusky" / "settings" / "dusky_keys" / "config.toml"

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            print(f"Error reading config: {e}", file=sys.stderr)
    return {}

CONFIG = load_config()

# Configuration sections
CFG_DISP = CONFIG.get("display", {})
CFG_CHORD = CONFIG.get("chording", {})
CFG_MOUSE = CONFIG.get("mouse", {})
CFG_NOTIF = CONFIG.get("notification", {})
CFG_SYM = CONFIG.get("symbols", {})

BUFFER_SIZE = int(CFG_DISP.get("buffer_size", 10))
DISPLAY_TIMEOUT = float(CFG_DISP.get("display_timeout", 2.5))
COMPACT_SYMBOLS = bool(CFG_DISP.get("compact_symbols", True))
PANGO_MARKUP = bool(CFG_DISP.get("use_pango_markup", False))
SEPARATOR = str(CFG_DISP.get("separator", " "))

ENABLE_CHORDING = bool(CFG_CHORD.get("enable_chording", True))
SUPPRESS_PURE_MODS = bool(CFG_CHORD.get("suppress_pure_modifiers", True))
ENABLE_MOUSE = bool(CFG_MOUSE.get("enable_mouse", False))

APP_NAME = str(CFG_NOTIF.get("app_name", "dusky-keys"))
SYNC_ID = str(CFG_NOTIF.get("sync_id", "dusky-keys-sync"))
URGENCY = str(CFG_NOTIF.get("urgency", "low"))

# Symbol Mappings
def get_sym(key, default_compact, default_full):
    if COMPACT_SYMBOLS:
        return CFG_SYM.get(key, default_compact)
    return default_full

SYM_SUPER = get_sym("super", "❖", "Super")
SYM_CTRL = get_sym("ctrl", "⌃", "Ctrl")
SYM_ALT = get_sym("alt", "⌥", "Alt")
SYM_SHIFT = get_sym("shift", "⇧", "Shift")

MOD_MAP = {
    ecodes.KEY_LEFTMETA: ("super", SYM_SUPER),
    ecodes.KEY_RIGHTMETA: ("super", SYM_SUPER),
    ecodes.KEY_LEFTCTRL: ("ctrl", SYM_CTRL),
    ecodes.KEY_RIGHTCTRL: ("ctrl", SYM_CTRL),
    ecodes.KEY_LEFTALT: ("alt", SYM_ALT),
    ecodes.KEY_RIGHTALT: ("alt", SYM_ALT),
    ecodes.KEY_LEFTSHIFT: ("shift", SYM_SHIFT),
    ecodes.KEY_RIGHTSHIFT: ("shift", SYM_SHIFT),
}

KEYMAP = {
    ecodes.KEY_A: ('a', 'A'), ecodes.KEY_B: ('b', 'B'), ecodes.KEY_C: ('c', 'C'),
    ecodes.KEY_D: ('d', 'D'), ecodes.KEY_E: ('e', 'E'), ecodes.KEY_F: ('f', 'F'),
    ecodes.KEY_G: ('g', 'G'), ecodes.KEY_H: ('h', 'H'), ecodes.KEY_I: ('i', 'I'),
    ecodes.KEY_J: ('j', 'J'), ecodes.KEY_K: ('k', 'K'), ecodes.KEY_L: ('l', 'L'),
    ecodes.KEY_M: ('m', 'M'), ecodes.KEY_N: ('n', 'N'), ecodes.KEY_O: ('o', 'O'),
    ecodes.KEY_P: ('p', 'P'), ecodes.KEY_Q: ('q', 'Q'), ecodes.KEY_R: ('r', 'R'),
    ecodes.KEY_S: ('s', 'S'), ecodes.KEY_T: ('t', 'T'), ecodes.KEY_U: ('u', 'U'),
    ecodes.KEY_V: ('v', 'V'), ecodes.KEY_W: ('w', 'W'), ecodes.KEY_X: ('x', 'X'),
    ecodes.KEY_Y: ('y', 'Y'), ecodes.KEY_Z: ('z', 'Z'),
    ecodes.KEY_1: ('1', '!'), ecodes.KEY_2: ('2', '@'), ecodes.KEY_3: ('3', '#'),
    ecodes.KEY_4: ('4', '$'), ecodes.KEY_5: ('5', '%'), ecodes.KEY_6: ('6', '^'),
    ecodes.KEY_7: ('7', '&'), ecodes.KEY_8: ('8', '*'), ecodes.KEY_9: ('9', '('),
    ecodes.KEY_0: ('0', ')'),
    ecodes.KEY_MINUS: ('-', '_'), ecodes.KEY_EQUAL: ('=', '+'),
    ecodes.KEY_LEFTBRACE: ('[', '{'), ecodes.KEY_RIGHTBRACE: (']', '}'),
    ecodes.KEY_BACKSLASH: ('\\', '|'), ecodes.KEY_SEMICOLON: (';', ':'),
    ecodes.KEY_APOSTROPHE: ("'", '"'), ecodes.KEY_GRAVE: ('`', '~'),
    ecodes.KEY_COMMA: (',', '<'), ecodes.KEY_DOT: ('.', '>'), ecodes.KEY_SLASH: ('/', '?'),
    ecodes.KEY_SPACE: (' ', ' '),
}

SPECIAL_KEYS = {
    ecodes.KEY_TAB: get_sym("tab", "⇥", "Tab"),
    ecodes.KEY_ENTER: get_sym("enter", "⏎", "Enter"),
    ecodes.KEY_KPENTER: get_sym("enter", "⏎", "Enter"),
    ecodes.KEY_BACKSPACE: get_sym("backspace", "⌫", "Backspace"),
    ecodes.KEY_DELETE: get_sym("delete", "⌦", "Delete"),
    ecodes.KEY_ESC: get_sym("escape", "⎋", "Esc"),
    ecodes.KEY_CAPSLOCK: get_sym("caps_lock", "⇪", "CapsLock"),
    ecodes.KEY_UP: get_sym("up", "↑", "Up"),
    ecodes.KEY_DOWN: get_sym("down", "↓", "Down"),
    ecodes.KEY_LEFT: get_sym("left", "←", "Left"),
    ecodes.KEY_RIGHT: get_sym("right", "→", "Right"),
    ecodes.KEY_PAGEUP: get_sym("page_up", "PgUp", "PgUp"),
    ecodes.KEY_PAGEDOWN: get_sym("page_down", "PgDn", "PgDn"),
    ecodes.KEY_HOME: get_sym("home", "Home", "Home"),
    ecodes.KEY_END: get_sym("end", "End", "End"),
}

# Function keys F1-F12
for i in range(1, 13):
    fk_code = getattr(ecodes, f"KEY_F{i}", None)
    if fk_code:
        SPECIAL_KEYS[fk_code] = f"F{i}"

MOUSE_BUTTONS = {
    ecodes.BTN_LEFT: CFG_MOUSE.get("left_click", "LMB"),
    ecodes.BTN_RIGHT: CFG_MOUSE.get("right_click", "RMB"),
    ecodes.BTN_MIDDLE: CFG_MOUSE.get("middle_click", "MMB"),
    ecodes.BTN_SIDE: CFG_MOUSE.get("side_click", "Back"),
    ecodes.BTN_EXTRA: CFG_MOUSE.get("extra_click", "Fwd"),
}

# State variables
_held_mods = {}           # mod_name -> symbol
_mod_used_in_chord = False
_shift_pressed = False
_caps_active = False
_key_buffer = []
_clear_task = None

async def update_display():
    if not _key_buffer:
        return
    display_text = SEPARATOR.join(_key_buffer)
    proc = await asyncio.create_subprocess_exec(
        "notify-send", "-a", APP_NAME,
        "-u", URGENCY,
        "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}",
        "-t", "3000", display_text,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

async def clear_buffer_after_delay():
    global _key_buffer
    await asyncio.sleep(DISPLAY_TIMEOUT)
    _key_buffer.clear()
    proc = await asyncio.create_subprocess_exec(
        "notify-send", "-a", APP_NAME,
        "-u", URGENCY,
        "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}",
        "-t", "1", " ",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()

def push_to_buffer(item):
    global _clear_task, _key_buffer
    _key_buffer.append(item)
    if len(_key_buffer) > BUFFER_SIZE:
        _key_buffer.pop(0)

    if _clear_task and not _clear_task.done():
        _clear_task.cancel()
    _clear_task = asyncio.create_task(clear_buffer_after_delay())
    asyncio.create_task(update_display())

def process_event(event):
    global _held_mods, _mod_used_in_chord, _shift_pressed, _caps_active

    # --- 1. MODIFIER HANDLING ---
    if event.code in MOD_MAP:
        mod_name, mod_sym = MOD_MAP[event.code]
        if event.code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            _shift_pressed = (event.value in (1, 2))

        if event.value in (1, 2):  # Press or repeat
            _held_mods[mod_name] = mod_sym
        elif event.value == 0:     # Release
            if mod_name in _held_mods:
                del _held_mods[mod_name]
                # If modifier was tapped alone without forming a chord:
                if not _mod_used_in_chord and SUPPRESS_PURE_MODS:
                    if not _held_mods:  # All mods released
                        push_to_buffer(f"<b>{mod_sym}</b>" if PANGO_MARKUP else mod_sym)
                if not _held_mods:
                    _mod_used_in_chord = False
        return

    # Process only press events (value == 1) for regular keys & buttons
    if event.value != 1:
        return

    if event.code == ecodes.KEY_CAPSLOCK:
        _caps_active = not _caps_active

    # --- 2. RESOLVE BASE KEY STRING ---
    raw_char = ""
    is_alpha = False
    is_special_or_mouse = False

    if event.code in KEYMAP:
        base, shifted = KEYMAP[event.code]
        if base.isalpha():
            is_alpha = True
            raw_char = shifted if (_shift_pressed ^ _caps_active) else base
        else:
            raw_char = shifted if _shift_pressed else base
    elif event.code in SPECIAL_KEYS:
        is_special_or_mouse = True
        raw_char = SPECIAL_KEYS[event.code]
    elif ENABLE_MOUSE and event.code in MOUSE_BUTTONS:
        is_special_or_mouse = True
        raw_char = MOUSE_BUTTONS[event.code]
    else:
        return

    # --- 3. CHORDING & FORMATTING ---
    has_primary_mod = any(m in _held_mods for m in ("super", "ctrl", "alt"))
    is_chord = (has_primary_mod or (is_special_or_mouse and bool(_held_mods))) and ENABLE_CHORDING

    if is_chord:
        _mod_used_in_chord = True
        ordered_syms = []
        for m in ("super", "ctrl", "alt", "shift"):
            if m in _held_mods:
                ordered_syms.append(_held_mods[m])

        chord_key = raw_char.upper() if (is_alpha or len(raw_char) == 1) else raw_char
        if COMPACT_SYMBOLS:
            chord_str = "".join(ordered_syms) + chord_key
        else:
            chord_str = "+".join(ordered_syms) + "+" + chord_key

        formatted = f"<b>{chord_str}</b>" if PANGO_MARKUP else chord_str
        push_to_buffer(formatted)
    else:
        # Non-chorded keypress (clean text without HTML tags)
        formatted = f"<b>{raw_char}</b>" if (PANGO_MARKUP and len(raw_char) > 1) else raw_char
        push_to_buffer(formatted)

async def read_device(dev: InputDevice, stop: asyncio.Event):
    try:
        async for event in dev.async_read_loop():
            if stop.is_set():
                break
            if event.type == ecodes.EV_KEY:
                process_event(event)
    except OSError:
        pass
    finally:
        try:
            dev.close()
        except OSError:
            pass

def scan_devices(monitored_tasks, stop):
    dead_paths = [p for p, t in monitored_tasks.items() if t.done()]
    for p in dead_paths:
        monitored_tasks.pop(p)

    for path in list_devices():
        if path in monitored_tasks:
            continue
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps:
                ev_keys = caps.get(ecodes.EV_KEY, [])
                is_keyboard = ecodes.KEY_ENTER in ev_keys
                is_mouse = ENABLE_MOUSE and ecodes.BTN_LEFT in ev_keys
                if is_keyboard or is_mouse:
                    monitored_tasks[path] = asyncio.create_task(read_device(dev, stop))
                else:
                    dev.close()
            else:
                dev.close()
        except OSError:
            pass

async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop():
        loop.call_soon_threadsafe(stop.set)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)

    monitored_tasks = {}
    print("Dusky Keys engine started...")

    try:
        scan_devices(monitored_tasks, stop)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                scan_devices(monitored_tasks, stop)
    finally:
        for task in monitored_tasks.values():
            task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
PYTHON_EOF

# --- EXECUTION ---
if [[ "$RUN_MODE" == "setup" ]]; then
    printf "%b[SUCCESS]%b Setup complete.\n" "${C_GREEN}" "${C_RESET}"
    exit 0
fi

printf "%b[RUN]%b Starting Dusky Keys background daemon...\n" "${C_BLUE}" "${C_RESET}"
"$PYTHON_BIN" -OO -B "$RUNNER_SCRIPT" &
RUNNER_CHILD_PID="$!"
echo "$RUNNER_CHILD_PID" > "$PID_FILE"

# Send notification that engine is enabled
if command -v notify-send >/dev/null 2>&1; then
    notify-send -u low -t 2000 --app-name="dusky-keys" "Dusky Keys" "Visualizer Enabled" || true
fi

wait "$RUNNER_CHILD_PID"
