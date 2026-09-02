#!/usr/bin/env python3
import json
import sys
import subprocess
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "network"
TARGET_FILE = "~/.cache/dusky_tui/wifi_cache.json"
APP_TITLE = "Dusky Network Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = False

TABS = ["Networks", "Saved", "Status", "Devices", "Speed Test", "Hotspot"]

SCHEMA = {0: [], 1: [], 2: [], 3: [], 4: [], 5: []}

# ============================================================================
#  Tab 0: Networks (populated from cache for instant startup)
# ============================================================================
# Immediate Wi-Fi radio toggle - available on first page (no need to visit Status)
def _radio_enabled_sync() -> bool:
    try:
        r = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=1)
        return r.stdout.strip() == "enabled"
    except Exception:
        return True

_radio_initial = _radio_enabled_sync()

SCHEMA[0].append(ConfigItem(
    label="Wi-Fi Radio",
    key="wifi_radio",
    scope="status",
    type_="bool",
    default=_radio_initial,
    group="Hardware",
    extended_help="Toggle Wi-Fi radio on/off."
))

SCHEMA[0].append(ConfigItem(
    label="Rescan",
    key="rescan",
    scope="network",
    type_="bool",
    default=False,
    group="Actions",
    options=["trigger"],
    extended_help="Scan for nearby Wi-Fi networks."
))

cache_path = Path.home() / ".cache" / "dusky_tui" / "wifi_cache.json"
_scans = []
if cache_path.exists():
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            _scans = json.load(f)
    except Exception:
        _scans = []

if not _scans and _radio_initial:
    try:
        p = subprocess.run(
            ["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL", "device", "wifi", "list", "--rescan", "no"],
            capture_output=True, text=True, timeout=2
        )
        seen = set()
        # Use regex split handling escaped colons like engine's _split_nmcli_line
        import re as _re
        _split = _re.compile(r'(?<!\\):')
        for line in p.stdout.splitlines():
            if not line:
                continue
            # Split by unescaped colon, then unescape
            parts = [f.replace("\\:", ":") for f in _split.split(line)]
            if len(parts) >= 4:
                in_use = parts[0].strip() == "*"
                # parts[1] may contain colon-escaped SSID, already unescaped above
                ssid = parts[1].strip()
                if ssid and ssid not in seen:
                    seen.add(ssid)
                    sec = parts[2] if parts[2] and parts[2] != "--" else "Open"
                    try:
                        sig = int(parts[3])
                    except ValueError:
                        sig = 0
                    _scans.append({"in_use": in_use, "ssid": ssid, "security": sec, "signal": sig})
        if _scans:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(_scans, f)
    except Exception:
        pass

# Only populate cached networks when radio is on — keeps first page clean when Wi-Fi is off
if _radio_initial:
    for _net in _scans:
        _ssid = _net.get("ssid", "")
        _signal = _net.get("signal", 0)
        _security = _net.get("security", "Open")
        _in_use = _net.get("in_use", False)

        def _bar(s):
            if s >= 80: return "▂▄▆█"
            if s >= 60: return "▂▄▆_"
            if s >= 40: return "▂▄__"
            if s >= 20: return "▂___"
            return "____"

        _icon = "●" if _in_use else "○"
        _status = "Active" if _in_use else "New"
        _label = f"{_icon} {_status:<6} {_ssid:<24} {_security:<10} {_signal}% {_bar(_signal)}"
        _pkey = f"net__{_ssid}"

        _item = ConfigItem(
            label=_label,
            key=_pkey,
            scope="network",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=_in_use,
            group="Networks"
        )
        _item.exists_in_target = True
        _item._initial_loaded = True
        SCHEMA[0].append(_item)

if len(SCHEMA[0]) <= 2:
    if not _radio_initial:
        SCHEMA[0].append(ConfigItem(
            label="Wi-Fi Off",
            key="wifi_off_notice",
            scope="network",
            type_="action",
            default=":",
            group="Networks"
        ))
    elif not _scans:
        SCHEMA[0].append(ConfigItem(
            label="Scanning...",
            key="loading_networks",
            scope="network",
            type_="action",
            default=":",
            group="Networks"
        ))

# ============================================================================
#  Tab 1: Saved Connections
# ============================================================================
SCHEMA[1].append(ConfigItem(
    label="Loading...",
    key="loading_saved",
    scope="saved",
    type_="action",
    default=":",
    group="Saved"
))

