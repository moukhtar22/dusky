# Shell Reference: Efficient Interactive `zsh` on Arch Linux

> [!note] Scope
> - Assumes **interactive `zsh`** in **`kitty`** or **`foot`** on **Arch Linux**
> - Typical session model: **Wayland** compositor such as **Hyprland**, often launched via **UWSM**
> - Keybindings below assume zsh’s default **emacs-style** editing mode: `bindkey -e`
> - If you use **vi mode** (`bindkey -v`), many line-editing bindings differ
> - `zsh` uses **ZLE** (Zsh Line Editor), not GNU Readline, though many default bindings are similar

## Useful Packages

```bash
sudo pacman -S --needed man-db man-pages curl wl-clipboard pacman-contrib tealdeer
```

- `man-db`, `man-pages`: local manual pages
- `wl-clipboard`: `wl-copy` / `wl-paste` for native Wayland clipboard access
- `pacman-contrib`: includes `checkupdates`
- `tealdeer`: provides the `tldr` command

---

## Filesystem Navigation

| Command | Effect | Notes |
|---|---|---|
| `cd` | Go to `$HOME` | Shorter than `cd ~` |
| `cd ~` | Go to `$HOME` | Equivalent to `cd` |
| `cd -` | Switch to previous working directory | Also prints the destination path |
| `pwd` | Print current working directory | Use `pwd -P` to resolve symlinks physically |
| `pushd DIR` | Push current directory onto the stack, then change to `DIR` | Useful for temporary jumps |
| `popd` | Pop the top directory from the stack and switch to it | Fails if the stack is empty |
| `dirs -v` | Show the directory stack with indices | Works in both bash and zsh |

### Example: Directory Stack

```bash
pwd
# /home/user

pushd /etc
# /etc ~
pushd /var/log
# /var/log /etc ~

dirs -v
# 0  /var/log
# 1  /etc
# 2  ~

popd
# /etc ~
popd
# ~
```

> [!tip] Why `pushd` / `popd` matter
> `cd -` remembers only the immediately previous directory.  
> The directory stack can remember multiple locations.

### Optional `zsh` Navigation Settings

```zsh
# ~/.zshrc
setopt AUTO_CD AUTO_PUSHD PUSHD_IGNORE_DUPS
DIRSTACKSIZE=20
```

- `AUTO_CD`: typing a directory name changes into it
- `AUTO_PUSHD`: every `cd` behaves like `pushd`
- `PUSHD_IGNORE_DUPS`: avoid duplicate stack entries

---

## Line Editing and Terminal State

### Common Emacs-Style Editing Bindings in `zsh`

| Binding | Effect |
|---|---|
| `Ctrl+A` | Move to beginning of line |
| `Ctrl+E` | Move to end of line |
| `Ctrl+U` | Delete from cursor to beginning of line |
| `Ctrl+K` | Delete from cursor to end of line |
| `Ctrl+W` | Delete previous word |
| `Ctrl+Y` | Yank/paste the last killed text |
| `Alt+B` | Move backward one word |
| `Alt+F` | Move forward one word |
| `Alt+.` | Insert the last argument from the previous command |
| `Ctrl+L` | Clear/redraw the visible screen |
| `Ctrl+D` | Delete character under cursor; on an empty prompt, send EOF/exit shell |

> [!note]
> `Alt+.` is often one of the fastest ways to reuse a path from the previous command.

### `clear`, `reset`, and `stty sane`

| Command | What it actually does |
|---|---|
| `clear` | Clears the visible screen; may also clear scrollback if the terminal and terminfo support it |
| `reset` | Reinitializes terminal state after binary garbage, broken modes, or a crashed full-screen app |
| `stty sane` | Restores basic TTY settings if echo/canonical mode is broken |

```bash
clear

# Attempt to clear scrollback too, if supported by the terminal/terminfo
tput E3 2>/dev/null
clear

# Reinitialize a broken terminal
reset

# Restore basic tty settings
stty sane
```

> [!warning] `reset` is not a reliable “clear scrollback” command
> It is for **terminal reinitialization**, not for guaranteed scrollback erasure.  
> Scrollback clearing depends on the terminal emulator and the active terminfo entry.

> [!tip]
> In `kitty` and `foot`, scrollback clearing usually works correctly when `$TERM` is correct and the matching terminfo is installed.

---

## History and Fast Recall

### Core History Operations

| Command / Binding | Effect |
|---|---|
| `history` | Show command history |
| `fc -l` | List history using the POSIX-style builtin |
| `fc -l -10` | Show the last 10 history events |
| `!!` | Expand to the previous command |
| `!$` | Expand to the last argument of the previous command |
| `!123` | Re-run history event 123 |
| `!pacman` | Re-run the most recent command beginning with `pacman` |
| `!?ssh?` | Re-run the most recent command containing `ssh` |
| `Ctrl+R` | Incremental reverse search through history |

