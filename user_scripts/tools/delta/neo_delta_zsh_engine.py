#!/usr/bin/env python3
"""Compare two files, or a file and its previous saved Neovim undo state."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

# Match the ISO's config/options.lua without starting its plugin manager.
# DUSKY_DIFF_UNDODIR can point at a different persistent-undo directory.
UNDO_SCRIPT = r'''
local undo_dir = vim.env.DUSKY_DIFF_UNDODIR
if undo_dir and undo_dir ~= "" then
    vim.opt.undodir = undo_dir
else
    vim.opt.undodir:prepend(vim.fn.stdpath("data") .. "/undodir")
end
vim.opt.undofile = true
vim.opt.swapfile = false
vim.opt.backup = false
vim.opt.writebackup = false
vim.api.nvim_cmd({cmd = "edit", args = {arg[1]}}, {})
local initial_seq = vim.fn.undotree().seq_cur
assert(vim.fn.undotree().seq_last > 0, "No usable persistent undo history for " .. arg[1])
vim.cmd("silent earlier 1f")
assert(vim.fn.undotree().seq_cur ~= initial_seq, "No earlier saved undo state for " .. arg[1])
-- Write a copy using the buffer's encoding and file format, without touching
-- the source file or its undo history. Preserve a missing final newline.
vim.opt_local.undofile = false
vim.opt_local.fixendofline = false
vim.api.nvim_cmd({cmd = "write", args = {arg[2]}, mods = {silent = true, noautocmd = true}}, {})
'''


def extract_nvim_previous_state(filepath: Path, dump_path: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".lua", encoding="utf-8") as script:
        script.write(UNDO_SCRIPT)
        script.flush()
        result = subprocess.run(
            ["nvim", "-u", "NONE", "-i", "NONE", "-n", "--headless",
             "-l", script.name, filepath, dump_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    if result.returncode or not dump_path.is_file():
        detail = result.stderr.strip() or "Neovim did not write the previous state."
        raise RuntimeError(f"Neovim undo extraction failed: {detail}")


def render_with_delta(old_file: Path, new_file: Path) -> int:
    # Native comparison preserves bytes, line endings and binary-file detection,
    # and avoids building multiple full-file/diff copies in Python.
    result = subprocess.run([
        "delta", "--side-by-side", "--line-numbers", "--paging=auto", "--dark",
        "--keep-plus-minus-markers", "--tabs=4", "--wrap-max-lines=unlimited",
        "--navigate", "--diff-args=--no-ext-diff --no-textconv",
        "--", old_file, new_file,
    ])
    if result.returncode == 0:
        print("No differences found. Both states are identical.")
    # Native delta uses diff's status 1 for differences; those are successful
    # comparisons here. Preserve actual failures, including terminated children.
    if result.returncode in (0, 1):
        return 0
    return result.returncode if result.returncode > 0 else 128 - result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-n", "--nvim", action="store_true",
                      help="Compare one file against its previous saved Neovim undo state.")
    mode.add_argument("-m", "--multiple", action="store_true",
                      help="Explicit two-file comparison (also the default).")
    parser.add_argument("files", nargs="+", help="One file with -n, otherwise exactly two files.")
    args = parser.parse_args()
    expected = 1 if args.nvim else 2
    if len(args.files) != expected:
        parser.error(f"this mode requires exactly {expected} file(s)")

    try:
        files = [Path(name).expanduser().absolute() for name in args.files]
    except (OSError, RuntimeError) as error:
        parser.error(str(error))
    for path in files:
        if not path.is_file():
            parser.error(f"not a regular file: {path}")
        try:
            # Git can compare empty files by size without opening them. Keep
            # unreadable inputs an error consistently, including empty files.
            with path.open("rb"):
                pass
        except OSError as error:
            parser.error(f"cannot read {path}: {error.strerror}")
    for command in ("delta", "git", *(("nvim",) if args.nvim else ())):
        if shutil.which(command) is None:
            print(f"NeoDiff: required command not found: {command}", file=sys.stderr)
            return 127

    try:
        if args.nvim:
            with tempfile.TemporaryDirectory(prefix="neodiff-") as tmpdir:
                # Retain the extension for delta's syntax highlighting.
                previous_dir = Path(tmpdir) / "previous-save"
                previous_dir.mkdir()
                previous = previous_dir / files[0].name
                extract_nvim_previous_state(files[0], previous)
                return render_with_delta(previous, files[0])
        return render_with_delta(*files)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"NeoDiff: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
