#!/usr/bin/env python3
"""
Dusky Backup Manager - Unified Dotfiles Setup & Linker
Architecture: Arch Linux / Python 3.14 Strict Synchronous I/O
Features: Rich-based Interactive Menu & Command-line flags (--new / --relink)
"""

import os
import sys
import json
import shutil
import fnmatch
import argparse
import subprocess
import readline
from pathlib import Path
from dataclasses import dataclass
from typing import Never

# Rich UI components
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme
from rich import box

# Constants
DEFAULT_REPO_NAME = "dusky"
HOME = Path.home()
DOTFILES_DIR = HOME / "dusky"
DOTFILES_LIST = HOME / ".git_dusky_list"
SSH_DIR = HOME / ".ssh"
SSH_KEY_PATH = SSH_DIR / "id_ed25519"
MATUGEN_JSON = HOME / ".config" / "matugen" / "generated" / "dusky_tui.json"
REQUIRED_CMDS = ("git", "ssh", "ssh-keygen", "ssh-agent", "ssh-add", "wl-copy")

def load_matugen_theme() -> Theme:
    """Loads dynamic Matugen UI colors from ~/.config/matugen/generated/dusky_tui.json."""
    if MATUGEN_JSON.is_file():
        try:
            data = json.loads(MATUGEN_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return Theme({
                    "info": f"bold {data.get('accent', '#82d3e2')}",
                    "warning": f"bold {data.get('warning', '#b1cbd0')}",
                    "error": f"bold {data.get('error', '#ffb4ab')}",
                    "success": f"bold {data.get('success', '#bbc5ea')}",
                    "highlight": f"bold {data.get('accent', '#82d3e2')}",
                    "muted": data.get("muted", "#3f484a"),
                })
        except Exception:
            pass
    return Theme({
        "info": "bold cyan",
        "warning": "bold yellow",
        "error": "bold red",
        "success": "bold green",
        "highlight": "bold cyan",
        "muted": "dim white"
    })

console = Console(theme=load_matugen_theme())

def _ask(prompt: str = " ❯ ") -> str:
    """Reads a stripped line, exiting gracefully on Ctrl-D (EOF) instead of crashing."""
    try:
        return input(prompt).strip()
    except EOFError:
        console.print()
        console.print("[warning]⚠ Input stream closed — aborting cleanly.[/warning]")
        kill_ssh_agent()
        sys.exit(0)

ssh_agent_pid: str | None = None

def reconcile_upstream_changes(branch: str, remote_ref: str) -> None:
    """Updates local disk files from upstream for files modified, added, or deleted in upstream PRs

    that were NOT modified locally. Prevents upstream PR changes from being reverted during sync.
    """
    code_mb, mb_out, _ = dotgit("merge-base", branch, remote_ref)
    mb = mb_out.strip()
    if code_mb != 0 or not mb:
        return

    # List files and OIDs in remote_ref
    _, ls_remote, _ = dotgit("ls-tree", "-r", "-z", remote_ref)
    remote_files: dict[str, str] = {}
    for entry in ls_remote.split('\0'):
        if not entry or '\t' not in entry:
            continue
        meta, path = entry.split('\t', 1)
        parts = meta.split()
        if len(parts) >= 3:
            remote_files[path] = parts[2]

    # List files and OIDs in common merge base
    _, ls_base, _ = dotgit("ls-tree", "-r", "-z", mb)
    base_files: dict[str, str] = {}
    for entry in ls_base.split('\0'):
        if not entry or '\t' not in entry:
            continue
        meta, path = entry.split('\t', 1)
        parts = meta.split()
        if len(parts) >= 3:
            base_files[path] = parts[2]

    files_to_checkout: list[str] = []
    files_to_delete: list[str] = []

    # 1. Handle updated or newly added files from remote PRs
    for path, r_oid in remote_files.items():
        b_oid = base_files.get(path, "")
        if r_oid == b_oid:
            continue  # Remote didn't touch this file

        disk_path = HOME / path
        if not (disk_path.exists() or disk_path.is_symlink()):
            if b_oid == "":
                # Brand new file added upstream in PR -> check it out
                files_to_checkout.append(path)
            continue

        code_h, disk_oid, _ = dotgit("hash-object", str(disk_path))
        if code_h == 0 and disk_oid.strip() == b_oid:
            # User never modified this file locally -> update from remote PR
            files_to_checkout.append(path)

    # 2. Handle files deleted upstream in PRs
    for path, b_oid in base_files.items():
        if path not in remote_files:
            disk_path = HOME / path
            if disk_path.exists() or disk_path.is_symlink():
                code_h, disk_oid, _ = dotgit("hash-object", str(disk_path))
                if code_h == 0 and disk_oid.strip() == b_oid:
                    files_to_delete.append(path)

    if files_to_delete:
        console.print(f"[info]Removing {len(files_to_delete)} file(s) deleted in upstream PRs...[/info]")
        for p in files_to_delete:
            dp = HOME / p
            if dp.is_file() or dp.is_symlink():
                dp.unlink(missing_ok=True)
            elif dp.is_dir():
                shutil.rmtree(dp, ignore_errors=True)

    if files_to_checkout:
        console.print(f"[info]Applying {len(files_to_checkout)} upstream update(s) to local files (from merged PRs)...[/info]")
        payload = "\0".join(files_to_checkout) + "\0"
        dotgit("checkout", remote_ref, "--pathspec-from-file=-", "--pathspec-file-nul",
               input_data=payload.encode("utf-8"), literal_pathspecs=True)

@dataclass(frozen=True, kw_only=True, slots=True)
class AppConfig:
    username: str
    email: str
    gh_user: str
    repo: str
    commit_msg: str

    @property
    def repo_url(self) -> str:
        return f"git@github.com:{self.gh_user}/{self.repo}.git"

def run_cmd(
    args: list[str],
    capture: bool = True,
    check: bool = False,
    input_data: bytes | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
) -> tuple[int, str, str]:
    """Runs a system command cleanly inheriting parent environment."""
    kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE if capture else None,
        "stderr": subprocess.PIPE if capture else None,
        "cwd": str(cwd or HOME),
        "env": env if env is not None else os.environ,
    }
    if input_data is not None:
        kwargs["input"] = input_data

    proc = subprocess.run(args, **kwargs)
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, output=stdout, stderr=stderr)

    return proc.returncode, stdout, stderr

