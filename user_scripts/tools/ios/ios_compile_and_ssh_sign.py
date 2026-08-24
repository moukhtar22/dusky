#!/usr/bin/env python3
"""
Autonomous Multi-Threaded iOS App Cross-Compiler, Ad-Hoc Signer & SSH Deployer
Designed for Arch Linux (Python 3.12+ / 3.14, Hyprland/Wayland) & Jailbroken iOS (Dopamine Rootless / palera1n)

Key Features:
- Auto-dependency installation via pacman / AUR helpers (yay/paru) or building ldid from source.
- Auto-download, verification, and extraction of iPhoneOS.sdk from official theos/sdks mirrors.
- USB auto-detection & iproxy tunnel management (with Wi-Fi SSH fallback).
- Multi-threaded parallel compilation utilizing multi-core CPUs (e.g., Intel i7-12700H 20 worker threads).
- Interactive CLI mode when invoked without arguments.
- Signal handling (SIGINT/SIGTERM) for clean resource teardown on Linux and remote iPhone.
"""

import argparse
import atexit
import concurrent.futures
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import NoReturn, Optional, Tuple


# ==============================================================================
# Color & Formatting System (TTY & NO_COLOR Standard Compliant)
# ==============================================================================
class Color:
    ENABLED: bool = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"

    RESET: str = "\033[0m" if ENABLED else ""
    BOLD: str = "\033[1m" if ENABLED else ""
    DIM: str = "\033[2m" if ENABLED else ""

    RED: str = "\033[31m" if ENABLED else ""
    GREEN: str = "\033[32m" if ENABLED else ""
    YELLOW: str = "\033[33m" if ENABLED else ""
    BLUE: str = "\033[34m" if ENABLED else ""
    MAGENTA: str = "\033[35m" if ENABLED else ""
    CYAN: str = "\033[36m" if ENABLED else ""


def log_info(msg: str) -> None:
    print(f"{Color.BLUE}{Color.BOLD}[*]{Color.RESET} {msg}")


def log_success(msg: str) -> None:
    print(f"{Color.GREEN}{Color.BOLD}[+]{Color.RESET} {msg}")


def log_warn(msg: str) -> None:
    print(f"{Color.YELLOW}{Color.BOLD}[!]{Color.RESET} {msg}")


def log_error(msg: str) -> None:
    print(f"{Color.RED}{Color.BOLD}[-]{Color.RESET} {msg}", file=sys.stderr)


class DeployError(Exception):
    """Custom exception for deployment failures."""
    pass


