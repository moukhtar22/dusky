#!/usr/bin/env python3
"""Dusky UFW manager: CLI rules, sockets, resolved IP lists and framework controls.

Socket views show rule hints, not remote exposure. Default policies preserve
existing allowances. Domain entries filter IPs and selected TCP ports.
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from typing import Any

# Bootstrap Dusky TUI root
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.console import Group

from python.frontend.core_types import ConfigItem
from python.engines.ufw import UfwEngine

# =============================================================================
# 1. CORE APPLICATION ROUTING & METADATA
# =============================================================================
ENGINE_TYPE = "ufw"
TARGET_FILE = "/etc/default/ufw"
REQUIRE_ROOT = True
APP_TITLE = "Dusky Firewall"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = False

# =============================================================================
# 2. TABS DEFINITION
# =============================================================================
TABS = [
    "Status",
    "Controls",
    "Sockets",
    "Ports",
    "Rules",
    "Builder",
    "Domains",
    "Whitelist",
    "Routing",
    "Traffic",
    "Bans",
    "Profiles",
    "Presets",
    "Audit",
    "Reports",
]

SCHEMA: dict[int, list[ConfigItem]] = {i: [] for i in range(len(TABS))}

# =============================================================================
# 3. SCHEMA DEFINITIONS
# =============================================================================

# -----------------------------------------------------------------------------
# TAB 1: CONTROLS (Power, Panic Killswitch, Policies, Logging)
# -----------------------------------------------------------------------------
# TAB 0: 'Status' is a full-height Rich live dashboard view (show_options=False)
SCHEMA[1] = [
    ConfigItem(
        label="Firewall Active",
        key="firewall_enabled",
        scope="status",
        type_="bool",
        default=True,
        group="Power",
        extended_help="**Master Firewall Switch**\n\nEnables or disables netfilter packet inspection on all network interfaces. Auto-starts on system boot.",
    ),
    ConfigItem(
        label="Reload Rules",
        key="action_reload",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reload"],
        group="Power",
        extended_help="**Reload Firewall**\n\nReloads the UFW framework. This may interrupt connections; ordinary CLI rule edits apply immediately and do not need a reload.",
    ),
    ConfigItem(
        label="Reset All",
        key="action_reset",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reset"],
        confirm_message="WARNING: Reset all firewall rules and policies back to installation defaults?",
        group="Power",
        extended_help="**Factory Reset**\n\nUnloads UFW and restores all rules to default installation state.",
    ),
    ConfigItem(
        label="Deny All Default Policies",
        key="action_panic_lockdown",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Deny Defaults"],
        confirm_message="Set incoming, outgoing and routed defaults to deny? Existing rules and established sessions remain allowed.",
        group="Panic Controls",
        extended_help="**Deny Default Policies**\n\nSets incoming, outgoing and routed defaults to deny. Existing user rules, framework allowances and established sessions remain. This does not disconnect all traffic.",
    ),
    ConfigItem(
        label="Restore Traffic",
        key="action_panic_restore",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Restore Traffic"],
        group="Panic Controls",
        extended_help="**Restore Traffic**\n\nRestores normal firewall operating policy (Incoming Deny, Outgoing Allow).",
    ),
    ConfigItem(
        label="Logging Level",
        key="logging_level",
        scope="status",
        type_="cycle",
        default="low",
        options=["off", "low", "medium", "high", "full"],
        group="Policies",
        extended_help="**Kernel Logging Verbosity**\n\n- `off`: disables logging\n- `low`: rate-limited blocked packets and explicitly logged rules\n- `medium`: low plus invalid/new connections and allowed packets differing from policy, rate-limited\n- `high`: medium without rate limiting plus all packets with rate limiting\n- `full`: all logging without rate limiting",
    ),
    ConfigItem(
        label="Default Incoming",
        key="default_incoming",
        scope="status",
        type_="cycle",
        default="deny",
        options=["deny", "reject", "allow"],
        group="Policies",
        extended_help="**Default Ingress Policy**\n\nTraffic direction entering this machine. `deny` silently drops packets; `reject` replies with ICMP unreachable; `allow` accepts by default.",
    ),
    ConfigItem(
        label="Default Outgoing",
        key="default_outgoing",
        scope="status",
        type_="cycle",
        default="allow",
        options=["allow", "deny", "reject"],
        group="Policies",
        extended_help="**Default Egress Policy**\n\nTraffic direction originating from this host. Standard workstations use `allow`; lockdown whitelist environments use `deny`.",
    ),
    ConfigItem(
        label="Default Routed",
        key="default_routed",
        scope="status",
        type_="cycle",
        default="deny",
        options=["deny", "reject", "allow"],
        group="Policies",
        extended_help="**Default Forwarding Policy**\n\nControls traffic routed between interfaces (e.g. Docker, Waydroid, KVM VMs). Set to `deny` for strict isolation.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 3: PORTS (Quick Port Tool, Diagnostic Prober, Common Services)
# -----------------------------------------------------------------------------
# TAB 2: 'Sockets' is a full-height Rich live listening sockets view (show_options=False)
SCHEMA[3] = [
    # Quick Port Open / Close
    ConfigItem(
        label="Target Port",
        key="quick_port",
        scope="ports",
        type_="string",
        default="8080",
        group="Quick Port Tool",
        extended_help="**Port to Open or Close**\n\nEnter a single port (e.g. `8080`, `22`), comma list (`80,443`), or port range (`40000:40100`).",
    ),
    ConfigItem(
        label="Protocol",
        key="quick_proto",
        scope="ports",
        type_="cycle",
        default="tcp",
        options=["tcp", "udp", "both"],
        group="Quick Port Tool",
        extended_help="**Protocol**\n\n`tcp`, `udp`, or `both`.",
    ),
    ConfigItem(
        label="Ingress Scope",
        key="quick_scope",
        scope="ports",
        type_="string",
        default="any",
        options=["any", "lan", "127.0.0.1", "192.168.0.0/16", "10.0.0.0/8"],
        group="Quick Port Tool",
        extended_help="**Allowed Source Scope**\n\n- `any`: Open to the entire internet / all networks\n- `lan`: Restricted to local RFC1918 private subnets\n- or specific CIDR.",
    ),
    ConfigItem(
        label="Open / Allow Port",
        key="action_open_port",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Open Port"],

        group="Quick Port Tool",
        extended_help="**Open Port**\n\nCreates an explicit `ufw allow` rule for the target port and protocol.",
    ),
    ConfigItem(
        label="Close / Block Port",
        key="action_close_port",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Close Port"],

        group="Quick Port Tool",
        extended_help="**Close Port**\n\nPrepends an ingress deny rule for the selected protocol. Existing rules are retained. Framework allowances and established sessions may still pass traffic.",
    ),
    ConfigItem(
        label="Reject Port",
        key="action_reject_port",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reject Port"],
        group="Quick Port Tool",
        extended_help="**Reject Port**\n\nCloses the port with an ICMP unreachable reply.",
    ),
    ConfigItem(
        label="Delete Port Rules",
        key="action_delete_port_rules",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Delete Rules"],
        group="Quick Port Tool",
        extended_help="**Delete Rules for Port**\n\nDeletes ingress rules whose complete destination port specification and protocol match the input. Retains broader port lists/ranges, mixed-protocol rules, source-port rules and outgoing/routed rules.",
    ),
    # Port Prober
    ConfigItem(
        label="Probe Port #",
        key="probe_port",
        scope="ports",
        type_="int",
        default=22,
        min_val=1,
        max_val=65535,
        step=1,
        group="Port Diagnostic Prober",
        extended_help="**Port to Probe**\n\nTests whether a local service is listening, tests socket connect response, and cross-checks UFW rules.",
    ),
    ConfigItem(
        label="Run Port Probe",
        key="action_probe_port",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Probe Port"],
        group="Port Diagnostic Prober",
        extended_help="**Run Probe**\n\nExecutes active socket probe and reports reachability and firewall policy.",
    ),
    # Common Services Switches
    ConfigItem(
        label="SSH Server (22/tcp)",
        key="ssh",
        scope="services",
        type_="bool",
        default=True,
        group="Common Services",
        extended_help="**OpenSSH Server**\n\nPort 22/tcp for remote terminal access.",
    ),
    ConfigItem(
        label="Web HTTP (80/tcp)",
        key="http",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**HTTP Web Server**\n\nPort 80/tcp for unencrypted web traffic.",
    ),
    ConfigItem(
        label="Web HTTPS (443/tcp)",
        key="https",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**HTTPS Web Server**\n\nPort 443/tcp for encrypted TLS web traffic.",
    ),
    ConfigItem(
        label="FTP Server (21/tcp)",
        key="ftp",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**FTP File Transfer**\n\nPort 21/tcp for vsftpd/proftpd file transfer.",
    ),
    ConfigItem(
        label="DNS Server (53/udp+tcp)",
        key="dns",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**DNS Resolution Service**\n\nPort 53 for local DNS resolver or Pi-hole/dnsmasq.",
    ),
    ConfigItem(
        label="WireGuard VPN (51820/udp)",
        key="wireguard",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**WireGuard VPN Tunnel**\n\nPort 51820/udp for WireGuard ingress.",
    ),
    ConfigItem(
        label="Tailscale P2P (41641/udp)",
        key="tailscale",
        scope="services",
        type_="bool",
        default=True,
        group="Common Services",
        extended_help="**Tailscale Direct P2P**\n\nPort 41641/udp for direct peer-to-peer wireguard mesh traffic.",
    ),
    ConfigItem(
        label="Moonlight Streaming",
        key="moonlight",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**Sunshine / Moonlight Streaming**\n\nPorts 47984, 47989, 48010/tcp and 47998:48000/udp.",
    ),
    ConfigItem(
        label="Plex Media (32400/tcp)",
        key="plex",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**Plex Media Server**\n\nPort 32400/tcp for local media streaming.",
    ),
    ConfigItem(
        label="Minecraft (25565/tcp)",
        key="minecraft",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**Minecraft Game Server**\n\nPort 25565/tcp.",
    ),
    ConfigItem(
        label="Samba Share (445/tcp)",
        key="samba",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**Samba / Windows Share**\n\nPort 445/tcp for SMB network file sharing.",
    ),
    ConfigItem(
        label="VNC Display (5901/tcp)",
        key="vnc",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**VNC Remote Desktop**\n\nPort 5901/tcp for virtual desktop sharing.",
    ),
    ConfigItem(
        label="Syncthing (22000/tcp)",
        key="syncthing",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**Syncthing Device Sync**\n\nPort 22000/tcp for peer-to-peer file synchronization.",
    ),
    ConfigItem(
        label="BitTorrent (6881/both)",
        key="torrent",
        scope="services",
        type_="bool",
        default=False,
        group="Common Services",
        extended_help="**BitTorrent Ingress**\n\nPort 6881 for torrent peer connections.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 5: BUILDER (Interactive Rule Constructor & Rule Management)
# -----------------------------------------------------------------------------
# TAB 4: 'Rules' is a full-height Rich live numbered rules view (show_options=False)
SCHEMA[5] = [
    ConfigItem(
        label="Action",
        key="action",
        scope="builder",
        type_="cycle",
        default="allow",
        options=["allow", "deny", "reject", "limit"],
        group="Rule Parameters",
        extended_help="**Rule Action**\n\n- `allow`: Permit matching traffic\n- `deny`: Silently drop packet\n- `reject`: Refuse with ICMP unreachable reply\n- `limit`: Rate-limit connections (denies IP if 6 or more new connections within 30s; ideal for SSH)",
    ),
    ConfigItem(
        label="Direction",
        key="direction",
        scope="builder",
        type_="cycle",
        default="in",
        options=["in", "out", "route"],
        group="Rule Parameters",
        extended_help="**Traffic Direction**\n\n- `in`: Inbound to this machine\n- `out`: Outbound from this machine\n- `route`: Forwarded across network interfaces",
    ),
    ConfigItem(
        label="Protocol",
        key="proto",
        scope="builder",
        type_="cycle",
        default="any",
        options=["any", "tcp", "udp", "ah", "esp", "gre", "vrrp", "ipv6", "igmp"],
        group="Rule Parameters",
        extended_help="**Network Protocol**\n\nSelect layer 4 protocol. Choose `any` to apply across both TCP and UDP.",
    ),
    ConfigItem(
        label="Port(s) or Range",
        key="port",
        scope="builder",
        type_="string",
        default="",
        group="Rule Parameters",
        extended_help="**Port or Port Range**\n\nExamples:\n- Single port: `22` or `80`\n- Comma-separated: `80,443,8080` (max 15 ports)\n- Port range: `40000:40100`\n- Leave empty for any port.",
    ),
    ConfigItem(
        label="Source IP / Subnet",
        key="source",
        scope="builder",
        type_="string",
        default="any",
        group="Addressing",
        extended_help="**Source IP Address**\n\nOrigin of packets. `any` for anywhere, or single IP (`192.168.1.50`), or CIDR subnet (`192.168.0.0/16`).",
    ),
    ConfigItem(
        label="Destination IP / Subnet",
        key="dest",
        scope="builder",
        type_="string",
        default="any",
        group="Addressing",
        extended_help="**Destination IP Address**\n\nTarget destination. `any`, or host IP, or CIDR network.",
    ),
    ConfigItem(
        label="Interface (Host / Route In)",
        key="interface",
        scope="builder",
        type_="string",
        default="any",
        options=["any", *UfwEngine.get_network_interfaces()],
        group="Interfaces & Logging",
        extended_help="**Ingress Interface**\n\nHost rule interface (in or out according to direction), or routed ingress interface. Enter a name or select a discovered interface.",
    ),
    ConfigItem(
        label="Interface (Out / Routed)",
        key="out_interface",
        scope="builder",
        type_="string",
        default="any",
        options=["any", *UfwEngine.get_network_interfaces()],
        group="Interfaces & Logging",
        extended_help="**Egress Interface (Routed Only)**\n\nDestination interface for forwarded traffic traversing the host.",
    ),
    ConfigItem(
        label="Per-Rule Logging",
        key="log",
        scope="builder",
        type_="cycle",
        default="none",
        options=["none", "log", "log-all"],
        group="Interfaces & Logging",
        extended_help="**Rule Packet Logging**\n\n- `none`: Default\n- `log`: Log new matched connections\n- `log-all`: Log every matched packet",
    ),
    ConfigItem(
        label="Comment",
        key="comment",
        scope="builder",
        type_="string",
        default="",
        group="Placement & Execution",
        extended_help="**Rule Comment**\n\nHuman-readable description attached to the rule (e.g. `Dev API Server`).",
    ),
    ConfigItem(
        label="Placement",
        key="placement",
        scope="builder",
        type_="cycle",
        default="append",
        options=["append", "prepend", "insert"],
        group="Placement & Execution",
        extended_help="**Rule Placement Order**\n\n- `append`: Add to bottom of ruleset\n- `prepend`: Place at very beginning (highest priority match)\n- `insert`: Insert at specific index number",
    ),
    ConfigItem(
        label="Insert Index",
        key="insert_num",
        scope="builder",
        type_="int",
        default=1,
        min_val=1,
        max_val=500,
        step=1,
        group="Placement & Execution",
        extended_help="**Rule Index for Insertion**\n\nUsed when placement is set to `insert`.",
    ),
    ConfigItem(
        label="Apply Rule",
        key="action_apply_rule",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Commit Rule"],

        group="Placement & Execution",
        extended_help="**Commit Rule**\n\nExecutes the compiled UFW command. UFW applies the rule immediately when active and stores it for later when inactive.",
    ),
    ConfigItem(
        label="Target Rule Number",
        key="target_delete_num",
        scope="builder",
        type_="int",
        default=1,
        min_val=1,
        max_val=500,
        step=1,
        group="Manage Rules",
        extended_help="**Rule Number**\n\nEnter the index number of the rule to delete, as shown in the 'Rules' tab.",
    ),
    ConfigItem(
        label="Delete Rule",
        key="action_delete_rule",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Delete Rule"],
        confirm_message="Delete the selected rule number from the active firewall?",
        group="Manage Rules",
        extended_help="**Delete Numbered Rule**\n\nPermanently removes the rule matching the specified active index. UFW applies the deletion immediately.",
    ),
    ConfigItem(
        label="Reload Ruleset",
        key="action_reload",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reload"],
        group="Manage Rules",
        extended_help="**Synchronize Rules**\n\nReloads the UFW framework.",
    ),
    ConfigItem(
        label="Clear Domain Rules",
        key="action_sync_domains",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Clean & Sync Domains"],
        group="Manage Rules",
        extended_help="**Scrub & Refresh Domain Rules**\n\nRemoves stale resolved domain IPs and injects fresh DNS records.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 7: WHITELIST (Lockdown Mode, Domain Registry)
# -----------------------------------------------------------------------------
# TAB 6: 'Domains' is a full-height Rich live registered domains view (show_options=False)
SCHEMA[7] = [
    ConfigItem(
        label="Domain Allowlist Defaults",
        key="whitelist_mode",
        scope="domains",
        type_="bool",
        default=False,
        group="Lockdown Mode",
        extended_help="**Domain Allowlist Defaults**\n\nResolves registered addresses, adds selected TCP port rules and DNS/DHCP support, then sets all default policies to deny and enables UFW. Existing user/framework allowances and established sessions remain. Rules match IP addresses, not website names: shared hosting, DNS changes and QUIC can behave differently. Disabling restores deny incoming/routed and allow outgoing defaults; registered IP rules remain.",
    ),
    ConfigItem(
        label="Sync All Domain IPs",
        key="action_sync_domains",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Sync Domain IPs"],
        group="Lockdown Mode",
        extended_help="**DNS Re-Resolution & Sync**\n\nQueries DNS servers for current IPv4 (A) and IPv6 (AAAA) addresses of all registered domains and updates UFW rules.",
    ),
    ConfigItem(
        label="Domain Name",
        key="draft_domain",
        scope="domains",
        type_="string",
        default="",
        group="Register Domain",
        extended_help="**Domain FQDN**\n\ne.g. `archlinux.org`, `github.com`, `wikipedia.org`, or `ads.tracker.com`.",
    ),
    ConfigItem(
        label="Action",
        key="draft_action",
        scope="domains",
        type_="cycle",
        default="allow",
        options=["allow", "deny"],
        group="Register Domain",
        extended_help="**Domain Action**\n\n- `allow`: Add outbound rules for resolved addresses\n- `deny`: Prepend outbound blocks for resolved addresses\nRules with selected ports use TCP; `any` covers all protocols. Shared IPs affect other domains too. Existing sessions and framework rules still apply.",
    ),
    ConfigItem(
        label="Ports",
        key="draft_ports",
        scope="domains",
        type_="string",
        default="80,443",
        group="Register Domain",
        extended_help="**Allowed Egress Ports**\n\nComma-separated ports allowed for this domain (default: `80,443` for HTTP/HTTPS). Use `any` for all ports.",
    ),
    ConfigItem(
        label="Add Domain",
        key="action_add_domain",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Add Domain"],
        group="Register Domain",
        extended_help="**Register Domain**\n\nResolves domain IPs and commits rule to firewall.",
    ),
    ConfigItem(
        label="Remove Domain",
        key="action_remove_domain",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Remove Domain"],
        group="Register Domain",
        extended_help="**Remove Domain**\n\nRemoves domain from registry and scrubs its UFW rules.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 8: ROUTING (Port Forwarding DNAT, Sysctl, Containers)
# -----------------------------------------------------------------------------
SCHEMA[8] = [
    # Port Forwarding
    ConfigItem(
        label="WAN External Port",
        key="forward_ext_port",
        scope="nat",
        type_="string",
        default="8080",
        group="Port Forwarding (DNAT)",
        extended_help="**External Port**\n\nIncoming port on your WAN network adapter.",
    ),
    ConfigItem(
        label="Internal Destination IP",
        key="forward_dest_ip",
        scope="nat",
        type_="string",
        default="",
        group="Port Forwarding (DNAT)",
        extended_help="**Internal IP**\n\nTarget IP address (e.g. Waydroid Android container, Docker container, or Libvirt VM).",
    ),
    ConfigItem(
        label="Internal Destination Port",
        key="forward_dest_port",
        scope="nat",
        type_="string",
        default="80",
        group="Port Forwarding (DNAT)",
        extended_help="**Internal Port**\n\nPort running inside the container or target machine.",
    ),
    ConfigItem(
        label="Protocol",
        key="forward_proto",
        scope="nat",
        type_="cycle",
        default="tcp",
        options=["tcp", "udp"],
        group="Port Forwarding (DNAT)",
        extended_help="**Protocol**\n\nTCP or UDP forwarding. DNAT supports IPv4 destinations and single numeric ports.",
    ),
    ConfigItem(
        label="Add Port Forward",
        key="action_add_forward",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Add Forward"],
        group="Port Forwarding (DNAT)",
        extended_help="**Commit Port Forward**\n\nInjects PREROUTING DNAT into `/etc/ufw/before.rules` and adds forwarding route rule.",
    ),
    ConfigItem(
        label="Remove Port Forward",
        key="action_remove_forward",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Remove Forward"],
        group="Port Forwarding (DNAT)",
        extended_help="**Delete Port Forward**\n\nRemoves PREROUTING rule from `/etc/ufw/before.rules`.",
    ),
    # System & Containers
    ConfigItem(
        label="Kernel IP Forwarding",
        key="ip_forward",
        scope="framework",
        type_="bool",
        default=True,
        group="Routing & Containers",
        extended_help="**Sysctl IP Forwarding**\n\nToggles `net/ipv4/ip_forward=1` and `net/ipv6/conf/all/forwarding=1` in `/etc/ufw/sysctl.conf`.",
    ),
    ConfigItem(
        label="Waydroid NAT Masquerade",
        key="waydroid_nat",
        scope="framework",
        type_="bool",
        default=True,
        group="Routing & Containers",
        extended_help="**Container NAT Integration**\n\nDiscovers IPv4 subnets on the configured container interface and masquerades through the selected egress interface. Uses an owned NAT chain and the UFW after.init hook. Forwarding and route allowances are also required. Existing manual NAT is retained.",
    ),
    ConfigItem(
        label="Docker Daemon Mitigation",
        key="docker_mitigation",
        scope="framework",
        type_="bool",
        default=True,
        group="Routing & Containers",
        extended_help="**Docker iptables Guard**\n\nRequires Docker's iptables backend and an existing DOCKER-USER chain. Adds an owned chain that retains established sessions and drops new ingress on the selected egress interface. Other DOCKER-USER rules are retained. The hook attaches only when Docker's chain exists; restart/reload UFW after Docker recreates its rules. Docker's native nftables backend is unsupported.",
    ),
    ConfigItem(
        label="Reload Framework",
        key="action_reload",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reload"],
        group="Routing & Containers",
        extended_help="**Reload UFW Framework**\n\nApplies changes to `before.rules`, `after.rules`, and sysctl.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 10: BANS (IP Blacklisting, ICMP Stealth, Panic Killswitch)
# -----------------------------------------------------------------------------
# TAB 9: 'Traffic' is a full-height Rich live active connections view (show_options=False)
SCHEMA[10] = [
    ConfigItem(
        label="Target IP to Ban/Unban",
        key="ban_ip_target",
        scope="connections",
        type_="string",
        default="",
        group="IP Blacklisting",
        extended_help="**Remote Host IP**\n\nEnter the remote IP address (IPv4 or IPv6) to ban or unban.",
    ),
    ConfigItem(
        label="Ban IP (Top Priority Drop)",
        key="action_ban_ip",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Ban IP"],
        confirm_message="Prepend top-priority DROP rule for this IP address?",
        group="IP Blacklisting",
        extended_help="**Ingress IP Ban**\n\nPrepends a managed source deny rule. Framework allowances and established sessions can still pass traffic. This does not disconnect all traffic from that host.",
    ),
    ConfigItem(
        label="Unban IP",
        key="action_unban_ip",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Unban IP"],
        group="IP Blacklisting",
        extended_help="**Unban IP**\n\nRemoves only rules with this exact managed ban tag. Other deny rules are retained.",
    ),
    ConfigItem(
        label="Stealth ICMP Ping Mode",
        key="icmp_stealth",
        scope="framework",
        type_="bool",
        default=False,
        group="Stealth & Defenses",
        extended_help="**Input ICMP Echo**\n\nChanges standard IPv4/IPv6 input echo-request rules to DROP. Routed ping rules and other network discovery traffic are retained.",
    ),
    ConfigItem(
        label="Deny All Default Policies",
        key="action_panic_lockdown",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Deny Defaults"],
        confirm_message="Set all default policies to deny? Existing allowances and established sessions remain.",
        group="Stealth & Defenses",
        extended_help="**Deny Default Policies**\n\nSets incoming, outgoing and routed defaults to deny. Existing allowances and established sessions remain.",
    ),
    ConfigItem(
        label="Restore Traffic",
        key="action_panic_restore",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Restore Normal"],
        group="Stealth & Defenses",
        extended_help="**Restore Traffic**\n\nRestores standard operating policies.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 11: PROFILES (UFW Application Profiles)
# -----------------------------------------------------------------------------
SCHEMA[11] = [
    ConfigItem(
        label="Profile Name",
        key="target_app",
        scope="app",
        type_="string",
        default="",
        options=[],
        group="Application Control",
        extended_help="**UFW Application Name**\n\nName of application profile from `/etc/ufw/applications.d` (e.g. `OpenSSH`, `Samba`, `DNS`, `NFS`, `WWW Full`).",
    ),
    ConfigItem(
        label="Allow Application",
        key="app_allow",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Allow App"],
        group="Application Control",
        extended_help="**Allow Profile**\n\nOpens all ports associated with this application profile.",
    ),
    ConfigItem(
        label="Deny Application",
        key="app_deny",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Deny App"],
        group="Application Control",
        extended_help="**Deny Profile**\n\nBlocks all ports associated with this application profile.",
    ),
    ConfigItem(
        label="Reload Applications",
        key="action_reload",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Reload"],
        group="Application Control",
        extended_help="**Refresh Profiles**\n\nReloads application profiles.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 12: PRESETS (System and Hardened Security Presets)
# -----------------------------------------------------------------------------
SCHEMA[12] = [
    ConfigItem(
        label="Dusky Full Setup",
        key="action_preset_dusky_full",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Apply Dusky Full"],
        confirm_message="Apply Dusky defaults, present trusted interfaces, discovered SSH ports and container NAT? Existing rules remain.",
        group="System Profiles",
        extended_help="**Dusky Full Setup**\n\nApplies deny incoming/routed and allow outgoing defaults, configured SSH ports when sshd -T succeeds, Tailscale 41641/udp, configured trusted interfaces that are currently present, forwarding to the selected egress interface, and discovered container NAT when available. Existing rules are retained. Docker guard is configured separately.",
    ),
    ConfigItem(
        label="Strict Workstation",
        key="action_preset_strict_workstation",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Apply Strict"],
        confirm_message="Apply Strict Workstation profile?",
        group="System Profiles",
        extended_help="**Strict Workstation**\n\nSets deny incoming/routed and allow outgoing defaults, adds SSH 22/tcp and enables UFW. Existing rules remain.",
    ),
    ConfigItem(
        label="Domain Allowlist Defaults",
        key="action_preset_lockdown_whitelist",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Activate Lockdown"],
        confirm_message="Enable UFW with deny defaults and registered IP allowances? Existing rules and established sessions remain.",
        group="System Profiles",
        extended_help="**Domain Allowlist Defaults**\n\nAdds resolved IP rules and DNS/DHCP support, sets deny defaults and enables UFW. Preserves existing allowances and established sessions. This is IP filtering, not exclusive website filtering.",
    ),
    ConfigItem(
        label="Developer & Local LAN",
        key="action_preset_dev_lan",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Apply Dev LAN"],
        group="System Profiles",
        extended_help="**Dev & Local LAN**\n\nAllows incoming connections from private RFC1918 subnets (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12) and opens common dev ports (3000, 5173, 8000, 8080).",
    ),
    ConfigItem(
        label="Stealth Mode",
        key="action_preset_stealth",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Apply Stealth"],
        group="Hardened Profiles",
        extended_help="**Stealth Mode**\n\nRejects (instead of dropping) incoming packets, rate-limits SSH connections, drops ICMP pings, and logs invalid packets at medium level.",
    ),
    ConfigItem(
        label="Moonlight & Streaming",
        key="action_preset_streaming_moonlight",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Apply Streaming"],
        group="Hardened Profiles",
        extended_help="**Moonlight & Phone Display**\n\nOpens ports for Sunshine/Moonlight game streaming (47984, 47989, 48010/tcp, 47998:48000/udp) and VNC (5901).",
    ),
    ConfigItem(
        label="Factory Reset",
        key="action_preset_factory_reset",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Factory Reset"],
        confirm_message="WARNING: Reset all firewall rules to clean defaults?",
        group="Hardened Profiles",
        extended_help="**Factory Reset**\n\nCleans out all UFW rules and restores default settings.",
    ),
]

# -----------------------------------------------------------------------------
# TAB 13: AUDIT (Netfilter Diagnostic Reports Selector)
# -----------------------------------------------------------------------------
# TAB 14: 'Reports' is a full-height Rich live netfilter diagnostic report view (show_options=False)
SCHEMA[13] = [
    ConfigItem(
        label="Report Type",
        key="selected_report",
        scope="reports",
        type_="cycle",
        default="listening",
        options=["listening", "added", "user-rules", "before-rules", "after-rules", "logging-rules", "raw"],
        group="Netfilter Reports",
        extended_help="**Report Type**\n\n- `listening`: Open sockets and bound daemon rules\n- `added`: Rules as created on command line\n- `user-rules`: Raw /etc/ufw/user.rules table\n- `before-rules`: Early netfilter evaluation rules\n- `after-rules`: Trailing evaluation rules (Docker)\n- `raw`: Live kernel iptables packet counters\n\n*Note: View the full live report output under the 'Reports' tab.*",
    ),
    ConfigItem(
        label="Refresh Report",
        key="action_reload",
        scope="actions",
        type_="bool",
        default=False,
        options=["trigger:Refresh"],
        group="Netfilter Reports",
        extended_help="**Refresh Report**\n\nRe-reads live netfilter diagnostic report from the kernel.",
    ),
]

# Controls for runtime-discovered hardware and configurable integration.
SCHEMA[8].extend([
    ConfigItem(label="Egress Interface", key="wan_interface", scope="framework", type_="string", default="",
               options=["", *UfwEngine.get_network_interfaces()], group="Integration Parameters",
               extended_help="Blank uses the lowest-metric default route. Set explicitly for container NAT, Docker guard and provisioning. Reapply integrations after changing it."),
    ConfigItem(label="Container Interface", key="waydroid_interface", scope="framework", type_="string", default="waydroid0",
               options=UfwEngine.get_network_interfaces(), group="Integration Parameters",
               extended_help="Interface whose IPv4 subnet is discovered for NAT. Reapply NAT after changes."),
    ConfigItem(label="Trusted Interfaces", key="trusted_interfaces", scope="framework", type_="string",
               default="tailscale0,waydroid0,virbr0,docker0,wg0,tun0,tap0", group="Integration Parameters",
               extended_help="Comma-separated interfaces trusted by Dusky Full Setup. Only interfaces present at apply time receive rules."),
])
for item in SCHEMA[3]:
    if item.scope == "services":
        item.default = False

SCHEMA[3].insert(3, ConfigItem(label="Rule Comment", key="quick_comment", scope="ports", type_="string",
                             default="Custom Port Rule", group="Quick Port Tool",
                             extended_help="Comment attached to quick allow, deny and reject rules."))
SCHEMA[5].insert(6, ConfigItem(label="Source Port(s)", key="source_port", scope="builder", type_="string", default="",
                             group="Addressing", extended_help="Optional source port, comma list or colon range. Multiport rules require tcp or udp."))
def discover_ufw_choices():
    engine = UfwEngine._instance
    if engine is None:
        return []
    interfaces = engine.get_network_interfaces()
    for item in SCHEMA[5]:
        if item.key in {"interface", "out_interface"}:
            item.options = ["any", *interfaces]
    for item in SCHEMA[8]:
        if item.key in {"wan_interface", "waydroid_interface"}:
            item.options = ["", *interfaces]
    SCHEMA[11][0].options = ["", *engine.get_app_profile_names()]
    return [5, 8, 11]


DEFERRED_LOAD = discover_ufw_choices

TAB_NOTICES = {
    2: {"level": "info", "message": "UFW rule hints exclude framework and other firewalls. Remote reachability requires a test from another host."},
    3: {"level": "info", "message": "Service switches show their own tagged allow rules. Disable removes only their tagged rules; existing rules and defaults remain."},
    7: {"level": "info", "message": "Resolved IP rules affect shared hosts. Deny defaults preserve existing rules, framework allowances and established sessions."},
    12: {"level": "info", "message": "Presets add rules and set policies; existing rules remain unless Factory Reset is selected."},
}

# =============================================================================
# 4. RICH CUSTOM VIEWS
# =============================================================================
def prepare_ufw_view(kind):
    """Capture the engine and selection on the UI thread."""
    def prepare(app):
        report = next((item.value or "listening" for rows in app.schema.values()
                       for item in rows if item.key == "selected_report"), "listening")
        return app.engine_pool[app.default_engine_key], kind, report
    return prepare


def collect_ufw_view(prepared):
    """Blocking reads only; the framework serializes these with engine saves."""
    eng, kind, report = prepared
    match kind:
        case "status":
            rules = eng.get_numbered_rules()
            return {
                "status": eng.get_status_verbose(), "rules": rules,
                "wan": eng.detect_wan_interface() or "Unknown",
                "domains": eng._read_domain_registry(), "forward": eng.get_sysctl_forwarding(),
                "waydroid": eng.get_waydroid_nat(), "docker": eng.get_docker_mitigation(),
                "stealth": eng.get_icmp_ping_stealth(), "banned": eng.get_banned_ips(rules=rules),
                "probe": eng.cache.get("ports/probe_result", "Not run"),
                "listening": eng.get_listening_ports(),
            }
        case "ports":
            return eng.get_detailed_port_map()
        case "rules":
            return eng.get_numbered_rules()
        case "domains":
            return eng._read_domain_registry()
        case "connections":
            return eng.get_active_connections(), eng.get_banned_ips()
        case "reports":
            return report, eng.get_report(report)
    raise ValueError(f"Unknown UFW view: {kind}")


def render_ufw_dashboard_view(snapshot: dict) -> Any:
    status = snapshot["status"]
    rules = snapshot["rules"]
    wan = snapshot["wan"]
    whitelist_active = snapshot["domains"].get("whitelist_mode", False)

    is_active = status.get("active", False)
    status_text = Text("● ACTIVE", style="bold green") if is_active else Text("○ INACTIVE", style="bold red")

    # Card 1: Power & State
    t_power = Table(show_header=False, box=None, padding=(0, 1))
    t_power.add_column(style="dim", justify="right")
    t_power.add_column(style="bold white", justify="left")
    t_power.add_row("Status:", status_text)
    t_power.add_row("Logging:", Text(status.get("logging", "off").upper(), style="bold cyan"))
    t_power.add_row("Active Rules:", Text(str(len(rules)), style="bold yellow"))
    t_power.add_row("WAN Interface:", Text(wan, style="bold magenta"))
    t_power.add_row("Stored IP Forward:", Text("ENABLED" if snapshot["forward"] else "DISABLED", style="green" if snapshot["forward"] else "dim"))
    t_power.add_row("Waydroid NAT:", Text("CONFIGURED" if snapshot["waydroid"] else "OFF", style="green" if snapshot["waydroid"] else "dim"))
    t_power.add_row("Docker Guard:", Text("CONFIGURED" if snapshot["docker"] else "OFF", style="green" if snapshot["docker"] else "dim"))
    t_power.add_row("Stored Input Ping:", Text("DROP" if snapshot["stealth"] else "ACCEPT", style="bold yellow" if snapshot["stealth"] else "dim"))
    p_power = Panel(t_power, title="[bold cyan] 󰒃 FIREWALL STATUS [/bold cyan]", border_style="cyan", expand=True)

    # Card 2: Traffic Policies
    t_policy = Table(show_header=False, box=None, padding=(0, 1))
    t_policy.add_column(style="dim", justify="right")
    t_policy.add_column(style="bold white", justify="left")
    inc_style = "bold green" if status.get("default_incoming") == "allow" else "bold red"
    out_style = "bold red" if status.get("default_outgoing") in ("deny", "reject") else "bold green"
    t_policy.add_row("Incoming Default:", Text(status.get("default_incoming", "deny").upper(), style=inc_style))
    t_policy.add_row("Outgoing Default:", Text(status.get("default_outgoing", "allow").upper(), style=out_style))
    t_policy.add_row("Routed Default:", Text(status.get("default_routed", "deny").upper(), style="bold yellow"))
    if status.get("routing_disabled"):
        t_policy.add_row("Runtime Routing:", Text("DISABLED", style="dim"))
    wl_style = "bold green" if whitelist_active else "dim"
    t_policy.add_row("Whitelist Mode:", Text("DENY DEFAULTS + IP RULES" if whitelist_active else "DISABLED (STANDARD)", style=wl_style))
    t_policy.add_row("Last Local Probe:", Text(snapshot.get("probe", "Not run")))
    t_policy.add_row("Banned IPs:", Text(str(len(snapshot["banned"])), style="bold red" if snapshot["banned"] else "dim"))
    p_policy = Panel(t_policy, title="[bold green] 󰈀 DEFAULT POLICIES [/bold green]", border_style="green", expand=True)

    # Card 3: Listening Services Snapshot
    listening = snapshot["listening"]
    t_listen = Table(box=None, padding=(0, 1), show_header=True)
    t_listen.add_column("Proto", style="dim", width=6)
    t_listen.add_column("Port", style="bold yellow", width=8)
    t_listen.add_column("Process", style="bold cyan", width=16)
    t_listen.add_column("UFW Mapped Rule", style="white")

    for item in listening[:6]:
        rule_str = item["rules"][0] if item["rules"] else "—"
        t_listen.add_row(item["proto"], str(item["port"]), item["process"], rule_str)

    p_listen = Panel(t_listen, title="[bold yellow] 󰒋 LISTENING SOCKETS (PORTS) [/bold yellow]", border_style="yellow", expand=True)

    top_grid = Table.grid(expand=True)
    top_grid.add_column(ratio=1)
    top_grid.add_column(ratio=1)
    top_grid.add_row(p_power, p_policy)

    main_grid = Table.grid(expand=True)
    main_grid.add_column()
    main_grid.add_row(top_grid)
    main_grid.add_row(p_listen)

    return main_grid


def render_ports_view(port_map: list) -> Any:
    t = Table(expand=True, box=None, padding=(0, 1), show_header=True)
    t.add_column("Port", style="bold yellow", width=8, justify="right")
    t.add_column("Proto", style="dim", width=6)
    t.add_column("Process (PID)", style="bold cyan", width=20)
    t.add_column("Bound Address", style="white", ratio=2)
    t.add_column("Scope", style="dim", ratio=2)
    t.add_column("Firewall Status", width=18)

    for item in port_map:
        fw_status = item["fw_status"]
        status_style = "bold cyan" if fw_status == "PROTECTED" else (
            "bold green" if fw_status == "RULE ALLOW" else "bold yellow")
        tag = "LOCALHOST" if fw_status == "PROTECTED" else fw_status

        proc_str = f"{item['process']} ({item['pid']})" if item["pid"] else item["process"]
        t.add_row(
            str(item["port"]),
            item["proto"].upper(),
            proc_str,
            item["ip"],
            item["scope"],
            Text(tag, style=status_style),
        )

    return Panel(
        t,
        title=f"[bold cyan] 󰒋 LISTENING SOCKETS & UFW RULE HINTS ({len(port_map)} Ports) [/bold cyan]",
        border_style="cyan",
        expand=True,
    )


def render_rules_view(rules: list) -> Any:
    t = Table(expand=True, box=None, padding=(0, 1), show_header=True)
    t.add_column("#", style="dim bold", width=4, justify="right")
    t.add_column("Action", width=12)
    t.add_column("Destination (To)", style="bold white", ratio=2)
    t.add_column("Source (From)", style="cyan", ratio=2)
    t.add_column("Comment / Tag", style="dim italic", ratio=2)

    for r in rules:
        act_style = "bold green" if "ALLOW" in r.action else ("bold red" if "DENY" in r.action else "bold yellow")
        t.add_row(
            str(r.number),
            Text(r.action, style=act_style),
            r.to_addr,
            r.from_addr,
            r.comment,
        )

    return Panel(t, title=f"[bold green] 󰒃 ACTIVE NUMBERED RULES ({len(rules)}) [/bold green]", border_style="green", expand=True)


def render_domains_view(data: dict) -> Any:
    domains = data.get("domains", [])
    wl = data.get("whitelist_mode", False)

    t = Table(expand=True, box=None, padding=(0, 1), show_header=True)
    t.add_column("Domain", style="bold white", ratio=2)
    t.add_column("Action", width=10)
    t.add_column("Ports", style="yellow", width=12)
    t.add_column("Resolved IPs", style="cyan", ratio=3)

    for d in domains:
        act = d.get("action", "allow")
        act_style = "bold green" if act == "allow" else "bold red"
        ips_str = ", ".join(d.get("ips", [])) if d.get("ips") else "Pending resolution"
        t.add_row(
            d.get("domain", ""),
            Text(act.upper(), style=act_style),
            d.get("ports", "80,443"),
            ips_str,
        )

    mode_text = "[bold green]DENY DEFAULTS + IP RULES[/bold green]" if wl else "[bold red]DISABLED (STANDARD)[/bold red]"
    return Panel(
        t,
        title=f"[bold cyan] 󰖟 REGISTERED DOMAIN WHITELIST / BLOCKLIST — Mode: {mode_text} [/bold cyan]",
        border_style="cyan",
        expand=True,
    )


def render_connections_view(snapshot: tuple) -> Any:
    conns, banned = snapshot

    t = Table(expand=True, box=None, padding=(0, 1), show_header=True)
    t.add_column("Proto", style="dim", width=6)
    t.add_column("Local IP:Port", style="bold white", ratio=2)
    t.add_column("Remote IP:Port", style="bold yellow", ratio=2)
    t.add_column("Process (PID)", style="cyan", width=22)
    t.add_column("Status", width=12)

    for c in conns[:16]:
        is_banned = c["remote_ip"] in banned
        status_text = Text("BANNED", style="bold red") if is_banned else Text("ESTABLISHED", style="bold green")
        proc_str = f"{c['process']} ({c['pid']})" if c["pid"] else c["process"]
        t.add_row(
            c["proto"].upper(),
            f"{c['local_ip']}:{c['local_port']}",
            f"{c['remote_ip']}:{c['remote_port']}",
            proc_str,
            status_text,
        )

    banned_str = ", ".join(banned) if banned else "None"
    return Panel(
        t,
        title=f"[bold yellow] 󰛳 LIVE ESTABLISHED CONNECTIONS ({len(conns)}) — Banned IPs: [bold red]{banned_str}[/bold red] [/bold yellow]",
        border_style="yellow",
        expand=True,
    )


def render_reports_view(snapshot: tuple) -> Any:
    rep_name, content = snapshot
    return Panel(
        Text(content, style="white"),
        title=f"[bold yellow] 󰑓 NETFILTER LIVE REPORT: '{rep_name}' [/bold yellow]",
        border_style="yellow",
        expand=True,
    )


# =============================================================================
# 5. CUSTOM VIEWS REGISTRATION
# =============================================================================
CUSTOM_VIEWS = {
    0: {
        "view": render_ufw_dashboard_view,
        "prepare": prepare_ufw_view("status"),
        "collect": collect_ufw_view,
        "interval": 2.0,
        "show_options": False,
    },
    2: {
        "view": render_ports_view,
        "prepare": prepare_ufw_view("ports"),
        "collect": collect_ufw_view,
        "interval": 2.0,
        "show_options": False,
    },
    4: {
        "view": render_rules_view,
        "prepare": prepare_ufw_view("rules"),
        "collect": collect_ufw_view,
        "interval": 2.0,
        "show_options": False,
    },
    6: {
        "view": render_domains_view,
        "prepare": prepare_ufw_view("domains"),
        "collect": collect_ufw_view,
        "interval": 3.0,
        "show_options": False,
    },
    9: {
        "view": render_connections_view,
        "prepare": prepare_ufw_view("connections"),
        "collect": collect_ufw_view,
        "interval": 2.0,
        "show_options": False,
    },
    14: {
        "view": render_reports_view,
        "prepare": prepare_ufw_view("reports"),
        "collect": collect_ufw_view,
        "interval": 3.0,
        "show_options": False,
    },
}

# =============================================================================
# 6. DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
