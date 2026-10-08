#!/usr/bin/env python3
"""Update the live ISO installer from GitHub; run before starting installation."""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPOSITORY = "https://github.com/dusklinux/dusky.git"
PAYLOAD_PATH = "user_scripts/arch_iso_scripts/offline_iso"


def fetch_payload(workspace: Path, ref: str) -> tuple[Path, str]:
    repo = workspace / "checkout"
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)

    def git(*args: str, capture: bool = False):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              text=True, capture_output=capture)

    git("remote", "add", "origin", REPOSITORY)
    git("config", "remote.origin.promisor", "true")
    git("config", "remote.origin.partialclonefilter", "blob:none")
    git("sparse-checkout", "set", "--no-cone", f"/{PAYLOAD_PATH}/")
    git("-c", "http.lowSpeedLimit=1024", "-c", "http.lowSpeedTime=60",
        "fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", ref)
    git("checkout", "--quiet", "--detach", "FETCH_HEAD")
    payload = repo / PAYLOAD_PATH
    for required in ("000_dusky_arch_install.sh", "orchestrator.py",
                     "profiles/001_offline.toml", "online/003_network_connect.sh"):
        if not (payload / required).is_file():
            raise RuntimeError(f"Downloaded installer is missing {required}")
    return payload, git("rev-parse", "HEAD", capture=True).stdout.strip()


def apply_payload(payload: Path, destination: Path) -> int:
    """Replace individual files atomically; keep local files absent from Git."""
    count = 0
    for source in sorted(payload.rglob("*")):
        relative = source.relative_to(payload)
        # These belong to this ISO/run, not the remote installer checkout.
        if relative.parts[0] in {".git", ".gitignore", ".installer-update.lock",
                                 "compiled_packages.txt"} or relative.parts[0].startswith("."):
            continue
        target = destination / relative
        if source.is_dir() and not source.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".update-", dir=target.parent)
        os.close(fd)
        temporary = Path(temporary)
        try:
            if source.is_symlink():
                temporary.unlink()
                temporary.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Git branch, tag, or commit (default: remote HEAD)")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root on the live ISO.")
    if not shutil.which("git"):
        parser.error("Git is required; it is included in the Dusky ISO.")
    destination = Path(__file__).resolve().parents[2]
    try:
        with (destination / ".installer-update.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            subprocess.run(["bash", str(destination / "online/003_network_connect.sh")], check=True)
            with tempfile.TemporaryDirectory(prefix="dusky-installer-update-") as workspace:
                payload, revision = fetch_payload(Path(workspace), args.ref)
                count = apply_payload(payload, destination)
            print(f"Updated {count} installer files in {destination} from {revision}.")
            print("Start the installer now. Packages and ISO skeleton files were not updated.")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Installer update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
