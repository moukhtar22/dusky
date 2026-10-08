#!/usr/bin/env python3
#d: Install and configure the UFW firewall
"""Provision UFW with discovered networking and TUI-compatible owned chains.

Requires the ISO Python 3.14 baseline and installed UFW 0.36.2 interfaces.
Existing user rules remain; defaults are not a complete traffic lockdown.
Use --help for interface, SSH-port and Docker guard options.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

DEFAULT = Path('/etc/default/ufw')
RULES = Path('/etc/ufw')


def log(message):
    print(message, flush=True)


def run(*args, check=True, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    if check and result.returncode:
        raise RuntimeError(f'{shlex.join(args)}: {result.stderr.strip() or result.stdout.strip() or result.returncode}')
    return result


def atomic_write(path, content, mode=0o644):
    path = path.resolve()
    old = path.stat() if path.exists() else None
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, encoding='utf-8', delete=False) as file:
            temp = file.name
            if old:
                os.fchown(file.fileno(), old.st_uid, old.st_gid)
            os.fchmod(file.fileno(), stat.S_IMODE(old.st_mode) if old else mode)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def setting(content, key, value):
    # Normalize every occurrence: a later duplicate must not override this value.
    pattern = r'^\s*#?\s*' + re.escape(key).replace('/', r'[/.]') + r'\s*=.*$'
    line = f'{key}={value}'
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, line, content, flags=re.MULTILINE)
    return content.rstrip() + '\n' + line + '\n'


def block(content, name, body):
    content = re.sub(rf'^# BEGIN DUSKY {name}\n.*?^# END DUSKY {name}\n?', '', content,
                     flags=re.MULTILINE | re.DOTALL)
    return content.rstrip() + f'\n\n# BEGIN DUSKY {name}\n{body.rstrip()}\n# END DUSKY {name}\n'


def hook(content):
    # Same owned chains/markers as the TUI engine; preserve the existing script.
    if not re.match(r'^#![^\n]*(?:/sh|/bash|env (?:sh|bash))(?:\s|$)', content):
        raise ValueError('after.init must be a sh/bash script')
    content = re.sub(r'^# BEGIN DUSKY HOOKS\n.*?^# END DUSKY HOOKS\n?', '', content,
                     flags=re.MULTILINE | re.DOTALL)
    first, _, rest = content.partition('\n')
    return first + '\n' + HOOK + rest


HOOK = '''# BEGIN DUSKY HOOKS
(
    set -e
    dusky_jump() {
        dusky_cmd=$1 dusky_table=$2 dusky_parent=$3 dusky_chain=$4
        command -v "$dusky_cmd" >/dev/null || return 0
        if "$dusky_cmd" -w 5 -t "$dusky_table" -S "$dusky_chain" >/dev/null 2>&1; then
            if "$dusky_cmd" -w 5 -t "$dusky_table" -S "$dusky_parent" >/dev/null 2>&1; then
                while "$dusky_cmd" -w 5 -t "$dusky_table" -C "$dusky_parent" -j "$dusky_chain" 2>/dev/null; do
                    "$dusky_cmd" -w 5 -t "$dusky_table" -D "$dusky_parent" -j "$dusky_chain"
                done
                if [ "$dusky_action" = start ]; then
                    "$dusky_cmd" -w 5 -t "$dusky_table" -I "$dusky_parent" 1 -j "$dusky_chain"
                fi
            fi
            if [ "$dusky_action" != start ]; then
                "$dusky_cmd" -w 5 -t "$dusky_table" -F "$dusky_chain"
                "$dusky_cmd" -w 5 -t "$dusky_table" -X "$dusky_chain"
            fi
        fi
    }
    dusky_action=$1
    case "$dusky_action" in
        start|stop|flush-all)
            dusky_jump iptables nat PREROUTING dusky-dnat
            dusky_jump iptables nat POSTROUTING dusky-waydroid
            dusky_jump iptables filter DOCKER-USER dusky-docker
            dusky_jump ip6tables filter DOCKER-USER dusky-docker
            ;;
    esac
) || exit $?
# END DUSKY HOOKS
'''


def interface(name):
    if not re.fullmatch(r'[A-Za-z0-9_.:+-]{1,15}', name):
        raise ValueError(f'Invalid interface name: {name!r}')
    return name


def ports(values):
    selected = set()
    for value in values:
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ValueError(f'Invalid SSH port: {value!r}')
        selected.add(int(value))
    return sorted(selected)


def ssh_ports(override):
    if override is not None:
        return ports(override.split(','))
    result = run('systemctl', 'show', 'sshd.socket', '--all',
                 '--property=ActiveState,UnitFileState,Listen')
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if properties.get('ActiveState') == 'active' or properties.get('UnitFileState') in {'enabled', 'enabled-runtime', 'linked', 'linked-runtime'}:
        # Manager properties include effective drop-ins and ListenStream resets.
        listeners = re.findall(r'(\S+) \(Stream\)', properties.get('Listen', ''))
        selected = []
        for address in listeners:
            if address.startswith(('/', '@')):
                continue  # Unix sockets do not need TCP firewall rules.
            value = address.rsplit(':', 1)[-1]
            selected.append(value)
        return ports(selected)
    if shutil.which('sshd'):
        result = run('sshd', '-T', check=False)
        if result.returncode == 0:
            selected = re.findall(r'^port\s+([0-9]+)\s*$', result.stdout, re.MULTILINE | re.IGNORECASE)
            if selected:
                return ports(selected)
        log(f'[WARN] sshd -T failed: {result.stderr.strip() or "no ports reported"}')
    log('[WARN] Using SSH 22/tcp for the subsequent OpenSSH setup; override with --ssh-ports.')
    return [22]


def validate_table(binary, body):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8') as file:
        file.write(body + '\n')
        file.flush()
        result = run(binary, '--test', '--noflush', file.name)
    if re.search(r'Extension \S+ revision .*not supported', result.stderr):
        raise RuntimeError(result.stderr.strip())


def main():
    parser = argparse.ArgumentParser(prog=Path(__file__).name, description='Provision UFW defaults and discovered integrations; existing user rules remain.')
    parser.add_argument('--wan-interface', help='Egress interface; default: lowest-metric IPv4 default route')
    parser.add_argument('--container-interface', default='waydroid0', help='Interface whose IPv4 subnets need NAT (default: waydroid0)')
    parser.add_argument('--trusted-interfaces', default='tailscale0,waydroid0,virbr0,docker0,wg0,tun0,tap0',
                        help='Comma-separated interfaces; only present interfaces receive rules')
    parser.add_argument('--ssh-ports', help='Comma-separated TCP ports; default: effective socket/sshd configuration')
    parser.add_argument('--docker-guard', choices=('auto', 'on', 'off'), default='auto',
                        help='auto requires an existing Docker iptables forwarding hook; on requires it; off removes the owned guard')
    args = parser.parse_args()
    if os.geteuid() != 0:
        os.execvp('sudo', ['sudo', '--', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    # Validate explicit inputs before package/configuration changes.
    interface(args.container_interface)
    trusted = [interface(name.strip()) for name in args.trusted_interfaces.split(',') if name.strip()]
    if args.wan_interface:
        interface(args.wan_interface)
    if args.ssh_ports is not None:
        ports(args.ssh_ports.split(','))
    if run('pacman', '-Q', 'ufw', check=False).returncode:
        run('pacman', '-S', '--noconfirm', '--needed', 'ufw', timeout=None)
    for name in ('ufw', 'ip', 'iptables', 'iptables-restore', 'sysctl', 'systemctl'):
        if not shutil.which(name):
            raise RuntimeError(f'Required command missing: {name}')

    links = json.loads(run('ip', '-j', 'link', 'show').stdout)
    interfaces = {link['ifname'] for link in links}
    defaults = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default').stdout)
    routes = sorted((route for route in defaults if route.get('dev')), key=lambda route: route.get('metric', 0))
    wan = args.wan_interface or (routes[0]['dev'] if routes else '')
    if wan:
        interface(wan)
        if wan not in interfaces:
            raise ValueError(f'Egress interface is not present: {wan}')
        log(f'[INFO] Egress interface: {wan}')
    else:
        log('[WARN] No IPv4 default route: egress-dependent NAT/guard/route rules skipped.')
    ssh = ssh_ports(args.ssh_ports)
    ipv6 = Path('/proc/sys/net/ipv6').exists()
    families = [('iptables', 'iptables-restore', RULES / 'after.rules')]
    if ipv6:
        families.append(('ip6tables', 'ip6tables-restore', RULES / 'after6.rules'))
    docker_families = set()
    if args.docker_guard != 'off' and wan:
        for binary, _, _ in families:
            if (run(binary, '-w', '5', '-S', 'DOCKER-USER', check=False).returncode == 0 and
                    run(binary, '-w', '5', '-C', 'FORWARD', '-j', 'DOCKER-USER', check=False).returncode == 0):
                docker_families.add(binary)
    if args.docker_guard == 'on' and 'iptables' not in docker_families:
        raise RuntimeError('Docker guard requires an egress interface and Docker iptables DOCKER-USER/FORWARD hook.')
    log('[INFO] Docker guard: ' + (', '.join(sorted(docker_families)) if docker_families else 'skipped (off or no Docker iptables hook)'))

    config = DEFAULT.read_text()
    # Respect the installed UFW sysctl file override.
    match = re.search(r'^\s*IPT_SYSCTL=["\']?([^"\'\n#]+)', config, re.MULTILINE)
    sysctl_file = Path(match[1].strip()) if match else RULES / 'sysctl.conf'
    config = setting(config, 'IPV6', 'yes' if ipv6 else 'no')
    updates = {DEFAULT: setting(config, 'MANAGE_BUILTINS', 'no')}
    keys = ['net/ipv4/ip_forward']
    if ipv6:
        keys += ['net/ipv6/conf/default/forwarding', 'net/ipv6/conf/all/forwarding']
    content = sysctl_file.read_text() if sysctl_file.exists() else ''
    for key in keys:
        content = setting(content, key, '1')
    updates[sysctl_file] = content
    before = (RULES / 'before.rules').read_text()
    # Adopt ONLY the exact legacy block emitted by the previous script.
    legacy_nat = ('*nat\n:POSTROUTING ACCEPT [0:0]\n# Waydroid NAT Integration\n'
                  '-A POSTROUTING -s 192.168.240.0/24 -j MASQUERADE\n'
                  '-A POSTROUTING -s 192.168.250.0/24 -j MASQUERADE\nCOMMIT\n')
    migrated_nat = legacy_nat in before
    before = before.replace(legacy_nat, '')
    if '# Waydroid NAT Integration' in before:
        log('[WARN] Modified legacy Waydroid block retained; inspect its manual NAT rules separately.')
    networks = set()
    if args.container_interface in interfaces and wan:
        addresses = json.loads(run('ip', '-j', '-4', 'address', 'show', 'dev', args.container_interface).stdout)
        networks = {str(ipaddress.ip_interface(f"{address['local']}/{address['prefixlen']}").network)
                    for link in addresses for address in link.get('addr_info', []) if address.get('family') == 'inet'}
    nat = '*nat\n:dusky-waydroid - [0:0]\n'
    nat += ''.join(f'-A dusky-waydroid -s {network} -o {wan} -j MASQUERADE\n' for network in sorted(networks))
    nat += 'COMMIT\n'
    validate_table('iptables-restore', nat)
    updates[RULES / 'before.rules'] = block(before, 'WAYDROID', nat)
    log('[INFO] Container NAT: ' + (', '.join(sorted(networks)) if networks else 'no discovered subnet; no masquerade rules'))

    legacy_rules = []
    # Remove legacy file blocks in both families, even when IPv6 is disabled.
    for binary, restore, path in [('iptables', 'iptables-restore', RULES / 'after.rules'),
                                   ('ip6tables', 'ip6tables-restore', RULES / 'after6.rules')]:
        if not path.exists():
            continue
        content = path.read_text()
        pattern = r'^# BEGIN DOCKER-USER MITIGATION\n(.*?)^# END DOCKER-USER MITIGATION\n?'
        for old in re.findall(pattern, content, re.MULTILINE | re.DOTALL):
            legacy_rules += [(binary, shlex.split(line)[1:]) for line in old.splitlines() if line.startswith('-A DOCKER-USER ')]
        content = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)
        body = '*filter\n:dusky-docker - [0:0]\n'
        if binary in docker_families:
            body += '-A dusky-docker -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN\n'
            body += f'-A dusky-docker -i {wan} -j DROP\n'
        body += 'COMMIT\n'
        if binary == 'iptables' or ipv6:
            validate_table(restore, body)
        updates[path] = block(content, 'DOCKER', body)
    init = RULES / 'after.init'
    updates[init] = hook(init.read_text() if init.exists() else '#!/bin/sh\n')
    with tempfile.NamedTemporaryFile('w') as file:
        file.write(updates[init]); file.flush()
        run('sh', '-n', file.name)

    originals = {path: (path.read_text(), path.stat().st_mode) if path.exists() else None for path in updates}
    runtime_keys = {key: run('sysctl', '-n', key).stdout.strip() for key in keys}
    was_active = 'Status: active' in run('ufw', 'status').stdout
    try:
        for path, content in updates.items():
            atomic_write(path, content, 0o755 if path == init else 0o644)
            if path == init:
                path.chmod(path.stat().st_mode | 0o100)
        run('sysctl', '-w', *[f'{key}=1' for key in keys])
        # CLI policies also update the running firewall; never edit only a file.
        for policy, direction in [('deny', 'incoming'), ('allow', 'outgoing'), ('deny', 'routed')]:
            run('ufw', 'default', policy, direction)
        for port in ssh:
            run('ufw', 'allow', f'{port}/tcp', 'comment', 'OpenSSH')
        run('ufw', 'allow', '41641/udp', 'comment', 'Tailscale Direct P2P')
        for name in trusted:
            if name not in interfaces:
                continue
            run('ufw', 'allow', 'in', 'on', name, 'comment', f'Trust IN: {name}')
            if wan:
                run('ufw', 'route', 'allow', 'in', 'on', name, 'out', 'on', wan, 'comment', f'Forward: {name} -> WAN')
            if name == 'virbr0':
                run('ufw', 'route', 'allow', 'in', 'on', name, 'comment', f'Route IN: {name}')
                run('ufw', 'route', 'allow', 'out', 'on', name, 'comment', f'Route OUT: {name}')
        run('ufw', '--force', 'enable')
        if 'Status: active' not in run('ufw', 'status').stdout:
            raise RuntimeError('UFW did not become active')
        for key in keys:
            if run('sysctl', '-n', key).stdout.strip() != '1':
                raise RuntimeError(f'Forwarding verification failed: {key}')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, original[0])
                path.chmod(stat.S_IMODE(original[1]))
        run('sysctl', '-w', *[f'{key}={value}' for key, value in runtime_keys.items()], check=False)
        recovery = run('ufw', 'reload' if was_active else 'disable', check=False)
        log('[WARN] Framework files restored; earlier CLI rule changes may remain.' +
            (f' Recovery failed: {recovery.stderr.strip() or recovery.stdout.strip()}' if recovery.returncode else ''))
        raise

    # --noflush retains legacy builtin NAT rules and undeclared Docker chains.
    # Clean only rule signatures belonging to identified legacy file blocks.
    if migrated_nat:
        legacy_rules += [('iptables', ['POSTROUTING', '-s', subnet, '-j', 'MASQUERADE'])
                         for subnet in ('192.168.240.0/24', '192.168.250.0/24')]
    for binary, rule in legacy_rules:
        if binary == 'ip6tables' and not ipv6:
            continue
        table = 'nat' if rule[0] == 'POSTROUTING' else 'filter'
        while run(binary, '-w', '5', '-t', table, '-C', *rule, check=False).returncode == 0:
            run(binary, '-w', '5', '-t', table, '-D', *rule)
    if networks and run('iptables', '-w', '5', '-t', 'nat', '-C', 'POSTROUTING', '-j', 'dusky-waydroid', check=False).returncode:
        raise RuntimeError('Container NAT hook was not installed')
    for binary in docker_families:
        run(binary, '-w', '5', '-C', 'FORWARD', '-j', 'DOCKER-USER')
        run(binary, '-w', '5', '-C', 'DOCKER-USER', '-j', 'dusky-docker')
    run('systemctl', 'enable', 'ufw.service')
    log('[OK] UFW active; deny incoming/routed, allow outgoing. Existing user rules retained.')
    log(f'[OK] Forwarding verified; SSH TCP ports: {", ".join(map(str, ssh)) or "none (Unix sockets only)"}.')
    if migrated_nat or legacy_rules:
        log('[OK] Migrated identifiable legacy setup rules to TUI-compatible owned chains.')
    log(run('ufw', 'status', 'verbose').stdout.strip())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        log(f'[ERROR] {error}')
        sys.exit(1)
