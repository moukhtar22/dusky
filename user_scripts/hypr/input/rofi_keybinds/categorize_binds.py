#!/usr/bin/env python3
# ==============================================================================
# Rofi Keybindings Menu Categorizer & Formatter
# ==============================================================================

import sys

cache_path = sys.argv[1] if len(sys.argv) > 1 else ""
delim = sys.argv[2] if len(sys.argv) > 2 else ":::"

keymap = {}
if cache_path:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    keymap[parts[0]] = parts[1]
    except Exception:
        pass

def esc(text):
    return (text.replace("&", "&amp;")
               .replace("<", "&lt;")
               .replace(">", "&gt;")
               .replace("\r", " ")
               .replace("\n", " "))

def icon_for(dsp):
    dsp = dsp.lower()
    if dsp.startswith("exec"): return " "
    if "kill" in dsp: return " "
    if "resize" in dsp: return "󰩨 "
    if "move" in dsp: return "󰆾 "
    if "float" in dsp: return " "
    if "full" in dsp: return " "
    if "work" in dsp: return " "
    if "pass" in dsp: return " "
    return " "

def format_mods(mask):
    out = []
    if mask & 1:   out.append("SHIFT")
    if mask & 2:   out.append("CAPS")
    if mask & 4:   out.append("CTRL")
    if mask & 8:   out.append("ALT")
    if mask & 16:  out.append("MOD2")
    if mask & 32:  out.append("MOD3")
    if mask & 64:  out.append("SUPER")
    if mask & 128: out.append("MOD5")
    return " ".join(out)

def get_mod_priority(mask):
    if mask & 64: # SUPER
        if mask & 1: return 1  # SUPER + SHIFT
        if mask & 4: return 2  # SUPER + CTRL
        if mask & 8: return 3  # SUPER + ALT
        return 0               # SUPER
    if mask & 8:  return 4     # ALT
    if mask & 4:  return 5     # CTRL
    if mask & 1:  return 6     # SHIFT
    return 7                   # None

cat1, cat2, cat3, cat4, cat5 = [], [], [], [], []

def classify(desc, key, dsp):
    d = desc.lower()
    k = key.upper()
    
    # Category 5: Hardware & Media Keys (All XF86... and PRINT keys at bottom)
    if k.startswith("XF86") or k in ["PRINT", "PAUSE", "SCROLLLOCK"]:
        return cat5
        
    # Category 4: System & Control Tools
    if any(x in d for x in ["screenshot", "color picker", "picker", "zoom", "rotate", "dpms", "wi-fi", "wifi", "bluetooth", "audio", "pavucontrol", "btop", "monitor", "scale", "volume", "mic", "brightness", "media", "play", "next", "previous", "stop", "reload hyprland"]):
        return cat4
    # Category 3: Workspaces & Navigation
    elif any(x in d for x in ["context", "workspace", "scratchpad", "spotify", "cycle workspace", "game mode", "passthrough", "submap"]):
        return cat3
    # Category 2: Window Management & Tiling
    elif any(x in d for x in ["window", "close", "fullscreen", "float", "split", "pin", "sticky", "maximize", "focus", "move window", "opacity", "blur", "resize", "kill", "drag"]):
        return cat2
    # Category 1: Launchers, Menus & Quick Access
    else:
        return cat1

for line in sys.stdin:
    line = line.strip()
    if not line: continue
    parts = line.split(delim)
    if len(parts) < 7: continue
    
    submap, key, keycode_s, modmask_s, description, dispatcher, argument = parts[:7]
    try: keycode = int(keycode_s)
    except Exception: keycode = 0
    try: modmask = int(modmask_s)
    except Exception: modmask = 0
    
    if not key.startswith("mouse:") and keycode > 0 and str(keycode) in keymap:
        key = keymap[str(keycode)]
    elif not key and keycode > 0:
        key = f"code:{keycode}"
        
    bind_obj = {
        "submap": submap,
        "key": key.upper(),
        "keycode": keycode,
        "modmask": modmask,
        "description": description,
        "dispatcher": dispatcher,
        "argument": argument
    }
    c_list = classify(description, key, dispatcher)
    c_list.append(bind_obj)

categories = [
    ("󰀻  LAUNCHERS & QUICK ACCESS", "#a6e3a1", cat1),
    ("󰝣  WINDOW MANAGEMENT & TILING", "#89b4fa", cat2),
    ("󰽙  WORKSPACES & NAVIGATION", "#f9e2af", cat3),
    ("󰒓  SYSTEM & CONTROL TOOLS", "#f38ba8", cat4),
    ("󰌌  HARDWARE & MEDIA KEYS", "#cba6f7", cat5),
]

for title, color, items in categories:
    if not items: continue
    header_display = f"<b><span foreground=\"{color}\">---  {esc(title)}  ---</span></b>"
    print(f"{header_display}{delim}header{delim}header{delim}header{delim}header{delim}header")
    
    items.sort(key=lambda x: (get_mod_priority(x["modmask"]), x["key"], x["description"]))
    
    for b in items:
        submap = b["submap"]
        key = b["key"]
        modmask = b["modmask"]
        description = b["description"]
        dispatcher = b["dispatcher"]
        argument = b["argument"]
        
        mods = format_mods(modmask)
        m_fmt = f"{mods:<7}"
        k_fmt = f"{key:<10}"
        display_key = f'<span alpha="65%">{esc(m_fmt)}</span> <span weight="bold">{esc(k_fmt)}</span>'
        
        if description:
            action = esc(description)
        elif argument:
            action = f'{esc(dispatcher)} <span alpha="50%" style="italic">({esc(argument)})</span>'
        else:
            action = esc(dispatcher)
            
        if submap and submap != "global":
            action = f'<span weight="bold" foreground="#f38ba8">[{esc(submap.upper())}]</span> {action}'
            
        icon = icon_for(dispatcher)
        display_row = f"{icon}  {display_key}  {action}"
        print(f"{display_row}{delim}{dispatcher}{delim}{argument}{delim}{description}{delim}{key}{delim}{modmask}")
