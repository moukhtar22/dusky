# systemd-resolved on Arch Linux

> [!note]
> This is a **system-level DNS reference**. It applies the same way on Arch regardless of **Wayland/X11**, **Hyprland**, or **UWSM**.

## Overview

`systemd-resolved` is a **local caching stub resolver** and DNS policy engine. It sits between applications and upstream DNS servers and can handle:

- classic unicast DNS
- split DNS from multiple interfaces/VPNs
- DNSSEC validation
- DNS over TLS (DoT)
- mDNS (`.local`) and LLMNR on the local link
- caching and per-link routing of queries

Typical flow:

```text
application -> libc/NSS -> /etc/resolv.conf -> 127.0.0.53 -> systemd-resolved -> upstream DNS servers
```

If the stub listener is disabled, applications can instead use the direct upstream list exposed by:

```text
/run/systemd/resolve/resolv.conf
```

## What DNS, DNSSEC, and DoT actually mean

### DNS

DNS maps names to data. Common record types:

- `A` / `AAAA` — IPv4 / IPv6 addresses
- `CNAME` — alias to another name
- `MX` — mail servers
- `TXT` — arbitrary text, often verification/policy data
- `PTR` — reverse lookup
- `SRV` — service location

### DNSSEC

DNSSEC adds cryptographic signatures so the resolver can verify that DNS data is authentic and not tampered with in transit or by an upstream server.

- `DNSSEC=yes` = strict validation; failures break resolution
- `DNSSEC=allow-downgrade` = validate when possible, but allow fallback on broken networks
- `DNSSEC=no` = disabled

### DNS over TLS

DoT encrypts DNS traffic between `systemd-resolved` and the upstream resolver, usually on port `853`.

- `DNSOverTLS=yes` = strict; no plaintext fallback
- `DNSOverTLS=opportunistic` = use DoT if possible, otherwise silently fall back to plaintext
- `DNSOverTLS=no` = disabled

> [!important]
> **DNSSEC and DoT solve different problems**:
> - **DNSSEC** verifies authenticity of DNS data.
> - **DoT** encrypts transport to the upstream resolver.
>
> DoT does **not** make the resolver “trustless”; it only changes **who can observe** your queries in transit.

## Core corrections and design rules

> [!important]
> Do **not** maintain a giant copied vendor template in `/etc/systemd/resolved.conf`.  
> On Arch, the clean approach is to use a **drop-in** under:
>
> ```text
> /etc/systemd/resolved.conf.d/*.conf
> ```

> [!warning]
> `DNS=` in `resolved.conf` sets **global** DNS servers, but it does **not** suppress link-specific DNS learned from:
>
> - NetworkManager
> - systemd-networkd
> - DHCP/DHCPv6
> - IPv6 RDNSS
> - VPN software
>
> If you want to use **only** your manually chosen resolvers, you must also disable automatic DNS on the relevant network connections.

> [!warning]
> `FallbackDNS=` is **not** ordinary failover for `DNS=`.  
> It is used only when **no other DNS server information is known**.  
> If you already define `DNS=...`, `FallbackDNS=` is usually unnecessary.

> [!warning]
> Do **not** add `Domains=~.` globally unless you explicitly want to steer **all** DNS routing that way and understand the impact on VPN split DNS.  
> A global `~.` can interfere with VPN/corporate DNS behavior, internal domains, or route DNS in ways you did not intend.

## Recommended configuration model

### Use a drop-in, not the main file

Create a dedicated drop-in:

```bash
sudo install -d -m 0755 /etc/systemd/resolved.conf.d
sudoedit /etc/systemd/resolved.conf.d/10-privacy.conf
```

### Recommended balanced profile

This is a good general-purpose desktop profile:

- Quad9 as explicit global resolver
- DNSSEC enabled in compatibility mode
- DoT enabled opportunistically
- mDNS allowed for local `.local` lookups but without answering/advertising
- LLMNR disabled

```ini
# /etc/systemd/resolved.conf.d/10-privacy.conf
[Resolve]
DNS=9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net
DNSSEC=allow-downgrade
DNSOverTLS=opportunistic
MulticastDNS=resolve
LLMNR=no
```

### Strict profile

Use this only if you want strict encrypted DNS and are prepared for some networks to fail:

```ini
# /etc/systemd/resolved.conf.d/10-strict-dot.conf
[Resolve]
DNS=9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net
DNSSEC=yes
DNSOverTLS=yes
MulticastDNS=no
LLMNR=no
```

> [!warning]
> `DNSOverTLS=yes` and `DNSSEC=yes` can break:
> - captive portals
> - restrictive hotel/airport Wi-Fi
> - broken enterprise networks
> - networks that intercept or block port `853`

### LAN-friendly profile

If you want local `.local` name resolution but do **not** want your host to answer mDNS queries:

```ini
[Resolve]
MulticastDNS=resolve
LLMNR=no
```

### If you want your host to be discoverable on the LAN

```ini
[Resolve]
MulticastDNS=yes
LLMNR=no
```

`MulticastDNS=yes` makes `systemd-resolved` act as both:

- mDNS resolver
- mDNS responder for the local host

That makes your hostname more discoverable on the LAN.

> [!note]
> mDNS name resolution is not the same as full desktop service discovery.  
> Some printer/service browsing workflows still rely on **Avahi** or application-specific DNS-SD support.

---

## Enabling the service and setting `/etc/resolv.conf`

Enable and start the resolver:

```bash
sudo systemctl enable --now systemd-resolved.service
```

For the normal stub-listener setup, point `/etc/resolv.conf` at the generated stub file:

```bash
sudo ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
```

Apply changes:

```bash
sudo systemctl restart systemd-resolved.service
```

Verify:

```bash
readlink -f /etc/resolv.conf
resolvectl status
```

### Which `resolv.conf` target should be used?

| Target | Use when | Effect |
|---|---|---|
| `/run/systemd/resolve/stub-resolv.conf` | Normal desktop/server setup | Applications talk to local stub `127.0.0.53`; caching/split DNS/DNSSEC/DoT handled by `systemd-resolved` |
| `/run/systemd/resolve/resolv.conf` | Stub listener disabled, container/chroot edge cases, or port `53` conflicts | Applications bypass the local stub but still use the current upstream server list managed by `systemd-resolved` |
| `/usr/lib/systemd/resolv.conf` | Static fallback stub file | Always points to `127.0.0.53`, but does not include runtime search domains |

> [!warning]
> If `DNSStubListener=no`, do **not** link `/etc/resolv.conf` to `stub-resolv.conf`.  
> Use:
>
> ```bash
> sudo ln -sfn /run/systemd/resolve/resolv.conf /etc/resolv.conf
> ```

---

## Inspecting the effective configuration

Show the merged config from vendor defaults + local overrides:

```bash
systemd-analyze cat-config systemd/resolved.conf
```

This is the authoritative view of what `systemd-resolved` will use.

> [!tip]
> In systemd config files, some settings are scalar and override earlier values, while list-like settings can accumulate.  
> If multiple drop-ins define `DNS=`, keep your configuration tidy and avoid duplicate/conflicting files.

---

## NetworkManager integration

On many modern setups, NetworkManager can work with `systemd-resolved` automatically, but for deterministic behavior on Arch, set it explicitly.

Create a NetworkManager drop-in:

```bash
sudo install -d -m 0755 /etc/NetworkManager/conf.d
sudo tee /etc/NetworkManager/conf.d/10-dns-systemd-resolved.conf >/dev/null <<'EOF'
[main]
dns=systemd-resolved
EOF
```

Restart NetworkManager:

```bash
sudo systemctl restart NetworkManager.service
```

### If you want only your manual DNS servers

If you leave auto-DNS enabled, DHCP/VPN-provided DNS may still be used. To disable that per connection:

```bash
nmcli connection modify "<connection-name>" ipv4.ignore-auto-dns yes ipv6.ignore-auto-dns yes
nmcli connection modify "<connection-name>" ipv4.dns "9.9.9.9 149.112.112.112" ipv6.dns "2620:fe::fe 2620:fe::9"
nmcli connection up "<connection-name>"
```

> [!warning]
> Disabling automatic DNS on a VPN or corporate connection can break:
> - internal hostnames
> - split DNS
> - intranet services
>
> Only do this if you intentionally want to override the connection’s DNS policy.

### Useful NetworkManager checks

```bash
nmcli device show
nmcli connection show "<connection-name>"
resolvectl status
```

---

## systemd-networkd integration

If you use `systemd-networkd`, it feeds per-link DNS information directly to `systemd-resolved`.