# ============================================================================
#  Tab 2: Status & Live Traffic — updated dynamically by engine
# ============================================================================
SCHEMA[2].extend([
    ConfigItem(
        label="Wi-Fi Radio",
        key="wifi_radio",
        scope="status",
        type_="bool",
        default=True,
        group="Hardware",
        extended_help="Toggle Wi-Fi radio on/off."
    ),
    ConfigItem(
        label="Connection:   Disconnected",
        key="status_type",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="SSID:  None",
        key="status_ssid",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="IP:   N/A",
        key="status_ip",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Gateway:      N/A",
        key="status_gateway",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Link:  N/A",
        key="status_detail",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Iface:    N/A",
        key="status_device",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Info"
    ),
    ConfigItem(
        label="Down: ↓ 0 B/s",
        key="throughput_down",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="Up:   ↑ 0 B/s",
        key="throughput_up",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="RX Total: 0 B",
        key="throughput_rx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="TX Total: 0 B",
        key="throughput_tx_total",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Throughput"
    ),
    ConfigItem(
        label="Router Ping: N/A",
        key="ping_router",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency"
    ),
    ConfigItem(
        label="Internet Ping: N/A",
        key="ping_internet",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Latency"
    ),
    ConfigItem(
        label="Loss: 0%",
        key="ping_packet_loss",
        scope="clipboard",
        type_="bool",
        default=False,
        group="Latency"
    ),
    ConfigItem(
        label="Disconnect",
        key="disconnect",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Disconnect current Wi-Fi."
    ),
    ConfigItem(
        label="Reconnect",
        key="reconnect",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Reconnect current Wi-Fi."
    ),
    ConfigItem(
        label="Share QR",
        key="qr_active",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Show QR for active Wi-Fi."
    ),
    ConfigItem(
        label="Band: Auto",
        key="wifi_band",
        scope="status_action",
        type_="cycle",
        default="Auto",
        options=["Auto", "2.4 GHz", "5 GHz", "6 GHz"],
        group="Actions",
        extended_help="Pin band with auto-rollback."
    ),
    ConfigItem(
        label="Restart NM",
        key="restart_nm",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Restart NetworkManager."
    ),
    ConfigItem(
        label="Force Rescan",
        key="rescan",
        scope="status_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Force Wi-Fi rescan."
    )
])

