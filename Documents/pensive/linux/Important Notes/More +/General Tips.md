# Arch Linux & Bash Shell Reference

> [!info] Scope
> This note assumes **Arch Linux userland** as of **March 2026**: GNU coreutils, findutils, grep, and **Bash 5.3+**.  
> Most operator semantics also apply to POSIX shells and zsh unless explicitly marked **Bash-specific**.

> [!note] GNU vs non-GNU systems
> Several commands below use GNU-specific behavior or options, such as:
> - `find -printf`
> - `ln -r`
> - `grep --color=auto`
>
> These are available on Arch Linux, but may differ on BSD/macOS systems.

---

## Core commands and utilities

### Launching a separate GUI application instance

Many GUI applications are **single-instance** by default. When launched again, they may send a message to the already running process instead of starting a new one. Whether a **new process** or **independent window** can be forced is **application-specific**.

#### FeatherPad example

`featherpad` supports `--standalone` to force a separate instance:

```bash
featherpad --standalone /path/to/textfile
```

> [!tip]
> Similar flags vary by application:
> - `--standalone`
> - `--new-instance`
> - `--new-window`
> - profile-specific options
>
> Always verify with:
>
> ```bash
> appname --help
> ```

> [!note]
> A **new window** is not necessarily a **new process** or a separate **profile/session**. Browser and editor behavior often depends on profile locking, IPC, or D-Bus integration.

---

### System and user identification

| Task | Command | Notes |
|---|---|---|
| Show running kernel release | `uname -r` | Shows the **currently running** kernel release. |
| Show broader kernel/platform info | `uname -srvmo` | Kernel name, release, version, machine, OS. |
| Show distro metadata | `cat /etc/os-release` | Preferred for distro name/version. |
| Show current username | `id -un` | Script-friendly. |
| Show current user's group names | `id -nG` | Script-friendly and concise. |
| Show current user's groups | `groups` | Fine for interactive use. |

> [!important]
> `uname -r` reports the **running kernel**, not necessarily the kernel package most recently installed by `pacman`. After a kernel update, `uname -r` will not change until you **boot into the new kernel**.

> [!note]
> `groups "$(whoami)"` works, but is unnecessarily indirect. On Arch/Linux, prefer:
>
> ```bash
> groups
> ```
>
> or, for scripts:
>
> ```bash
> id -nG
> ```

> [!note]
> If you add a user to a new group, existing login sessions typically do **not** gain that group immediately. Log out and back in, or start a new login session.

---

### Counting text and entries with `wc`

`wc` means **word count**, but it can count several things.

#### Common `wc` options

| Option | Meaning |
|---|---|
| `wc -l` | Count **newline characters** / lines |
| `wc -w` | Count words |
| `wc -c` | Count bytes |
| `wc -m` | Count characters |

#### Basic examples

```bash
wc -l /etc/pacman.conf
```

```bash
printf '%s\n' alpha beta gamma | wc -l
```

> [!warning]
> `wc -l` counts **newline characters**. If the last line of a file does **not** end with a newline, `wc -l` will report one fewer line than a human may visually expect.

#### Counting directory entries

Your original note used:

```bash
ls -l | wc -l
```

That is **not a reliable file count**.

Reasons:

1. `ls -l` adds a `total ...` header line.
2. `ls` output is not robust for scripting.
3. Filenames containing unusual characters can break line-based assumptions.

#### Better choices

**Quick interactive count** of entries in the current directory, excluding `.` and `..`:

```bash
ls -1A | wc -l
```

**Robust GNU/Arch method** for counting all immediate entries in the current directory:

```bash
find . -mindepth 1 -maxdepth 1 -printf . | wc -c
```

Count only regular files:

```bash
find . -mindepth 1 -maxdepth 1 -type f -printf . | wc -c
```

Count only directories:

```bash
find . -mindepth 1 -maxdepth 1 -type d -printf . | wc -c
```

> [!tip]
> Use `find ... -printf . | wc -c` when you want a count that is safe even with filenames containing spaces, tabs, or newlines.

---

### Searching text with `grep`

`grep` searches input for lines matching a pattern. By default, the pattern is a **basic regular expression**.

#### Common `grep` options

| Option | Meaning |
|---|---|
| `-i` | Ignore case |
| `-v` | Invert match; show non-matching lines |
| `-n` | Show line numbers |
| `-E` | Use extended regular expressions |
| `-F` | Match a fixed string, not a regex |
| `-r` | Recurse into directories |
| `-q` | Quiet mode; exit status only |

#### Examples

Case-insensitive search with line numbers:

