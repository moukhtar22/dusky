#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEMD-RESOLVED DNS SCHEMA
===============================================================================
Target: /etc/systemd/resolved.conf.d/99-dns-tui.conf
Engine: systemd_dns (Atomic POSIX / resolvectl)
===============================================================================
"""

import sys
from pathlib import Path

# Bootstrap the Python path to locate the core TUI modules
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "systemd_dns"
TARGET_FILE = "/etc/systemd/resolved.conf.d/99-dns-tui.conf"
REQUIRE_ROOT = True
APP_TITLE = "DNS Settings"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = {
    0: "Presets",
    1: "Security",
    2: "Custom",
    3: "System",
}

# =============================================================================
# 4. HARDENED PRESET PAYLOADS
# =============================================================================
FALLBACK_QUAD9 = "9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net"

PRESET_CLOUDFLARE = {
    "Resolve.DNS": "1.1.1.1#cloudflare-dns.com 1.0.0.1#cloudflare-dns.com 2606:4700:4700::1111#cloudflare-dns.com 2606:4700:4700::1001#cloudflare-dns.com",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_QUAD9 = {
    "Resolve.DNS": "9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net",
    "Resolve.FallbackDNS": "1.1.1.1#cloudflare-dns.com 1.0.0.1#cloudflare-dns.com",
    "Resolve.DNSOverTLS": "yes",
    "Resolve.DNSSEC": "allow-downgrade",
}

PRESET_MULLVAD = {
    "Resolve.DNS": "194.242.2.2#dns.mullvad.net 194.242.2.3#dns.mullvad.net 2a07:e180:2::1#dns.mullvad.net",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "yes",
    "Resolve.DNSSEC": "no",
}

PRESET_ADGUARD = {
    "Resolve.DNS": "94.140.14.14#dns.adguard-dns.com 94.140.15.15#dns.adguard-dns.com 2a10:50c0::ad1:ff#dns.adguard-dns.com 2a10:50c0::ad2:ff#dns.adguard-dns.com",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_GOOGLE = {
    "Resolve.DNS": "8.8.8.8#dns.google 8.8.4.4#dns.google 2001:4860:4860::8888#dns.google 2001:4860:4860::8844#dns.google",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_CONTROLD = {
    "Resolve.DNS": "76.76.2.0#p0.freedns.controld.com 76.76.10.0#p0.freedns.controld.com 2606:1a40::#p0.freedns.controld.com",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_CLOUDFLARE_FAMILY = {
    "Resolve.DNS": "1.1.1.3#family.cloudflare-dns.com 1.0.0.3#family.cloudflare-dns.com 2606:4700:4700::1113#family.cloudflare-dns.com 2606:4700:4700::1003#family.cloudflare-dns.com",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_OPENDNS = {
    "Resolve.DNS": "208.67.222.222#dns.opendns.com 208.67.220.220#dns.opendns.com 2620:119:35::35#dns.opendns.com 2620:119:53::53#dns.opendns.com",
    "Resolve.FallbackDNS": FALLBACK_QUAD9,
    "Resolve.DNSOverTLS": "opportunistic",
    "Resolve.DNSSEC": "no",
}

PRESET_DHCP = {
    "Resolve.DNS": "",
    "Resolve.FallbackDNS": "",
    "Resolve.Domains": "",
    "Resolve.DNSOverTLS": "no",
    "Resolve.DNSSEC": "no",
}

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: PRESETS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Cloudflare",
            key="preset_cloudflare",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_CLOUDFLARE,
            extended_help="Configures Cloudflare high-performance global DNS with TLS SNI authentication (#cloudflare-dns.com).",
            exists_in_target=True
        ),
        ConfigItem(
            label="Cloudflare Family",
            key="preset_cloudflare_family",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_CLOUDFLARE_FAMILY,
            extended_help="Cloudflare Family DNS filtering out malware and adult content.",
            exists_in_target=True
        ),
        ConfigItem(
            label="Quad9",
            key="preset_quad9",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_QUAD9,
            extended_help="Configures Quad9 with strict DNS-over-TLS encryption and malware blocking.",
            exists_in_target=True
        ),
        ConfigItem(
            label="Mullvad",
            key="preset_mullvad",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_MULLVAD,
            extended_help="Routes lookups through Mullvad zero-log public DNS servers with strict DoT.",
            exists_in_target=True
        ),
        ConfigItem(
            label="AdGuard",
            key="preset_adguard",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_ADGUARD,
            extended_help="Blocks advertisements and tracking domains at the resolver level.",
            exists_in_target=True
        ),
        ConfigItem(
            label="OpenDNS",
            key="preset_opendns",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_OPENDNS,
            extended_help="Reliable global DNS from Cisco Umbrella with DoT SNI support.",
            exists_in_target=True
        ),
        ConfigItem(
            label="Google",
            key="preset_google",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_GOOGLE,
            extended_help="Standard Google Public DNS with IPv4 and IPv6 resolvers.",
            exists_in_target=True
        ),
        ConfigItem(
            label="ControlD",
            key="preset_controld",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_CONTROLD,
            extended_help="ControlD free unfiltered resolver network with DoT hostname verification.",
            exists_in_target=True
        ),
        ConfigItem(
            label="DHCP",
            key="preset_dhcp",
            type_="preset",
            default=False,
            scope="DEFAULT",
            preset_payload=PRESET_DHCP,
            extended_help="Clears static drop-in overrides, returning full control to local DHCP/NetworkManager.",
            exists_in_target=True
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: SECURITY
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="DNS-over-TLS",
            key="DNSOverTLS",
            type_="cycle",
            default="opportunistic",
            scope="Resolve",
            options=["opportunistic", "yes", "no"],
            extended_help=(
                "Controls encryption of DNS queries over TLS (Port 853).\n"
                "  • yes: Strict mode (refuses plaintext fallback).\n"
                "  • opportunistic: Upgrades to TLS if supported, falls back to UDP/53.\n"
                "  • no: Plaintext UDP/53 only."
            ),
        ),
        ConfigItem(
            label="DNSSEC",
            key="DNSSEC",
            type_="cycle",
            default="no",
            scope="Resolve",
            options=["no", "allow-downgrade", "yes"],
            extended_help=(
                "Enables cryptographic signature verification of DNS records.\n"
                "  • allow-downgrade: Validates if supported.\n"
                "  • yes: Strict enforcement (may break broken captive portals).\n"
                "  • no: Disabled."
            ),
        ),
        ConfigItem(
            label="Cache Mode",
            key="Cache",
            type_="cycle",
            default="yes",
            scope="Resolve",
            options=["yes", "no-negative", "no"],
            extended_help=(
                "Controls local DNS response caching.\n"
                "  • yes: Full positive and negative response caching.\n"
                "  • no-negative: Caches positive lookups only.\n"
                "  • no: Disables local cache entirely."
            ),
        ),
        ConfigItem(
            label="Fallback DNS",
            key="FallbackDNS",
            type_="string",
            default=FALLBACK_QUAD9,
            scope="Resolve",
            extended_help="Space-separated list of fallback resolvers used only if primary DNS servers fail.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: CUSTOM
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="DNS Servers",
            key="DNS",
            type_="string",
            default=PRESET_CLOUDFLARE["Resolve.DNS"],
            scope="Resolve",
            extended_help="Space-separated explicit DNS servers (Format: IP#HOSTNAME).",
        ),
        ConfigItem(
            label="Domains",
            key="Domains",
            type_="string",
            default="",
            scope="Resolve",
            extended_help="Space-separated search suffixes and routing domains (e.g. '~.').",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: SYSTEM
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Stub Listener",
            key="DNSStubListener",
            type_="cycle",
            default="yes",
            scope="Resolve",
            options=["yes", "no", "udp", "tcp"],
            extended_help="Controls systemd-resolved local stub listener (127.0.0.53). Note: Disabling without a local DNS proxy breaks resolution.",
        ),
        ConfigItem(
            label="MulticastDNS",
            key="MulticastDNS",
            type_="cycle",
            default="no",
            scope="Resolve",
            options=["no", "resolve", "yes"],
            extended_help="Controls mDNS handling (UDP 5353). Keep 'no' if using Avahi.",
        ),
        ConfigItem(
            label="LLMNR",
            key="LLMNR",
            type_="cycle",
            default="no",
            scope="Resolve",
            options=["no", "resolve", "yes"],
            extended_help="Legacy local multicast resolution. Recommended: 'no'.",
        ),
        ConfigItem(
            label="Flush DNS Cache",
            key="flush_dns_cache",
            type_="action",  
            default="resolvectl flush-caches",
            scope="DEFAULT",
            exists_in_target=True,
            confirm_message="Flush all cached DNS records?",
            extended_help="Executes resolvectl flush-caches to purge cached DNS queries.",
        ),
    ],
}

# =============================================================================
# 6. DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
