#!/usr/bin/env python3

import os
import sys
import json
import re
import shlex
import datetime
import subprocess
import tempfile

# --- Global Environment Setup ---

def get_state_file():
    """Manages the lifecycle of the shared state file across FZF execution boundaries."""
    sf = os.environ.get("DUSKY_JOURNAL_STATE")
    if not sf:
        fd, sf = tempfile.mkstemp(prefix="dusky_", suffix=".json")
        os.close(fd)
        os.environ["DUSKY_JOURNAL_STATE"] = sf
        reset_state()
    return sf

def load_state():
    sf = get_state_file()
    try:
        with open(sf, 'r') as f:
            return json.load(f)
    except Exception:
        return reset_state()

def save_state(state):
    with open(get_state_file(), 'w') as f:
        json.dump(state, f)

def reset_state():
    state = {
        "boot": "0",
        "sys_units": [],
        "user_units": [],
        "invocations": [],
        "priority": "",
        "since": "",
        "kernel_only": False,
        "reverse": True
    }
    save_state(state)
    return state


# --- Theme & Configuration Loading ---

def load_theme():
    """Loads the dusky_tui.json theme, merging with defaults if fields are missing."""
    theme = {
        "bg": "#151218",
        "fg": "#e7e0e8",
        "accent": "#d8bafb",
        "error": "#ffb4ab",
        "warning": "#cfc1da",
        "success": "#f2b7c1",
        "muted": "#4a454e"
    }
    theme_path = os.path.expanduser("~/.config/matugen/generated/dusky_tui.json")
    if os.path.exists(theme_path):
        try:
            with open(theme_path, 'r') as f:
                data = json.load(f)
            theme.update(data)
        except Exception:
            pass
    return theme

def hex_to_ansi(hex_color, bg=False):
    """Converts a hex color string to a 24-bit ANSI escape sequence."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return "\033[0m"
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    code = 48 if bg else 38
    return f"\033[{code};2;{r};{g};{b}m"

def get_fzf_color_args(theme):
    """Generates the --color argument for fzf mapping the theme constraints."""
    c = f"bg:{theme['bg']},fg:{theme['fg']},bg+:{theme['bg']},fg+:{theme['fg']}"
    c += f",hl:{theme['accent']},hl+:{theme['accent']}"
    c += f",prompt:{theme['accent']},pointer:{theme['accent']},marker:{theme['success']}"
    # Use 'warning' for borders/info so they remain highly visible (muted was too dark)
    c += f",info:{theme['warning']},border:{theme['warning']}"
    c += f",preview-bg:{theme['bg']},preview-fg:{theme['fg']}"
    c += f",header:{theme['warning']},gutter:{theme['bg']},query:{theme['fg']}"
    c += f",preview-border:{theme['warning']},label:{theme['accent']}"
    return ["--color", c]


# --- TUI Utilities ---

def strip_ansi(text: str) -> str:
    """Removes ANSI escape sequences to accurately calculate visual string width."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)

def draw_tui_panel(theme: dict, title: str, lines: list[str], width: int = 46) -> None:
    """Renders a pixel-perfect boxed panel adapting perfectly to text length."""
    c_bord = hex_to_ansi(theme['warning']) # Highly visible border
    c_acc = hex_to_ansi(theme['accent'])
    c_reset = "\033[0m"
    
    title_clean = strip_ansi(title)
    dash_count = max(0, width - len(title_clean) - 5)
    print(f"{c_bord}╭─ {title} {c_bord}{'─' * dash_count}╮{c_reset}")
    
    for line in lines:
        line_clean = strip_ansi(line)
        pad = max(0, width - len(line_clean) - 4)
        print(f"{c_bord}│{c_reset} {line}{' ' * pad} {c_bord}│{c_reset}")
        
    print(f"{c_bord}╰{'─' * (width - 2)}╯{c_reset}\n")


# --- Command Builder ---