### Reverse Search Behavior

- Press `Ctrl+R`, then type part of a command
- Press `Ctrl+R` again to search older matches
- Press `Enter` to accept the current match
- `Ctrl+G` usually aborts the search and restores the original line
- `Ctrl+C` sends an interrupt and may discard the current line

> [!warning] `sudo !!` is convenient, but dangerous
> `sudo !!` expands and executes the previous command immediately unless you configure history verification.
>
> Safer workflow:
> 1. Recall the command with `Up` or `Ctrl+R`
> 2. Add `sudo ` at the beginning
> 3. Review
> 4. Run it

### Make History Expansion Safer in `zsh`

```zsh
# ~/.zshrc
setopt HIST_VERIFY
```

With `HIST_VERIFY`, history expansions such as `!!` are placed back into the editing buffer for review before execution.

### Literal `!` Characters

Interactive history expansion can surprise you when typing YAML, URLs, or strings containing `!`.

If you do **not** want `!` history expansion in interactive `zsh`:

```zsh
# ~/.zshrc
setopt NO_BANG_HIST
```

> [!note]
> History expansion is an **interactive-shell feature**. Do not rely on `!!`, `!$`, or similar syntax in scripts.

### Recommended `zsh` History Settings

```zsh
# ~/.zshrc
HISTFILE=${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history
mkdir -p -- "${HISTFILE:h}"

HISTSIZE=50000
SAVEHIST=50000

setopt EXTENDED_HISTORY HIST_VERIFY HIST_IGNORE_SPACE HIST_IGNORE_ALL_DUPS \
       HIST_REDUCE_BLANKS INC_APPEND_HISTORY HIST_FCNTL_LOCK
```

- `EXTENDED_HISTORY`: timestamp and duration metadata
- `HIST_IGNORE_SPACE`: commands prefixed with a space are not saved
- `HIST_IGNORE_ALL_DUPS`: remove older duplicates
- `INC_APPEND_HISTORY`: append commands incrementally
- `HIST_FCNTL_LOCK`: safer history file locking across concurrent shells

---

## Job Control and Long-Running Commands

### Foreground / Background Control

| Command / Binding | Effect |
|---|---|
| `Ctrl+C` | Send `SIGINT` to the foreground process |
| `Ctrl+Z` | Send `SIGTSTP` and suspend the foreground job |
| `jobs` | List jobs managed by the current shell |
| `jobs -l` | List jobs with job numbers and PIDs |
| `bg` | Resume the current suspended job in the background |
| `bg %1` | Resume job 1 in the background |
| `fg` | Bring the current job to the foreground |
| `fg %1` | Bring job 1 to the foreground |
| `kill %1` | Send the default signal to job 1 |
| `disown %1` | Remove job 1 from the shell’s job table |

> [!note]
> `jobs` only knows about processes started by the **current shell**.  
> For general process lookup, use tools such as:
>
> ```bash
> pgrep -af rsync
> ps -ef | grep '[r]sync'
> systemctl --user --type=service
> ```

> [!warning] Backgrounding is not persistence
> `bg` and `disown` do **not** turn a command into a managed service.  
> Terminal-attached programs may still receive `SIGHUP`, block on terminal I/O, or stop again if they try to read from the TTY.

### Full-Screen TUI Programs

Suspending `htop`, `btop`, `less`, `vim`, or similar applications with `Ctrl+Z` is normal.  
Resuming them with `fg` is usually safe.

Resuming them with `bg` is often **not** useful because terminal I/O can stop them again.

### Better Detach Method on Arch: `systemd-run --user`

In a systemd-managed user session, this is usually cleaner than `nohup`:

```bash
systemd-run --user --same-dir --collect --unit=backup-job rsync -a ~/src/ /mnt/backup/
```

Follow logs:

```bash
journalctl --user -u backup-job -f
```

> [!tip]
> Under Hyprland/UWSM, `systemd-run --user` is often the best way to detach work from `kitty` or `foot`.

> [!warning]
> Transient user services typically stop when your **last user session** ends unless you deliberately configure persistence.  
> For jobs that must survive logout, use a proper user service/timer or enable lingering:
>
> ```bash
> loginctl enable-linger "$USER"
> ```

---

## Command Composition, Pipes, and Redirection

### Basic Operators

