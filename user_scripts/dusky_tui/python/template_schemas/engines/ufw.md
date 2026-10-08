# Engine: `ufw`

`UfwEngine` manages UFW through its documented CLI on Linux. The schema must set
`REQUIRE_ROOT = True` for framework file edits. Reads use the C locale; writes
are serialized, commands have timeouts, and failures reach the frontend.
Multi-step failures can leave earlier CLI changes applied; they are reported as
failures rather than successes. Framework files are restored if reload fails.

## Rule semantics

- CLI rule changes apply immediately when UFW is active and are stored when
  inactive. They do not require an extra reload. Framework file edits do.
- Builder host directions are explicit even without interfaces. Routed placement
  uses `ufw route insert NUM ...` or `ufw route prepend ...`.
- Numeric ports support comma lists and colon ranges, with at most 15 slots;
  a range takes two slots. Lists/ranges require TCP or UDP in the builder.
  Portless protocols cannot specify source or destination ports.
- Quick close/reject prepends an ingress block, retaining existing rules.
  Framework allowances and established sessions may still take precedence.
- Quick deletion matches the **entire** ingress destination port specification
  and protocol. It retains broader lists/ranges, other protocols, source-port
  matches and outgoing/routed rules. `both` deletes separate TCP and UDP rules;
  rules with unspecified protocol are retained.
- Service switches indicate their own `dusky:service:<name>` rules. Disable
  removes only those rules. State is read from stored rules even while inactive.
  Other rules/default policies can still allow traffic.
  Moonlight includes `47984,47989,48010/tcp` and `47998:48000/udp`.
- Ban prepends an ingress deny rule tagged `Banned: <canonical IP>`. Unban removes
  only that exact tag. It does not terminate existing connections.
- Tagged cleanup uses `ufw show added` and deletion by rule signature, so it also
  works while inactive. Numbered views show only active UFW user rules.

## Reporting and default policies

Socket reports correlate destination ports, protocols, directions and address
families. They show `RULE ALLOW/DENY/REJECT/LIMIT`, `CONDITIONAL`, `DEFAULT ...`,
`UFW INACTIVE`, or `PROTECTED` for loopback. They are configuration hints:
`ufw status` omits before/after rules and other firewall tables. They cannot
establish WAN exposure or an effective packet verdict. Test remote access from
another host. The active probe checks a local TCP connection; its last result
appears in the Status dashboard.

If UFW reports routed traffic as disabled, the editable default still shows the
stored routed policy, and the dashboard reports runtime routing separately.
Forwarding, container NAT, Docker and input-ping indicators describe stored
configuration, not an independent check of live enforcement.

The legacy `action_panic_lockdown` key sets all default policies to deny. It is
labelled **Deny All Default Policies**, because existing rules, framework
allowances and established sessions remain. Restore sets deny incoming/routed
and allow outgoing; it does not restore an earlier custom policy snapshot.
Presets add rules and set defaults, retaining existing rules except Factory Reset.

## Domain registry

The registry lives at `$XDG_CONFIG_HOME/dusky/settings/firewall/domains.json`,
falling back to `~/.config`. Reading state does not create files. Writes are
atomic, and malformed registries produce an error rather than being overwritten.

DNS resolution runs in bounded child processes (10 seconds each), with up to
8 concurrent lookups. Sync records actual UTC timestamps. Failed resolutions
retain last-known addresses and report failure. IPv6 addresses remain in the
registry but are only applied when both UFW and the kernel support IPv6.

Rules filter resolved **IP addresses**, not domain names. Selected ports use
TCP; `any` covers all protocols. Shared hosting, CDNs, changed DNS records and
QUIC can differ from website-level filtering. Blocks are prepended.

`whitelist_mode` adds registered IP rules and DNS/DHCP support, sets deny defaults,
and enables UFW. It preserves existing allowances and established sessions.
Disabling restores workstation defaults and removes mode support rules, retaining
registered IP allow/block rules. Reset disables UFW and clears the registry mode
flag, retaining registered domain entries.

## Framework integration

- Atomic edits preserve existing file mode and ownership.
- ICMP control changes standard IPv4/IPv6 **input** echo-request rules. Routed
  ICMP and other discovery traffic are retained.
- Forwarding edits accept dotted/slash sysctl keys, store the selected settings,
  reload UFW, and explicitly apply/check touched sysctls even while UFW is inactive.
- DNAT supports single numeric ports and IPv4 destinations. It requires forwarding
  and validates the kernel target with `iptables-restore --test --noflush` before
  writing. It affects all ingress interfaces. Return-path routing/NAT is the
  administrator's responsibility.
- Application actions use the selected profile and validate the kernel's comment
  match before creating rules. This extension is required by UFW profiles.
- Container NAT discovers the configured interface's IPv4 subnets and masquerades
  through the chosen egress interface. Forwarding and route allowances are needed.
- Docker guard requires its **iptables backend** and an existing DOCKER-USER chain.
  It retains established sessions and drops new ingress on the selected interface.
  Native Docker nftables rules are unsupported. Reload UFW after Docker recreates
  its rules; guard configuration alone does not prove that Docker is guarded.

DNAT, container NAT and Docker guard use owned chains and marked blocks in
`before.rules`/`after[6].rules`. A marked shell block is inserted after the shebang
in `/etc/ufw/after.init`, preserving the rest of an existing sh/bash script.
It installs one owned jump on start and removes owned jumps/chains on stop.
Declared user chains are rebuilt under UFW's `--noflush` restores, preventing
stale translations and accumulating jumps across reloads. Existing manually
configured NAT/Docker rules are retained and must be managed separately.

## Scopes and editable keys

| Scope | Keys |
|---|---|
| `status` | `firewall_enabled`, `logging_level`, `default_incoming`, `default_outgoing`, `default_routed` |
| `ports` | `quick_port`, `quick_proto`, `quick_scope`, `quick_comment`, `probe_port` |
| `builder` | `action`, `direction`, `proto`, `port`, `source_port`, `source`, `dest`, `interface`, `out_interface`, `log`, `comment`, `placement`, `insert_num`, `target_delete_num` |
| `services` | Keys in `COMMON_SERVICES` |
| `domains` | `whitelist_mode`, `draft_domain`, `draft_action`, `draft_ports` |
| `nat` | `forward_ext_port`, `forward_dest_ip`, `forward_dest_port`, `forward_proto` |
| `framework` | `ip_forward`, `waydroid_nat`, `docker_mitigation`, `icmp_stealth`, `wan_interface`, `waydroid_interface`, `trusted_interfaces` |
| `connections` | `ban_ip_target` |
| `app` | `target_app` (used by application trigger buttons) |
| `reports` | `selected_report` |
| `actions` | Momentary `action_*`, `app_allow`, `app_deny`; false does nothing |

Draft fields survive F5 within an engine instance. They are not persisted between
sessions. Blank egress selection uses the lowest-metric default route; explicit
selection is available. Interface dropdowns show runtime discoveries and permit
custom names. Reapply integrations after changing parameters. Dusky Full Setup
uses configured trusted interfaces only when present, discovers SSH ports, and
leaves Docker guard as a separate explicit control.
