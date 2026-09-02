#!/usr/bin/env python3
import hashlib, os, pwd, grp, shutil, socket, struct, subprocess, sys, time
from pathlib import Path

REPORT_FILE = Path(__file__).resolve().parent / "polkit_audit_report.txt"
lines = []

def banner(title):
    sep = "=" * 80
    lines.append(f"\n{sep}\n  {title}\n{sep}\n")
    print(f"\033[1;34m[*] {title}\033[0m")

def subheader(title):
    lines.append(f"\n--- {title} ---")

def record(key, val):
    lines.append(f"{key:<38}: {val}")

def run_cmd(cmd, timeout=4.0):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return res.stdout.strip()
    except subprocess.TimeoutExpired:
        return f"[ERROR: Command '{' '.join(cmd)}' timed out]"
    except FileNotFoundError:
        return f"[ERROR: Command '{cmd[0]}' not found]"
    except Exception as e:
        return f"[ERROR: {e}]"

def hash_file(path):
    if not path.exists() or path.is_dir():
        return "N/A"
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()[:16] + "..."
    except Exception as e:
        return f"Error: {e}"

def extract_bus_path(addr):
    for token in addr.replace(";", ",").split(","):
        if token.startswith("path="):
            return token[5:]
        if token.startswith("unix:path="):
            return token[10:]
    if addr.startswith("/"):
        return addr
    return "/run/dbus/system_bus_socket"