Example `.network` file:

```ini
# /etc/systemd/network/25-wired.network
[Match]
Name=en*

[Network]
DHCP=yes
DNS=9.9.9.9#dns.quad9.net
DNS=149.112.112.112#dns.quad9.net
# Domains=~.   # only if you deliberately want this link preferred for all DNS
```

Restart networking:

```bash
sudo systemctl restart systemd-networkd.service systemd-resolved.service
```

---

## Meaning of the important options

| Option | Meaning | Recommended usage |
|---|---|---|
| `DNS=` | Global upstream DNS servers | Use when you want explicit system-wide upstream resolvers |
| `FallbackDNS=` | Used only if no other DNS servers are known | Usually leave unset if `DNS=` is already defined |
| `Domains=` | Search domains and route-only domains | Usually leave unset globally unless you know exactly why you need it |
| `DNSSEC=` | DNSSEC validation policy | `allow-downgrade` for compatibility, `yes` for strictness |
| `DNSOverTLS=` | DNS over TLS policy | `opportunistic` for compatibility, `yes` for strict privacy |
| `MulticastDNS=` | Local mDNS behavior | `no` for privacy, `resolve` for local lookup only, `yes` to also respond |
| `LLMNR=` | Link-local Multicast Name Resolution | Usually `no` |
| `DNSStubListener=` | Local stub on `127.0.0.53:53` | Usually leave enabled |
| `ResolveUnicastSingleLabel=` | Whether single-label names like `printer` are sent to unicast DNS | Usually leave default `no` to avoid name leakage |

### `Domains=` semantics

`Domains=` has two distinct forms:

- `example.com`  
  Search domain. Single-label lookups like `host` may become `host.example.com`.

- `~example.com`  
  Route-only domain. Influences which DNS servers should answer names under that domain but does **not** create a search suffix.

- `~.`  
  Route-only root domain. Means “prefer this DNS route for everything”.

> [!warning]
> `~.` is powerful and easy to misuse.  
> On VPNs, it can override or fight with split DNS expectations.

---

## mDNS vs LLMNR vs unicast DNS

### Unicast DNS
Normal internet DNS via your configured DNS servers.

### mDNS
Local-link multicast DNS, typically for `.local` names.

- Good for local devices like `printer.local`
- `MulticastDNS=resolve` = resolve only
- `MulticastDNS=yes` = resolve and answer

### LLMNR
Legacy local-link name resolution mainly seen on Windows networks.

- noisier
- weaker security properties
- easier to spoof than normal DNS

**Recommendation:** keep `LLMNR=no` unless you have a specific compatibility reason.

---

## VPN and split DNS

This is where many “privacy DNS” guides go wrong.

### Important behavior

`systemd-resolved` can maintain DNS data per link:

- Wi-Fi
- Ethernet
- VPN tunnel
- containers/virtual interfaces

A VPN may provide:

- DNS servers
- search domains
- route-only domains
- a default DNS route

### Common pitfalls

#### Global `DNS=` does not force exclusive usage
Per-link DNS can still be active.

#### Global `Domains=~.` is often the wrong fix
It can redirect all queries away from the VPN’s intended DNS routing.

#### Disabling auto-DNS everywhere can break internal services
Corporate VPNs often depend on VPN-provided DNS.

### Best practice

- If you want **VPN-aware split DNS**, let the VPN/network manager supply per-link DNS and domains.
- If you want **all DNS forced to your chosen public resolvers**, disable automatic DNS on the relevant connections and verify with `resolvectl status`.
- If you want **all DNS through the VPN**, configure that at the VPN/link level, not by adding a global `~.` blindly.

---

## Verification and day-to-day commands

### High-value inspection commands

| Command | What it shows |
|---|---|
| `resolvectl status` | Full global and per-link DNS state |
| `resolvectl dns` | DNS servers per link |
| `resolvectl domain` | Search/route-only domains per link |
| `resolvectl query example.com` | Resolve a name through `systemd-resolved` |
| `resolvectl query -4 example.com` | IPv4-only lookup |
| `resolvectl query -6 example.com` | IPv6-only lookup |
| `resolvectl statistics` | Cache and resolver statistics |
| `resolvectl flush-caches` | Clear caches |
| `resolvectl reset-server-features` | Forget remembered server feature probing |
| `resolvectl revert <ifname>` | Drop runtime-only per-link settings |
| `systemd-analyze cat-config systemd/resolved.conf` | Show merged configuration |
| `journalctl -u systemd-resolved -b` | Current boot logs for the service |
| `getent ahosts example.com` | Test name resolution through libc/NSS |
| `readlink -f /etc/resolv.conf` | Confirm which resolver file applications see |

