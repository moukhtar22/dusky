#!/usr/bin/env python3
"""
hypr-ws-overlay — click-through workspace indicator for Hyprland

DEPENDENCIES
  Arch:    sudo pacman -S python-gobject gtk3 gtk-layer-shell
  Fedora:  sudo dnf install python3-gobject gtk3 gtk-layer-shell
  Ubuntu:  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1

ADD TO ~/.config/hypr/hyprland.conf
  exec-once = python3 ~/.config/hypr/hypr-ws-overlay.py

FLAGS
  --help            show this message
  --clk             hide the clock
  --notific         hide the notification counter
  --updt            hide the update counter
  --bg-alpha FLOAT  background pill opacity  (0.0 – 1.0, default 0.9)

OPTIONAL KILL/RESTART BIND
  bind = SUPER SHIFT, O, exec, pkill -f hypr-ws-overlay || python3 ~/.config/hypr/hypr-ws-overlay.py

INTERACTION
  The entire overlay is click-through.
  Right-click on a workspace box switches to that workspace.
  Right-click on the special-workspace box toggles it.
  Notification and Updates widgets are passive (display-only, like the clock).
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, GtkLayerShell, GLib, Gdk
import cairo
import math
import re
import socket
import json
import os
import glob
import subprocess
import threading
import time
import sys
import argparse

PI = math.pi
SPECIAL_WS_ID = -99

# ── CLI flags ─────────────────────────────────────────────────────────────────
def _parse_args():
    p = argparse.ArgumentParser(
        prog='hypr-ws-overlay',
        description='Click-through workspace indicator for Hyprland',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python3 hypr-ws-overlay.py\n'
            '  python3 hypr-ws-overlay.py --clk --bg-alpha 0.30\n'
            '  python3 hypr-ws-overlay.py --notific --updt\n'
        ),
    )
    p.add_argument('--clk',      action='store_true', help='hide the clock widget')
    p.add_argument('--notific',  action='store_true', help='hide the notification counter')
    p.add_argument('--updt',     action='store_true', help='hide the update counter')
    p.add_argument('--bg-alpha', type=float, default=0.9, metavar='FLOAT',
                   help='background pill opacity 0.0–1.0 (default: 0.9)')
    return p.parse_args()

ARGS     = _parse_args()
BG_ALPHA = max(0.0, min(1.0, ARGS.bg_alpha))

# ── Hyprland IPC ──────────────────────────────────────────────────────────────
def _find_socket(name: str) -> str:
    sig = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
    xdg = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    candidates = [
        f'/tmp/hypr/{sig}/.{name}.sock',
        f'{xdg}/hypr/{sig}/.{name}.sock',
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    for pattern in [f'/tmp/hypr/*/.{name}.sock', f'{xdg}/hypr/*/.{name}.sock']:
        found = glob.glob(pattern)
        if found:
            return found[0]
    return candidates[0]

SOCK_CMD   = _find_socket('socket')
SOCK_EVENT = _find_socket('socket2')

def hypr_query(cmd: str):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(SOCK_CMD)
            s.sendall(f'j/{cmd}'.encode())
            chunks = []
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
        return json.loads(b''.join(chunks))
    except Exception as e:
        print(f'[IPC cmd] {e}', file=sys.stderr)
        return None

def hypr_dispatch(ws_id: int):
    """
    Switch to a regular workspace (1-10) or toggle the special workspace.
    Uses hyprctl dispatch directly — no shell injection.
    """
    try:
        if ws_id == SPECIAL_WS_ID:
            subprocess.run(
                ['hyprctl', 'dispatch', 'hl.dsp.workspace.toggle_special("magic")'],
                check=False,
            )
        else:
            subprocess.run(
                ['hyprctl', 'dispatch', f'hl.dsp.focus({{ workspace = "{ws_id}" }})'],
                check=False,
            )
    except Exception as e:
        print(f'[dispatch] {e}', file=sys.stderr)

# ── Palette ───────────────────────────────────────────────────────────────────
STYLE_EMPTY   = dict(bg=(1,1,1,0.03),  border=(1,1,1,0.05),  fg=(1,1,1,0.22))
STYLE_USED    = dict(bg=(1,1,1,0.07),  border=(1,1,1,0.12),  fg=(1,1,1,0.60))
STYLE_ACTIVE  = dict(bg=(0.388, 0.702, 0.929, 0.14),
                     border=(0.388, 0.702, 0.929, 0.52),
                     fg=(0.388, 0.702, 0.929, 1.0))
STYLE_URGENT  = dict(bg=(0.988, 0.506, 0.290, 0.12),
                     border=(0.988, 0.506, 0.290, 0.58),
                     fg=(0.988, 0.506, 0.290, 1.0))
STYLE_SPECIAL = dict(bg=(0.686, 0.459, 0.929, 0.12),
                     border=(0.686, 0.459, 0.929, 0.52),
                     fg=(0.686, 0.459, 0.929, 1.0))

HOVER_BG_BOOST = 0.08
WS_W, WS_H    = 30, 22

# ── Helpers ───────────────────────────────────────────────────────────────────
def _rrect(cr, x, y, w, h, r=2.0):
    cr.new_sub_path()
    cr.arc(x + r,     y + r,     r, PI,     3*PI/2)
    cr.arc(x + w - r, y + r,     r, 3*PI/2, 0)
    cr.arc(x + w - r, y + h - r, r, 0,      PI/2)
    cr.arc(x + r,     y + h - r, r, PI/2,   PI)
    cr.close_path()

# ── Workspace cell ────────────────────────────────────────────────────────────
class WsBox(Gtk.DrawingArea):
    def __init__(self, ws_id: int):
        super().__init__()
        self.ws_id       = ws_id
        self.active      = False
        self.windows     = 0
        self.urgent      = False
        self._hovered    = False
        self._is_special = (ws_id == SPECIAL_WS_ID)
        self.set_size_request(WS_W, WS_H)
        self.connect('draw', self._on_draw)
        self.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect('button-press-event', self._on_click)
        self.connect('enter-notify-event', self._on_enter)
        self.connect('leave-notify-event', self._on_leave)
        self.connect('realize',            self._on_realize)

    def _on_realize(self, _w):
        self.get_window().set_cursor(
            Gdk.Cursor.new_from_name(self.get_display(), 'pointer')
        )

    def refresh(self, active: bool, windows: int, urgent: bool):
        changed = (self.active != active or
                   self.windows != windows or
                   self.urgent != urgent)
        self.active  = active
        self.windows = windows
        self.urgent  = urgent
        if changed:
            self.queue_draw()

    def _on_click(self, _w, event):
        if event.button == 3:
            hypr_dispatch(self.ws_id)
        return event.button == 3

    def _on_enter(self, *_):
        self._hovered = True;  self.queue_draw()

    def _on_leave(self, *_):
        self._hovered = False; self.queue_draw()

    def _on_draw(self, _w, cr: cairo.Context):
        w, h, r = WS_W, WS_H, 5
        if self._is_special:
            style = STYLE_SPECIAL if self.active else STYLE_USED if self.windows else STYLE_EMPTY
        else:
            style = (STYLE_ACTIVE if self.active else
                     STYLE_URGENT if self.urgent else
                     STYLE_USED   if self.windows else
                     STYLE_EMPTY)
        bg = style['bg']
        if self._hovered and not self.active:
            bg = (bg[0], bg[1], bg[2], min(1.0, bg[3] + HOVER_BG_BOOST))
        cr.new_sub_path()
        cr.arc(r,   r,   r, PI,     3*PI/2)
        cr.arc(w-r, r,   r, 3*PI/2, 0)
        cr.arc(w-r, h-r, r, 0,      PI/2)
        cr.arc(r,   h-r, r, PI/2,   PI)
        cr.close_path()
        cr.set_source_rgba(*bg); cr.fill_preserve()
        cr.set_source_rgba(*style['border']); cr.set_line_width(1.0); cr.stroke()
        if self.windows > 0 or self.active:
            self._draw_window_dots(cr, style['fg'], w, h)
        self._draw_label(cr, style['fg'], w, h)

    def _draw_label(self, cr, fg, w, h):
        cr.set_source_rgba(*fg)
        cr.select_font_face('DSEG7 Classic', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(20)
        cr.move_to(-2, 21)
        cr.show_text('-9' if self._is_special else str(self.ws_id))

    def _draw_window_dots(self, cr, fg, w, h):
        r, g, b, a = fg
        cr.set_source_rgba(r, g, b, a * 0.65)
        pad = 4.0
        iw  = w - pad * 2
        ih  = h - pad * 2
        n   = min(max(self.windows, 1 if self.active else 0), 4)
        if n == 0:
            return
        elif n == 1:
            _rrect(cr, pad, pad, iw, ih); cr.fill()
        elif n == 2:
            gap  = 1.5
            rowh = (ih - gap) / 2
            _rrect(cr, pad, pad,          iw, rowh); cr.fill()
            _rrect(cr, pad, pad+rowh+gap, iw, rowh); cr.fill()
        elif n == 3:
            gap  = 1.5
            rowh = (ih - gap) / 2
            colw = (iw - gap) / 2
            _rrect(cr, pad,          pad,          iw,   rowh); cr.fill()
            _rrect(cr, pad,          pad+rowh+gap, colw, rowh); cr.fill()
            _rrect(cr, pad+colw+gap, pad+rowh+gap, colw, rowh); cr.fill()
        else:
            gap  = 1.5
            colw = (iw - gap) / 2
            rowh = (ih - gap) / 2
            for col in [pad, pad+colw+gap]:
                for row in [pad, pad+rowh+gap]:
                    _rrect(cr, col, row, colw, rowh); cr.fill()

# ── Clock ─────────────────────────────────────────────────────────────────────
class Clock(Gtk.Label):
    def __init__(self):
        super().__init__()
        self._blink = True
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
            label {
                font-family: "DSEG7 Classic", "Digital-7", monospace;
                font-size: 14px;
                letter-spacing: 2px;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._tick()
        GLib.timeout_add(1000, self._tick)

    def _tick(self):
        now = time.strftime('%I:%M').lstrip('0')
        self.set_text(now if self._blink else now.replace(':', ' '))
        self._blink = not self._blink
        return True

# ── Notification (passive, display-only) ─────────────────────────────────────
class Notification(Gtk.Label):
    """
    Passive label showing mako notification count.

    Uses a single long-lived worker thread that blocks on makoctl subscribe
    for instant updates, and re-reads mako.sh on each event plus a 5 s
    fallback poll via GLib.timeout_add — no per-tick thread spawning.
    """
    _POLL_MS = 5000
    _MAKO_SH = os.path.expanduser('~/user_scripts/waybar/mako.sh')

    def __init__(self):
        super().__init__(label='…')
        self._fetch()
        threading.Thread(target=self._subscribe_loop, daemon=True).start()
        GLib.timeout_add(self._POLL_MS, self._tick)

    def _tick(self):
        # Slow fallback poll — runs on GLib main loop, spawns no extra thread
        threading.Thread(target=self._read_once, daemon=True).start()
        return True

    def _fetch(self):
        threading.Thread(target=self._read_once, daemon=True).start()

    def _read_once(self):
        try:
            out = subprocess.check_output(
                [self._MAKO_SH], stderr=subprocess.DEVNULL, timeout=4,
            ).decode().strip()
            data = json.loads(out)
            text_field = data.get('text', '0')
            m = re.search(r'(\d+)\s*$', text_field)
            text = m.group(1) if m else '0'
        except FileNotFoundError:
            GLib.idle_add(self.set_text, 'n/a')
            return
        except Exception:
            return
        GLib.idle_add(self.set_text, text)

    def _subscribe_loop(self):
        # Single persistent thread — wakes on every mako event, then re-reads
        while True:
            try:
                proc = subprocess.Popen(
                    ['makoctl', 'subscribe'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                for _ in proc.stdout:
                    self._read_once()
                proc.wait()
            except FileNotFoundError:
                break
            except Exception:
                pass
            time.sleep(2)

# ── Updates (passive, display-only) ──────────────────────────────────────────
class Updates(Gtk.Label):
    """
    Passive label showing pending update count.

    Reads ~/.config/dusky/settings/waybar_update_counter_h once at startup,
    then tails it in a single persistent thread for live updates.
    No periodic polling — the file is the source of truth.
    """
    _WAYBAR_PATH = os.path.expanduser(
        '~/.config/dusky/settings/waybar_update_counter_h'
    )

    def __init__(self):
        super().__init__(label='…')
        threading.Thread(target=self._tail_loop, daemon=True).start()

    def _tail_loop(self):
        """
        Single long-lived thread: reads the last line once, then tails
        for new lines. No polling timer, no thread respawning.
        """
        if not os.path.exists(self._WAYBAR_PATH):
            GLib.idle_add(self.set_text, 'n/a')
            return
        try:
            with open(self._WAYBAR_PATH, 'r') as f:
                # Show last known value immediately
                last = None
                for line in f:
                    if line.strip():
                        last = line.strip()
                if last:
                    self._parse_and_set(last)
                # Now tail for new lines indefinitely
                while True:
                    line = f.readline()
                    if line:
                        if line.strip():
                            self._parse_and_set(line.strip())
                    else:
                        time.sleep(0.5)
        except Exception:
            pass

    def _parse_and_set(self, line: str):
        try:
            data    = json.loads(line)
            tooltip = data.get('tooltip', '')
            m = re.search(r'Total:\s*(\d+)', tooltip)
            if m:
                text = m.group(1)
            else:
                text_field = data.get('text', '0')
                m2 = re.search(r'(\d+)\s*$', text_field)
                text = m2.group(1) if m2 else '0'
        except Exception:
            return
        GLib.idle_add(self.set_text, text)

# ── Background pill ───────────────────────────────────────────────────────────
def _draw_pill(cr: cairo.Context, w: int, h: int, alpha: float):
    """
    Drawn onto the window's own Cairo surface before children are composited,
    so it is always visually behind widgets at any alpha value.
    """
    r = 10.0

    def rrect():
        cr.new_sub_path()
        cr.arc(r,     r,     r, PI,     3*PI/2)
        cr.arc(w - r, r,     r, 3*PI/2, 0)
        cr.arc(w - r, h - r, r, 0,      PI/2)
        cr.arc(r,     h - r, r, PI/2,   PI)
        cr.close_path()

    rrect()
    cr.set_source_rgba(0.08, 0.08, 0.12, alpha)
    cr.fill()

    if alpha > 0.05:
        rrect()
        cr.set_source_rgba(1.0, 1.0, 1.0, alpha * 0.35)
        cr.set_line_width(0.8)
        cr.stroke()

# ── Overlay window ────────────────────────────────────────────────────────────
class WorkspaceOverlay(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._workspaces:      dict[int, dict]  = {}
        self._active_id:       int              = 1
        self._urgent_ids:      set[int]         = set()
        self._ws_widgets:      dict[int, WsBox] = {}
        self._special_active:  bool             = False
        self._special_windows: int              = 0
        self._init_layer_shell()
        self._build_static_ui()
        self._fetch_state()
        self._start_event_thread()

    # ── Layer shell ────────────────────────────────────────────────────────────
    def _init_layer_shell(self):
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_namespace(self, 'hypr-ws-overlay')
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 18)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.connect('size-allocate', self._update_input_region)

    def _update_input_region(self, _widget, _alloc):
        """Only workspace boxes receive pointer events; everything else is click-through."""
        win = self.get_window()
        if not win:
            return
        region = cairo.Region()
        for child in self._ws_widgets.values():
            a = child.get_allocation()
            if a.width > 0 and a.height > 0:
                region.union(cairo.RectangleInt(a.x, a.y, a.width, a.height))
        win.input_shape_combine_region(region, 0, 0)

    # ── Static UI ──────────────────────────────────────────────────────────────
    def _build_static_ui(self):
        self.connect('draw', self._on_window_draw)

        self._row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self._row.set_margin_top(7)
        self._row.set_margin_bottom(7)
        self._row.set_margin_start(10)
        self._row.set_margin_end(10)

        if not ARGS.clk:
            self._clock = Clock()
            self._row.pack_start(self._clock, False, False, 6)

        if not ARGS.updt:
            self._updates = Updates()
            self._row.pack_end(self._updates, False, False, 4)

        if not ARGS.notific:
            self._notif = Notification()
            self._row.pack_end(self._notif, False, False, 4)

        self.add(self._row)
        self.show_all()

    def _on_window_draw(self, _widget, cr: cairo.Context):
        alloc = self.get_allocation()
        _draw_pill(cr, alloc.width, alloc.height, BG_ALPHA)
        return False

    # ── State fetch ────────────────────────────────────────────────────────────
    def _fetch_state(self):
        ws_list = hypr_query('workspaces')      or []
        active  = hypr_query('activeworkspace') or {}
        self._workspaces.clear()
        self._special_active  = False
        self._special_windows = 0
        for ws in ws_list:
            wid  = ws['id']
            wins = ws.get('windows', 0)
            name = ws.get('name', str(wid))
            if wid == SPECIAL_WS_ID or name.startswith('special'):
                self._special_windows = wins
            else:
                self._workspaces[wid] = {'id': wid, 'name': name, 'windows': wins}
        act_id   = active.get('id', 1)
        act_name = active.get('name', '')
        if act_id == SPECIAL_WS_ID or act_name.startswith('special'):
            self._special_active = True
        else:
            self._active_id = act_id
        GLib.idle_add(self._rebuild)

    # ── Rebuild UI ─────────────────────────────────────────────────────────────
    def _rebuild(self):
        row = self._row
        regular_ids = sorted({
            i for i in range(1, 11)
            if (i == self._active_id or
                self._workspaces.get(i, {}).get('windows', 0) > 0 or
                i in self._urgent_ids)
        })
        show_special = self._special_active or self._special_windows > 0
        visible_ids  = regular_ids + ([SPECIAL_WS_ID] if show_special else [])

        for ws_id in list(self._ws_widgets.keys()):
            if ws_id not in visible_ids:
                row.remove(self._ws_widgets[ws_id])
                del self._ws_widgets[ws_id]

        clk_offset = 0 if ARGS.clk else 1

        for pos, ws_id in enumerate(regular_ids):
            ws     = self._workspaces.get(ws_id, {'windows': 0})
            active = (ws_id == self._active_id) and not self._special_active
            urgent = ws_id in self._urgent_ids
            if ws_id not in self._ws_widgets:
                box = WsBox(ws_id)
                self._ws_widgets[ws_id] = box
                row.pack_start(box, False, False, 0)
                box.show()
            row.reorder_child(self._ws_widgets[ws_id], pos + clk_offset)
            self._ws_widgets[ws_id].refresh(active, ws.get('windows', 0), urgent)

        if show_special:
            pos = len(regular_ids) + clk_offset
            if SPECIAL_WS_ID not in self._ws_widgets:
                box = WsBox(SPECIAL_WS_ID)
                self._ws_widgets[SPECIAL_WS_ID] = box
                row.pack_start(box, False, False, 0)
                box.show()
            row.reorder_child(self._ws_widgets[SPECIAL_WS_ID], pos)
            self._ws_widgets[SPECIAL_WS_ID].refresh(
                self._special_active, self._special_windows, False)
        return False

    # ── IPC event loop ─────────────────────────────────────────────────────────
    def _start_event_thread(self):
        threading.Thread(target=self._event_loop, daemon=True).start()

    def _event_loop(self):
        while True:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(SOCK_EVENT)
                    buf = ''
                    while True:
                        data = s.recv(4096).decode('utf-8', errors='ignore')
                        if not data:
                            break
                        buf += data
                        while '\n' in buf:
                            line, buf = buf.split('\n', 1)
                            line = line.strip()
                            if line:
                                GLib.idle_add(self._dispatch, line)
            except Exception as e:
                print(f'[IPC event] {e}', file=sys.stderr)
                time.sleep(1)

    def _dispatch(self, line: str):
        if '>>' not in line:
            return False
        ev, data = line.split('>>', 1)
        if ev == 'workspacev2':
            parts = data.split(',', 1)
            name  = parts[1].strip() if len(parts) > 1 else ''
            try:
                wid = int(parts[0])
                if wid == SPECIAL_WS_ID or name.startswith('special'):
                    self._special_active = True
                else:
                    self._special_active = False
                    self._active_id = wid
                self._rebuild()
            except ValueError:
                pass
        elif ev == 'workspace':
            name = data.strip()
            if name.startswith('special'):
                self._special_active = True
            else:
                self._special_active = False
                try:
                    self._active_id = int(name)
                except ValueError:
                    pass
            self._rebuild()
        elif ev == 'activespecial':
            ws_name = data.split(',')[0].strip()
            self._special_active = bool(ws_name)
            self._rebuild()
        elif ev in ('openwindow', 'closewindow', 'movewindow',
                    'createworkspace',  'createworkspacev2',
                    'destroyworkspace', 'destroyworkspacev2',
                    'urgent'):
            self._refetch()
        return False

    def _refetch(self):
        ws_list = hypr_query('workspaces') or []
        self._workspaces.clear()
        self._special_windows = 0
        for ws in ws_list:
            wid  = ws['id']
            name = ws.get('name', str(wid))
            wins = ws.get('windows', 0)
            if wid == SPECIAL_WS_ID or name.startswith('special'):
                self._special_windows = wins
            else:
                self._workspaces[wid] = {'id': wid, 'name': name, 'windows': wins}
        self._rebuild()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    if not os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
        print('[WARN] HYPRLAND_INSTANCE_SIGNATURE not set — is Hyprland running?',
              file=sys.stderr)
    overlay = WorkspaceOverlay()
    overlay.connect('destroy', Gtk.main_quit)
    Gtk.main()