def audit_system_basics():
    banner("1. SYSTEM, KERNEL, AND RUNTIME ENVIRONMENT")
    record("Report Timestamp (UTC)", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    record("Hostname", socket.gethostname())
    try:
        u = pwd.getpwuid(os.getuid())
        record("Current User", f"{u.pw_name} (UID={u.pw_uid}, GID={u.pw_gid})")
    except KeyError:
        record("Current User", f"UID={os.getuid()}, GID={os.getgid()}")
    record("Current Working Dir", os.getcwd())
    record("Kernel (uname -a)", run_cmd(["uname", "-a"]))
    record("Kernel Boot Arguments", run_cmd(["cat", "/proc/cmdline"]))
    record("Python Version", sys.version.replace("\n", " "))
    record("Python Executable", sys.executable)
    record("Process PID / PPID", f"{os.getpid()} / {os.getppid()}")
    record("Process CGroup", run_cmd(["cat", f"/proc/{os.getpid()}/cgroup"]))
    subheader("/etc/os-release")
    lines.append(run_cmd(["cat", "/etc/os-release"]))

def audit_packages_and_binaries():
    banner("2. INSTALLED PACKAGES, BINARIES, AND METADATA")
    packages = ["polkit", "polkit-gnome", "polkit-kde-agent", "hyprpolkitagent", "lxqt-policykit", "systemd", "systemd-libs", "dbus", "dbus-broker", "pam", "glibc", "linux", "foot", "hyprland", "sway", "niri", "river", "wayland"]
    subheader("Package Versions via pacman -Q")
    for pkg in packages:
        out = run_cmd(["pacman", "-Q", pkg])
        record(f"Package {pkg}", out if not out.startswith("[ERROR") else "Not installed")

    subheader("Important Binaries Inspection")
    target_binaries = ["/usr/lib/polkit-1/polkitd", "/usr/lib/polkit-1/polkit-agent-helper-1", "/usr/bin/pkexec", "/usr/bin/pkcheck", "/usr/bin/pkaction", "/usr/bin/busctl", "/usr/bin/loginctl", "/usr/bin/foot", "/usr/bin/dbus-daemon", "/usr/bin/dbus-broker"]
    for b_str in target_binaries:
        bp = Path(b_str)
        if bp.exists():
            st = bp.stat()
            suid = " [SETUID ROOT]" if (st.st_mode & 0o4000) else ""
            owner = f"{st.st_uid}:{st.st_gid}"
            mode = oct(st.st_mode)
            sha = hash_file(bp)
            lines.append(f"  * {b_str:<42} | Mode: {mode}{suid:<15} | Owner: {owner} | SHA256: {sha}")
        else:
            lines.append(f"  * {b_str:<42} | [NOT FOUND]")

def audit_systemd_units():
    banner("3. SYSTEMD UNIT CONFIGURATIONS & STATUS")
    system_units = ["polkit.service", "polkit-agent-helper.socket", "polkit-agent-helper@.service", "dbus.service", "dbus.socket", "dbus-broker.service"]
    for u in system_units:
        subheader(f"System Unit: {u}")
        record(f"Active State ({u})", run_cmd(["systemctl", "is-active", u]))
        record(f"Enabled State ({u})", run_cmd(["systemctl", "is-enabled", u]))
        lines.append("\n[systemctl cat " + u + "]")
        lines.append(run_cmd(["systemctl", "cat", u]))

    subheader("User Manager Units & Targets")
    lines.append(run_cmd(["systemctl", "--user", "list-units", "--type=service,target", "--state=active", "--no-pager", "--no-legend"]))
    subheader("Dusky Polkit User Service Status")
    lines.append(run_cmd(["systemctl", "--user", "status", "dusky_polkit.service", "--no-pager"]))
    lines.append("\n[systemctl --user cat dusky_polkit.service]")
    lines.append(run_cmd(["systemctl", "--user", "cat", "dusky_polkit.service"]))
    lines.append("\n[Journal: dusky_polkit.service (last 30 lines)]")
    lines.append(run_cmd(["journalctl", "--user", "-u", "dusky_polkit.service", "-n", "30", "--no-pager"]))

def audit_polkit_subsystem():
    banner("4. POLKIT SUBSYSTEM, SOCKETS, & RULES")
    sock_path = Path("/run/polkit/agent-helper.socket")
    subheader("Polkit Helper Socket (/run/polkit/agent-helper.socket)")
    if sock_path.exists():
        st = sock_path.stat()
        record("Socket Path", str(sock_path))
        record("Socket Permissions", oct(st.st_mode))
        try:
            owner_name = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner_name = str(st.st_uid)
        record("Socket Owner", f"UID={st.st_uid} ({owner_name}), GID={st.st_gid}")
        record("Is UNIX Socket", sock_path.is_socket())
    else:
        record("Socket Path", "DOES NOT EXIST")

    subheader("Polkit Configuration & Rules Files")
    rule_dirs = [Path("/etc/polkit-1/rules.d"), Path("/usr/share/polkit-1/rules.d")]
    for rdir in rule_dirs:
        if rdir.exists():
            lines.append(f"Directory: {rdir}")
            for rf in sorted(rdir.glob("*.rules")):
                lines.append(f"  - {rf.name} ({rf.stat().st_size} bytes)")
        else:
            lines.append(f"Directory: {rdir} [DOES NOT EXIST]")

    polkit_conf = Path("/etc/polkit-1/polkitd.conf")
    if polkit_conf.exists():
        subheader("/etc/polkit-1/polkitd.conf")
        try:
            lines.append(polkit_conf.read_text())
        except Exception as e:
            lines.append(f"[Error reading polkitd.conf: {e}]")

def audit_pam_stack():
    banner("5. PAM (PLUGGABLE AUTHENTICATION MODULES) CONFIGURATION")
    pam_files = [Path("/etc/pam.d/polkit-1"), Path("/etc/pam.d/system-auth"), Path("/etc/pam.d/system-login"), Path("/etc/pam.d/other"), Path("/etc/pam.d/sudo")]
    for pf in pam_files:
        subheader(f"PAM Config: {pf}")
        if pf.exists():
            try:
                lines.append(pf.read_text().strip())
            except Exception as e:
                lines.append(f"[Error reading {pf}: {e}]")
        else:
            lines.append(f"[{pf} not found]")

def audit_logind_sessions():
    banner("6. SYSTEMD-LOGIND SESSIONS & PATH DECODING")
    subheader("loginctl list-sessions")
    lines.append(run_cmd(["loginctl", "list-sessions", "--no-legend"]))
    subheader("loginctl list-users")
    lines.append(run_cmd(["loginctl", "list-users", "--no-legend"]))
    subheader("loginctl list-seats")
    lines.append(run_cmd(["loginctl", "list-seats", "--no-legend"]))

    subheader("Active Session Detailed Properties")
    out = run_cmd(["loginctl", "list-sessions", "--no-legend"])
    for row in out.splitlines():
        parts = row.split()
        if len(parts) > 0:
            sid = parts[0]
            lines.append(f"\n>>> loginctl show-session {sid} <<<")
            lines.append(run_cmd(["loginctl", "show-session", sid]))

    subheader("D-Bus Object Tree under org.freedesktop.login1")
    tree = run_cmd(["busctl", "tree", "org.freedesktop.login1"])
    session_paths = []
    for l in tree.splitlines():
        if "/org/freedesktop/login1/session/" in l:
            parts = l.strip().split()
            if len(parts) > 0:
                session_paths.append(parts[-1])
    
    lines.append("Found session paths:")
    from importlib.machinery import SourceFileLoader
    try:
        dp_mod = SourceFileLoader("dusky_polkit", str(Path(__file__).resolve().parent / "dusky_polkit")).load_module()
        dp_decode = dp_mod.Agent.unescape_component
    except Exception as e:
        dp_decode = lambda c: f"[Error importing: {e}]"

    for sp in session_paths:
        comp = sp.rsplit("/", 1)[-1]
        def standard_decode(c: str) -> str:
            out, i = [], 0
            while i < len(c):
                if c[i] == "_" and i + 3 <= len(c):
                    try:
                        out.append(chr(int(c[i + 1:i + 3], 16)))
                        i += 3
                        continue
                    except ValueError:
                        pass
                out.append(c[i])
                i += 1
            return "".join(out)

        lines.append(f"  * Object Path: {sp}")
        lines.append(f"      Raw component:         {comp}")
        lines.append(f"      Dusky script decoded:  {dp_decode(comp)}")
        lines.append(f"      Standard decoded ID:   {standard_decode(comp)}")
        lines.append(f"      Match status:          {'PASS' if dp_decode(comp) == standard_decode(comp) else 'FAIL'}")

def audit_environment():
    banner("7. PROCESS & SYSTEMD USER ENVIRONMENT")
    target_vars = ["XDG_SESSION_ID", "XDG_SESSION_TYPE", "XDG_SESSION_CLASS", "XDG_SESSION_DESKTOP", "XDG_CURRENT_DESKTOP", "XDG_RUNTIME_DIR", "XDG_SEAT", "XDG_VTNR", "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SYSTEM_BUS_ADDRESS", "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE", "SWAYSOCK", "XCURSOR_THEME", "XCURSOR_SIZE", "LANG"]
    subheader("Process Environment (os.environ)")
    for var in target_vars:
        record(f"Env: {var}", os.environ.get(var, "[UNSET]"))

    subheader("systemd --user Imported Environment")
    sysd_env = run_cmd(["systemctl", "--user", "show-environment"])
    for line in sysd_env.splitlines():
        if any(line.startswith(f"{v}=") for v in target_vars):
            lines.append(f"  {line}")

def audit_terminal():
    banner("8. FOOT TERMINAL CAPABILITIES & FLAGS")
    foot_path = shutil.which("foot")
    record("Foot Binary Location", foot_path or "NOT IN PATH")
    if foot_path:
        record("Foot Version", run_cmd(["foot", "--version"]))
        subheader("Foot CLI Help & Supported Flags")
        lines.append(run_cmd(["foot", "--help"]))

def audit_dbus_and_socket_probe():
    banner("9. D-BUS WIRE & SOCKET PROTOCOL PROBING")
    sys_bus_addr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/run/dbus/system_bus_socket")
    path = extract_bus_path(sys_bus_addr)

    record("System Bus Socket Path", path)
    subheader("Raw SASL Handshake to System Bus")
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(path)
        uid_hex = str(os.getuid()).encode().hex().encode()
        s.sendall(b"\0AUTH EXTERNAL " + uid_hex + b"\r\nBEGIN\r\n")
        resp = s.recv(256)
        record("SASL Handshake Response", repr(resp.decode("utf-8", "replace")))
        s.close()
    except Exception as e:
        record("SASL Handshake Error", str(e))

    subheader("Live Probe of /run/polkit/agent-helper.socket")
    helper_sock = "/run/polkit/agent-helper.socket"
    try:
        hs = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        hs.settimeout(2.0)
        hs.connect(helper_sock)
        record("Helper Socket Connection", "SUCCESS (Socket accepted connection)")
        hs.close()
    except Exception as e:
        record("Helper Socket Connection", f"FAILED ({e})")

    subheader("Polkit Authority D-Bus Introspection")
    lines.append(run_cmd(["busctl", "introspect", "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority"]))

def audit_command_manuals():
    banner("10. COMMAND ARGUMENT MANUALS & USAGE")
    tools = [
        ("pkexec", ["pkexec", "--help"]),
        ("pkcheck", ["pkcheck", "--help"]),
        ("busctl", ["busctl", "--help"]),
        ("loginctl", ["loginctl", "--help"]),
    ]
    for name, cmd in tools:
        subheader(f"Command Usage: {name}")
        lines.append(run_cmd(cmd))

def main():
    print("\033[1;32mStarting Comprehensive Polkit Audit...\033[0m")
    audit_system_basics()
    audit_packages_and_binaries()
    audit_systemd_units()
    audit_polkit_subsystem()
    audit_pam_stack()
    audit_logind_sessions()
    audit_environment()
    audit_terminal()
    audit_dbus_and_socket_probe()
    audit_command_manuals()

    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n\033[1;32m[+] Audit completed successfully!\033[0m")
    print(f"\033[1;33m[+] Report written to: {REPORT_FILE}\033[0m")

if __name__ == "__main__":
    main()