# ==============================================================================
# Global Cleanup & Lifecycle Manager
# ==============================================================================
class CleanupManager:
    """Ensures temporary local directories, background processes, and remote files are cleaned up."""

    def __init__(self) -> None:
        self.tmp_dirs: list[Path] = []
        self.processes: list[subprocess.Popen] = []
        self.remote_cleanup_target: Optional[Tuple[str, int, str, str]] = None

        atexit.register(self.cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def register_tmp_dir(self, path: Path) -> None:
        self.tmp_dirs.append(path)

    def register_process(self, proc: subprocess.Popen) -> None:
        self.processes.append(proc)

    def set_remote_cleanup(self, host: str, port: int, user: str, remote_path: str) -> None:
        self.remote_cleanup_target = (host, port, user, remote_path)

    def clear_remote_cleanup(self) -> None:
        self.remote_cleanup_target = None

    def _signal_handler(self, signum: int, frame: object) -> NoReturn:
        print(f"\n{Color.YELLOW}[!] Interrupted by signal ({signum}). Cleaning up...{Color.RESET}")
        self.cleanup()
        sys.exit(128 + signum)

    def cleanup(self) -> None:
        # 1. Terminate background processes
        for proc in self.processes:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
        self.processes.clear()

        # 2. Clean up local temporary directories
        for tmp_dir in self.tmp_dirs:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
        self.tmp_dirs.clear()

        # 3. Clean up staged remote file on iPhone
        if self.remote_cleanup_target:
            host, port, user, remote_path = self.remote_cleanup_target
            ssh_cmd = [
                "ssh", "-p", str(port),
                "-o", "ConnectTimeout=3",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                f"{user}@{host}", f"rm -f '{remote_path}'"
            ]
            subprocess.run(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.remote_cleanup_target = None


CLEANUP = CleanupManager()


# ==============================================================================
# Arch Linux Autonomous Package & Dependency Manager
# ==============================================================================
class ArchDependencyManager:
    OFFICIAL_PACKAGES: dict[str, str] = {
        "clang": "clang",
        "ssh": "openssh",
        "scp": "openssh",
        "zip": "zip",
        "git": "git",
        "iproxy": "libimobiledevice",
        "usbmuxd": "usbmuxd",
        "make": "make",
    }

    @staticmethod
    def is_arch_linux() -> bool:
        os_release = Path("/etc/os-release")
        if os_release.exists():
            content = os_release.read_text().lower()
            return any(k in content for k in ("arch", "archarm", "manjaro", "endeavouros"))
        return False

    @staticmethod
    def is_installed(binary: str) -> bool:
        return shutil.which(binary) is not None

    def get_missing_binaries(self, required_binaries: list[str]) -> list[str]:
        return [b for b in required_binaries if not self.is_installed(b)]

    def detect_aur_helper(self) -> Optional[str]:
        if self.is_installed("yay"):
            return "yay"
        elif self.is_installed("paru"):
            return "paru"
        return None

    def install_official_packages(self, missing_binaries: list[str]) -> None:
        pkgs = {self.OFFICIAL_PACKAGES[b] for b in missing_binaries if b in self.OFFICIAL_PACKAGES}
        if not pkgs:
            return

        pkgs_list = list(pkgs)
        log_info(f"Auto-installing missing Arch packages via pacman: {', '.join(pkgs_list)}...")

        cmd = ["pacman", "-S", "--noconfirm", "--needed"] + pkgs_list
        if os.geteuid() != 0:
            cmd = ["sudo"] + cmd

        res = subprocess.run(cmd)
        if res.returncode != 0:
            raise DeployError("Failed to auto-install Arch packages via pacman.")

    def install_aur_package(self, package: str) -> bool:
        helper = self.detect_aur_helper()
        if not helper:
            return False

        log_info(f"Installing AUR package '{package}' via '{helper}'...")
        cmd = [helper, "-S", "--noconfirm", "--needed", package]
        res = subprocess.run(cmd)
        return res.returncode == 0

    def build_ldid_from_source(self) -> None:
        log_warn("No AUR helper found. Building 'ldid' autonomously from source...")
        self.install_official_packages(["git", "make", "clang"])

        with tempfile.TemporaryDirectory(prefix="ldid_build_") as tmpdir:
            tmppath = Path(tmpdir)
            repo_dir = tmppath / "ldid"

            log_info("Cloning ProcursusTeam/ldid repository...")
            clone_cmd = ["git", "clone", "--depth=1", "https://github.com/ProcursusTeam/ldid.git", str(repo_dir)]
            if subprocess.run(clone_cmd).returncode != 0:
                raise DeployError("Failed to clone ldid repository.")

            log_info("Compiling ldid binary via make...")
            if subprocess.run(["make", "-C", str(repo_dir)]).returncode != 0:
                raise DeployError("Failed to compile ldid binary.")

            compiled_bin = repo_dir / "ldid"
            if not compiled_bin.exists():
                raise DeployError("Compiled ldid binary not found.")

            user_bin_dir = Path.home() / ".local" / "bin"
            user_bin_dir.mkdir(parents=True, exist_ok=True)
            target_path = user_bin_dir / "ldid"

            shutil.copy(compiled_bin, target_path)
            target_path.chmod(0o755)

            if str(user_bin_dir) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{user_bin_dir}:{os.environ.get('PATH', '')}"

            log_success(f"'ldid' compiled and installed to: {target_path}")

    def ensure_dependencies(self, required_binaries: list[str]) -> None:
        missing = self.get_missing_binaries(required_binaries)
        if not missing:
            return

        log_warn(f"Missing required system dependencies: {', '.join(missing)}")

        if not self.is_arch_linux():
            raise DeployError(
                f"Missing dependencies ({', '.join(missing)}) on non-Arch system.\n"
                f"Please install them using your Linux package manager."
            )

        official_missing = [b for b in missing if b in self.OFFICIAL_PACKAGES]
        if official_missing:
            self.install_official_packages(official_missing)

        if "ldid" in missing:
            if not self.install_aur_package("ldid"):
                self.build_ldid_from_source()

        still_missing = self.get_missing_binaries(required_binaries)
        if still_missing:
            raise DeployError(f"Could not automatically resolve dependencies: {', '.join(still_missing)}")

        log_success("All Arch Linux system dependencies resolved.")


# ==============================================================================
# Automatic iOS SDK Downloader & Cache Manager
# ==============================================================================
class SDKManager:
    CACHE_DIR: Path = Path.home() / ".cache" / "ios_deploy" / "sdks"
    DEFAULT_SDK_NAME: str = "iPhoneOS16.5.sdk"
    DEFAULT_ARCHIVE_NAME: str = f"{DEFAULT_SDK_NAME}.tar.xz"

    SDK_URLS: list[str] = [
        f"https://github.com/theos/sdks/releases/download/master-146e41f/{DEFAULT_ARCHIVE_NAME}",
        f"https://raw.githubusercontent.com/theos/sdks/master/{DEFAULT_ARCHIVE_NAME}",
    ]

    REQUIRED_HEADERS: list[str] = [
        "System/Library/Frameworks/UIKit.framework/Headers/UIKit.h",
        "System/Library/Frameworks/Foundation.framework/Headers/Foundation.h",
        "System/Library/Frameworks/CoreGraphics.framework/Headers/CoreGraphics.h",
    ]

    @classmethod
    def verify_sdk_integrity(cls, sdk_dir: Path) -> bool:
        if not sdk_dir.exists() or not sdk_dir.is_dir():
            return False
        for header_rel_path in cls.REQUIRED_HEADERS:
            if not (sdk_dir / header_rel_path).exists():
                return False
        return True

    @classmethod
    def download_with_progress(cls, url: str, dest_path: Path) -> bool:
        log_info(f"Auto-downloading iOS SDK from GitHub mirror:\n    {url}")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; ArchLinux x86_64) iOSDeploy"},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                block_size = 8192
                downloaded = 0

                with open(dest_path, "wb") as f:
                    while True:
                        chunk = response.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0 and sys.stdout.isatty():
                            percent = (downloaded / total_size) * 100
                            mb_dl = downloaded / (1024 * 1024)
                            mb_total = total_size / (1024 * 1024)
                            sys.stdout.write(
                                f"\r{Color.GREEN}[+]{Color.RESET} Download Progress: [{mb_dl:.1f} / {mb_total:.1f} MB] ({percent:.1f}%)"
                            )
                            sys.stdout.flush()

                if sys.stdout.isatty():
                    print()
                return True
        except Exception as e:
            log_warn(f"Download mirror failed ({url}): {e}")
            if dest_path.exists():
                dest_path.unlink()
            return False

    @classmethod
    def ensure_sdk(cls, custom_sdk_path: Optional[str] = None) -> Path:
        if custom_sdk_path:
            user_path = Path(custom_sdk_path).expanduser().resolve()
            if cls.verify_sdk_integrity(user_path):
                log_success(f"Using validated custom iOS SDK: {user_path}")
                return user_path
            else:
                raise DeployError(f"Custom SDK path at '{user_path}' is missing essential framework headers.")

        cls.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target_sdk_dir = cls.CACHE_DIR / cls.DEFAULT_SDK_NAME

        if cls.verify_sdk_integrity(target_sdk_dir):
            log_success(f"Cached iOS SDK verified: {target_sdk_dir}")
            return target_sdk_dir

        log_warn(f"No cached {cls.DEFAULT_SDK_NAME} found. Initiating autonomous download...")
        archive_path = cls.CACHE_DIR / cls.DEFAULT_ARCHIVE_NAME

        download_success = False
        for url in cls.SDK_URLS:
            if cls.download_with_progress(url, archive_path):
                download_success = True
                break

        if not download_success:
            raise DeployError("Failed to auto-download iPhoneOS.sdk from all mirrors. Check internet connection.")

        log_info(f"Extracting {archive_path.name}...")
        try:
            with tarfile.open(archive_path, "r:xz") as tar:
                tar.extractall(path=cls.CACHE_DIR)
        except Exception as e:
            archive_path.unlink(missing_ok=True)
            raise DeployError(f"Failed to extract SDK archive: {e}")

        archive_path.unlink(missing_ok=True)

        if not cls.verify_sdk_integrity(target_sdk_dir):
            shutil.rmtree(target_sdk_dir, ignore_errors=True)
            raise DeployError("Extracted iOS SDK failed integrity verification.")

        log_success(f"iOS SDK ready: {target_sdk_dir}")
        return target_sdk_dir


# ==============================================================================
# USB Auto-Detection & iproxy Tunnel Manager
# ==============================================================================
class USBTunnelManager:
    def __init__(
        self,
        wifi_host: str = "localhost",
        usb_local_port: int = 2222,
        device_ssh_port: int = 44,
        ssh_user: str = "root",
    ) -> None:
        self.wifi_host = wifi_host
        self.usb_local_port = usb_local_port
        self.device_ssh_port = device_ssh_port
        self.ssh_user = ssh_user
        self.iproxy_proc: Optional[subprocess.Popen] = None

    @staticmethod
    def is_usb_iphone_connected() -> bool:
        if shutil.which("idevice_id"):
            try:
                res = subprocess.run(["idevice_id", "-l"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    return True
            except Exception:
                pass

        if shutil.which("lsusb"):
            try:
                res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and ("05ac:" in res.stdout.lower() or "apple" in res.stdout.lower()):
                    return True
            except Exception:
                pass

        return False

    def is_port_listening(self, port: int, timeout: float = 0.5) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def start_iproxy(self) -> bool:
        if self.is_port_listening(self.usb_local_port):
            log_info(f"iproxy tunnel already listening on 127.0.0.1:{self.usb_local_port}")
            return True

        if not shutil.which("iproxy"):
            return False

        try:
            log_info(f"Spawning USB tunnel: iproxy {self.usb_local_port} {self.device_ssh_port}...")
            self.iproxy_proc = subprocess.Popen(
                ["iproxy", str(self.usb_local_port), str(self.device_ssh_port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            CLEANUP.register_process(self.iproxy_proc)

            for _ in range(10):
                if self.is_port_listening(self.usb_local_port):
                    log_success(f"iproxy tunnel bound on 127.0.0.1:{self.usb_local_port}")
                    return True
                time.sleep(0.2)
        except Exception as e:
            log_warn(f"Failed to spawn iproxy: {e}")

        return False

    def test_ssh(self, host: str, port: int, timeout: int = 3) -> bool:
        cmd = [
            "ssh", "-p", str(port),
            "-o", f"ConnectTimeout={timeout}",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{self.ssh_user}@{host}",
            "echo connection_ok",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
            return res.returncode == 0 and "connection_ok" in res.stdout
        except Exception:
            return False

    def resolve_ssh_endpoint(self) -> Tuple[str, int]:
        log_info("Resolving iPhone SSH connection endpoint...")

        if self.is_usb_iphone_connected():
            log_success("iPhone detected via USB.")
            if self.start_iproxy():
                if self.test_ssh("127.0.0.1", self.usb_local_port):
                    log_success("Verified SSH over USB tunnel (127.0.0.1:2222).")
                    return "127.0.0.1", self.usb_local_port

        log_info(f"Attempting SSH connection over Wi-Fi ({self.wifi_host}:{self.device_ssh_port})...")
        if self.test_ssh(self.wifi_host, self.device_ssh_port):
            log_success(f"Verified SSH over Wi-Fi ({self.wifi_host}:{self.device_ssh_port}).")
            return self.wifi_host, self.device_ssh_port

        raise DeployError(
            f"Could not connect to iPhone via USB tunnel or Wi-Fi SSH ({self.wifi_host}).\n"
            f"Ensure SSH is active on iPhone and SSH keys or passwordless auth is set up."
        )


# ==============================================================================
# Multi-Threaded Parallel Compiler Engine (Intel i7-12700H Optimized)
# ==============================================================================
def _compile_single_file(args_tuple: tuple) -> tuple[Path, float]:
    src_file, obj_file, sdk_path, target_ios, extra_flags = args_tuple
    start_time = time.perf_counter()

    cmd = [
        "clang",
        "-target", f"arm64-apple-ios{target_ios}",
        "-isysroot", str(sdk_path),
        "-fobjc-arc",
        "-O2",
        "-c", str(src_file),
        "-o", str(obj_file),
    ] + extra_flags

    res = subprocess.run(cmd, capture_output=True, text=True)
    duration = time.perf_counter() - start_time

    if res.returncode != 0:
        raise DeployError(f"Compilation error in '{src_file.name}':\n{res.stderr}")

    return obj_file, duration


class IOSDeployer:
    SUPPORTED_EXTENSIONS = {".m", ".c", ".mm", ".cpp", ".cc"}

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output_ipa: Path = Path(args.output).expanduser().resolve()
        self.app_name: str = args.app_name or self.output_ipa.stem
        self.bundle_id: str = args.bundle_id or f"com.vibe.{self.app_name.lower()}"
        self.target_ios: str = args.target_ios
        self.max_workers: int = args.jobs or (os.cpu_count() or 4)
        self.sdk_path: Path = Path()

    def collect_sources(self, inputs: list[str]) -> list[Path]:
        sources: list[Path] = []
        for inp in inputs:
            p = Path(inp).expanduser().resolve()
            if p.is_file() and p.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                sources.append(p)
            elif p.is_dir():
                for root, _, files in os.walk(p):
                    for file in files:
                        fp = Path(root) / file
                        if fp.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                            sources.append(fp)

        unique_sources: list[Path] = []
        seen = set()
        for s in sources:
            if s not in seen:
                seen.add(s)
                unique_sources.append(s)

        if not unique_sources:
            raise DeployError("No C/Objective-C/C++ source files (.m, .c, .mm) found.")

        return unique_sources

    def generate_info_plist(self, dest_path: Path) -> None:
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>{self.app_name}</string>
    <key>CFBundleIdentifier</key><string>{self.bundle_id}</string>
    <key>CFBundleName</key><string>{self.app_name}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSRequiresIPhoneOS</key><true/>
    <key>MinimumOSVersion</key><string>{self.target_ios}</string>
</dict>
</plist>"""
        dest_path.write_text(plist, encoding="utf-8")

    def generate_entitlements(self, dest_path: Path) -> None:
        system_entitlements = ""
        if self.args.system_app:
            system_entitlements = """
    <key>com.apple.private.security.no-container</key><true/>
    <key>platform-application</key><true/>"""

        entitlements = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>get-task-allow</key><true/>
    <key>application-identifier</key><string>{self.bundle_id}</string>{system_entitlements}
</dict>
</plist>"""
        dest_path.write_text(entitlements, encoding="utf-8")

    def compile_parallel(self, sources: list[Path], obj_dir: Path) -> list[Path]:
        start_time = time.perf_counter()
        log_info(f"Stage 1: Multi-threaded compilation ({len(sources)} files across {self.max_workers} threads)...")

        tasks = [
            (src, obj_dir / f"{idx}_{src.stem}.o", self.sdk_path, self.target_ios, [])
            for idx, src in enumerate(sources)
        ]

        object_files: list[Path] = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(_compile_single_file, task): task[0] for task in tasks}

            for future in concurrent.futures.as_completed(futures):
                src_file = futures[future]
                try:
                    obj_file, duration = future.result()
                    object_files.append(obj_file)
                    log_info(f"  [Compiled] {src_file.name} ({duration:.3f}s)")
                except Exception as exc:
                    raise DeployError(f"Build error in {src_file.name}: {exc}")

        total_time = time.perf_counter() - start_time
        log_success(f"Stage 1 Complete: Compiled {len(object_files)} files in {total_time:.3f}s.")
        return object_files

    def link_binary(self, object_files: list[Path], output_binary: Path) -> None:
        start_time = time.perf_counter()
        log_info(f"Stage 2: Linking {len(object_files)} object files into Mach-O executable...")

        cmd: list[str] = [
            "clang",
            "-target", f"arm64-apple-ios{self.target_ios}",
            "-isysroot", str(self.sdk_path),
            "-O2",
            "-framework", "UIKit",
            "-framework", "Foundation",
            "-framework", "CoreGraphics",
        ]

        for fw in self.args.frameworks:
            cmd.extend(["-framework", fw])

        for obj in object_files:
            cmd.append(str(obj))

        cmd.extend(["-o", str(output_binary)])

        res = subprocess.run(cmd, capture_output=True, text=True)
        duration = time.perf_counter() - start_time

        if res.returncode != 0:
            raise DeployError(f"Linking failed:\n{res.stderr}")

        log_success(f"Stage 2 Complete: Executable linked in {duration:.3f}s.")

    def sign_binary(self, binary_path: Path, entitlements_path: Path) -> None:
        log_info("Applying ad-hoc code signature via ldid...")
        cmd = ["ldid", f"-S{entitlements_path}", str(binary_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise DeployError(f"ldid signing failed:\n{res.stderr}")
        log_success("Binary ad-hoc signed.")

    def create_ipa(self, sources: list[Path]) -> Path:
        total_start = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="ios_build_") as tmpdir:
            tmp_path = Path(tmpdir)
            CLEANUP.register_tmp_dir(tmp_path)

            obj_dir = tmp_path / "obj"
            obj_dir.mkdir(parents=True, exist_ok=True)

            payload_dir = tmp_path / "Payload"
            app_dir = payload_dir / f"{self.app_name}.app"
            app_dir.mkdir(parents=True, exist_ok=True)

            # 1. Parallel compile
            object_files = self.compile_parallel(sources, obj_dir)

            # 2. Link executable
            binary_path = app_dir / self.app_name
            self.link_binary(object_files, binary_path)

            # 3. Setup Info.plist
            info_plist = app_dir / "Info.plist"
            if self.args.info_plist:
                shutil.copy(Path(self.args.info_plist).expanduser(), info_plist)
            else:
                self.generate_info_plist(info_plist)

            # 4. Setup Entitlements & Sign
            entitlements_file = tmp_path / "entitlements.plist"
            if self.args.entitlements:
                shutil.copy(Path(self.args.entitlements).expanduser(), entitlements_file)
            else:
                self.generate_entitlements(entitlements_file)

            self.sign_binary(binary_path, entitlements_file)

            # 5. Copy extra resources
            if self.args.resources:
                for res in self.args.resources:
                    res_path = Path(res).expanduser().resolve()
                    if res_path.is_dir():
                        shutil.copytree(res_path, app_dir / res_path.name, dirs_exist_ok=True)
                    elif res_path.is_file():
                        shutil.copy(res_path, app_dir / res_path.name)

            # 6. Compress into .ipa
            self.output_ipa.parent.mkdir(parents=True, exist_ok=True)
            log_info(f"Packaging IPA to {self.output_ipa}...")

            with zipfile.ZipFile(self.output_ipa, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(tmp_path):
                    for file in files:
                        file_path = Path(root) / file
                        if "obj" in file_path.parts:
                            continue
                        arcname = file_path.relative_to(tmp_path)
                        zipf.write(file_path, arcname)

            log_success(f"IPA created in {time.perf_counter() - total_start:.3f}s: {self.output_ipa}")
            return self.output_ipa

    def deploy_to_iphone(self, ipa_path: Path, host: str, port: int) -> None:
        user = self.args.user
        log_info(f"Deploying to iPhone over SSH ({user}@{host}:{port})...")

        remote_ipa_path = "/var/mobile/tmp_app_deploy.ipa"
        CLEANUP.set_remote_cleanup(host, port, user, remote_ipa_path)

        scp_cmd = [
            "scp", "-P", str(port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            str(ipa_path),
            f"{user}@{host}:{remote_ipa_path}",
        ]

        log_info("Uploading IPA over SSH...")
        if subprocess.run(scp_cmd).returncode != 0:
            raise DeployError("SCP upload failed.")

        remote_script = (
            "APPINST=$(which appinst 2>/dev/null || echo '/var/jb/usr/bin/appinst'); "
            "UICACHE=$(which uicache 2>/dev/null || echo '/var/jb/usr/bin/uicache'); "
            'if [ ! -f "$APPINST" ]; then exit 101; fi; '
            '"$APPINST" "' + remote_ipa_path + '"; '
            "STATUS=$?; "
            'rm -f "' + remote_ipa_path + '"; '
            'if [ $STATUS -eq 0 ]; then "$UICACHE" -a; fi; '
            "exit $STATUS"
        )

        ssh_cmd = [
            "ssh", "-p", str(port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{user}@{host}", remote_script
        ]

        log_info("Executing on-device appinst installation...")
        res = subprocess.run(ssh_cmd)
        CLEANUP.clear_remote_cleanup()

        if res.returncode == 101:
            raise DeployError(
                "appinst is not installed on your iPhone.\n"
                "Open Sileo -> Add Source 'https://cydia.akemi.ai/' -> Install 'AppSync Unified' and 'appinst'."
            )
        elif res.returncode != 0:
            raise DeployError(f"On-device installation failed (exit code {res.returncode}).")

        log_success(f"App '{self.app_name}' successfully installed on your iPhone!")

    def execute(self) -> None:
        arch_mgr = ArchDependencyManager()
        arch_mgr.ensure_dependencies(["clang", "ssh", "scp", "zip", "git", "ldid", "iproxy"])

        if self.args.sign_only:
            target = Path(self.args.sign_only).expanduser().resolve()
            if not target.exists():
                raise DeployError(f"Target path does not exist: {target}")

            entitlements = Path(self.args.entitlements).expanduser().resolve() if self.args.entitlements else None
            with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as tmp_ent:
                tmp_path = Path(tmp_ent.name)
                if entitlements:
                    shutil.copy(entitlements, tmp_path)
                else:
                    self.generate_entitlements(tmp_path)

                binary_to_sign = target / target.stem if target.is_dir() else target
                self.sign_binary(binary_to_sign, tmp_path)
                tmp_path.unlink(missing_ok=True)
            return

        self.sdk_path = SDKManager.ensure_sdk(self.args.sdk if self.args.sdk != "/opt/sdks/iPhoneOS.sdk" else None)

        sources = self.collect_sources(self.args.sources)
        ipa_path = self.create_ipa(sources)

        if not self.args.no_deploy:
            tunnel_mgr = USBTunnelManager(
                wifi_host=self.args.host,
                usb_local_port=self.args.port,
                ssh_user=self.args.user
            )
            active_host, active_port = tunnel_mgr.resolve_ssh_endpoint()
            self.deploy_to_iphone(ipa_path, active_host, active_port)


# ==============================================================================
# Interactive Mode & CLI Interface
# ==============================================================================
def prompt_interactive_menu() -> str:
    print(f"\n{Color.BOLD}{Color.CYAN}=== Autonomous iOS Cross-Compiler & Deployer ==={Color.RESET}")
    print("No build arguments provided. Select an option:\n")
    print(f"  {Color.GREEN}[1]{Color.RESET} Build & Deploy C/Objective-C Project")
    print(f"  {Color.GREEN}[2]{Color.RESET} Setup / Auto-Install Arch Dependencies & iOS SDK")
    print(f"  {Color.GREEN}[3]{Color.RESET} Test USB / Wi-Fi iPhone Connection")
    print(f"  {Color.GREEN}[4]{Color.RESET} Clean Build Artifacts & Cache")
    print(f"  {Color.RED}[q]{Color.RESET} Quit\n")

    try:
        return input(f"{Color.BOLD}Select choice [1-4, q]: {Color.RESET}").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ios_deploy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f"""
{Color.BOLD}{Color.CYAN}Autonomous Multi-Threaded iOS Cross-Compiler & SSH Deployer{Color.RESET}
Builds, ad-hoc signs, and deploys iOS apps to jailbroken devices (Dopamine/palera1n).

{Color.BOLD}Examples:{Color.RESET}
  python ios_deploy.py src/ -o MyApp.ipa -p 2222
  python ios_deploy.py main.m AppDelegate.m --system-app
  python ios_deploy.py --sign-only build/MyApp.app
""",
    )

    parser.add_argument("sources", nargs="*", help="Source file(s) or directory (.m, .c, .mm)")
    parser.add_argument("-s", "--sdk", default="/opt/sdks/iPhoneOS.sdk", help="Path to iPhoneOS.sdk")
    parser.add_argument("-o", "--output", default="MyApp.ipa", help="Output IPA filename")
    parser.add_argument("-j", "--jobs", type=int, help=f"Compiler threads (default: os.cpu_count() = {os.cpu_count()})")
    parser.add_argument("--app-name", help="App display and binary name")
    parser.add_argument("--bundle-id", help="Bundle ID (e.g. com.vibe.myapp)")
    parser.add_argument("--target-ios", default="16.0", help="Target iOS version (default: 16.0)")
    parser.add_argument("--system-app", action="store_true", help="Include unsandboxed root platform entitlements")
    parser.add_argument("-F", "--frameworks", nargs="*", default=[], help="Extra iOS frameworks to link")
    parser.add_argument("-r", "--resources", nargs="*", default=[], help="Extra assets or resource folders")
    parser.add_argument("--info-plist", help="Path to custom Info.plist")
    parser.add_argument("--entitlements", help="Path to custom entitlements.plist")

    parser.add_argument("--host", default="localhost", help="iPhone Wi-Fi host/IP")
    parser.add_argument("-p", "--port", type=int, default=2222, help="SSH port (2222 for iproxy, 22 for Wi-Fi)")
    parser.add_argument("-u", "--user", default="root", help="SSH user (default: root)")
    parser.add_argument("--no-deploy", action="store_true", help="Build IPA locally without SSH deploy")
    parser.add_argument("--sign-only", help="Ad-hoc sign an existing binary or .app bundle")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.sources and not args.sign_only:
        choice = prompt_interactive_menu()
        if choice == "1":
            inp = input("Enter path to source files or directory (e.g. src/ or main.m): ").strip()
            if inp:
                args.sources = [inp]
            else:
                sys.exit(0)
        elif choice == "2":
            ArchDependencyManager().ensure_dependencies(["clang", "ssh", "scp", "zip", "git", "ldid", "iproxy"])
            SDKManager.ensure_sdk()
            sys.exit(0)
        elif choice == "3":
            tunnel = USBTunnelManager()
            host, port = tunnel.resolve_ssh_endpoint()
            log_success(f"Connection test successful: {host}:{port}")
            sys.exit(0)
        elif choice == "4":
            shutil.rmtree(SDKManager.CACHE_DIR, ignore_errors=True)
            log_success("Cleared SDK cache.")
            sys.exit(0)
        else:
            sys.exit(0)

    try:
        deployer = IOSDeployer(args)
        deployer.execute()
    except DeployError as err:
        log_error(str(err))
        sys.exit(1)


if __name__ == "__main__":
    main()