# ============================================================================
#  CUSTOM RICH VIEW FOR TAB 2 (Status / Live Metrics Dashboard)
# ============================================================================
def render_network_dashboard_view(app):
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.console import Group
    from rich.columns import Columns
    from rich.align import Align
    from python.engines.network_manager import (
        NetworkManagerEngine,
        parse_key_value,
        format_rate,
        format_bytes,
        format_ping_latency,
        format_packet_loss
    )

    verb = {}
    tp = {}
    ping = {}
    dns_provider = "DHCP"

    eng = getattr(NetworkManagerEngine, "_instance", None)

    if not eng and app and hasattr(app, "engine_pool"):
        for e in list(app.engine_pool.values()):
            if isinstance(e, NetworkManagerEngine):
                eng = e
                break

    if eng:
        verb = dict(getattr(eng, "_verbose_info", {}))
        if not verb or not verb.get("iface"):
            try:
                act = eng._get_active_wifi_connection()
                verb = eng._enrich_network_status(verb, act)
                eng._verbose_info = verb
            except Exception:
                pass

        tp = dict(getattr(eng, "_tp_state", {}))
        ping = dict(getattr(eng, "_ping_state", {}))
        dns_provider = getattr(eng, "_dns_provider", "DHCP")

    conn_type = verb.get("type", "wifi").upper()
    ssid = verb.get("ssid", "None")
    ip = verb.get("ip", "N/A")
    prefix = verb.get("prefix", "")
    if ip != "N/A" and prefix:
        ip = f"{ip}/{prefix}"
    gw = verb.get("gateway", "N/A")
    iface = verb.get("iface", "N/A")
    phy_iface = verb.get("phy_iface", "")
    if phy_iface and phy_iface != iface and iface != "N/A":
        iface_str = f"{iface} ({phy_iface})"
    else:
        iface_str = iface

    freq = verb.get("freq", "")
    bitrate = verb.get("bitrate", "")
    link_detail = (f"{freq} MHz" if freq else "N/A") + (f" ({bitrate})" if bitrate else "")

    is_wifi = conn_type == "WIFI"
    conn_icon = "󰤨" if is_wifi else "󰈀"

    dl_rate_val = tp.get("download_rate", 0)
    ul_rate_val = tp.get("upload_rate", 0)

    rx_raw = tp.get("total_rx")
    if rx_raw is None or rx_raw == 0:
        try: rx_raw = int(verb.get("rx_bytes", 0))
        except ValueError: rx_raw = 0

    tx_raw = tp.get("total_tx")
    if tx_raw is None or tx_raw == 0:
        try: tx_raw = int(verb.get("tx_bytes", 0))
        except ValueError: tx_raw = 0

    dl_rate = format_rate(dl_rate_val)
    ul_rate = format_rate(ul_rate_val)
    rx_total = format_bytes(rx_raw)
    tx_total = format_bytes(tx_raw)

    r_lat = ping.get("router_ping_latency")
    if r_lat is None and verb.get("router_ping_ms"):
        try: r_lat = float(verb["router_ping_ms"])
        except ValueError: pass

    i_lat = ping.get("internet_ping_latency")
    if i_lat is None and verb.get("internet_ping_ms"):
        try: i_lat = float(verb["internet_ping_ms"])
        except ValueError: pass

    router_ping = format_ping_latency(r_lat)
    internet_ping = format_ping_latency(i_lat)
    packet_loss = format_packet_loss(ping.get("internet_ping_packet_loss", 0))

    t_conn = Table(show_header=False, box=None, padding=(0, 1))
    t_conn.add_column(style="dim", justify="right")
    t_conn.add_column(style="bold white", justify="left")
    t_conn.add_row("Connection:", f"{conn_icon} {conn_type} ({ssid})")
    t_conn.add_row("SSID:", ssid)
    t_conn.add_row("IP:", ip)
    t_conn.add_row("Gateway:", gw)
    t_conn.add_row("Iface:", iface_str)
    t_conn.add_row("Link:", link_detail)
    p_conn = Panel(t_conn, title="[bold cyan] 󰤨 CONNECTION [/bold cyan]", border_style="cyan", expand=True)

    t_tp = Table(show_header=False, box=None, padding=(0, 1))
    t_tp.add_column(style="dim", justify="right")
    t_tp.add_column(style="bold green", justify="left")
    t_tp.add_row("Down:", f"↓ {dl_rate}")
    t_tp.add_row("Up:", f"↑ {ul_rate}")
    t_tp.add_row("RX Total:", rx_total)
    t_tp.add_row("TX Total:", tx_total)
    p_tp = Panel(t_tp, title="[bold green] 󰓅 THROUGHPUT [/bold green]", border_style="green", expand=True)

    t_ping = Table(show_header=False, box=None, padding=(0, 1))
    t_ping.add_column(style="dim", justify="right")
    t_ping.add_column(style="bold yellow", justify="left")
    t_ping.add_row("Router Ping:", router_ping)
    t_ping.add_row("Internet Ping:", internet_ping)
    t_ping.add_row("Loss:", packet_loss)
    t_ping.add_row("DNS:", dns_provider)
    p_ping = Panel(t_ping, title="[bold yellow] 󰛳 LATENCY [/bold yellow]", border_style="yellow", expand=True)

    right_group = Group(p_tp, p_ping)

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(p_conn, right_group)

    return grid