```bash
grep -in 'error' /var/log/pacman.log
```

Extended regex:

```bash
grep -nE 'error|warning|failed' /var/log/pacman.log
```

Literal string search in Hyprland config:

```bash
grep -rF 'exec-once' ~/.config/hypr/
```

Invert match:

```bash
grep -v '^[[:space:]]*$' /etc/pacman.conf
```

> [!note]
> `grep` is line-oriented and ubiquitous. On Arch, for large code or config trees, **ripgrep** (`rg`, package: `ripgrep`) is usually faster and more ergonomic, but `grep` remains the baseline tool you can expect almost everywhere.

---

## Symbolic links

> [!important] Foundational filesystem skill
> Symbolic links are essential for dotfiles, config layout, application paths, and system organization.

### What a symbolic link is

A **symbolic link** (symlink) is a special filesystem entry that stores a **path** to another file or directory.

Key properties:

- It can point to a **file** or a **directory**
- It can cross **filesystem boundaries**
- It can point to a target that does **not yet exist**
- If the target disappears, the symlink becomes **broken**

#### Symlink vs hard link

| Type | Can cross filesystems | Can link directories | Stores path text | Refers to same inode |
|---|---:|---:|---:|---:|
| Symbolic link | Yes | Yes | Yes | No |
| Hard link | No | Normally no | No | Yes |

---

### Basic syntax

Create a symlink:

```bash
ln -s -- TARGET LINK_NAME
```

- `TARGET`: the existing path you want the link to point to
- `LINK_NAME`: the new symlink path to create

> [!tip]
> The order matters:
>
> ```bash
> ln -s TARGET LINK_NAME
> ```
>
> Think: “create `LINK_NAME` pointing to `TARGET`”.

---

### Common `ln` options on Arch/GNU systems

| Option | Meaning |
|---|---|
| `-s` | Create a symbolic link |
| `-f` | Remove an existing destination file/symlink first |
| `-n` | Do not dereference destination if it is a symlink to a directory |
| `-T` | Treat `LINK_NAME` as a normal path, not as a directory target |
| `-r` | Create a relative symlink |

#### Recommended patterns

Create a new symlink when destination does not exist:

```bash
ln -s -- /path/to/target /path/to/link
```

Replace an existing **file or symlink** and ensure the destination is treated as a single path:

```bash
ln -sfT -- /path/to/target /path/to/link
```

Create a relative symlink:

```bash
ln -srv -- /path/to/target /path/to/link
```

> [!note]
> `-T` is often the safest choice when your intent is “this exact path should become a symlink” rather than “place a symlink inside this directory”.

---

### Absolute vs relative symlinks

#### Absolute symlink

```bash
ln -s -- "$HOME/Downloads/Geekbench-6.4.0-Linux" "$HOME/Desktop/yothis"
```

- Easy to reason about
- Still works if accessed from anywhere
- Breaks if the target tree is moved elsewhere

#### Relative symlink

```bash
ln -srv -- "$HOME/Downloads/Geekbench-6.4.0-Linux" "$HOME/Desktop/yothis"
```

- Better when moving a directory tree **as a unit**
- More portable inside repos, dotfiles, and self-contained config trees

---

### Corrected behavior of your original example

#### Goal

You have:

- target directory: `~/Downloads/Geekbench-6.4.0-Linux`
- existing directory on desktop: `~/Desktop/yothis`

You want `~/Desktop/yothis` itself to become a symlink to the Geekbench directory.

#### Important correction

This original command is **not correct** if `~/Desktop/yothis` already exists as a **real directory**:

```bash
ln -nfs ~/Downloads/Geekbench-6.4.0-Linux ~/Desktop/yothis
```

Why:

- If `~/Desktop/yothis` is a real directory, `ln` treats it as a **target directory**
- It will attempt to place a symlink **inside** `yothis`, usually named after the basename of the target
- It will **not** replace that real directory with a symlink

#### Correct approaches

##### If `yothis` is an empty directory

```bash
rmdir -- "$HOME/Desktop/yothis"
ln -s -- "$HOME/Downloads/Geekbench-6.4.0-Linux" "$HOME/Desktop/yothis"
```

##### If `yothis` contains files and you want to keep them

```bash
mv -- "$HOME/Desktop/yothis" "$HOME/Desktop/yothis.bak"
ln -s -- "$HOME/Downloads/Geekbench-6.4.0-Linux" "$HOME/Desktop/yothis"
```

##### If `yothis` is already a file or symlink and should be replaced

```bash
ln -sfT -- "$HOME/Downloads/Geekbench-6.4.0-Linux" "$HOME/Desktop/yothis"
```

