#!/usr/bin/env python3
"""
NeoDiff - Advanced Side-by-Side Diffing Engine with Frecency & FZF Tab-Completion.
Optimized for the Dusky Ecosystem.
[HEADLESS MODE] Strictly relies on ZSH proxy for UI and routing.
"""

# ---------------------------------------------------------
# FAST PATH: Minimal imports for sub-10ms FZF execution
# ---------------------------------------------------------
import sys
import os
import shlex
from pathlib import Path

DELIM = '\x1f'

# ==========================================
# 0. Internal FZF API (Tab Completion & File Explorer)
# ==========================================
if len(sys.argv) > 1 and sys.argv[1].startswith("--_fzf_"):

    def load_frecency_fast() -> dict[str, int]:
        state_file = Path.home() / ".config" / "dusky" / "settings" / "neodiff" / "state.tsv"
        weights = {}
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.rstrip('\n').split(DELIM)
                    if len(parts) == 2 and parts[0].isdigit():
                        weights[parts[1]] = int(parts[0])
        except OSError:
            pass
        return weights

    def get_fzf_action(actions: list[tuple[str, str]]) -> str:
        combined = "".join(arg for _, arg in actions if arg)
        for d in "!@#$%^&*|~/":
            if d not in combined:
                safe_delim = d
                break
        else:
            safe_delim = DELIM
        
        parts = []
        for action, arg in actions:
            if arg:
                parts.append(f"{action}{safe_delim}{arg}{safe_delim}")
            else:
                parts.append(action)
        return "+".join(parts)

    python_exec = shlex.quote(sys.executable)
    script_path = shlex.quote(sys.argv[0])

    if sys.argv[1] == "--_fzf_explore":
        query_str = sys.argv[2] if len(sys.argv) > 2 else ""
        if not query_str:
            query_str = "./"

        p = Path(query_str).expanduser()

        if p.is_dir() and (query_str.endswith('/') or query_str in (".", "..", "~", "./", "../")):
            scan_dir = p
            prefix = query_str
            if not prefix.endswith('/') and prefix != "":
                prefix += '/'
        else:
            scan_dir = p.parent
            if "/" in query_str:
                prefix = query_str[:query_str.rindex("/") + 1]
            else:
                prefix = ""

        if not scan_dir.exists() or not scan_dir.is_dir():
            sys.exit(0)

        weights = load_frecency_fast()
        items = []
        
        try:
            if scan_dir != Path("/"):
                parent_prefix = prefix + "../" if prefix else "../"
                items.append((9999999, parent_prefix, f"  \033[1;34m{parent_prefix}\033[0m"))
            
            for entry in os.scandir(scan_dir):
                if entry.name in {".git", ".svn", "node_modules", "__pycache__"}:
                    continue
                    
                abs_path = os.path.abspath(entry.path)
                w = weights.get(abs_path, 0)
                score = w * 10
                
                display_path = prefix + entry.name
                
                if entry.is_dir():
                    display_path += "/"
                    display = f"  \033[1;34m{display_path}\033[0m"
                    items.append((score + 10000, display_path, display))
                else:
                    badge = f" \t\033[3;38;2;249;226;175m {w}\033[0m" if w > 0 else ""
                    display = f"  \033[1;36m{display_path}\033[0m{badge}"
                    items.append((score, display_path, display))
        except OSError:
            pass 
            
        items.sort(key=lambda x: (-x[0], x[1]))
        
        for score, raw_path, display in items:
            print(f"{score}{DELIM}{raw_path}{DELIM}{display}")
        sys.exit(0)

    elif sys.argv[1] == "--_fzf_tab":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        selected = sys.argv[3] if len(sys.argv) > 3 else ""
        
        if selected:
            if selected.endswith('/'):
                q_dir = shlex.quote(selected)
                print(get_fzf_action([("change-query", selected), ("reload", f"{python_exec} {script_path} --_fzf_explore {q_dir}")]))
            else:
                print(get_fzf_action([("change-query", selected)]))
        else:
            q_dir = shlex.quote(query)
            print(get_fzf_action([("reload", f"{python_exec} {script_path} --_fzf_explore {q_dir}")]))
        sys.exit(0)

    elif sys.argv[1] == "--_fzf_enter":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        selected = sys.argv[3] if len(sys.argv) > 3 else ""
        
        target = selected if selected else query
        if not target:
            print("accept")
            sys.exit(0)
            
        expanded = str(Path(target).expanduser().resolve())
        if Path(expanded).is_dir():
            if not target.endswith('/'):
                target += '/'
            q_dir = shlex.quote(target)
            print(get_fzf_action([("change-query", target), ("reload", f"{python_exec} {script_path} --_fzf_explore {q_dir}")]))
        else:
            print("accept")
        sys.exit(0)

