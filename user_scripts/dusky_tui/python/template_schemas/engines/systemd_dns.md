# Engine: `systemd_dns`

- **Class:** `SystemdDnsEngine` — `engines/dns_systemd.py`
- **Engine types:** `systemd_dns`
- **Default target:** `/etc/systemd/resolved.conf.d/99-dns-tui.conf` (a systemd-resolved drop-in)

## Target format

A single **fully managed** `[Resolve]` drop-in. Every write regenerates the entire file from the engine's fixed state model (plus any extra `[Resolve]` keys it parsed from the file):

```ini
# Managed strictly by the Dusky TUI systemd_dns Engine.
# Do not edit manually; modifications will be overridden.
[Resolve]
DNS=1.1.1.1 1.0.0.1
DNSOverTLS=opportunistic
...
```

## Scope / key mapping

- `scope` is ignored entirely — use `"DEFAULT"`. State keys are flat (no prefix).
- Fixed key catalog (defaults as loaded when the file is missing/absent keys):

| key | default | notes |
|---|---|---|
| `DNS` | `""` | space-separated server addresses |
| `FallbackDNS` | `""` | |
| `DNSOverTLS` | `opportunistic` | |
| `DNSSEC` | `no` | |
| `LLMNR` | `no` | |
| `MulticastDNS` | `no` | |
| `DNSStubListener` | `yes` | `yes`/`udp`/`tcp` select the stub resolv.conf |
| `Cache` | `yes` | |

## Types & value handling

- Any non-action type (`string`, `cycle`, `picker`, `int`, `bool`, …) → written as `key=value` (value = serialized string).
- `type_="action"` with `key="flush_dns_cache"` → runs `resolvectl flush-caches` — but **only when it is the sole change in the batch**; mixed with config changes it is silently skipped.
- Unknown keys written by a schema are added as new `[Resolve]` lines; extra `[Resolve]` keys found in the file are preserved and rewritten.

## Quirks

- **Full-file rewrite:** manual edits are clobbered on the next save (the file is a managed drop-in).
- After every successful write the engine: syncs `/etc/resolv.conf` to `/run/systemd/resolve/stub-resolv.conf` when `DNSStubListener` is `yes`/`udp`/`tcp`, otherwise to the direct `resolv.conf`; unmasks + enables + **restarts** `systemd-resolved.service`; flushes caches; and runs `nmcli general reload dns-full` if NetworkManager is active.
- **Port-53 pre-flight:** if the requested `DNSStubListener` is `yes`/`udp`/`tcp` and another DNS service (dnsmasq, bind, unbound, pihole-FTL, dnscrypt-proxy, adguard-home, coredns, knot) holds port 53, the whole transaction is aborted with an explanatory message.
- **No `AUTH_REQUIRED` / no sudo fallback** — the engine writes directly and fails with a rollback message on `PermissionError`. The schema should set `REQUIRE_ROOT = True` (main.py re-executes the TUI with sudo).
- Transactional rollback: on any failure the previous file content (or its absence) is restored and the service restarted.
- On restart failure the write still counts as failed and is rolled back even though the drop-in was replaced.

## Example items

```python
ConfigItem(label="DNS Servers", key="DNS", scope="DEFAULT", type_="string",
           default="1.1.1.1 1.0.0.1", group="Servers"),
ConfigItem(label="DNS Over TLS", key="DNSOverTLS", scope="DEFAULT", type_="cycle",
           default="opportunistic", options=["no", "opportunistic", "yes"], group="Security"),
ConfigItem(label="Flush Cache", key="flush_dns_cache", scope="DEFAULT",
           type_="action", default="resolvectl flush-caches", group="Actions"),
```