# ============================================================================
#  Devices Dashboard (Tab 3) — nmcli device status filtered view
# ============================================================================
def render_devices_dashboard_view(app):
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from python.engines.network_manager import NetworkManagerEngine

    devices = []
    details_map = {}
    eng = getattr(NetworkManagerEngine, "_instance", None)
    if not eng and app and hasattr(app, "engine_pool"):
        for e in list(app.engine_pool.values()):
            if isinstance(e, NetworkManagerEngine):
                eng = e
                break
    if eng:
        devices = list(getattr(eng, "_devices_cache", []))
        details_map = dict(getattr(eng, "_device_details", {}))
        if not devices:
            try:
                devices = eng._get_nmcli_devices()
                details_map = eng._get_device_details_map()
            except Exception:
                pass

    if not devices:
        t = Table(show_header=False, box=None, padding=(0, 1))
        t.add_column(justify="center")
        t.add_row(Text("No devices found", style="dim italic"))
        return Panel(t, title="[bold cyan] DEVICES [/bold cyan]", border_style="cyan")

    # Main device table
    tbl = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1), expand=True)
    tbl.add_column("Device", style="bold white", no_wrap=True)
    tbl.add_column("Type", style="dim", no_wrap=True)
    tbl.add_column("State", justify="left")
    tbl.add_column("Connection", style="white", overflow="fold")
    tbl.add_column("IP / Details", style="dim", overflow="fold")

    for d in devices:
        dev = d.get("device", "")
        dtype = d.get("type", "")
        state = d.get("state", "")
        conn = d.get("connection", "") or "--"
        det = details_map.get(dev, {})
        ip = det.get("IP4.ADDRESS[1]", "") or det.get("IP4.ADDRESS", "") or d.get("ip", "")
        if not ip:
            ip = det.get("IP4.GATEWAY", "") or ""
            if ip:
                ip = f"gw {ip}"
            else:
                # fallback to hwaddr for identification
                hw = det.get("GENERAL.HWADDR", "")
                ip = hw if hw and hw != "(unknown)" else "--"

        # Icons & colors by state
        if "connected" in state.lower():
            state_txt = Text(state, style="bold green")
            icon = "●"
        elif "disconnected" in state.lower():
            state_txt = Text(state, style="yellow")
            icon = "○"
        elif "unavailable" in state.lower():
            state_txt = Text(state, style="dim")
            icon = "◯"
        else:
            state_txt = Text(state, style="dim")
            icon = "·"

        dev_txt = Text(f"{icon} {dev}", style="bold white")
        tbl.add_row(dev_txt, dtype, state_txt, conn, ip)

    panel = Panel(tbl, title="[bold cyan] 󰈀 DEVICES — nmcli device status [/bold cyan]", border_style="cyan", expand=True)

    # Optional detail grid for selected/connected devices
    # Add summary footer
    footer = Text(f"{len(devices)} devices • filtered view • see list below for details", style="dim italic")
    from rich.console import Group
    return Group(panel, footer)


CUSTOM_VIEWS = {
    2: {
        "view": render_network_dashboard_view,
        "interval": 1.0
    },
    3: {
        "view": render_devices_dashboard_view,
        "interval": 2.0
    }
}

# ============================================================================
#  Tab 3 (index 3): Devices — details populated by engine
# ============================================================================
SCHEMA[3].extend([
    ConfigItem(
        label="Loading...",
        key="loading_devices",
        scope="devices",
        type_="action",
        default=":",
        group="Devices"
    )
])

# ============================================================================
#  Tab 4: Speed Test — Fast.com speed test integration
# ============================================================================
SCHEMA[4].extend([
    ConfigItem(
        label="Run All",
        key="speedtest_full",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run",
        extended_help="Run download & upload test."
    ),
    ConfigItem(
        label="Download",
        key="speedtest_down",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run"
    ),
    ConfigItem(
        label="Upload",
        key="speedtest_up",
        scope="speedtest_action",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Run"
    ),
    ConfigItem(
        label="Status: Ready",
        key="speedtest_status",
        scope="speedtest_info",
        type_="action",
        default=":",
        group="Results"
    ),
    ConfigItem(
        label="Down: --",
        key="speedtest_down_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Results"
    ),
    ConfigItem(
        label="Up: --",
        key="speedtest_up_result",
        scope="clipboard",
        type_="bool",
        default=False,
        options=["copy"],
        group="Results"
    ),
])

# ============================================================================
#  Tab 5: Hotspot
# ============================================================================
SCHEMA[5].extend([
    ConfigItem(
        label="SSID",
        key="hotspot_ssid",
        scope="hotspot",
        type_="string",
        default="MyHotspot",
        group="Config",
        extended_help="Hotspot name."
    ),
    ConfigItem(
        label="Password",
        key="hotspot_password",
        scope="hotspot",
        type_="string",
        default="",
        group="Config",
        extended_help="Min 8 chars; empty = open."
    ),
    ConfigItem(
        label="Start 2.4 GHz",
        key="start_hotspot_24",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Start 2.4 GHz hotspot."
    ),
    ConfigItem(
        label="Start 5 GHz",
        key="start_hotspot_5",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Start 5 GHz hotspot."
    ),
    ConfigItem(
        label="Stop",
        key="stop_hotspot",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Stop hotspot."
    ),
    ConfigItem(
        label="Share QR",
        key="qr_hotspot",
        scope="hotspot",
        type_="bool",
        default=False,
        options=["trigger"],
        group="Actions",
        extended_help="Show QR for hotspot."
    ),
    ConfigItem(
        label="Status: Inactive",
        key="hotspot_status_info",
        scope="hotspot",
        type_="action",
        default=":",
        group="Status"
    ),
    ConfigItem(
        label="Clients: N/A",
        key="hotspot_clients_info",
        scope="hotspot",
        type_="action",
        default=":",
        group="Status"
    )
])

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