### Examples

```bash
resolvectl status
resolvectl query archlinux.org
resolvectl query -4 example.com
resolvectl dns
resolvectl domain
resolvectl statistics
sudo resolvectl flush-caches
sudo resolvectl reset-server-features
journalctl -u systemd-resolved -b --no-pager
```

> [!tip]
> If `/etc/resolv.conf` points to the stub file, `cat /etc/resolv.conf` will only show `127.0.0.53`.  
> That is expected. Use `resolvectl status` to see the real upstream DNS servers.

---

## Troubleshooting

### 1. `/etc/resolv.conf` points to the wrong file

Check:

```bash
readlink -f /etc/resolv.conf
```

Fix for normal stub mode:

```bash
sudo ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
sudo systemctl restart systemd-resolved.service
```

Fix if stub listener is disabled:

```bash
sudo ln -sfn /run/systemd/resolve/resolv.conf /etc/resolv.conf
sudo systemctl restart systemd-resolved.service
```

---

### 2. Another service keeps overwriting `/etc/resolv.conf`

Possible culprits:

- NetworkManager not configured for `systemd-resolved`
- `openresolv` / `resolvconf`
- `dhcpcd` hooks
- VPN clients that manage `resolv.conf` directly

Inspect:

```bash
ls -l /etc/resolv.conf
grep -R . /etc/NetworkManager/conf.d 2>/dev/null
pacman -Qs 'openresolv|resolvconf|dhcpcd'
```

---

### 3. Port `53` conflict with another local resolver

If something else binds local DNS port `53`, the stub listener may fail.

Check listeners:

```bash
sudo ss -luntp | grep ':53'
```

Typical conflicting software:

- `dnsmasq`
- `unbound`
- Ad-blocking DNS daemons
- containers/VM tooling with local DNS proxies

If you intentionally run another local resolver, either:

- disable `DNSStubListener` and use `/run/systemd/resolve/resolv.conf`, or
- have `systemd-resolved` forward to that local resolver intentionally

---

### 4. Strict DoT/DNSSEC breaks on captive portals or hostile networks

Symptoms:

- DNS fails until login/portal acceptance
- `resolvectl status` shows no working current server
- websites do not resolve on hotel/airport Wi-Fi

Temporary mitigations:

- use `DNSSEC=allow-downgrade` instead of `yes`
- use `DNSOverTLS=opportunistic` instead of `yes`
- temporarily disable your strict drop-in, restart, complete portal login, then restore

---

### 5. Local printers or `.local` hosts do not resolve

Try:

```ini
[Resolve]
MulticastDNS=resolve
LLMNR=no
```

Then restart:

```bash
sudo systemctl restart systemd-resolved.service
```

If service browsing still does not appear in GUI apps, you may also need **Avahi**.

---

### 6. Browser still ignores your system DNS settings

Some applications bypass the system resolver entirely.

Common examples:

- Firefox DoH / TRR
- Chromium-based browsers with secure DNS
- VPN clients with embedded DNS
- containers with their own `/etc/resolv.conf`

> [!warning]
> `systemd-resolved` does **not** provide DNS-over-HTTPS (DoH).  
> If an application uses DoH directly, your `systemd-resolved` policy may not apply to that app.

---

### 7. VPN internal names stop working

Check:

```bash
resolvectl status
resolvectl domain
nmcli device show
```

Typical cause:

- manual global DNS plus disabled auto-DNS
- global `Domains=~.`
- VPN’s split-DNS domains no longer routed to VPN DNS

Fix:

- remove global `Domains=~.`
- re-enable VPN-provided DNS if internal names are required
- configure split DNS on the VPN/link itself rather than globally

---

### 8. Name lookups changed but old answers persist

Flush the cache:

```bash
sudo resolvectl flush-caches
sudo resolvectl reset-server-features
```

Then re-test:

```bash
resolvectl query example.com
```

---

## Minimal manual setup, end-to-end