| Syntax | Meaning |
|---|---|
| `cmd1 ; cmd2` | Run `cmd1`, then `cmd2`, regardless of success/failure |
| `cmd1 && cmd2` | Run `cmd2` only if `cmd1` succeeds |
| `cmd1 || cmd2` | Run `cmd2` only if `cmd1` fails |
| `cmd1 \| cmd2` | Pipe **stdout** of `cmd1` to **stdin** of `cmd2` |
| `cmd1 \|& cmd2` | Pipe **stdout + stderr** of `cmd1` to `cmd2` |
| `> file` | Overwrite file with stdout |
| `>> file` | Append stdout to file |
| `2> file` | Redirect stderr to file |
| `2>&1` | Redirect stderr to the current stdout target |

### Practical Examples

```bash
# Upgrade the system, then print a success message only if pacman succeeded
sudo pacman -Syu && printf '%s\n' 'Upgrade completed.'

# Idempotently create a directory
mkdir -p -- my_dir

# Read boot warnings through a pager
journalctl -b -p warning | less

# Capture both stdout and stderr
make |& tee build.log
```

> [!warning] `cmd1 && cmd2 || cmd3` is not a true `if/else`
> If `cmd2` fails, `cmd3` runs too.  
> Use an actual `if` statement when the distinction matters.

### Pipeline Exit Status

By default, a pipeline’s exit status is usually the status of the **last** command.

If you care whether **any** stage failed, enable `pipefail`:

```bash
set -o pipefail
```

This works in interactive bash and zsh, and is especially important in scripts.

### Grouping Commands

```bash
# Group in the current shell
{ cd /etc && ls; }

# Group in a subshell
( cd /tmp && pwd )
```

- `{ ...; }` runs in the **current shell**
- `( ... )` runs in a **subshell**, so directory changes and variable assignments do not persist afterward

### Redirection Order Matters

```bash
cmd >out.log 2>&1
```

Both stdout and stderr go to `out.log`.

```bash
cmd 2>&1 >out.log
```

Stderr goes to the terminal; only stdout goes to `out.log`.

---

## Safe Interactive Habits

| Prefer | Instead of | Why |
|---|---|---|
| `printf '%s\n' "$var"` | `echo "$var"` | `printf` is predictable; `echo` has option/escape quirks |
| `cmd -- "$path"` | `cmd $path` | Protects pathnames beginning with `-` |
| `"$var"` | `$var` | Preserves whitespace and avoids accidental globbing |
| `$(command)` | `` `command` `` | Easier to read and nest |

### Examples

```bash
cp -- "$src" "$dst"
rm -- "$file"
printf '%s\n' "$value"
archive=$(date +%F).tar.zst
```

> [!warning]
> Interactive conveniences such as aliases, history expansion, and loose quoting are fine at the prompt, but they are poor practice in scripts.

---

## Aliases, Functions, and Command Inspection

### Alias Basics

Aliases are best for **short interactive shortcuts**.

```zsh
# ~/.zshrc
alias ll='ls -lh --group-directories-first --color=auto'
alias sysup='sudo pacman -Syu'
alias updates='checkupdates'
```

Reload after editing:

```bash
source ~/.zshrc
```

> [!warning] Arch Linux: avoid partial upgrades
> Never use `pacman -Sy` without `-u`.  
> If you sync package databases, perform the full upgrade:
>
> ```bash
> sudo pacman -Syu
> ```

### Prefer Functions When Arguments Are Needed

Aliases do **not** take positional parameters. Use a function for anything with arguments or logic.

```zsh
# ~/.zshrc
cheat() {
  emulate -L zsh
  local topic=${1:?usage: cheat <topic>}
  command curl -fL --compressed "https://cheat.sh/${topic}"
}
```

Example:

```bash
cheat rsync | less -R
```

> [!note]
> The simple function above is ideal for ordinary topics such as `rsync`, `tar`, or `openssl/x509`.

### Where to Put `zsh` Configuration

- `~/.zshrc`: interactive settings, aliases, functions, keybindings
- `~/.zprofile`: login-shell environment setup
- `~/.zshenv`: sourced for **every** zsh invocation, including scripts; avoid interactive customizations here

### Inspect What a Command Name Really Means

```bash
type -a ls
command -v rg
whence -va ls   # zsh
```

Use these instead of relying on `which`.

---

## Getting Help Quickly

### Local-First Help

| Command | Purpose |
|---|---|
| `man rsync` | Full manual page |
| `apropos archive` | Search man page descriptions |
| `tldr tar` | Concise examples (`tealdeer`) |
| `cmd --help` | Built-in summary for many commands |

### `zsh`-Specific Documentation

```bash
man zshbuiltins
man zshexpn
man zshzle
man zshoptions
```

These cover most of the shell behavior discussed in this note.

### External Cheatsheets

```bash
curl -fL --compressed 'https://cheat.sh/rsync' | less -R
```