def build_journalctl_args(state):
    """Converts the internal state map to a list of arguments for journalctl."""
    args = []
    if state["boot"] is not None:
        args.extend(["-b", str(state["boot"])])
    for u in state["sys_units"]:
        args.extend(["-u", u])
    for u in state["user_units"]:
        args.extend(["--user-unit", u])
    for i in state["invocations"]:
        args.extend(["-I", str(i)])
    if state["priority"]:
        args.extend(["-p", str(state["priority"])])
    if state["since"]:
        args.extend(["--since", state["since"]])
    if state["kernel_only"]:
        args.append("-k")
    if state["reverse"]:
        args.append("-r")
    return args


# --- FZF Sub-Menus & Modals ---

def fzf_prompt(items, prompt, header=""):
    """An interactive sub-modal utilizing fzf for selection tasks."""
    theme = load_theme()
    cmd = ['fzf', '--ansi', '--prompt', prompt, '--layout=reverse-list']
    if header:
        cmd.extend(['--header', header])
    cmd.extend(get_fzf_color_args(theme))
    cmd.extend(['--border', 'rounded', '--color', f'label:{theme["accent"]}'])
    
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate("\n".join(items))
    return out.strip().split('\n')[0] if out.strip() else ""

def interactive_set_boot():
    cmd = ["journalctl", "--list-boots", "--no-pager"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = ["0 (Current Boot)", "all (All Boots)"] + [l for l in res.stdout.strip().split('\n') if l]
    choice = fzf_prompt(lines, " :: Select Boot ❯ ", "journalctl --list-boots")
    if choice:
        state = load_state()
        if "Current Boot" in choice:
            state["boot"] = "0"
        elif "All Boots" in choice:
            state["boot"] = None
        else:
            parts = choice.strip().split()
            state["boot"] = parts[1] if len(parts[1]) == 32 else parts[0]
        save_state(state)
        return True
    return False

def interactive_add_sys_unit():
    cmd = ["systemctl", "list-units", "--all", "--type=service,timer,socket", "--no-pager", "--no-legend"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = [line.strip() for line in res.stdout.strip().split('\n') if line.strip()]
    choice = fzf_prompt(lines, " :: System Unit ❯ ", " ".join(cmd))
    if choice:
        state = load_state()
        state["sys_units"].append(choice.split()[0])
        save_state(state)
        return True
    return False

def interactive_add_usr_unit():
    cmd = ["systemctl", "--user", "list-units", "--all", "--type=service,timer,socket", "--no-pager", "--no-legend"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = [line.strip() for line in res.stdout.strip().split('\n') if line.strip()]
    choice = fzf_prompt(lines, " :: User Unit ❯ ", " ".join(cmd))
    if choice:
        state = load_state()
        state["user_units"].append(choice.split()[0])
        save_state(state)
        return True
    return False

def interactive_add_invocation():
    state = load_state()
    unit = None
    user_mode = False
    
    if state["sys_units"]:
        unit = state["sys_units"][0]
    elif state["user_units"]:
        unit = state["user_units"][0]
        user_mode = True
    else:
        fzf_prompt(["Select a unit first (ctrl-u or alt-u)."], " :: Info ❯ ")
        return False

    cmd = ["journalctl", "--list-invocations", "--no-pager"]
    if user_mode:
        cmd += ["--user-unit", unit]
    else:
        cmd += ["-u", unit]

    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = [l for l in res.stdout.strip().split('\n') if l]
    if res.returncode != 0 or not lines or "No invocations" in res.stdout:
         fzf_prompt([f"No invocations found for {unit}"], " :: Info ❯ ")
         return False

    choice = fzf_prompt(lines, " :: Select Invocation ❯ ", " ".join(cmd))
    if choice:
        parts = choice.strip().split()
        if len(parts) >= 2:
            state["invocations"] = [parts[1] if len(parts[1]) == 32 else parts[0]]
            save_state(state)
            return True
    return False

def interactive_set_priority():
    options = ["0 (emerg)", "1 (alert)", "2 (crit)", "3 (err)", "4 (warning)", "5 (notice)", "6 (info)", "7 (debug)"]
    choice = fzf_prompt(options, " :: Select Priority ❯ ", "journalctl -p")
    if choice:
        state = load_state()
        state["priority"] = choice.split()[0]
        save_state(state)
        return True
    return False

def interactive_set_time():
    options = ["today", "yesterday", "since 1 hour ago", "since 24 hours ago", "all time"]
    choice = fzf_prompt(options, " :: Time Range ❯ ", "journalctl --since")
    if choice:
        state = load_state()
        state["since"] = "" if "all time" in choice else choice
        save_state(state)
        return True
    return False

def toggle_kernel():
    state = load_state()
    state["kernel_only"] = not state["kernel_only"]
    save_state(state)

def toggle_reverse():
    state = load_state()
    state["reverse"] = not state["reverse"]
    save_state(state)

def view_disk_usage():
    cmd = ["journalctl", "--disk-usage"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = res.stdout.strip().split('\n')
    lines.extend(["", "--- Vacuum Options (Requires Root) ---", "1. Vacuum time (keep 7 days)", "2. Vacuum size (keep 500M)", "3. Return"])
    choice = fzf_prompt(lines, " :: Disk Usage ❯ ", "journalctl --disk-usage")
    if choice:
        if "Vacuum time" in choice:
            subprocess.run(["journalctl", "--vacuum-time=7d"])
        elif "Vacuum size" in choice:
            subprocess.run(["journalctl", "--vacuum-size=500M"])


# --- Middle-tier Stream Processing ---

def stream_journal():
    """
    Acts as an internal pipeline: Executes journalctl -o json, formats it into an ANSI string,
    and appends the raw cursor ID behind a special Unit Separator (\\x1f) to enable FZF previews.
    """
    state = load_state()
    args = build_journalctl_args(state)
    cmd = ['journalctl', '-o', 'json'] + args
    
    # Unbuffer stdout natively where possible, preventing stalling
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)

    theme = load_theme()
    c_err = hex_to_ansi(theme['error'])
    c_warn = hex_to_ansi(theme['warning']) # Visible mid-tone
    c_fg = hex_to_ansi(theme['fg'])
    c_accent = hex_to_ansi(theme['accent'])
    c_reset = "\033[0m"
    
    c_sep = f"{c_warn}│{c_reset}"

    for i, line in enumerate(proc.stdout):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            cursor = entry.get('__CURSOR', '')

            ts_micro = entry.get('__REALTIME_TIMESTAMP')
            if ts_micro:
                dt = datetime.datetime.fromtimestamp(int(ts_micro) / 1000000.0)
                ts_str = dt.strftime('%m-%d %H:%M:%S')
            else:
                ts_str = "Unknown"

            pri = str(entry.get('PRIORITY', '6'))
            if pri in ('0', '1', '2', '3'):
                c_pri = c_err
                icon = "󰅙 "
            elif pri == '4':
                c_pri = c_warn
                icon = "󰀪 "
            elif pri in ('5', '6'):
                c_pri = c_fg
                icon = "󰋽 "
            else:
                c_pri = c_warn
                icon = "󰌽 "

            unit = entry.get('USER_UNIT') or entry.get('_SYSTEMD_USER_UNIT') or entry.get('_SYSTEMD_UNIT') or entry.get('SYSLOG_IDENTIFIER') or 'kernel'
            unit_display = (unit[:21] + "..") if len(unit) > 23 else unit.ljust(23)

            msg = entry.get('MESSAGE', '')
            if isinstance(msg, list):
                try:
                    msg = bytes(msg).decode('utf-8', errors='replace')
                except Exception:
                    msg = "[Binary Data]"
            elif not isinstance(msg, str):
                msg = str(msg)
            msg = msg.replace('\n', ' ')

            # [REFINED LAYOUT]: Perfect column alignment mapping structural visuals clearly
            str_ts = f"{c_warn}{ts_str}{c_reset}"
            str_unit = f"{c_fg}{unit_display}{c_reset}"
            str_msg = f"{c_pri}{icon}{msg}{c_reset}"
            
            formatted = f" {str_ts} {c_sep} {str_unit} {c_sep} {str_msg}"
            sys.stdout.write(f"{formatted}\x1f{cursor}\n")
            
            if i % 100 == 0:
                sys.stdout.flush()
        except Exception:
            pass
    
    sys.stdout.flush()


def preview_journal_entry(cursor: str):
    """
    Renders the beautifully formatted, colorful preview pane for the selected log entry.
    """
    if not cursor:
        return

    theme = load_theme()
    c_acc = hex_to_ansi(theme['accent'])
    c_fg = hex_to_ansi(theme['fg'])
    c_bord = hex_to_ansi(theme['warning']) # Perfect bright-gray/lavender for keys and borders
    c_err = hex_to_ansi(theme['error'])
    c_suc = hex_to_ansi(theme['success'])
    c_reset = "\033[0m"

    cmd = ['journalctl', '--cursor', cursor, '-n', '1', '-o', 'json']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        print(f"{c_err}[!] Failed to load log details.{c_reset}")
        return
        
    try:
        entry = json.loads(res.stdout.strip().split('\n')[0])
    except Exception as e:
        print(f"{c_err}[!] Error parsing log entry: {e}{c_reset}")
        return

    # 1. Shortcuts Panel mapped specifically to theme success/warning/accent
    shortcut_lines = [
        f"{c_suc}[CTRL-B]{c_reset}   {c_fg}󰑐 Filter by Boot{c_reset}",
        f"{c_suc}[CTRL-U]{c_reset}   {c_fg}󰒋 Filter by System Unit{c_reset}",
        f"{c_suc}[ALT-U]{c_reset}    {c_fg}󰋜 Filter by User Unit{c_reset}",
        f"{c_suc}[CTRL-I]{c_reset}   {c_fg}󰆧 Filter by Invocation{c_reset}",
        f"{c_suc}[CTRL-P]{c_reset}   {c_fg}󰈸 Filter by Priority{c_reset}",
        f"{c_suc}[CTRL-T]{c_reset}   {c_fg}󰔠 Filter by Time Range{c_reset}",
        f"{c_acc}[CTRL-K]{c_reset}   {c_fg}󰣇 Toggle Kernel Logs{c_reset}",
        f"{c_acc}[ALT-R]{c_reset}    {c_fg}󰑃 Toggle Reverse Order{c_reset}",
        f"{c_err}[CTRL-R]{c_reset}   {c_fg}󰑓 Reset All Filters{c_reset}",
        f"{c_bord}[ESC]{c_reset}      {c_fg}󰅙 Back / Exit{c_reset}"
    ]
    draw_tui_panel(theme, f"{c_acc}󰏖 SHORTCUTS{c_reset}", shortcut_lines, width=46)

    # 2. Main Log Details Section
    print(f"{c_acc}󰆑 LOG DETAILS{c_reset}")
    print(f"{c_bord}" + "─" * 46 + c_reset)

    keys_to_show = ['__REALTIME_TIMESTAMP', 'PRIORITY', '_SYSTEMD_UNIT', 'SYSLOG_IDENTIFIER', '_PID', '_UID', '_COMM', '_EXE', '_CMDLINE']
    
    ts = entry.get('__REALTIME_TIMESTAMP')
    if ts:
        dt = datetime.datetime.fromtimestamp(int(ts) / 1000000.0)
        # Using c_bord for pipes and labels for superior contrast
        print(f" {c_acc}{'DATE':<12}{c_reset} {c_bord}│{c_reset} {c_fg}{dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}{c_reset}")

    for k in keys_to_show[1:]:
        val = entry.get(k)
        if val:
            label = k.replace('_', '').strip()
            if label == 'SYSTEMDUNIT': label = 'UNIT'
            if label == 'SYSLOGIDENTIFIER': label = 'SYSLOG ID'
            print(f" {c_acc}{label:<12}{c_reset} {c_bord}│{c_reset} {c_fg}{val}{c_reset}")

    print(f"{c_bord}" + "─" * 46 + c_reset)
    
    msg = entry.get('MESSAGE', '')
    if isinstance(msg, list):
        try:
            msg = bytes(msg).decode('utf-8', errors='replace')
        except Exception:
            msg = "[Binary Data]"
    
    print(f"\n{c_fg}{msg}{c_reset}\n")
    
    # 3. Raw Fields dynamically rendered at the bottom
    exclude = set(keys_to_show + ['MESSAGE', '__CURSOR', '__MONOTONIC_TIMESTAMP', '_SOURCE_REALTIME_TIMESTAMP', 'SYSLOG_FACILITY'])
    extra = {k: v for k, v in entry.items() if k not in exclude and not k.startswith('__')}
    
    if extra:
        print(f"{c_acc}󰐃 RAW FIELDS{c_reset}")
        print(f"{c_bord}" + "─" * 46 + c_reset)
        for k, v in sorted(extra.items()):
            val_str = str(v).replace('\n', ' ')
            if len(val_str) > 70: val_str = val_str[:67] + '...'
            print(f" {c_bord}{k:<20}{c_reset} {c_bord}={c_reset} {c_fg}{val_str}{c_reset}")


# --- FZF Transformer ---

def get_actions():
    """
    Hook executed by FZF `transform` feature to update the UI gracefully without redrawing
    the entire screen. Generates `reload` and `change-header`/`change-footer` operations dynamically.
    """
    state = load_state()
    args = build_journalctl_args(state)
    cmd_str = "journalctl " + " ".join(shlex.quote(a) for a in args)
    
    theme = load_theme()
    c_acc = hex_to_ansi(theme['accent'])
    c_warn = hex_to_ansi(theme['warning'])
    c_fg = hex_to_ansi(theme['fg'])
    c_reset = "\033[0m"

    filters = []
    if state["boot"] is not None: filters.append(f"Boot:{state['boot']}")
    if state["sys_units"]: filters.append(f"SysUnit:{','.join(state['sys_units'])}")
    if state["user_units"]: filters.append(f"UsrUnit:{','.join(state['user_units'])}")
    if state["invocations"]: filters.append(f"Invoc:{','.join(state['invocations'])}")
    if state["priority"]: filters.append(f"Prio:{state['priority']}")
    if state["since"]: filters.append(f"Since:{state['since']}")
    if state["kernel_only"]: filters.append("Kernel:Yes")
    
    filter_val = f" {c_warn}|{c_reset} ".join(f"{c_fg}{f}{c_reset}" for f in filters) if filters else f"{c_warn}None{c_reset}"
    order_str = f"{c_warn}[Reverse Order]{c_reset}" if state["reverse"] else f"{c_warn}[Chronological Order]{c_reset}"
    header_text = f" {c_acc}󰈸 ACTIVE FILTERS:{c_reset} {filter_val}  {order_str}"
    
    exe = shlex.quote(sys.executable)
    script = shlex.quote(os.path.abspath(__file__))
    reload_cmd = f"{exe} {script} stream"
    
    # We use the syntax `[...]` to prevent FZF payload injection breakage on special chars
    print(f"reload[{reload_cmd}]+change-header[{header_text}]+change-footer[ Cmd: {cmd_str}]")


# --- Main Application Runner ---

def run_log_viewer():
    """Renders the main FZF log environment adhering rigorously to the snapshot manager aesthetics."""
    theme = load_theme()
    state = load_state()
    
    exe = shlex.quote(sys.executable)
    script = shlex.quote(os.path.abspath(__file__))
    
    c_acc = hex_to_ansi(theme['accent'])
    c_warn = hex_to_ansi(theme['warning'])
    c_fg = hex_to_ansi(theme['fg'])
    c_reset = "\033[0m"

    filters = []
    if state["boot"] is not None: filters.append(f"Boot:{state['boot']}")
    if state["sys_units"]: filters.append(f"SysUnit:{','.join(state['sys_units'])}")
    if state["user_units"]: filters.append(f"UsrUnit:{','.join(state['user_units'])}")
    if state["invocations"]: filters.append(f"Invoc:{','.join(state['invocations'])}")
    if state["priority"]: filters.append(f"Prio:{state['priority']}")
    if state["since"]: filters.append(f"Since:{state['since']}")
    if state["kernel_only"]: filters.append("Kernel:Yes")
    
    filter_val = f" {c_warn}|{c_reset} ".join(f"{c_fg}{f}{c_reset}" for f in filters) if filters else f"{c_warn}None{c_reset}"
    order_str = f"{c_warn}[Reverse Order]{c_reset}" if state["reverse"] else f"{c_warn}[Chronological Order]{c_reset}"
    header_text = f" {c_acc}󰈸 ACTIVE FILTERS:{c_reset} {filter_val}  {order_str}"

    cmd_str = "journalctl " + " ".join(shlex.quote(a) for a in build_journalctl_args(state))

    # Rigorous Layout Engineering
    fzf_cmd = ['fzf', '--ansi', '--delimiter', '\x1f', '--with-nth', '1']
    fzf_cmd += ['--layout=reverse-list'] 
    
    # Utilize Python for rendering a fully structured right-side preview pane
    fzf_cmd += ['--preview', f"{exe} {script} preview {{2}}"]
    fzf_cmd += ['--preview-window', 'right,48%,border-left,wrap']
    
    fzf_cmd += ['--border', 'rounded', '--border-label', ' 󰆑 Dusky Journal ']
    fzf_cmd += ['--preview-label', ' Details ']
    fzf_cmd += ['--header', header_text]
    fzf_cmd += ['--footer', f' Cmd: {cmd_str}']
    fzf_cmd += ['--prompt', ' :: Journal Logs ❯ ']
    fzf_cmd += ['--pointer=▌', '--marker=▶']
    fzf_cmd += ['--info=hidden', '--no-hscroll', '--ellipsis=...']
    
    fzf_cmd += get_fzf_color_args(theme)
    fzf_cmd += ['--expect', 'ctrl-b,ctrl-u,alt-u,ctrl-i,ctrl-p,ctrl-t,ctrl-k,ctrl-r,alt-r,esc']
    
    # High-level event-driven interaction pipeline natively parsing fzf's 0.73.1 syntax safely
    def bind_action(key, action_cmd):
        if action_cmd in ('toggle-kernel', 'toggle-reverse', 'reset'):
            return f"{key}:execute-silent({exe} {script} {action_cmd})+transform[{exe} {script} get-actions]"
        else:
            return f"{key}:execute({exe} {script} {action_cmd})+transform[{exe} {script} get-actions]"

    fzf_cmd += ['--bind', bind_action('ctrl-b', 'set-boot')]
    fzf_cmd += ['--bind', bind_action('ctrl-u', 'add-sys-unit')]
    fzf_cmd += ['--bind', bind_action('alt-u', 'add-usr-unit')]
    fzf_cmd += ['--bind', bind_action('ctrl-i', 'add-invocation')]
    fzf_cmd += ['--bind', bind_action('ctrl-p', 'set-priority')]
    fzf_cmd += ['--bind', bind_action('ctrl-t', 'set-time')]
    fzf_cmd += ['--bind', bind_action('ctrl-k', 'toggle-kernel')]
    fzf_cmd += ['--bind', bind_action('alt-r', 'toggle-reverse')]
    fzf_cmd += ['--bind', bind_action('ctrl-r', 'reset')]

    # Execute main sub-shell safely and interactively
    stream_cmd = [sys.executable, os.path.abspath(__file__), "stream"]
    stream_proc = subprocess.Popen(stream_cmd, stdout=subprocess.PIPE)
    subprocess.run(fzf_cmd, stdin=stream_proc.stdout)
    stream_proc.stdout.close()


def main_menu():
    """Standard user-centric onboarding view offering logical abstractions."""
    while True:
        options = [
            "1. 󰑐 Current Boot Logs",
            "2. 󰒋 Browse Previous Boots",
            "3. 󰒄 System Unit Logs",
            "4. 󰋜 User Unit Logs",
            "5. 󰆧 Failed System Units",
            "6. 󰆧 Failed User Units",
            "7. 󰣇 Kernel Logs",
            "8. 󰋊 Disk Usage & Maintenance",
            "9. 󰅙 Quit"
        ]
        choice = fzf_prompt(options, " :: Main Menu ❯ ", " 󰆑 Dusky Navigator ")
        
        if not choice or "Quit" in choice:
            break

        reset_state()
        state = load_state()

        if "Current Boot Logs" in choice:
            pass # State initializes properly to '0'
        elif "Browse Previous Boots" in choice:
            if not interactive_set_boot(): continue
        elif "System Unit Logs" in choice:
            if not interactive_add_sys_unit(): continue
        elif "User Unit Logs" in choice:
            if not interactive_add_usr_unit(): continue
        elif "Failed System Units" in choice:
            cmd = ["systemctl", "list-units", "--state=failed", "--no-pager", "--no-legend"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            lines = [l.strip() for l in res.stdout.strip().split('\n') if l.strip()]
            if not lines:
                fzf_prompt(["No failed system units found."], " :: Info ❯ ")
                continue
            sel = fzf_prompt(lines, " :: Failed Sys Unit ❯ ", " ".join(cmd))
            if sel:
                state["sys_units"].append(sel.split()[0])
                save_state(state)
            else: continue
        elif "Failed User Units" in choice:
            cmd = ["systemctl", "--user", "list-units", "--state=failed", "--no-pager", "--no-legend"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            lines = [l.strip() for l in res.stdout.strip().split('\n') if l.strip()]
            if not lines:
                fzf_prompt(["No failed user units found."], " :: Info ❯ ")
                continue
            sel = fzf_prompt(lines, " :: Failed Usr Unit ❯ ", " ".join(cmd))
            if sel:
                state["user_units"].append(sel.split()[0])
                save_state(state)
            else: continue
        elif "Kernel Logs" in choice:
            state["kernel_only"] = True
            save_state(state)
        elif "Disk Usage" in choice:
            view_disk_usage()
            continue

        run_log_viewer()


def main():
    # Setup state tracking immediately for safe environment propogation
    state_file = get_state_file()

    # Fast internal RPC router handling sub-actions seamlessly 
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "stream": stream_journal()
        elif cmd == "get-actions": get_actions()
        elif cmd == "preview" and len(sys.argv) > 2: preview_journal_entry(sys.argv[2])
        elif cmd == "set-boot": interactive_set_boot()
        elif cmd == "add-sys-unit": interactive_add_sys_unit()
        elif cmd == "add-usr-unit": interactive_add_usr_unit()
        elif cmd == "add-invocation": interactive_add_invocation()
        elif cmd == "set-priority": interactive_set_priority()
        elif cmd == "set-time": interactive_set_time()
        elif cmd == "toggle-kernel": toggle_kernel()
        elif cmd == "toggle-reverse": toggle_reverse()
        elif cmd == "reset": reset_state()
        sys.exit(0)

    # Initial program entry sequence
    for d in ['fzf', 'journalctl', 'systemctl']:
        if subprocess.run(['which', d], capture_output=True).returncode != 0:
            print(f"Error: '{d}' missing from PATH.", file=sys.stderr)
            sys.exit(1)

    try:
        main_menu()
    except KeyboardInterrupt:
        pass
    finally:
        if os.path.exists(state_file):
            try:
                os.remove(state_file)
            except OSError:
                pass

if __name__ == "__main__":
    main()