```bash
sudo install -d -m 0755 /etc/systemd/resolved.conf.d

sudo tee /etc/systemd/resolved.conf.d/10-privacy.conf >/dev/null <<'EOF'
[Resolve]
DNS=9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net
DNSSEC=allow-downgrade
DNSOverTLS=opportunistic
MulticastDNS=resolve
LLMNR=no
EOF

sudo systemctl enable --now systemd-resolved.service
sudo ln -sfn /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
sudo systemctl restart systemd-resolved.service

resolvectl status
```

If using NetworkManager and you want deterministic integration:

```bash
sudo install -d -m 0755 /etc/NetworkManager/conf.d
sudo tee /etc/NetworkManager/conf.d/10-dns-systemd-resolved.conf >/dev/null <<'EOF'
[main]
dns=systemd-resolved
EOF

sudo systemctl restart NetworkManager.service
resolvectl status
```

---

## Improved Bash setup script

This version is safer than overwriting the full main config file. It:

- uses a drop-in under `/etc/systemd/resolved.conf.d`
- keeps the vendor main file untouched
- enables and restarts `systemd-resolved`
- chooses the stub `resolv.conf` when available, otherwise falls back to the direct file
- verifies status at the end

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

trap 'printf >&2 "ERROR: line %d: %s\n" "$LINENO" "${BASH_COMMAND:-?}"' ERR

main() {
    if (( EUID != 0 )); then
        exec sudo -- "$0" "$@"
    fi

    command -v systemctl >/dev/null
    command -v resolvectl >/dev/null

    local -r confd='/etc/systemd/resolved.conf.d'
    local -r conf="${confd}/10-privacy.conf"
    local -r stub='/run/systemd/resolve/stub-resolv.conf'
    local -r direct='/run/systemd/resolve/resolv.conf'
    local target="$direct"
    local tmp

    install -d -m 0755 "$confd"

    tmp=$(mktemp)
    cat >"$tmp" <<'EOF'
[Resolve]
DNS=9.9.9.9#dns.quad9.net 149.112.112.112#dns.quad9.net 2620:fe::fe#dns.quad9.net 2620:fe::9#dns.quad9.net
DNSSEC=allow-downgrade
DNSOverTLS=opportunistic
MulticastDNS=resolve
LLMNR=no
EOF

    if ! cmp -s "$tmp" "$conf" 2>/dev/null; then
        install -m 0644 "$tmp" "$conf"
    fi
    rm -f "$tmp"

    systemctl enable --now systemd-resolved.service
    systemctl restart systemd-resolved.service

    for _ in {1..50}; do
        if [[ -e "$stub" ]]; then
            target="$stub"
            break
        fi
        [[ -e "$direct" ]] && target="$direct"
        sleep 0.1
    done

    ln -sfn "$target" /etc/resolv.conf

    printf 'Configured /etc/resolv.conf -> %s\n' "$target"
    printf 'Current link target: %s\n' "$(readlink -f /etc/resolv.conf)"

    systemctl --quiet is-active systemd-resolved.service
    resolvectl status
}

main "$@"
```

> [!note]
> This script intentionally does **not** modify NetworkManager connection profiles.  
> If you want to prevent DHCP/VPN DNS from being used, do that explicitly with `nmcli`.

---

## Rollback

Remove the drop-in:

```bash
sudo rm -f /etc/systemd/resolved.conf.d/10-privacy.conf
sudo systemctl restart systemd-resolved.service
```

If you want to stop using `systemd-resolved` entirely, first reconfigure whichever network manager should own `/etc/resolv.conf`, then disable the service:

```bash
sudo systemctl disable --now systemd-resolved.service
```

> [!warning]
> Do **not** disable `systemd-resolved` while `/etc/resolv.conf` still points at its stub unless another resolver is already in place.

---

## Recommended defaults

For most Arch desktop systems:

- `DNSSEC=allow-downgrade`
- `DNSOverTLS=opportunistic`
- `LLMNR=no`
- `MulticastDNS=resolve` if you need local `.local` lookups
- `MulticastDNS=no` if you want stricter privacy and do not need local mDNS
- avoid global `Domains=~.`
- avoid `FallbackDNS=` unless you know why you need it

## Useful man pages

```text
resolved.conf(5)
systemd-resolved.service(8)
resolvectl(1)
systemd.network(5)
NetworkManager.conf(5)
nmcli(1)
```