> [!note]
> `cheat.sh` is convenient, but it is an external network service.  
> Prefer local docs first when privacy, offline access, or long-term reliability matter.

---

## Wayland, `kitty`, and `foot`

### Clipboard: Use `wl-clipboard`

Under native Wayland sessions, prefer `wl-copy` / `wl-paste` over X11-only tools such as `xclip` or `xsel`.

```bash
printf '%s\n' 'hello world' | wl-copy
wl-paste
```

Primary selection, if supported:

```bash
printf '%s\n' 'hello world' | wl-copy -p
wl-paste -p
```

> [!tip]
> For shell workflows in Hyprland, `wl-clipboard` is the canonical clipboard interface.

### Remote Hosts and `$TERM`

`kitty` and `foot` rely on correct terminfo. On remote systems, missing terminfo can break:

- colors
- function keys
- `clear` / `reset`
- pagers and TUIs
- line drawing / box characters

Check local terminfo:

```bash
echo "$TERM"
infocmp "$TERM" >/dev/null
```

Install your local terminal definition on a remote host:

```bash
infocmp -x "$TERM" | ssh host 'mkdir -p ~/.terminfo && tic -x -o ~/.terminfo /dev/stdin'
```

> [!warning]
> If the remote host does not know your terminal type, do **not** just ignore the problem.  
> A wrong or missing terminfo entry causes subtle breakage.

Fallback only when necessary:

```bash
TERM=xterm-256color ssh host
```

This is less capable than using the correct terminal definition, but can be acceptable for simple remote work.

---

## Optional `zsh` Quality-of-Life Snippet

```zsh
# ~/.zshrc

# Editing mode
bindkey -e

# Edit the current command line in $EDITOR with Ctrl+X Ctrl+E
autoload -Uz edit-command-line
zle -N edit-command-line
bindkey '^X^E' edit-command-line

# Allow comments at the interactive prompt
setopt INTERACTIVE_COMMENTS

# Navigation
setopt AUTO_CD AUTO_PUSHD PUSHD_IGNORE_DUPS
DIRSTACKSIZE=20

# History
HISTFILE=${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history
mkdir -p -- "${HISTFILE:h}"
HISTSIZE=50000
SAVEHIST=50000
setopt EXTENDED_HISTORY HIST_VERIFY HIST_IGNORE_SPACE HIST_IGNORE_ALL_DUPS \
       HIST_REDUCE_BLANKS INC_APPEND_HISTORY HIST_FCNTL_LOCK

# Arch-specific convenience
alias sysup='sudo pacman -Syu'
alias updates='checkupdates'

# External cheatsheets
cheat() {
  emulate -L zsh
  local topic=${1:?usage: cheat <topic>}
  command curl -fL --compressed "https://cheat.sh/${topic}"
}
```

Reload:

```bash
source ~/.zshrc
```

---

## Quick Reference

### Most Useful Shortcuts

| Shortcut | Effect |
|---|---|
| `Ctrl+A` | Start of line |
| `Ctrl+E` | End of line |
| `Ctrl+U` | Delete to beginning |
| `Ctrl+K` | Delete to end |
| `Ctrl+W` | Delete previous word |
| `Alt+.` | Insert previous command’s last argument |
| `Ctrl+R` | Reverse-search history |
| `Ctrl+C` | Interrupt foreground job |
| `Ctrl+Z` | Suspend foreground job |
| `Ctrl+L` | Clear/redraw screen |

### Most Useful Commands

| Command | Effect |
|---|---|
| `cd -` | Previous directory |
| `pushd DIR` / `popd` | Directory stack navigation |
| `history` / `fc -l` | Show history |
| `jobs -l` | Show shell-managed jobs |
| `fg` / `bg` | Foreground/background control |
| `type -a CMD` | Inspect aliases/functions/binaries |
| `command -v CMD` | Find executable or builtin |
| `man CMD` | Full manual |
| `tldr CMD` | Short examples |
| `wl-copy` / `wl-paste` | Wayland clipboard |
| `systemd-run --user ...` | Detach work into the user manager |

--- 

## Bottom Line

For interactive shell work on Arch in `kitty` or `foot` with `zsh`:

1. Use the **directory stack** for temporary navigation
2. Learn the **editing bindings** you use dozens of times per day
3. Use **history search** aggressively, but configure it safely
4. Treat `bg`/`disown` as convenience, not service management
5. Prefer **functions over aliases** when arguments are involved
6. On Wayland, use **`wl-copy` / `wl-paste`**
7. On remote hosts, ensure your **terminfo matches `$TERM`**
8. On Arch, always upgrade with **`pacman -Syu`**, never partial-upgrade with `-Sy` alone