> [!warning]
> `ln -sfT` can replace a **file** or **symlink**, but it will **not** safely replace a real directory. If the destination is a real directory, remove or rename it first.

---

### Inspecting symlinks

Show the link and its raw target:

```bash
ls -l -- "$HOME/Desktop/yothis"
```

Print the stored target path:

```bash
readlink -- "$HOME/Desktop/yothis"
```

Resolve to a canonical path when the target exists:

```bash
readlink -f -- "$HOME/Desktop/yothis"
```

or:

```bash
realpath -- "$HOME/Desktop/yothis"
```

---

### Removing symlinks

Remove the symlink itself:

```bash
rm -- "$HOME/Desktop/yothis"
```

> [!warning]
> For a symlink to a directory, remove the link **without a trailing slash**:
>
> ```bash
> rm -- "$HOME/Desktop/yothis"
> ```
>
> Do **not** use:
>
> ```bash
> rm -r "$HOME/Desktop/yothis/"
> ```
>
> A trailing slash changes how the path is interpreted and can lead to confusing or dangerous behavior.

---

## Shell control operators and grouping

### Exit status fundamentals

Most shell operators use a command’s **exit status**:

- `0` = success
- non-zero = failure

You can inspect the previous command’s status with:

```bash
echo $?
```

> [!tip]
> In scripts, check exit statuses intentionally. Do not rely on human-readable output alone.

---

### Pipe: `|`

A pipe sends **standard output** (`stdout`) from the command on the left to **standard input** (`stdin`) of the command on the right.

#### Syntax

```bash
command1 | command2
```

#### Example

```bash
journalctl -b | grep -i hyprland
```

- `journalctl -b` writes log lines to stdout
- `grep -i hyprland` reads that output and filters matching lines

#### Important details

- `|` pipes **stdout only**
- **stderr** is not piped unless you redirect it

To pipe both stdout and stderr in Bash:

```bash
command1 |& command2
```

Portable equivalent:

```bash
command1 2>&1 | command2
```

Example with a build log:

```bash
make |& tee build.log
```

> [!important]
> In Bash, a pipeline’s exit status is normally the status of the **last** command in the pipeline.
>
> For scripts, enable:
>
> ```bash
> set -o pipefail
> ```
>
> so that pipeline failure is detected if **any** command in the pipeline fails.

---

### Command sequencing: `;`

A semicolon runs commands sequentially, regardless of success or failure.

#### Syntax

```bash
command1 ; command2 ; command3
```

#### Example

```bash
printf '%s\n' "Hello" ; printf '%s\n' "World"
```

Result:

```text
Hello
World
```

> [!note]
> In shell syntax, a **newline** often serves the same role as `;`. In scripts, newlines are usually more readable.

---

### Logical AND: `&&`

Run the command on the right **only if** the command on the left succeeds.

#### Syntax

```bash
command1 && command2
```

#### Example

```bash
mkdir -p -- "$HOME/build/demo" && cd -- "$HOME/build/demo"
```

- `cd` runs only if `mkdir -p` succeeds

This is the standard pattern for dependent steps.

> [!tip]
> Use `&&` when the second command only makes sense if the first one succeeded.

---

### Logical OR: `||`

Run the command on the right **only if** the command on the left fails.

#### Syntax

```bash
command1 || command2
```

#### Example

```bash
systemctl --user is-active --quiet hypridle || systemctl --user start hypridle
```

- If `hypridle` is already active, nothing happens
- If it is not active, the start command runs

Another example:

```bash
systemctl start my-service || printf '%s\n' 'Failed to start my-service' >&2
```

> [!tip]
> `||` is ideal for fallback behavior, retries, or emitting errors when a probe fails.

---

### `&&` and `||` precedence pitfall

This pattern is common but often misunderstood:

```bash
command_a && command_b || command_c
```

It means:

```bash
(command_a && command_b) || command_c
```

It does **not** mean:

> “If `command_a` succeeds, run `command_b`; otherwise run `command_c`.”

If `command_a` succeeds but `command_b` fails, `command_c` still runs.

#### Use a real `if` statement when that is the intent

```bash
if command_a; then
  command_b
else
  command_c
fi
```

> [!warning]
> Do not treat `a && b || c` as a safe `if/else` unless you are certain `b` cannot fail in a way that matters.

---

### Background execution: `&`

A trailing `&` starts a command in the background and immediately returns the shell prompt.

#### Syntax

```bash
command &
```

#### Example

```bash
firefox &
```

The terminal becomes usable immediately while Firefox continues running.