# ---------------------------------------------------------
# HEAVY PATH: Standard execution imports loaded dynamically
# ---------------------------------------------------------
import subprocess
import tempfile
import difflib
import importlib.util
import shutil
import argparse

def ensure_system_dependencies() -> None:
    missing_sys = []
    if not shutil.which("fzf"): missing_sys.append("fzf")
    if not shutil.which("delta"): missing_sys.append("git-delta")
    
    missing_py = []
    if importlib.util.find_spec("rich") is None: missing_py.append("python-rich")
    
    missing = missing_sys + missing_py
    if missing:
        print(f"[\033[93m*\033[0m] Missing Arch dependencies: {', '.join(missing)}")
        print(f"[\033[94m*\033[0m] Elevating to sudo to provision via pacman...")
        try:
            subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm"] + missing, check=True)
            print(f"[\033[92m✔\033[0m] Dependencies installed successfully. Restarting engine...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError:
            print(f"[\033[91m!\033[0m] Pacman failed. Please run: sudo pacman -S --needed {' '.join(missing)}")
            sys.exit(1)

ensure_system_dependencies()
from rich.console import Console
from rich.panel import Panel

console = Console()

# ==========================================
# 2. Frecency & State Management
# ==========================================
STATE_DIR = Path.home() / ".config" / "dusky" / "settings" / "neodiff"
STATE_FILE = STATE_DIR / "state.tsv"

def load_frecency() -> dict[str, int]:
    weights = {}
    if STATE_FILE.exists():
        try:
            for line in STATE_FILE.read_text(encoding='utf-8').splitlines():
                parts = line.split(DELIM)
                if len(parts) == 2 and parts[0].isdigit():
                    weights[parts[1]] = int(parts[0])
        except OSError:
            pass
    return weights

def update_frecency(filepath: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    weights = load_frecency()
    
    abs_path = str(Path(filepath).resolve())
    weights[abs_path] = weights.get(abs_path, 0) + 1
    
    tmp_file = STATE_FILE.with_suffix(f".tmp.{os.getpid()}")
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            for path, weight in weights.items():
                f.write(f"{weight}{DELIM}{path}\n")
        tmp_file.replace(STATE_FILE)
    except OSError:
        if tmp_file.exists():
            tmp_file.unlink()

# ==========================================
# 3. Neovim Headless Engine & File Readers
# ==========================================
def extract_nvim_previous_state(filepath: str) -> list[str]:
    target_file = Path(filepath).resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        lua_path = Path(tmpdir) / "extract.lua"
        dump_path = Path(tmpdir) / "dump.txt"

        lua_code = f"""
        vim.opt.shortmess:append("A") 
        local dump = "{dump_path.as_posix()}"
        
        local initial_state = table.concat(vim.api.nvim_buf_get_lines(0, 0, -1, false), "\\n")
        vim.cmd("silent! earlier 1f")
        local previous_state = table.concat(vim.api.nvim_buf_get_lines(0, 0, -1, false), "\\n")
        
        if initial_state == previous_state then
            vim.cmd("silent! undo")
        end
        
        local f = io.open(dump, "w")
        local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
        for _, line in ipairs(lines) do
            f:write(line .. "\\n")
        end
        f:close()
        
        vim.cmd("qa!")
        """
        lua_path.write_text(lua_code, encoding='utf-8')
    
        try:
            subprocess.run(
                ["nvim", "--headless", "--noplugin", "-c", f"luafile {lua_path.as_posix()}", str(target_file)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            return dump_path.read_text(encoding='utf-8').splitlines()
        
        except subprocess.CalledProcessError:
            console.print("[bold red]Failed to execute Neovim API. Check if the file has an active undo history.[/]")
            sys.exit(1)
        except OSError:
            console.print("[bold red]Error:[/] Neovim failed to write the state dump.")
            sys.exit(1)

def read_file_lines(filepath: str) -> list[str]:
    p = Path(filepath)
    if not p.exists() or not p.is_file():
        return []
    
    try:
        return p.read_text(encoding='utf-8').splitlines()
    except UnicodeDecodeError:
        try:
            return p.read_text(encoding='latin-1').splitlines()
        except Exception as e:
            console.print(f"[bold red]Error:[/] File could not be decoded as text: {filepath}\n{e}")
            sys.exit(1)
    except PermissionError:
        console.print(f"[bold red]Error:[/] Permission denied: {filepath}")
        sys.exit(1)

# ==========================================
# 4. Delta Side-by-Side Rendering
# ==========================================
def render_with_delta(old_lines: list[str], new_lines: list[str], title_old: str, title_new: str, filename: str) -> None:
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=title_old,
        tofile=title_new,
        lineterm=""
    ))

    if not diff:
        console.print(Panel("[bold green]No differences found![/] Both states are identical.", border_style="green"))
        return

    diff_text = "\n".join(diff) + "\n"
    ext = Path(filename).suffix.lstrip('.')
    
    delta_cmd = [
        "delta", 
        "--side-by-side", 
        "--line-numbers",
        "--paging=always",
        "--dark",
        "--keep-plus-minus-markers",
        "--tabs=4",
        "--wrap-max-lines=unlimited",
        "--navigate"
    ]
    
    if ext:
        delta_cmd.append(f"--default-language={ext}")
    
    try:
        subprocess.run(delta_cmd, input=diff_text, text=True)
    except KeyboardInterrupt:
        pass

# ==========================================
# 5. Core Execution Routing
# ==========================================
def main() -> None:
    parser = argparse.ArgumentParser(description="NeoDiff - Advanced side-by-side diffing engine.")
    parser.add_argument("files", nargs="*", help="Files to compare (1 or 2 files).")
    parser.add_argument("-n", "--nvim", action="store_true", help="Compare a file against its previous Neovim undo state.")
    parser.add_argument("-m", "--multiple", action="store_true", help="Alias flag (handled via ZSH proxy).")
    
    args = parser.parse_args()
    files = args.files
    
    if len(files) == 0:
        console.print("[bold red]Error:[/] Engine executed autonomously without file arguments. Check ZSH Proxy.")
        sys.exit(1)
        
    if args.nvim and len(files) >= 1:
        target = str(Path(files[0]).expanduser().resolve())
        if not Path(target).exists():
            console.print(f"[bold red]Error:[/] Target File not found: {target}")
            sys.exit(1)
            
        update_frecency(target)
        
        with console.status("[bold yellow]Extracting temporal state via Neovim RPC...[/]"):
            old_lines = extract_nvim_previous_state(target)
            new_lines = read_file_lines(target)
            
        render_with_delta(old_lines, new_lines, f"Neovim Undo History ({target})", f"Current State ({target})", target)
        sys.exit(0)
        
    elif len(files) >= 2:
        file1 = str(Path(files[0]).expanduser().resolve())
        file2 = str(Path(files[1]).expanduser().resolve())
        
        if not Path(file1).exists():
            console.print(f"[bold red]Error:[/] File 1 not found: {file1}")
            sys.exit(1)
        if not Path(file2).exists():
            console.print(f"[bold red]Error:[/] File 2 not found: {file2}")
            sys.exit(1)

        update_frecency(file1)
        update_frecency(file2)

        old_lines = read_file_lines(file1)
        new_lines = read_file_lines(file2)
        render_with_delta(old_lines, new_lines, file1, file2, file2)
        sys.exit(0)
        
    else:
        # Fallback if ZSH fails to inject -n for a single file
        console.print("[bold red]Error:[/] Invalid arguments passed to engine. Requires 2 files or 1 file with -n flag.")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