def dotgit(
    *args: str,
    input_data: bytes | None = None,
    check: bool = False,
    literal_pathspecs: bool = False,
) -> tuple[int, str, str]:
    """Helper to run git within the bare dotfiles repository context."""
    git_env = os.environ.copy()
    git_env["GIT_DIR"] = str(DOTFILES_DIR)
    git_env["GIT_WORK_TREE"] = str(HOME)
    if literal_pathspecs:
        git_env["GIT_LITERAL_PATHSPECS"] = "1"
    cmd = [
        "git",
        "--no-optional-locks",
        "--no-advice",
        "-c", "core.quotepath=false",
        *args
    ]
    return run_cmd(cmd, input_data=input_data, check=check, env=git_env, cwd=HOME)

def set_remote_origin(url: str) -> None:
    """Safely configures or updates the 'origin' remote and its fetch refspec."""
    code_rem, stdout_rem, _ = dotgit("remote")
    remotes = [r.strip() for r in stdout_rem.splitlines() if r.strip()]
    if "origin" in remotes:
        dotgit("remote", "set-url", "origin", url)
    else:
        code_add, _, _ = dotgit("remote", "add", "origin", url)
        if code_add != 0:
            # If origin partially existed in config without URL
            dotgit("config", "--local", "remote.origin.url", url)
    dotgit("config", "--local", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")

def check_dependencies() -> None:
    """Ensures Python version and all required binaries are present."""
    if sys.version_info < (3, 12):
        console.print(f"[error]✖ Error: Python 3.12+ required (found {sys.version.split()[0]}).[/error]")
        sys.exit(1)

    for cmd in REQUIRED_CMDS:
        if not shutil.which(cmd):
            console.print(f"[error]✖ Error: Missing dependency: '{cmd}' is not installed.[/error]")
            sys.exit(1)

def build_dependency_matrix() -> Table:
    """Constructs the visual verification matrix."""
    table = Table(title="Dependency Matrix Verification", box=box.MINIMAL_DOUBLE_HEAD)
    table.add_column("Binary Tool", style="bold cyan")
    table.add_column("Absolute Path", style="muted")
    table.add_column("Status", justify="center")
    
    for cmd in REQUIRED_CMDS:
        path = shutil.which(cmd) or "Not Found"
        table.add_row(cmd, path, "[success]✔[/success]" if path != "Not Found" else "[error]✖[/error]")
            
    return table

def start_ssh_agent() -> None:
    """Reuses a reachable SSH agent when present; otherwise spawns one."""
    global ssh_agent_pid
    if os.environ.get("SSH_AUTH_SOCK"):
        # rc 0 = agent with identities, rc 1 = agent alive without keys —
        # both mean a working agent is already reachable.
        code, _, _ = run_cmd(["ssh-add", "-l"])
        if code in (0, 1):
            console.print("[info]➔ Using existing SSH agent.[/info]")
            return
    try:
        code, stdout, stderr = run_cmd(["ssh-agent", "-s"])
        if code != 0:
            sock_path = f"/tmp/ssh_agent_{os.getpid()}.sock"
            code, stdout, stderr = run_cmd(["ssh-agent", "-a", sock_path, "-s"], check=True)

        for line in stdout.splitlines():
            if "SSH_AUTH_SOCK=" in line:
                sock = line.split(";")[0].split("=")[1]
                os.environ["SSH_AUTH_SOCK"] = sock
            if "SSH_AGENT_PID=" in line:
                pid = line.split(";")[0].split("=")[1]
                os.environ["SSH_AGENT_PID"] = pid
                ssh_agent_pid = pid
    except subprocess.CalledProcessError as e:
        console.print(f"[error]✖ Error: Failed to start ssh-agent:[/error] {e.stderr.strip()}")
        sys.exit(1)

def kill_ssh_agent() -> None:
    """Terminates the spawned ssh-agent."""
    global ssh_agent_pid
    if ssh_agent_pid:
        run_cmd(["kill", ssh_agent_pid])
        ssh_agent_pid = None
        os.environ.pop("SSH_AGENT_PID", None)
        os.environ.pop("SSH_AUTH_SOCK", None)

def generate_ssh_key(email: str) -> None:
    """Generates an ed25519 SSH key pair at the default path (interactive passphrase prompt)."""
    SSH_DIR.mkdir(parents=True, exist_ok=True)
    SSH_DIR.chmod(0o700)

    if SSH_KEY_PATH.is_file():
        console.print(f"[warning]⚠ Warn: SSH key already exists at {SSH_KEY_PATH}[/warning]")
        console.print("[bold cyan]Do you want to overwrite it? (y/N)[/bold cyan]")
        ans = _ask().lower()
        if ans not in ("y", "yes"):
            console.print("[info]➔ Using existing SSH key.[/info]")
            return
        SSH_KEY_PATH.unlink(missing_ok=True)
        Path(str(SSH_KEY_PATH) + ".pub").unlink(missing_ok=True)

    console.print("[info]Generating new SSH key...[/info]")
    run_cmd(["ssh-keygen", "-t", "ed25519", "-C", email, "-f", str(SSH_KEY_PATH)], capture=False, check=True)
    console.print("[success]✔ SSH key generated successfully.[/success]")

def add_ssh_key_to_agent() -> None:
    """Adds the SSH key to the running agent if not already present."""
    if not SSH_KEY_PATH.is_file():
        return

    # Check if key fingerprint is already loaded in agent
    code_fp, stdout_fp, _ = run_cmd(["ssh-keygen", "-lf", str(SSH_KEY_PATH)])
    if code_fp == 0 and stdout_fp.strip():
        key_fp = stdout_fp.split()[1] if len(stdout_fp.split()) > 1 else ""
        code_list, stdout_list, _ = run_cmd(["ssh-add", "-l"])
        if code_list == 0 and key_fp and key_fp in stdout_list:
            console.print("[info]➔ SSH key is already loaded in agent.[/info]")
            return

    console.print("[info]Adding SSH key to agent...[/info]")
    code, _, _ = run_cmd(["ssh-add", str(SSH_KEY_PATH)], capture=False)
    if code != 0:
        console.print("[warning]⚠ Passphrase prompt retry:[/warning]")
        run_cmd(["ssh-add", str(SSH_KEY_PATH)], capture=False, check=True)

def copy_to_wayland_clipboard(text: str) -> bool:
    """Copies text to the Wayland system clipboard using native wl-copy."""
    wl_copy = shutil.which("wl-copy")
    if not wl_copy:
        return False

    try:
        proc = subprocess.run(
            [wl_copy],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.returncode == 0
    except Exception:
        return False

def setup_github_ssh_linking(email: str) -> None:
    """Handles generating, displaying, and verifying SSH keys on GitHub."""
    generate_ssh_key(email)
    add_ssh_key_to_agent()

    pub_key_file = Path(str(SSH_KEY_PATH) + ".pub")
    if not pub_key_file.is_file():
        console.print(f"[error]✖ Error: Missing public key file at {pub_key_file}[/error]")
        sys.exit(1)

    pub_key_content = pub_key_file.read_text(encoding="utf-8").strip()
    copied = copy_to_wayland_clipboard(pub_key_content)
    clip_msg = "\n\n[success]✔ Public key automatically copied to Wayland clipboard![/success]" if copied else ""

    console.print(Panel(
        f"[warning]ACTION REQUIRED:[/warning] Add this public key to GitHub:\n"
        f"1. Go to: [highlight]https://github.com/settings/keys[/highlight]\n"
        f"2. Click 'New SSH Key', give it a name, and paste the key below:\n\n"
        f"[white]{escape(pub_key_content)}[/white]"
        f"{clip_msg}",
        title="GitHub SSH Key Setup",
        border_style="yellow",
        box=box.ROUNDED
    ))
    console.print("Press [highlight][Enter][/highlight] once you have added the key to GitHub")
    _ask()

    console.print("[info]Verifying GitHub connection via SSH...[/info]")
    code, stdout, stderr = run_cmd(["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", "git@github.com"])
    combined = (stdout + "\n" + stderr).lower()
    
    # GitHub outputs: "Hi <user>! You've successfully authenticated, but GitHub does not provide shell access."
    # ssh exits with returncode 1 on GitHub auth success
    is_success = (
        "successfully authenticated" in combined
        or "authenticated" in combined
        or (code in (0, 1) and "permission denied" not in combined and "could not resolve" not in combined)
    )

    if is_success:
        console.print("[success]✔ GitHub authentication verified successfully.[/success]")
    else:
        err_detail = (stderr or stdout).strip()
        console.print(Panel(
            f"[error]✖ GitHub SSH connection failed.[/error]\n"
            f"[muted]{escape(err_detail)}[/muted]\n\n"
            f"Please verify your SSH key was added to https://github.com/settings/keys",
            title="Authentication Error",
            border_style="red",
            box=box.ROUNDED
        ))
        sys.exit(1)

def is_internal_gitdir(path: str) -> bool:
    """True when path resides within or matches the bare git directory."""
    git_dir_name = DOTFILES_DIR.name
    return path == git_dir_name or path.startswith(f"{git_dir_name}/")

def matches_pathspec(path: str | None, valid_paths: list[str]) -> bool:
    """Evaluates Git-style directory prefixes, exact matches, and globs."""
    if not path or is_internal_gitdir(path):
        return False

    if "__pycache__" in path or path.endswith((".pyc", ".pyo", ".pyd")):
        return False

    for vp in valid_paths:
        vp_clean = vp.rstrip("/")
        if vp_clean == ".":
            return True
        if path == vp_clean or path.startswith(vp_clean + "/"):
            return True
        if fnmatch.fnmatch(path, vp_clean) or fnmatch.fnmatch(path, vp_clean + "/*"):
            return True

    return False

def get_manifest_pathspecs() -> list[str] | None:
    """Reads ~/.git_dusky_list, normalizes paths relative to HOME, and filters safety constraints."""
    if not DOTFILES_LIST.is_file():
        return None

    raw_lines = DOTFILES_LIST.read_text(encoding="utf-8").splitlines()
    valid_paths: list[str] = []

    for line in raw_lines:
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue

        try:
            if clean.startswith("~/"):
                target = HOME / clean[2:]
            elif clean == "~":
                target = HOME
            else:
                p = Path(os.path.expandvars(clean))
                target = p if p.is_absolute() else (HOME / p)

            norm = Path(os.path.normpath(target))
            if norm == HOME:
                if "." not in valid_paths:
                    valid_paths.append(".")
                continue

            if norm.is_relative_to(HOME):
                rel = str(norm.relative_to(HOME))
                # Security constraint: never stage or track the bare repository directory inside itself
                if is_internal_gitdir(rel):
                    console.print(f"[warning]⚠ Ignoring recursive path inside bare repository: {escape(clean)}[/warning]")
                    continue
                if rel not in valid_paths:
                    valid_paths.append(rel)
            else:
                console.print(f"[warning]⚠ Ignoring path outside HOME directory: {escape(clean)}[/warning]")
        except (ValueError, OSError):
            continue

    return valid_paths

def prune_stale_tracked_files(valid_paths: list[str]) -> None:
    """Untracks files from Git index that were previously tracked but removed from .git_dusky_list."""
    code_ls, ls_out, _ = dotgit("ls-files", "-z")
    if code_ls != 0 or not ls_out:
        return

    tracked_files = [f for f in ls_out.split("\0") if f]
    stale = [
        f for f in tracked_files
        if not is_internal_gitdir(f) and not matches_pathspec(f, valid_paths)
    ]
    if stale:
        console.print(f"[info]Untracking {len(stale)} stale file(s) removed from manifest...[/info]")
        payload = "\0".join(stale) + "\0"
        dotgit("rm", "--cached", "-r", "--ignore-unmatch", "--quiet",
               "--pathspec-from-file=-", "--pathspec-file-nul",
               input_data=payload.encode("utf-8"),
               literal_pathspecs=True)

def stage_and_commit_dotfiles(commit_msg: str) -> bool:
    """Stages dotfiles matching the .git_dusky_list and commits them.

    Returns True when the repository is ready to push (committed or already
    clean), False when staging/commit failed.
    """
    valid_paths = get_manifest_pathspecs()

    if valid_paths is None:
        console.print(f"[warning]⚠ Warn: {DOTFILES_LIST} not found. Staging tracked changes only (-u).[/warning]")
        dotgit("add", "-u")
    elif not valid_paths:
        console.print("[warning]⚠ Warn: No valid paths in .git_dusky_list. Staging tracked changes only (-u).[/warning]")
        dotgit("add", "-u")
    else:
        # Prune files untracked from manifest
        prune_stale_tracked_files(valid_paths)

        console.print(f"[info]Processing staging payload ({len(valid_paths)} path(s))...[/info]")
        payload = "\0".join(valid_paths) + "\0"
        code_add, _, stderr_add = dotgit("add", "--pathspec-from-file=-", "--pathspec-file-nul",
                                         input_data=payload.encode("utf-8"),
                                         literal_pathspecs=True)
        if code_add != 0:
            console.print(f"[error]✖ Error staging files:[/error] {stderr_add.strip()}")
            return False

        # Count actual changed files that got staged
        _, diff_out, _ = dotgit("diff", "--cached", "--name-only", "-z")
        staged_files = [f for f in diff_out.split('\0') if f]
        if staged_files:
            console.print(f"[success]✔ Staged {len(staged_files)} changed file(s).[/success]")

    # Check index diff for uncommitted changes
    code_diff, _, _ = dotgit("diff", "--quiet", "--cached")
    if code_diff != 0:
        console.print("[info]Committing changes...[/info]")
        code_com, _, stderr_com = dotgit("commit", "-m", commit_msg)
        if code_com == 0:
            console.print("[success]✔ Changes committed successfully.[/success]")
        else:
            console.print(f"[error]✖ Commit failed:[/error] {stderr_com.strip()}")
            return False
    else:
        console.print("[info]➔ Nothing to commit (Working tree clean).[/info]")
    return True

def execute_sync(config: AppConfig, mode: str) -> None:
    """Orchestrates the repository sync process."""
    setup_github_ssh_linking(config.email)

    if mode == "NEW":
        console.print(Panel(
            f"[highlight]--- Mode: New Repository Setup ---[/highlight]\n"
            f"Local Path: {DOTFILES_DIR}\n"
            f"Remote URL: {config.repo_url}\n"
            f"Git User:   {config.username} <{config.email}>",
            border_style="cyan",
            box=box.ROUNDED
        ))

        # Initialize local bare repo
        if DOTFILES_DIR.exists():
            console.print(f"[warning]⚠ Warn: Removing existing bare repository at {DOTFILES_DIR}...[/warning]")
            if DOTFILES_DIR.is_dir() and not DOTFILES_DIR.is_symlink():
                shutil.rmtree(DOTFILES_DIR)
            else:
                DOTFILES_DIR.unlink()

        DOTFILES_DIR.mkdir(parents=True, exist_ok=True)
        code_init, _, stderr_init = run_cmd(["git", "init", "--bare", "-b", "main", str(DOTFILES_DIR)])
        if code_init != 0:
            console.print(f"[error]✖ Failed to initialize bare repository:[/error] {stderr_init.strip()}")
            sys.exit(1)

        # Configure local git settings
        dotgit("config", "--local", "status.showUntrackedFiles", "no")
        dotgit("config", "--local", "user.name", config.username)
        dotgit("config", "--local", "user.email", config.email)
        dotgit("config", "--local", "core.quotepath", "false")

        # Configure remote origin
        set_remote_origin(config.repo_url)

        # Stage and commit files
        if not stage_and_commit_dotfiles(config.commit_msg):
            console.print("[error]✖ Aborting sync: staging/commit failed — nothing was pushed.[/error]")
            sys.exit(1)

        # Check if commits exist
        code_head, _, _ = dotgit("rev-parse", "--verify", "HEAD")
        if code_head != 0:
            console.print(Panel(
                "[warning]⚠ Bare repository initialized and remote origin linked successfully.[/warning]\n"
                "However, no files were staged or committed (manifest was empty or files were missing).\n"
                f"Add files to [highlight]{DOTFILES_LIST}[/highlight] and run this tool again to push your first commit.",
                title="Initialization Notice",
                border_style="yellow",
                box=box.ROUNDED
            ))
            return

        # Ensure branch name is main
        dotgit("branch", "-M", "main")

        # Push to origin
        console.print("[info]Pushing first commit to GitHub origin/main...[/info]")
        code_push, _, stderr_push = dotgit("push", "-u", "origin", "main")
        if code_push == 0:
            console.print(Panel("[success]✔ Setup Complete! Bare repository is initialized and synced with GitHub.[/success]", border_style="green", box=box.ROUNDED))
        else:
            console.print(Panel(
                f"[error]✖ Push failed.[/error]\n"
                f"Please ensure you created a repository named '{escape(config.repo)}' on GitHub.\n\n"
                f"Error: {escape(stderr_push.strip())}",
                border_style="red",
                box=box.ROUNDED
            ))
            sys.exit(1)

    elif mode == "RELINK":
        console.print(Panel(
            f"[highlight]--- Mode: Relink to Existing Repository ---[/highlight]\n"
            f"Local Path: {DOTFILES_DIR}\n"
            f"Remote URL: {config.repo_url}\n"
            f"Git User:   {config.username} <{config.email}>",
            border_style="cyan",
            box=box.ROUNDED
        ))

        # Clone or reuse bare repository
        if DOTFILES_DIR.exists():
            if not DOTFILES_DIR.is_dir():
                console.print(f"[error]✖ Error: {DOTFILES_DIR} exists but is not a directory.[/error]")
                sys.exit(1)
            code_verify, stdout_verify, _ = run_cmd(["git", "--git-dir", str(DOTFILES_DIR), "rev-parse", "--is-bare-repository"])
            if code_verify == 0 and stdout_verify.strip() == "true":
                console.print(f"[info]➔ Using existing bare repository at {DOTFILES_DIR}[/info]")
            else:
                console.print(f"[error]✖ Error: Existing path {DOTFILES_DIR} is not a bare Git repository.[/error]")
                sys.exit(1)
        else:
            console.print("[info]Cloning bare repository from GitHub...[/info]")
            code_clone, _, stderr_clone = run_cmd(["git", "clone", "--bare", config.repo_url, str(DOTFILES_DIR)])
            if code_clone != 0:
                console.print(Panel(
                    f"[error]✖ Clone failed. Ensure the repository '{escape(config.repo)}' exists on GitHub.[/error]\n\n"
                    f"Error: {escape(stderr_clone.strip())}",
                    border_style="red",
                    box=box.ROUNDED
                ))
                sys.exit(1)

        # Configure local git settings
        dotgit("config", "--local", "status.showUntrackedFiles", "no")
        dotgit("config", "--local", "user.name", config.username)
        dotgit("config", "--local", "user.email", config.email)
        dotgit("config", "--local", "core.quotepath", "false")

        # Set / Link Remote url
        set_remote_origin(config.repo_url)

        # Fetch origin
        console.print("[info]Pruning and fetching latest changes from remote...[/info]")
        code_fetch, _, stderr_fetch = dotgit("fetch", "--prune", "origin")
        if code_fetch != 0:
            console.print(f"[warning]⚠ Fetch warning (remote might be empty or unreachable): {escape(stderr_fetch.strip())}[/warning]")

        # Inspect remote branches and local HEAD
        _, rem_branches_out, _ = dotgit("branch", "-r")
        rem_branches = [b.strip() for b in rem_branches_out.splitlines() if b.strip()]

        code_local_head, local_head_sha, _ = dotgit("rev-parse", "--verify", "HEAD")

        if code_local_head != 0:
            # Local HEAD is unborn — connect to remote branch if available
            target_branch = "main"
            if "origin/main" in rem_branches:
                target_branch = "main"
            elif "origin/master" in rem_branches:
                target_branch = "master"
            elif rem_branches:
                target_branch = rem_branches[0].replace("origin/", "").split(" -> ")[0].strip()

            if f"origin/{target_branch}" in rem_branches:
                console.print(f"[info]Connecting local tracking branch to origin/{target_branch}...[/info]")
                dotgit("branch", "-f", target_branch, f"origin/{target_branch}")
                dotgit("symbolic-ref", "HEAD", f"refs/heads/{target_branch}")
                dotgit("branch", "-u", f"origin/{target_branch}", target_branch)
                dotgit("reset", "--mixed", "--quiet", "HEAD")
        else:
            # Local HEAD exists
            code_br, stdout_br, _ = dotgit("branch", "--show-current")
            current_branch = stdout_br.strip() or "main"
            remote_ref = f"origin/{current_branch}"

            if remote_ref in rem_branches:
                # Count commits ahead/behind between local and remote
                code_rev, out_rev, _ = dotgit("rev-list", "--left-right", "--count", f"{current_branch}...{remote_ref}")
                ahead, behind = 0, 0
                if code_rev == 0 and out_rev.strip():
                    parts = out_rev.split()
                    if len(parts) >= 2:
                        ahead, behind = int(parts[0]), int(parts[1])

                if ahead == 0 and behind > 0:
                    # Local is purely behind remote -> Fast-forward directly
                    console.print(f"[info]Fast-forwarding local '{current_branch}' ({behind} commit(s) behind {remote_ref})...[/info]")
                    reconcile_upstream_changes(current_branch, remote_ref)
                    dotgit("branch", "-f", current_branch, remote_ref)
                elif ahead > 0 and behind > 0:
                    # Branches have diverged -> Rebase local commits onto remote
                    console.print(f"[info]Local branch '{current_branch}' has diverged ({ahead} ahead, {behind} behind {remote_ref}).[/info]")
                    console.print(f"[info]Rebasing local commits on top of {remote_ref}...[/info]")
                    code_reb, _, stderr_reb = dotgit("rebase", remote_ref)
                    if code_reb == 0:
                        console.print(f"[success]✔ Successfully rebased local commits onto {remote_ref}.[/success]")
                    else:
                        # Rebase failed due to unstaged changes or conflicts: abort cleanly and prompt resolution
                        dotgit("rebase", "--abort")
                        console.print(Panel(
                            f"[warning]⚠ Diverged History Detected[/warning]\n"
                            f"GitHub has [bold cyan]{behind}[/bold cyan] newer commit(s) while your local machine has [bold cyan]{ahead}[/bold cyan] unpushed commit(s).\n\n"
                            f"[bold white]Choose how to reconcile your repository:[/bold white]\n\n"
                            f"  [bold cyan]1) Safe Sync (Recommended)[/bold cyan]\n"
                            f"     • Preserves all files and scripts on disk in $HOME (zero data loss)\n"
                            f"     • Pulls in GitHub's {behind} new commit(s) and creates a fresh sync commit with your local files on top\n"
                            f"     • Pushes cleanly to GitHub as a fast-forward\n\n"
                            f"  [bold yellow]2) Force Push (Overwrite GitHub)[/bold yellow]\n"
                            f"     • Overwrites GitHub remote branch with your {ahead} local commit(s)\n"
                            f"     • [bold red]Caution:[/bold red] Discards the {behind} newer commit(s) currently on GitHub\n\n"
                            f"  [bold red]3) Abort[/bold red]\n"
                            f"     • Cancels the operation without modifying Git history or any files",
                            title="Branch Divergence Resolution",
                            border_style="yellow",
                            box=box.ROUNDED
                        ))
                        choice = _ask("Select option [1/2/3] (default: 1): ") or "1"
                        if choice == "1":
                            console.print(f"[info]Aligning local branch '{current_branch}' with {remote_ref}...[/info]")
                            reconcile_upstream_changes(current_branch, remote_ref)
                            dotgit("branch", "-f", current_branch, remote_ref)
                        elif choice == "2":
                            console.print(f"[warning]⚠ Overwriting {remote_ref} with local commits...[/warning]")
                            code_fp, stdout_fp, stderr_fp = dotgit("push", "--force-with-lease", "-u", "origin", current_branch)
                            if code_fp == 0:
                                console.print(Panel("[success]✔ Force Push Complete! GitHub remote is updated.[/success]", border_style="green", box=box.ROUNDED))
                                return
                            else:
                                console.print(Panel(
                                    f"[error]✖ Force push failed.[/error]\n\n"
                                    f"Error: {escape(stderr_fp.strip() or stdout_fp.strip())}",
                                    border_style="red",
                                    box=box.ROUNDED
                                ))
                                sys.exit(1)
                        else:
                            console.print("[warning]⚠ Operation cancelled by user.[/warning]")
                            sys.exit(0)

                dotgit("branch", "-u", remote_ref, current_branch)

            console.print("[info]Performing mixed reset of the index to HEAD (preserving disk files)...[/info]")
            dotgit("reset", "--mixed", "--quiet", "HEAD")

        # Stage and commit files
        if not stage_and_commit_dotfiles(config.commit_msg):
            console.print("[error]✖ Aborting sync: staging/commit failed — nothing was pushed.[/error]")
            sys.exit(1)

        # Check if HEAD is valid before pushing
        code_final_head, _, _ = dotgit("rev-parse", "--verify", "HEAD")
        if code_final_head != 0:
            console.print(Panel(
                "[warning]⚠ Bare repository reconnected to remote.[/warning]\n"
                "No commits exist locally or on remote yet.\n"
                f"Add files to [highlight]{DOTFILES_LIST}[/highlight] to stage and push changes.",
                title="Relink Notice",
                border_style="yellow",
                box=box.ROUNDED
            ))
            return

        # Push to origin
        code_branch, stdout_branch, _ = dotgit("branch", "--show-current")
        current_branch = stdout_branch.strip() or "main"

        console.print(f"[info]Pushing commits to origin/{current_branch}...[/info]")
        code_push, stdout_push, stderr_push = dotgit("push", "-u", "origin", current_branch)
        if code_push == 0:
            console.print(Panel("[success]✔ Relink Complete! Bare repository is linked and in sync with GitHub.[/success]", border_style="green", box=box.ROUNDED))
        else:
            console.print(Panel(
                f"[error]✖ Push failed.[/error]\n\n"
                f"Error: {escape(stderr_push.strip() or stdout_push.strip())}",
                border_style="red",
                box=box.ROUNDED
            ))
            sys.exit(1)

def prompt_configuration(args: argparse.Namespace) -> AppConfig:
    """Prompts for config details; provided CLI flags become defaults and are
    never discarded. With both identity flags present the run stays fully
    non-interactive (original fast path)."""
    console.print("\n[highlight]=== Absolute Engine Parameters ===[/highlight]")

    username = (args.username or "").strip()
    if not username:
        console.print("[bold white]Git User Identity (Name for Git commits, e.g., 'dusk')[/bold white]")
        while not username:
            username = _ask()
            if not username:
                console.print("[error]✖ Git User Identity (Name for Git commits) is required and cannot be empty.[/error]")

    gh_user = (args.gh_user or "").strip()
    if not gh_user:
        console.print("[bold white]GitHub Username (Your GitHub account username, e.g., 'yourusername')[/bold white]")
        while not gh_user:
            gh_user = _ask()
            if not gh_user:
                console.print("[error]✖ GitHub Username (Your GitHub account username) is required and cannot be empty.[/error]")

    # Full identity supplied via flags -> zero-prompt fast path (unchanged).
    interactive = not (args.username and args.gh_user)

    default_email = args.email or f"{gh_user}@users.noreply.github.com"
    email = default_email
    if interactive and not args.email:
        console.print(f"[bold white]Git Email Address (Optional, used for commit history) [default: {default_email}][/bold white]")
        email = _ask() or default_email

    repo = args.repo or DEFAULT_REPO_NAME
    if interactive:
        console.print(f"[bold white]Target Repository Architecture (The GitHub repository name) [default: {repo}][/bold white]")
        repo = _ask() or repo

    commit_msg = args.commit_msg or "Dusky backup sync"
    if interactive and not args.commit_msg:
        console.print("[bold white]Initial/Sync Commit Payload (Commit message for syncing changes) [default: Dusky backup sync][/bold white]")
        commit_msg = _ask() or commit_msg

    return AppConfig(
        username=username,
        email=email,
        gh_user=gh_user,
        repo=repo,
        commit_msg=commit_msg
    )

def main() -> Never:
    check_dependencies()

    parser = argparse.ArgumentParser(description="Dusky Dotfiles Engine (Arch Linux / Python 3.14 Strict Mode)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-n", "--new", action="store_true", help="Force NEW sequence initialization")
    group.add_argument("-r", "--relink", action="store_true", help="Force RELINK sequence")

    # Config flags
    parser.add_argument("--username", help="Git username")
    parser.add_argument("--email", help="Git email")
    parser.add_argument("--gh-user", help="GitHub username")
    parser.add_argument("--repo", default=DEFAULT_REPO_NAME, help="Repository name")
    parser.add_argument("--commit-msg", help="Commit message")

    args = parser.parse_args()

    mode = "NEW" if args.new else "RELINK" if args.relink else None
    dep_table = build_dependency_matrix()

    # Interactive menu if no mode flag is provided
    if mode is None:
        console.clear()
        cmd_table = Table(title="󰏖 Dusky Engine Commands", show_header=False, box=box.MINIMAL_DOUBLE_HEAD, title_style="bold blue")
        cmd_table.add_column("Key", style="bold cyan")
        cmd_table.add_column("Action", style="bold white")
        cmd_table.add_row("1", "Initialize NEW bare architecture")
        cmd_table.add_row("2", "RELINK existing remote engine")
        cmd_table.add_row("q", "Terminate Execution")

        # Render dependency matrix and commands sequentially (vertical stack)
        console.print(dep_table)
        console.print()
        console.print(cmd_table)
        
        console.print("\n[highlight]Choose Action [1/2/q] [default: 1][/highlight]")
        choice = _ask() or "1"
        while choice not in ("1", "2", "q"):
            console.print("[error]✖ Invalid choice. Please choose '1', '2', or 'q'.[/error]")
            choice = _ask() or "1"
            
        if choice == "1":
            mode = "NEW"
        elif choice == "2":
            mode = "RELINK"
        else:
            sys.exit(0)

    # Gather parameters (via CLI or Interactive Prompt — flags are never discarded)
    config = prompt_configuration(args)

    # Final review confirmation
    console.print(Panel(
        f"Git Identity: {escape(config.username)} <{escape(config.email)}>\n"
        f"Target Node:  {escape(config.repo_url)}\n"
        f"Operation:    {mode}",
        title="Verify Final Deployment Parameters",
        border_style="cyan",
        box=box.ROUNDED
    ))
    console.print("\n[bold cyan]Execute architecture deployment? (Y/n)[/bold cyan]")
    ans = _ask().lower()
    if ans and ans not in ("y", "yes"):
        console.print("[error]✖ Deployment sequence completely aborted by operator.[/error]")
        sys.exit(1)

    try:
        start_ssh_agent()
        execute_sync(config, mode)
    finally:
        kill_ssh_agent()

    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[warning]⚠ Operator Termination Signal Received. Executing shutdown.[/warning]")
        kill_ssh_agent()
        sys.exit(0)