#### Important details

- Output still goes to the current terminal unless redirected
- The process is still associated with the current shell/session
- Backgrounding is **not** the same thing as turning a process into a service

#### Cleaner interactive example

```bash
firefox >/dev/null 2>&1 &
disown %%
```

- first line: start Firefox in the background and suppress terminal output
- second line: remove the current job from Bash job control

> [!note] Bash-specific
> `disown` is a Bash job-control builtin. It is mainly useful in interactive shells.

#### In scripts: capture and wait for the background PID

```bash
long_task &
pid=$!
wait "$pid"
```

- `$!` = PID of the most recently started background process
- `wait` returns that process’s exit status

#### Interactive job-control commands

| Command | Meaning |
|---|---|
| `jobs` | List jobs in current shell |
| `fg %1` | Bring job 1 to foreground |
| `bg %1` | Resume job 1 in background |
| `disown %%` | Remove current job from shell job table |

> [!important]
> For long-lived user daemons on Arch, especially desktop-session components such as notification daemons, idlers, wallpapers, or applets, prefer a **`systemd --user` service** over launching with `&`.

---

### Grouping in a subshell: `()`

Parentheses run commands in a **subshell**. Environment changes inside the group do **not** affect the parent shell.

#### Syntax

```bash
(command1 ; command2)
```

#### Example

```bash
printf '%s\n' "$PWD"
( cd /tmp && printf '%s\n' "$PWD" )
printf '%s\n' "$PWD"
```

Expected effect:

1. print current directory
2. enter `/tmp` in a subshell and print `/tmp`
3. print the original directory again

Typical subshell-isolated side effects:

- `cd`
- variable assignments
- `umask`
- shell options

---

### Grouping in the current shell: `{}`

Braces group commands **without** creating a subshell.

#### Syntax

```bash
{ command1; command2; }
```

Important syntax rules:

- spaces around braces matter
- the last command before `}` must end with `;` or a newline

#### Example

```bash
printf '%s\n' "$PWD"
{ cd /tmp && printf '%s\n' "$PWD"; }
printf '%s\n' "$PWD"
```

After this block, the current shell remains in `/tmp` if `cd` succeeded.

#### Common use: one redirection for multiple commands

```bash
{
  printf '%s\n' 'line 1'
  printf '%s\n' 'line 2'
} >output.txt
```

> [!tip]
> Use `()` when you want temporary isolation.  
> Use `{}` when you want grouping but need side effects to persist.

---

## Quick operator summary

| Symbol | Name | Effect | Important detail |
|---|---|---|---|
| `|` | Pipe | Send stdout of left command to stdin of right command | stderr is not included |
| `|&` | Pipe stdout+stderr | Send both stdout and stderr to the next command | Bash shorthand for `2>&1 \|` |
| `;` | Semicolon | Run commands sequentially | ignores previous success/failure |
| `&&` | Logical AND | Run next command only on success | based on exit status `0` |
| `||` | Logical OR | Run next command only on failure | useful for fallback |
| `&` | Background operator | Start command asynchronously | not a service manager |
| `()` | Subshell grouping | Run grouped commands in child shell | side effects do not persist |
| `{}` | Current-shell grouping | Run grouped commands in current shell | side effects persist |

---

## Arch Linux package-management note

Your original note referenced a Fedora/RHEL-style `dnf` workflow. On Arch Linux, the correct full system upgrade command is:

```bash
sudo pacman -Syu
```

This:

- refreshes package databases
- resolves upgrades
- upgrades installed packages as a single operation

> [!warning]
> Avoid:
>
> ```bash
> sudo pacman -Sy
> ```
>
> without `-u`. Refreshing databases without upgrading can create a **partial upgrade** state, which Arch explicitly does not support.

If you only want to **check** for pending updates without touching the live sync database, use `checkupdates` from `pacman-contrib`:

```bash
checkupdates
```

> [!note]
> This is especially relevant for kernel and graphics-stack updates:
> - `uname -r` shows the **currently running** kernel
> - `pacman` may have installed a newer kernel that will only be used after reboot

--- 

## Practical reminders

- Prefer `id -nG` over `groups "$(whoami)"`
- Prefer `find ... -printf . | wc -c` over `ls ... | wc -l` for accurate counts
- Prefer `grep -F` for literal strings and `grep -E` for clearer regexes
- Use `ln -s` to create links; use `ln -sfT` only when you intentionally want replacement behavior
- Remember: `()` isolates side effects, `{}` does not
- Remember: `&` backgrounds a process; it does **not** manage it like `systemd --user`

