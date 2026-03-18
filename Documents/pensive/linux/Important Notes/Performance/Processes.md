# Process Management on Arch Linux — Part 1: Discovery, Inspection, and Introspection

> [!info] Scope
> This part covers how Linux processes are represented, how to find them, how to inspect their metadata, threads, ancestry, cgroup membership, files, sockets, and `/proc` state, and how to build reliable inspection workflows on Arch Linux.
>  
> Part 2 covers signals, `kill`/`pkill`/`killall`, job control, waiting, termination strategy, and Bash `trap` patterns.

> [!note] Accuracy target
> Commands and behavior in this note are aligned with contemporary Arch Linux userspace as of **March 2026**, primarily:
> - `procps-ng`: `ps`, `pgrep`, `pkill`, `pidwait`, `top`
> - `util-linux`: external `kill`
> - `psmisc`: `pstree`, `killall`, `fuser`
> - `systemd`: `systemctl`, `systemd-cgls`, `systemd-cgtop`, `loginctl`

> [!note] Related
> Process analysis is tightly coupled to [[CPU]] behavior and [[Power Management]], because every runnable or blocked task consumes scheduler attention, memory, I/O bandwidth, and often wakeup budget.

---

## 1. Tooling on Arch Linux

Most of the core tooling is already present on a normal Arch installation. Optional inspection tools are worth installing explicitly if missing.

```bash
sudo pacman -S lsof sysstat htop
```

### Core tool origin

| Tool | Package | Primary use |
|---|---|---|
| `ps`, `pgrep`, `pkill`, `pidwait`, `top` | `procps-ng` | Process snapshots, matching, live inspection |
| `kill` | `util-linux` | External signal-sending command |
| `pstree`, `killall`, `fuser` | `psmisc` | Tree view, name-based killing, file/socket ownership |
| `systemctl`, `systemd-cgls`, `systemd-cgtop`, `loginctl` | `systemd` | Service, cgroup, and session-aware inspection |
| `ss` | `iproute2` | Socket inspection |
| `lsof` | `lsof` | Open files, sockets, deleted binaries/libraries |
| `pidstat` | `sysstat` | Per-process and per-thread live metrics |

> [!warning] Process inspection is inherently racy
> Every process lookup is a snapshot. A PID can exit and be reused between commands. Never assume that a PID identified earlier still refers to the same process later unless you re-verify it.

> [!note] Visibility is not absolute
> Output can be limited by:
> - permissions
> - `procfs` mount options such as `hidepid=`
> - namespaces and containers
> - ptrace-related restrictions
> - whether a tool can read a specific `/proc/<pid>` file for that target

---

## 2. Linux process model: what you are actually looking at

A technically correct mental model prevents most misinterpretation.

### 2.1. Process vs thread vs task

On Linux, what users casually call a “process” is usually a **thread group**. Individual threads are also schedulable kernel tasks.

- **PID**: process ID; in normal user-facing tools this usually means the **thread-group leader**
- **TID / LWP**: thread ID of an individual thread
- **TGID**: thread-group ID; for the leader, `TGID == PID`
- `/proc/<pid>/task/`: one directory per thread in that process

Practical consequences:

- `ps` normally shows processes; `ps -L` shows threads
- a multithreaded program can consume CPU on many cores at once
- one “application” may appear as one process with many thread IDs

### 2.2. Parentage and job-control identifiers

Several identifiers describe different relationships:

| Identifier | Meaning | Why it matters |
|---|---|---|
| **PID** | Process ID | Primary handle for inspection and signaling |
| **PPID** | Parent PID | Who spawned it most recently |
| **PGID** | Process Group ID | Shell pipelines and job control |
| **SID** | Session ID | Login/session grouping |
| **TTY** | Controlling terminal | Whether the process is tied to a terminal |

Important nuance:

- **PPID is not service ownership.** Parentage can change after reparenting, supervision, `exec`, double-forking, or parent exit.
- On a `systemd` system, **cgroup membership** is often a better answer to “what owns this process?” than PPID.

### 2.3. Cgroups and systemd units

Arch Linux uses `systemd`, and on standard systems that means **cgroup v2** is the normal resource hierarchy.

Every process belongs to a cgroup. This is often more operationally meaningful than ancestry:

- **process tree** answers: *who spawned this?*
- **cgroup tree** answers: *which service/session/scope owns this?*

This matters especially for:

- daemons
- user services
- sandboxed applications
- modern desktop sessions
- Hyprland/UWSM-driven user sessions, where user units and scopes often explain process placement better than raw PPID chains

### 2.4. Process states

The `STAT` or `S` columns describe scheduler state.

#### Primary state codes

| Code | Meaning | Operational interpretation |
|---|---|---|
| `R` | Running or runnable | On CPU or waiting on the run queue |
| `S` | Interruptible sleep | Waiting for an event; normal idle waiting state |
| `D` | Uninterruptible sleep | Usually blocked in I/O; often cannot react until the wait finishes |
| `T` | Stopped by job control | Suspended, typically from terminal job control |
| `t` | Stopped by debugger/tracing | Paused under tracing/debugging |
| `Z` | Zombie / defunct | Already exited; waiting for parent to reap it |
| `I` | Idle kernel thread | Kernel worker thread in idle state |
| `X` | Dead | Should rarely or never be seen |
| `W` | Paging | Obsolete on modern Linux |

#### Common state modifiers

| Modifier | Meaning |
|---|---|
| `<` | High-priority |
| `N` | Low-priority / nice |
| `L` | Locked pages in memory |
| `s` | Session leader |
| `l` | Multithreaded |
| `+` | Foreground process group |

Examples:

- `Ss` = sleeping session leader
- `Rl` = running multithreaded process
- `S+` = sleeping process in the foreground terminal job
- `Z` = zombie

> [!warning] A zombie is already dead
> A zombie cannot be “killed” in the usual sense. The fix is to investigate the parent that failed to reap it, or wait for the parent to exit so it can be reaped by PID 1 / init or another subreaper.

### 2.5. CPU and memory fields are easy to misread

Two common mistakes:

1. **`%CPU` in `ps` is not an instantaneous live rate.**  
   It is based on CPU time accumulated over the process lifetime relative to elapsed runtime.
2. **`RSS` is not full memory cost.**  
   It excludes some kernel bookkeeping and does not fully explain shared mappings.

Key memory terms:

| Field | Meaning | Caveat |
|---|---|---|
| `RSS` | Resident set size | Physical non-swapped memory currently mapped |
| `VSZ` | Virtual memory size | Includes mappings that may not be resident |
| `PSS` | Proportional set size | Better for shared-memory attribution |
| `USS` | Unique set size | Memory not shared with other processes |

> [!note] On multicore systems, CPU usage can exceed 100%
> A multithreaded process can legitimately consume more than one core at the same time.

---

## 3. First-response workflow

When a process is misbehaving, use the same sequence every time.

### 3.1. Minimal workflow

1. **Find the target**
   - `pgrep -a -f pattern`
   - or `ps -ww -eo ... | grep ...` if you truly need a broad visual sweep

2. **Inspect identity and state**
   - PID, PPID, PGID, SID, user, terminal, state, elapsed time, CPU, memory, command line

3. **Inspect ancestry**
   - `pstree -aps <PID>`

4. **Inspect threads**
   - `ps -L -p <PID> ...`
   - or `top -H -p <PID>`

5. **Inspect `/proc/<pid>` directly**
   - `status`, `cmdline`, `exe`, `cgroup`, `fd`, `smaps_rollup`, `io`, `limits`

6. **Correlate with systemd**
   - `ps -o unit,uunit,slice,cgroup,cmd -p <PID>`
   - `systemd-cgls`
   - `systemctl status <unit>`

7. **Inspect files and sockets**
   - `lsof -p <PID>`
   - `ss -tpn`
   - `fuser -v`

### 3.2. One-PID forensic bundle

```bash
pid=1234

ps -q "$pid" -ww -o pid,ppid,pgid,sid,user,tty,stat,lstart,etime,time,%cpu,%mem,nlwp,psr,cmd
pstree -aps "$pid"
ps -L -p "$pid" -o pid,tid,psr,pcpu,stat,wchan:24,comm --sort=-pcpu

sed -n '1,120p' "/proc/$pid/status"
readlink -f "/proc/$pid/exe"
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
cat "/proc/$pid/cgroup"
ls -l "/proc/$pid/fd"
```

---

## 4. `ps`: authoritative process snapshots

`ps` is the canonical snapshot tool. It reads process information from `procfs` and formats it in many different views.

## 4.1. Best practices for `ps`

### Use explicit formats for serious work

For interactive use, `ps aux` is fine on Linux. For reliable analysis and scripts, prefer explicit columns:

```bash
ps -ww -eo pid,ppid,pgid,sid,user,tty,stat,etime,%cpu,%mem,cmd --sort=pid
```

Why:

- stable columns
- predictable ordering
- no dependency on default format personality
- easier post-processing

### Prefer `-eo` / `-o` over default output

The default output changes based on option style and `ps` personality.

For scripts, explicit is safer:

```bash
env -u PS_FORMAT -u PS_PERSONALITY ps -ww -eo pid,ppid,stat,args --sort=pid
```

### Avoid `ps -aux`

`ps aux` is valid BSD-style syntax on Linux.  
`ps -aux` is a historical ambiguity and should be avoided.

### Use wide output when you care about full command lines

```bash
ps -ww -eo pid,args
```

Without wide output, long command lines may be truncated.

### Suppress headers explicitly

For machine-friendly output, prefer:

```bash
ps -q 1234 -o pid=,ppid=,stat=,cmd=
```

or

```bash
ps --no-headers -q 1234 -o pid,ppid,stat,cmd
```

`--no-headers` is clearer than `h`, whose behavior is historically personality-sensitive.

---

## 4.2. High-value `ps` commands

| Command | Use | Notes |
|---|---|---|
| `ps aux` | Quick human overview | Good interactively; not ideal for scripts |
| `ps -ef` | Full system snapshot | Common SysV-style view |
| `ps -ww -eo ...` | Stable custom output | Preferred for repeatable analysis |
| `ps axjf` | Process hierarchy | BSD tree with job/session fields |
| `ps -ejH` | Process hierarchy | SysV tree view alternative |
| `ps -eLf` | Thread inspection | Full thread listing across the system |
| `ps -q <PID> -o ...` | Exact PID lookup | Quick mode for known PID(s) |
| `ps -C <name> -o ...` | Match executable name | Matches command name, not full argv |
| `ps --forest -eo ...` | Tree view with custom columns | Often clearer than legacy presets |

> [!note] `ps -C` and the old “15-character limit”
> Older Linux lore often says command-name matching is limited to 15 characters. For modern `ps -C` in `procps-ng`, that old truncation limitation no longer applies in the same way.  
> This is **not** the same as `pgrep`, which still matches the short process name from `/proc/<pid>/stat` unless you use `-f`.

---

## 4.3. Most useful output specifiers

These are the fields worth memorizing.

| Specifier | Meaning | Notes |
|---|---|---|
| `pid` | Process ID | Thread-group leader in normal process view |
| `ppid` | Parent PID | Can change after reparenting |
| `pgid` | Process group ID | Important for shells and pipelines |
| `sid` | Session ID | Login/session grouping |
| `user` / `euser` | Effective user | Usually what you want operationally |
| `ruid` / `ruser` | Real user | Distinct from effective user |
| `tty` | Controlling terminal | `?` means none |
| `stat` | Process state + modifiers | Prefer this over single-letter `s` |
| `etime` | Elapsed wall time | Since start |
| `time` | Cumulative CPU time | Not wall time |
| `%cpu` / `pcpu` | CPU usage | Lifetime-based, not instantaneous |
| `%mem` / `pmem` | RSS as % of RAM | Not total footprint |
| `rss` | Resident memory KiB | Physical memory in use |
| `vsz` | Virtual size KiB | Can be large and misleading alone |
| `psr` | Last CPU run on | Not CPU affinity |
| `class` / `policy` | Scheduling class | `TS`, `FF`, `RR`, `DLN`, etc. |
| `rtprio` | Realtime priority | Relevant only for RT scheduling |
| `ni` | Nice value | Lower is more favored |
| `nlwp` | Number of threads | Useful with multithreaded apps |
| `tid` / `lwp` | Thread ID | Use with `-L` |
| `wchan` | Kernel wait channel | Valuable for blocked tasks |
| `comm` | Executable name only | Shorter, cleaner than full args |
| `args` / `cmd` / `command` | Full command line | Can be modified by the process itself |
| `exe` | Executable path | Often more authoritative than `args` |
| `cgroup` / `cgname` | Cgroup membership | Helpful on systemd systems |
| `unit` / `uunit` / `slice` | systemd metadata | Build-dependent, usually available on Arch |
| `fds` | Open file descriptor count | Quick descriptor pressure hint |
| `oom` / `oomadj` | OOM score data | Diagnostic, not resource usage |

### Command-line fields: `comm` vs `args` vs `exe`

| Field | What it shows | Best use |
|---|---|---|
| `comm` | Executable name only | Clean process identity column |
| `args` | Full argv string | Operational context, arguments, wrappers |
| `exe` | Path to executable inode | Confirm actual binary on disk |

Use cases:

- Want a compact name column: `comm`
- Want exact launch arguments: `args`
- Want to know which binary is actually executing: `/proc/<pid>/exe` or `ps -o exe=`

---

## 4.4. Practical `ps` patterns

### Full system view with stable columns

```bash
ps -ww -eo user,pid,ppid,pgid,sid,tty,stat,etime,%cpu,%mem,rss,vsz,comm,args --sort=user,pid
```

### Top CPU consumers

```bash
ps -ww -eo pid,user,ppid,stat,etime,%cpu,%mem,psr,comm,args --sort=-%cpu,-%mem | head -n 20
```

### Top memory consumers

```bash
ps -ww -eo pid,user,ppid,stat,etime,%mem,rss,vsz,comm,args --sort=-%mem,-rss | head -n 20
```

### Exact PID inspection

```bash
pid=1234
ps -q "$pid" -ww -o pid,ppid,pgid,sid,user,tty,stat,start,etime,time,%cpu,%mem,nlwp,cmd
```

### Exact executable-name match

```bash
ps -C sshd -o pid,ppid,stat,etime,cmd
```

### Forest view with custom columns

```bash
ps -ww -eo pid,ppid,pgid,sid,tty,stat,args --forest
```

### Thread view for one process

```bash
pid=1234
ps -L -p "$pid" -o pid,tid,nlwp,psr,pcpu,stat,wchan:24,comm --sort=-pcpu
```

### Show only zombie processes

```bash
ps -eo pid,ppid,stat,etime,cmd | awk '$3 ~ /Z/'
```

### Show tasks blocked in uninterruptible sleep

```bash
ps -eo pid,ppid,stat,wchan:24,etime,cmd | awk '$3 ~ /D/'
```

### Show systemd/cgroup metadata when available

```bash
pid=1234
ps -p "$pid" -o pid,unit,uunit,slice,cgroup,cmd
```

> [!warning] `ps` CPU numbers are not live sampling
> If you need time-based rates rather than lifetime-averaged snapshots, switch to `top`, `pidstat`, or another live sampler.

---

## 5. `pgrep`: the preferred process finder

If the goal is “find the right PID(s)”, `pgrep` is usually better than `ps | grep`.

### Why `pgrep` is better than `ps | grep`

`pgrep` gives you:

- exit status you can script against
- cleaner PID-oriented output
- matching by user, parent, terminal, session, state, namespace, and more
- no accidental match on the `grep` process itself
- direct support for full command lines with `-f`

### 5.1. Matching semantics you must know

By default, `pgrep` matches against the process **name**, not the full command line.

That name comes from `/proc/<pid>/stat`, and it is effectively limited to the short command name.

> [!warning] `pgrep` name matching is still limited
> If you need to match:
> - long executable names
> - wrapper scripts
> - specific arguments
> - full interpreter command lines  
> use **`pgrep -f`** to match the full command line from `/proc/<pid>/cmdline`.

Also:

- patterns are **extended regular expressions**
- quote patterns so the shell does not interpret them first
- use **`-x`** for exact matches

### 5.2. High-value `pgrep` options

| Option | Meaning | Use case |
|---|---|---|
| `-a` | List full command line with PID | Best general human-readable mode |
| `-l` | List process name with PID | Compact output |
| `-f` | Match full command line | Needed for interpreters/wrappers/long names |
| `-x` | Exact match | Avoid partial-name surprises |
| `-i` | Ignore case | Case-insensitive matching |
| `-u` | Match effective user | Restrict to a user |
| `-U` | Match real user | Rare but precise |
| `-P` | Match parent PID | Children of a given process |
| `-g` | Match process group | Job-control or pipeline analysis |
| `-s` | Match session ID | Session-oriented filtering |
| `-t` | Match controlling terminal | TTY-focused inspection |
| `-r` | Match run states | Example: only `D` or `Z` tasks |
| `-n` | Newest match only | Find most recently started instance |
| `-o` | Oldest match only | Find earliest instance |
| `-O <secs>` | Older than N seconds | Age filtering |
| `-c` | Count matches | For scripts/quick checks |
| `-A` | Ignore ancestors | Very useful under `sudo` or wrappers |
| `-w` | Show thread IDs | Thread-level listing in `pgrep` mode |
| `--env name[=value]` | Match environment variables | Session/display-aware inspection |
| `--ns <pid>` | Match same namespaces as PID | Container/namespace diagnostics |
| `--nslist ...` | Limit namespace types | Narrow namespace comparisons |
| `--cgroup name,...` | Match cgroup v2 names | Cgroup-aware lookup |

> [!note] `pgrep` exit codes
> - `0`: one or more matches
> - `1`: no matches
> - `2`: syntax error
> - `3`: fatal error

### 5.3. Practical `pgrep` patterns

### Exact process name

```bash
pgrep -a -x sshd
```

### Full command-line match

```bash
pgrep -af 'python .*server\.py'
```

### Current user's matching processes

```bash
pgrep -u "$USER" -af 'firefox|chromium'
```

### Most recent matching instance

```bash
pgrep -n -a -x pipewire
```

### Oldest matching instance

```bash
pgrep -o -a -x pipewire
```

### Ignore `sudo`/shell ancestors in a wrapped invocation

```bash
pgrep -A -u "$USER" -af 'waybar|Hyprland'
```

### Match by terminal

```bash
pgrep -t pts/0 -a -f 'bash|zsh|fish'
```

### Match only zombie or D-state tasks of a name pattern

```bash
pgrep -r Z,D -a -f 'myworker|mydaemon'
```

### Match by environment variable

```bash
pgrep --env WAYLAND_DISPLAY -af 'Hyprland|waybar|xdg-desktop-portal'
```

### Match by namespace similarity

```bash
pid=1234
pgrep --ns "$pid" --nslist pid,mnt,net -af 'bash|sh|systemd'
```

> [!tip] Prefer `pgrep -a -x name` over `ps aux | grep name`
> It is cleaner, more exact, and easier to script.

### 5.4. Safe Bash PID collection

For scripting, collect results into an array instead of parsing ad hoc whitespace.

```bash
#!/usr/bin/env bash

set -o nounset -o pipefail

mapfile -t pids < <(pgrep -x waybar)

if ((${#pids[@]} == 0)); then
  printf 'No waybar process found\n' >&2
  exit 1
fi

pid_csv=$(IFS=,; printf '%s' "${pids[*]}")
ps -fp "$pid_csv"
```

This avoids brittle `grep` pipelines and gives you clean behavior when zero, one, or many matches exist.

---

## 6. `pstree`: ancestry and structural visualization

`pstree` is purpose-built for process-tree visualization. It is usually the fastest way to answer:

- Who launched this?
- What is this process attached to?
- Is this thread-heavy process hiding under a supervisor?
- Did this worker reparent after its parent exited?

### 6.1. What `pstree` shows well

- parent/child ancestry
- compact visualization of duplicate branches
- thread grouping
- user transitions
- process-group IDs
- namespace transitions

### 6.2. High-value options

| Option | Meaning | Use case |
|---|---|---|
| `-a` | Show full command-line arguments | Distinguish similar instances |
| `-p` | Show PIDs | Essential for real diagnostics |
| `-u` | Show UID transitions | User/session changes |
| `-g` | Show PGIDs | Job-control and grouping |
| `-s` | Show parents of a PID only | Fast ancestry lookup |
| `-t` | Show full thread names | Thread-aware inspection |
| `-T` | Hide threads | Cleaner process-only tree |
| `-n` | Sort siblings by PID | Stable ordering |
| `-c` | Disable subtree compaction | Preserve duplicate branches explicitly |
| `-H <pid>` | Highlight a target PID | Interactive focus |
| `-N <ns>` | Separate trees by namespace | Container/namespace work |
| `-S` | Show namespace transitions | Advanced namespace diagnostics |

### 6.3. Practical `pstree` usage

### Whole-system overview

```bash
pstree -ap
```

### Most useful general diagnostic view

```bash
pstree -apu
```

### Show full ancestry of one PID

```bash
pid=1234
pstree -aps "$pid"
```

### Show process group IDs too

```bash
pstree -apg "$pid"
```

### Hide thread clutter

```bash
pstree -T -ap
```

### Thread-aware view

```bash
pstree -atp "$pid"
```

> [!note] `pstree` compacts identical branches by default
> This is useful visually, but it can obscure multiplicity. Use `-c`, `-a`, or `-p` when you need a less compact view.

> [!warning] Process tree != cgroup tree
> `pstree` answers ancestry.  
> `systemd-cgls` answers service/session/cgroup ownership.  
> On systemd-based Arch systems, you often need both.

---

## 7. `/proc/<pid>`: direct kernel-backed inspection

When you need the authoritative source, inspect `/proc/<pid>` directly.

### 7.1. Most important `/proc/<pid>` entries

| Path | Use | Notes |
|---|---|---|
| `/proc/<pid>/status` | Human-readable summary | IDs, memory summary, signals, thread count, seccomp, capabilities |
| `/proc/<pid>/stat` | Machine-oriented raw fields | Compact but awkward to parse safely |
| `/proc/<pid>/cmdline` | Full command line | NUL-separated, not newline-separated |
| `/proc/<pid>/comm` | Short command name | Good for quick identity |
| `/proc/<pid>/exe` | Executable symlink | Best way to verify actual binary |
| `/proc/<pid>/cwd` | Current working directory | Symlink |
| `/proc/<pid>/root` | Process root directory | Relevant for chroots/containers |
| `/proc/<pid>/fd/` | Open file descriptors | Symlinks to referenced objects |
| `/proc/<pid>/fdinfo/` | Per-FD metadata | Offsets, flags, mount IDs, more |
| `/proc/<pid>/task/` | Per-thread directories | One subdir per thread |
| `/proc/<pid>/cgroup` | Cgroup membership | Service/session ownership clues |
| `/proc/<pid>/mountinfo` | Mount namespace view | Crucial for container/sandbox diagnostics |
| `/proc/<pid>/maps` | Memory mappings | Full map list |
| `/proc/<pid>/smaps_rollup` | Aggregated memory accounting | Prefer this before full `smaps` |
| `/proc/<pid>/io` | I/O counters | Read/write accounting |
| `/proc/<pid>/limits` | Resource limits | RLIMITs, file limits, etc. |
| `/proc/<pid>/wchan` | Wait channel | Meaningful for sleeping tasks |
| `/proc/<pid>/sched` | Scheduler details | Advanced scheduler diagnostics |
| `/proc/<pid>/environ` | Environment variables | NUL-separated, often sensitive |
| `/proc/<pid>/ns/` | Namespace symlinks | Compare namespace membership |
| `/proc/<pid>/oom_score` | OOM score | Why OOM killer may prefer it |
| `/proc/<pid>/oom_score_adj` | OOM adjustment | Manual biasing signal for OOM selection |

### 7.2. Preferred files to inspect first

For most cases, start here:

1. `/proc/<pid>/status`
2. `/proc/<pid>/cmdline`
3. `/proc/<pid>/exe`
4. `/proc/<pid>/cgroup`
5. `/proc/<pid>/fd/`
6. `/proc/<pid>/smaps_rollup`
7. `/proc/<pid>/io`
8. `/proc/<pid>/limits`

### 7.3. Reliable shell snippets

```bash
pid=1234

# Human-readable summary
sed -n '1,160p' "/proc/$pid/status"

# Short command name
cat "/proc/$pid/comm"

# Full command line (NUL-separated)
tr '\0' ' ' < "/proc/$pid/cmdline"; echo

# Environment variables (sensitive)
tr '\0' '\n' < "/proc/$pid/environ" | sort

# Executable, cwd, and root
readlink -f "/proc/$pid/exe"
readlink -f "/proc/$pid/cwd"
readlink -f "/proc/$pid/root"

# Cgroup membership
cat "/proc/$pid/cgroup"

# Open file descriptors
ls -l "/proc/$pid/fd"

# Per-FD metadata example
sed -n '1,40p' "/proc/$pid/fdinfo/0"

# Memory summary
cat "/proc/$pid/smaps_rollup"

# I/O accounting
cat "/proc/$pid/io"

# Resource limits
cat "/proc/$pid/limits"

# Wait channel
cat "/proc/$pid/wchan"

# Scheduler details
sed -n '1,120p' "/proc/$pid/sched"

# Namespace membership
ls -l "/proc/$pid/ns"
```

> [!warning] `cmdline` and `environ` are NUL-separated
> Do not parse them as line-oriented text without converting `\0` first.

> [!warning] `stat` is easy to parse incorrectly
> The second field is the command name in parentheses and can contain spaces or unusual characters. Prefer `status` unless you specifically need raw `stat` fields.

### 7.4. Interpreting `/proc/<pid>/exe`

`/proc/<pid>/exe` is one of the most valuable entries on Arch.

It tells you which executable inode is actually running, which matters when:

- the command line is misleading
- a wrapper script launched the real binary
- a binary was replaced by a package upgrade
- a process is still running an unlinked executable

Example:

```bash
pid=1234
readlink -f "/proc/$pid/exe"
```

If you see something like:

```text
/usr/bin/foo (deleted)
```

the process is still executing a binary that no longer exists at that pathname.

This is common after package upgrades on rolling-release systems.

### 7.5. Thread inspection via `/proc/<pid>/task`

Each thread has its own directory:

```bash
pid=1234
ls "/proc/$pid/task"
```

Inspect one thread directly:

```bash
tid=1234
sed -n '1,120p' "/proc/$pid/task/$tid/status"
```

Useful facts:

- thread IDs live under `/proc/<pid>/task/<tid>/`
- many fields differ per thread
- for most day-to-day work, `ps -L` is easier than manual traversal

---

## 8. Live inspection beyond snapshots

`ps` tells you what is true now.  
Live tools tell you what changes over time.

### 8.1. `top`

Use `top` when you need rapidly updating metrics.

```bash
top
```

For one PID:

```bash
top -p 1234
```

For threads of one PID:

```bash
top -H -p 1234
```

Useful interactive toggles in `top`:

- `H`: show threads
- `c`: show full command line
- `P`: sort by CPU
- `M`: sort by memory

### 8.2. `pidstat`

`pidstat` is excellent for interval-based, script-friendly sampling.

```bash
pidstat -dur -p 1234 1
```

Meaning:

- `-u`: CPU
- `-d`: I/O
- `-r`: memory
- `-p 1234`: target PID
- `1`: sample every second

Thread-level view:

```bash
pidstat -t -p 1234 1
```

### 8.3. `htop`

If installed, `htop` is often the fastest interactive inspector for humans.

```bash
htop
```

Useful when you want:

- filtering
- tree view
- per-thread inspection
- interactive sorting and killing

---

## 9. Files, sockets, and object ownership

A process is often best understood by what it has open.

## 9.1. `lsof`

`lsof` decodes open files, sockets, deleted inodes, current working directories, libraries, and more.

### Open files for a process

```bash
lsof -p 1234
```

### Open network sockets for a process

```bash
lsof -i -p 1234
```

### Find deleted-but-still-open files

```bash
sudo lsof +L1
```

For one process only:

```bash
sudo lsof +L1 -p 1234
```

This is extremely useful after package upgrades on Arch to find processes still holding deleted executables or libraries.

## 9.2. `ss`

Prefer `ss` over legacy `netstat`.

### Listening TCP sockets with owning process info

```bash
ss -lptn
```

### Who owns port 8080

```bash
ss -lptn 'sport = :8080'
```

> [!note] Process names for sockets may require elevated privileges
> For processes owned by other users, root is often needed to see full socket ownership details.

## 9.3. `fuser`

`fuser` answers “what process is using this file, mount, or socket?”

### Which process is using a file

```bash
fuser -v /path/to/file
```

### Which process is using a TCP port

```bash
fuser -v 8080/tcp
```

---

## 10. systemd-aware inspection on Arch Linux

On Arch, process management is incomplete if you ignore `systemd`.

This is especially true for:

- services
- user services
- app scopes
- desktop sessions
- Hyprland/UWSM-managed user environments

### 10.1. Service-aware inspection

If you already know the unit:

```bash
systemctl status sshd.service
systemctl --user status pipewire.service
```

### 10.2. Cgroup tree view

Use `systemd-cgls` to inspect by cgroup hierarchy rather than parent PID:

```bash
systemd-cgls
```

This often answers ownership questions that `pstree` cannot.

### 10.3. Live cgroup resource usage

```bash
systemd-cgtop
```

This is often more useful than per-PID views when a service spawns many workers.

### 10.4. Mapping a PID to systemd metadata

If supported by your `ps` build:

```bash
pid=1234
ps -p "$pid" -o pid,unit,uunit,slice,cgroup,cmd
```

Interpretation:

- **`unit`**: system unit
- **`uunit`**: user unit
- **`slice`**: slice placement
- **`cgroup`**: raw cgroup path/name data

### 10.5. Session-aware inspection

For login/session context:

```bash
loginctl list-sessions
loginctl session-status "$XDG_SESSION_ID"
```

This is particularly valuable on desktop systems where user services, portals, compositors, and launchers are attached to a login session rather than a single obvious parent process.

> [!tip] On Hyprland/UWSM systems
> User-session processes are often easier to reason about through:
> - `systemctl --user status`
> - `loginctl session-status`
> - `systemd-cgls`
>  
> than through PPID chains alone.

---

## 11. Operational recipes

### 11.1. Find the exact command line for one PID

```bash
pid=1234
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
```

### 11.2. Find the real executable path

```bash
pid=1234
readlink -f "/proc/$pid/exe"
```

### 11.3. Show full ancestry of a PID

```bash
pid=1234
pstree -aps "$pid"
```

### 11.4. Show all threads of a process sorted by CPU

```bash
pid=1234
ps -L -p "$pid" -o pid,tid,psr,pcpu,stat,wchan:24,comm --sort=-pcpu
```

### 11.5. Show processes in `D` state with wait channel

```bash
ps -eo pid,ppid,stat,wchan:24,etime,cmd | awk '$3 ~ /D/'
```

### 11.6. Show zombie processes and their parents

```bash
ps -eo pid,ppid,stat,etime,cmd | awk '$3 ~ /Z/'
```

### 11.7. Show which systemd unit owns a PID

```bash
pid=1234
ps -p "$pid" -o pid,unit,uunit,slice,cgroup,cmd
```

### 11.8. Show open files for a PID

```bash
lsof -p 1234
```

### 11.9. Show sockets held by a PID

```bash
lsof -i -p 1234
```

### 11.10. Show which process owns a listening port

```bash
ss -lptn 'sport = :8080'
```

### 11.11. List all processes for one executable name

```bash
ps -C systemd -o pid,ppid,stat,etime,cmd
```

### 11.12. Prefer `pgrep` over `ps | grep`

```bash
pgrep -a -x waybar
pgrep -af 'python .*server\.py'
```

### 11.13. Combine `pgrep` with `ps`

```bash
ps -fp "$(pgrep -d, -x bash)"
```

### 11.14. Find deleted files still held open after upgrades

```bash
sudo lsof +L1
```

### 11.15. Check cgroup placement of a desktop process

```bash
pid=1234
cat "/proc/$pid/cgroup"
ps -p "$pid" -o pid,unit,uunit,slice,cgroup,cmd
```

---

## 12. Reliability rules

### 12.1. Prefer these habits

- Use **`pgrep`** for lookup
- Use **explicit `ps -o` fields** for inspection
- Use **`pstree -aps`** for ancestry
- Use **`/proc/<pid>`** when you need the kernel’s view
- Use **`systemd-cgls`** and **`ps -o unit,...`** for service ownership
- Use **`lsof`**, **`ss`**, and **`fuser`** to inspect external objects
- Use **`top`** or **`pidstat`** for live rates

### 12.2. Avoid these habits

- `ps -aux`
- relying on default `ps` output in scripts
- assuming PPID implies service ownership
- assuming `ps %CPU` is instantaneous
- parsing `/proc/<pid>/stat` casually
- using `ps aux | grep name` when `pgrep` would be exact
- assuming command line text is the real executable
- ignoring cgroups on systemd-based systems

---

## 13. Part 1 summary

Part 1 establishes the inspection side of process management:

- how Linux identifies processes, threads, groups, sessions, and cgroups
- how to use `ps`, `pgrep`, and `pstree` correctly
- how to read `/proc/<pid>` directly
- how to correlate processes with open files, sockets, sessions, and systemd units
- how to distinguish ancestry from ownership on Arch Linux

Part 2 continues with **signals, `kill` semantics, `pkill`/`killall`, safe termination strategy, job control, waiting, and robust Bash signal handling**.


# Process Management on Arch Linux — Part 2: Signals, Termination, Job Control, and Bash `trap` Patterns

> [!info] Scope
> This part covers how Linux signals actually behave, how to terminate or control processes safely, when to use `kill` vs `pkill` vs `killall` vs `pidwait`, how shell job control really works, and how to write robust Bash 5.3+ scripts that clean up correctly under `INT`, `TERM`, and related conditions.
>
> Part 1 covered discovery, inspection, threads, `/proc`, cgroups, files, sockets, and systemd-aware introspection.

> [!note] Accuracy target
> Commands and behavior in this note are aligned with contemporary Arch Linux userspace as of **March 2026**, primarily:
> - `procps-ng`: `pgrep`, `pkill`, `pidwait`, `ps`, `top`
> - `util-linux`: external `kill`
> - `psmisc`: `killall`, `pstree`, `fuser`
> - `systemd`: `systemctl`, `loginctl`

> [!warning] Prefer the correct control plane
> If a process is owned by:
> - a **systemd unit**
> - a **user unit/scope**
> - a **desktop session**
> - an **application-specific control interface**
>
> then that control plane is usually better than raw PID signaling.
>
> Examples:
> - use `systemctl stop` instead of killing a service’s main PID
> - use `systemctl --user restart` for user services
> - use a compositor or daemon’s own IPC/CLI for graceful reload/exit when available
> - use `loginctl terminate-session` for a full login/session teardown

---

## 1. Signal fundamentals

Signals are asynchronous notifications delivered by the kernel to a process.

They are used for:

- graceful termination
- forced termination
- reload requests
- stop/resume control
- user-defined actions
- reporting fatal conditions
- terminal job control

### 1.1. What a signal can do

For any given signal, a process may:

- accept the **default action**
- install a **handler**
- **ignore** it
- **block** it temporarily

Important exceptions:

- **`SIGKILL`** cannot be caught, blocked, or ignored
- **`SIGSTOP`** cannot be caught, blocked, or ignored

### 1.2. Default action categories

Signal default actions are usually one of:

| Category | Meaning |
|---|---|
| **Term** | terminate the process |
| **Core** | terminate and attempt a core dump |
| **Stop** | stop/suspend execution |
| **Cont** | resume if stopped |
| **Ign** | ignore by default |

### 1.3. Use signal names, not numbers

Signal numbers can vary by architecture and environment.

Prefer:

```bash
kill -TERM 1234
kill -HUP 1234
kill -KILL 1234
```

over hardcoded numeric forms.

If you need the current system’s mapping:

```bash
/usr/bin/kill -L
```

or:

```bash
kill -l
```

> [!note] Builtin vs external `kill`
> In shells such as Bash and Zsh, `kill` is usually a **shell builtin**.
>
> Use the external util-linux implementation when you need util-linux-specific features:
>
> ```bash
> /usr/bin/kill --version
> type -a kill
> ```

### 1.4. Common operational signals

| Signal | Default | Typical use | Important nuance |
|---|---|---|---|
| `SIGTERM` | Term | Polite shutdown request | Preferred first stop signal for scripts/services |
| `SIGINT` | Term | Interactive interrupt (`Ctrl+C`) | Common for foreground jobs, less common for service control |
| `SIGHUP` | Term | Traditionally “hangup”; often used for reload | **Reload is only a convention** if the app handles it |
| `SIGQUIT` | Core | Quit and often produce a core dump | Useful for forensic termination if supported |
| `SIGABRT` | Core | Abort and often produce a core dump | Often used when a dump is more useful than a plain stop |
| `SIGKILL` | Term | Immediate forced termination | No cleanup, no handler, no flush, no graceful exit |
| `SIGSTOP` | Stop | Immediate stop/freeze | Uncatchable; stronger than terminal suspend |
| `SIGTSTP` | Stop | Interactive suspend (`Ctrl+Z`) | Catchable job-control stop |
| `SIGCONT` | Cont | Resume a stopped process | Needed after `STOP`/`TSTP`/job suspension |
| `SIGUSR1` | Term | Application-defined action | Meaning is entirely application-specific |
| `SIGUSR2` | Term | Application-defined action | Meaning is entirely application-specific |
| `SIGPIPE` | Term | Broken pipe notification | Common in pipelines; often not manually sent |

> [!warning] `SIGHUP` does not universally mean “reload”
> Many daemons treat `HUP` as reload, but Linux does **not** enforce that meaning.
> Always check application documentation before assuming `HUP` is safe.

### 1.5. Signals, threads, and multithreaded processes

A CLI `kill` targets a **process**, not a specific userspace thread in the way most users expect.

Important consequences:

- a multithreaded process may receive a signal on an arbitrary eligible thread
- signaling a thread ID with `kill(1)` still targets the process/thread group, not a precisely chosen thread
- tools like `pgrep -w` can show thread IDs, but `pkill` does not send signals thread-by-thread

> [!note] There is no generic “kill this whole PPID tree” primitive
> Linux signal delivery can target:
> - a single PID
> - a process group
> - all permissible processes in special cases
>
> It does **not** natively broadcast down an arbitrary parent/child tree.
>
> If you need “everything belonging to this workload”, use:
> - a **process group**
> - a **session**
> - preferably a **systemd unit/cgroup**

### 1.6. Signal permissions

You generally need permission to signal a target process:

- same user is typically sufficient
- signaling other users’ processes usually requires elevated privilege
- `kill -0` checks both existence and permission

---

## 2. Inspecting signal state before acting

When a process “ignores” your signal, determine whether it is:

- actually ignoring the signal
- blocking it
- stopped
- in `D` state
- already dead and only present as a zombie
- being restarted by a supervisor

### 2.1. List available signals

```bash
kill -l
/usr/bin/kill -L
```

- `kill -l` lists names
- `/usr/bin/kill -L` prints a table with names and numbers

### 2.2. Decode a process’s blocked, ignored, and caught signals

The external util-linux `kill` can decode signal masks from `/proc/<pid>/status`:

```bash
pid=1234
/usr/bin/kill -d "$pid"
```

Example use case:

- `TERM` appears ineffective
- decode whether the process catches or ignores it

### 2.3. Show signal masks with `ps`

```bash
pid=1234
ps s "$pid"
```

This displays fields such as:

- `PENDING`
- `BLOCKED`
- `IGNORED`
- `CAUGHT`

### 2.4. Inspect raw signal fields in `/proc/<pid>/status`

```bash
pid=1234
grep -E '^(SigQ|SigPnd|ShdPnd|SigBlk|SigIgn|SigCgt):' "/proc/$pid/status"
```

Meaning:

| Field | Meaning |
|---|---|
| `SigPnd` | pending signals for this thread |
| `ShdPnd` | pending signals shared at process/thread-group level |
| `SigBlk` | blocked signals |
| `SigIgn` | ignored signals |
| `SigCgt` | caught signals |

> [!tip] Standard vs realtime signal behavior
> Standard signals are generally **coalesced**: many identical pending signals may collapse into one pending instance.
>
> Realtime signals are **queued and ordered** and can carry a value when sent with `sigqueue`-style interfaces.

---

## 3. `kill`: direct signaling by PID or process group

`kill` is the basic signaling tool.

For the most predictable examples below:

- use shell builtin `kill` for ordinary PID/job work
- use **external** `/usr/bin/kill` when you need util-linux extensions

## 3.1. Shell builtin vs external util-linux `kill`

### Shell builtin strengths

- supports shell job specs like `%1`
- convenient interactively
- usually enough for `kill -TERM <pid>`

### External util-linux `kill` strengths

Use `/usr/bin/kill` when you need:

- `--timeout`
- `-d`
- `-L`
- `-r` / `--require-handler`
- `-q` / `--queue`
- util-linux name-based extensions

Check what your shell resolves:

```bash
type -a kill
```

### 3.2. Signal one known PID

```bash
kill -TERM 1234
```

Equivalent explicit form:

```bash
kill -s TERM 1234
```

If you know the target needs a hard stop:

```bash
kill -KILL 1234
```

> [!warning] `SIGKILL` is last resort
> `SIGKILL` prevents:
> - graceful shutdown handlers
> - temporary-file cleanup
> - orderly socket close logic
> - state flushes
> - application-defined shutdown hooks

### 3.3. Existence and permission check with signal 0

```bash
pid=1234
if kill -0 "$pid" 2>/dev/null; then
  printf 'PID exists and is signalable right now\n'
fi
```

What this means:

- the PID currently exists
- you currently have permission to signal it

What this **does not** mean:

- the process is healthy
- the process is responsive
- the process is the same instance you saw earlier
- the process will still exist one millisecond later
- the process is not a zombie

### 3.4. Send to a process group

Signals can target a **process group** by using a negative numeric ID.

First get the PGID:

```bash
pid=1234
pgid=$(ps -o pgid= -p "$pid" | tr -d '[:space:]')
printf '%s\n' "$pgid"
```

Then signal the whole group:

```bash
kill -TERM -- "-$pgid"
```

This is often what you want for:

- shell pipelines
- foreground job groups
- tightly related worker sets in the same PGID

> [!note] Process group != process tree
> A process group can be a very useful control unit, but it is not the same as a PPID descendant tree.
> Some descendants may create their own process groups or sessions and escape PGID-based targeting.

### 3.5. Dangerous special targets

#### Current process group

```bash
kill 0
```

This signals **all processes in the current process group**.

This can be useful, but is dangerous in scripts unless you fully understand the group composition.

#### Almost everything you are allowed to signal

```bash
kill -1
```

This signals almost all permissible processes except PID 1 and some protected processes.

> [!danger] Do not use `kill -1` casually
> As root, this can catastrophically disrupt the system.

### 3.6. Race-safe escalation with `--timeout`

A common anti-pattern is:

```bash
kill -TERM "$pid"
sleep 5
kill -KILL "$pid"
```

This is racy because the PID may have exited and been reused.

Prefer util-linux `kill --timeout`, which uses Linux pidfds to avoid sending the follow-up signal to a recycled PID:

```bash
pid=1234
/usr/bin/kill --timeout 5000 KILL --signal TERM "$pid"
```

This means:

1. send `SIGTERM` now
2. wait 5000 ms
3. if it is still the **same process instance**, send `SIGKILL`

> [!tip] Prefer `--timeout` over `sleep` + second `kill`
> It is one of the cleanest modern improvements in util-linux process control.

### 3.7. Require a userspace handler before sending

The external util-linux `kill` can refuse to send a signal unless the target has a userspace handler for it:

```bash
/usr/bin/kill -r -s HUP 1234
```

This is useful when:

- `HUP` or `USR1` is meaningful only if the app actually handles it
- you want to avoid sending a signal that would just terminate by default

### 3.8. Send a queued signal value

```bash
/usr/bin/kill -q 42 -s USR1 1234
```

This uses `sigqueue(3)`-style delivery semantics and includes an integer payload.

Use this only when:

- the target program is explicitly designed for it
- the signal handler uses `SA_SIGINFO`

### 3.9. Util-linux name-based arguments

The external util-linux `kill` can accept process names as a local extension, but this is not the clearest or most portable approach.

Prefer `pkill` instead.

---

## 4. `pkill`, `killall`, and `pidwait`: choosing the correct higher-level tool

## 4.1. Decision table

| Goal | Best tool | Why |
|---|---|---|
| signal one known PID | `kill` | direct and explicit |
| signal many matches by name/attributes | `pkill` | flexible selection |
| wait for arbitrary existing PID(s) to exit | `pidwait` | pidfd-based waiting |
| signal all Linux processes with exact command name semantics | `killall` | concise on Arch/Linux |
| stop everything in a systemd unit | `systemctl stop` / `systemctl kill` | cgroup-aware and supervisor-aware |

## 4.2. `pkill`: attribute-aware signaling

`pkill` uses the same matching engine family as `pgrep`, but sends signals instead of printing PIDs.

### Important matching rules

- by default, it matches the **short process name**
- use `-f` for full command-line matching
- patterns are **extended regular expressions**
- all specified criteria must match
- comma-separated values inside one option are OR-like within that option

Examples:

```bash
# Exact name
pkill -TERM -x waybar

# Full command line
pkill -TERM -f 'python .*server\.py'

# Only current user's exact-name matches
pkill -TERM -u "$USER" -x mpv

# Children of a known parent
pkill -TERM -P 1234

# Exact process-group match
pkill -TERM -g 5678

# Echo what is being signaled
pkill -e -TERM -x foot
```

### High-value `pkill` options

| Option | Meaning | Use case |
|---|---|---|
| `-x` | exact match | avoid accidental partial matches |
| `-f` | match full command line | interpreters, wrappers, long argv |
| `-u` | effective user | restrict by owner |
| `-P` | parent PID | direct children only |
| `-g` | process group | group-wide signaling |
| `-s` | session ID | session-oriented control |
| `-t` | terminal | tty-specific operations |
| `-n` | newest match only | recent instance |
| `-o` | oldest match only | earliest instance |
| `-e` | echo matches | safer human confirmation |
| `-A` | ignore ancestors | useful under `sudo` or wrappers |
| `-H` | require handler | only targets processes handling that signal |
| `-q` | queue integer value | advanced signal payload |
| `--env` | match environment | display/session-aware matching |
| `--ns`, `--nslist` | namespace-aware matching | containers, sandboxes |
| `--cgroup` | match cgroup v2 names | advanced cgroup-aware lookup |

> [!warning] `pkill -f` is powerful and easy to misuse
> Always quote the regex and make it as specific as possible.

### `pkill` and PID reuse

Modern `pkill` may use `pidfd_send_signal(2)` when it can obtain a pidfd for a target. That improves correctness over naive PID-only signaling.

However:

- matching is still a live snapshot
- there is still a race before the tool opens the pidfd for the discovered process
- exact selection still matters

## 4.3. `killall`: Linux-specific name-based killing

On Arch Linux, `killall` from `psmisc` kills processes by command name.

Examples:

```bash
# All instances of a name
killall -TERM waybar

# Exact matching behavior for long names when possible
killall -e -TERM really-long-process-name

# Only one user's processes
killall -u "$USER" -TERM mpv

# Wait for them to die
killall -w -TERM mydaemon
```

### Useful `killall` options

| Option | Meaning |
|---|---|
| `-e` | exact long-name match; skip ambiguous long entries |
| `-r` | regex match |
| `-u` | restrict by user |
| `-g` | signal one time per process group |
| `-i` | interactive confirmation |
| `-w` | wait for death |
| `-o`, `-y` | older-than / younger-than filtering |
| `-n` | match same PID namespace as a reference PID |
| `-v` | verbose |
| `-q` | quiet if nothing matched |

### Special Linux `killall` file-path behavior

If the name argument contains a slash and regex mode is not used, `killall` matches processes executing that file path:

```bash
killall /usr/bin/mydaemon
```

This can be useful, but is subject to executable-handling caveats described in the `killall(1)` man page.

> [!warning] `killall` is not portable muscle memory
> On some non-Linux Unix systems, `killall` historically had very different semantics.
>
> On Arch Linux it is the `psmisc` tool described here, but do not assume the same meaning on other platforms.

### `killall -w` caveat

`killall -w` polls once per second and may wait forever if:

- the signal was ignored
- the process is stuck
- the process is a zombie
- a PID disappears and is replaced between scans

If you already have exact PIDs, `pidwait` is usually the cleaner wait primitive.

## 4.4. `pidwait`: waiting for arbitrary processes

`wait` in the shell only works for child processes known to that shell.

`pidwait` exists for arbitrary existing processes.

Example with a known PID:

```bash
pid=1234
printf '%s\n' "$pid" | pidwait -F -
```

Example with `pgrep` output:

```bash
pgrep -x mydaemon | pidwait -F -
```

Important facts:

- `pidwait` requires kernel support for pidfds; modern Arch kernels provide this
- it is much better than DIY polling loops for unrelated processes
- it still operates on a live system, so selection races before opening remain possible

---

## 5. Safe termination strategy

Good process control is not “send `-9` quickly”.  
It is:

1. identify the correct owner and scope
2. choose the correct control plane
3. request a graceful stop
4. wait appropriately
5. escalate only when necessary

## 5.1. Preferred control order

### 1. Application-native interface

If the program has a documented control command, IPC endpoint, or admin CLI, prefer that first.

Typical examples include:

- explicit `reload` commands
- admin sockets
- compositor/window-manager IPC
- service-specific CLIs

### 2. `systemd` unit management

If the process is unit-managed:

```bash
systemctl stop foo.service
systemctl reload foo.service
systemctl restart foo.service

systemctl --user stop bar.service
systemctl --user restart bar.service
```

### 3. Unit-scoped signaling

If you need a specific signal at unit scope:

```bash
systemctl kill -s TERM foo.service
systemctl kill -s HUP foo.service
systemctl kill --kill-whom=all -s TERM foo.service

systemctl --user kill -s TERM bar.service
```

This is often better than PID hunting because it targets the unit’s cgroup.

### 4. Raw PID or name-based signals

Use `kill`, `pkill`, or `killall` only when the process is not better managed elsewhere.

## 5.2. Standard escalation ladder

### Graceful stop

```bash
kill -TERM "$pid"
```

or for exact automatic escalation:

```bash
/usr/bin/kill --timeout 5000 KILL --signal TERM "$pid"
```

### If you need a diagnostic dump instead of silent death

Use a core-producing signal if appropriate and safe for the application:

```bash
kill -QUIT "$pid"
```

or:

```bash
kill -ABRT "$pid"
```

Then inspect with:

```bash
coredumpctl list
coredumpctl info "$pid"
```

> [!note] Core dumps depend on configuration
> Whether a core is produced depends on:
> - the program
> - resource limits
> - kernel/core pattern settings
> - `systemd-coredump` configuration on systemd systems

### Last resort

```bash
kill -KILL "$pid"
```

## 5.3. Special cases where signals seem ineffective

### Process is in `D` state

If a process is in uninterruptible sleep, usually due to I/O:

- `SIGTERM` may not take effect until the kernel wait returns
- even `SIGKILL` may not make it disappear immediately

Inspect:

```bash
ps -o pid,ppid,stat,wchan:24,etime,cmd -p "$pid"
```

A stuck `D`-state process usually points to:

- storage problems
- dead NFS/FUSE paths
- device/driver issues
- kernel-side waits

### Process is a zombie

A zombie is already dead.

You cannot meaningfully kill it.

Investigate the parent:

```bash
ps -o pid,ppid,stat,cmd -p "$pid"
pstree -aps "$pid"
```

### Process is stopped

If the process is stopped by job control or `SIGSTOP`, and you want it to run cleanup handlers, resume it first:

```bash
kill -CONT "$pid"
kill -TERM "$pid"
```

### Process keeps respawning

If the process immediately reappears, stop the supervisor, not the child.

Common causes:

- `systemd` unit with `Restart=`
- shell loop wrapper
- desktop launcher or watchdog
- transient user scope relaunched by another component

First inspect ownership:

```bash
ps -p "$pid" -o pid,ppid,unit,uunit,slice,cgroup,cmd
systemctl status some.service
systemctl --user status some.service
```

### Threads confuse the picture

A CPU-hungry process may have many threads, but signaling one thread ID is not normal CLI process control.

Use the process PID, process group, or systemd unit instead.

## 5.4. For service-managed workloads, use cgroup-aware stop logic

A service may have:

- helper children
- detached workers
- control processes
- post-stop hooks

A single PID is often not the right operational target.

Prefer:

```bash
systemctl stop foo.service
systemctl kill --kill-whom=all -s TERM foo.service
```

This is especially important on `systemd` systems because unit semantics include:

- `KillMode=`
- `KillSignal=`
- `SendSIGKILL=`
- `FinalKillSignal=`
- `TimeoutStopSec=`

> [!tip] If a service keeps coming back
> Inspect the unit rather than repeatedly killing the child:
>
> ```bash
> systemctl status foo.service
> systemctl cat foo.service
> ```

---

## 6. Interactive shell job control

Job control is a shell feature layered on top of process groups and controlling terminals.

It matters mainly in **interactive** shells.

## 6.1. Terminal-generated signals go to the foreground process group

When you press:

- `Ctrl+C` → terminal sends `SIGINT` to the foreground process group
- `Ctrl+\` → terminal sends `SIGQUIT`
- `Ctrl+Z` → terminal sends `SIGTSTP`

This means pipelines are affected as a group, not just one process.

Example:

```bash
yes | head
```

`Ctrl+C` is delivered to the foreground job’s process group.

## 6.2. Core job-control commands

| Action | Command / key | Effect |
|---|---|---|
| interrupt foreground job | `Ctrl+C` | send `SIGINT` |
| suspend foreground job | `Ctrl+Z` | send `SIGTSTP` |
| list jobs | `jobs` | show shell jobs |
| list jobs with PIDs | `jobs -l` | include PIDs |
| resume in background | `bg %1` | continue job in background |
| bring to foreground | `fg %1` | continue and foreground job |
| signal a shell job | `kill %1` | builtin resolves jobspec |
| remove job from shell job table | `disown %1` | shell stops tracking it |
| avoid HUP on shell exit | `disown -h %1` | keep job, mark no-HUP |

> [!note] `%1` is shell syntax
> Job specs like `%1` are resolved by the shell builtin `kill`, not by external `/usr/bin/kill`.

## 6.3. Background jobs and terminal I/O

Background jobs that try to read from the terminal may be stopped with `SIGTTIN`.

Depending on terminal settings, background writes may also trigger `SIGTTOU`.

Practical advice:

- redirect stdin/stdout/stderr for true background jobs
- do not assume a backgrounded interactive program will behave usefully

## 6.4. HUP on shell exit

When an interactive shell exits, jobs may receive `SIGHUP`.

Ways to avoid this:

### `nohup`

```bash
nohup long-job >"$HOME/.local/state/long-job.log" 2>&1 < /dev/null &
```

### `disown`

```bash
long-job &
disown -h %1
```

### `setsid`

```bash
setsid long-job >"$HOME/.local/state/long-job.log" 2>&1 < /dev/null &
```

### Preferred on Arch desktop systems: `systemd-run --user`

```bash
systemd-run --user --scope long-job arg1 arg2
```

This is usually the cleanest option on a `systemd`-managed Arch desktop because the workload becomes a user-managed scope rather than a fragile shell orphan.

> [!tip] For long-lived user workloads, prefer `systemd-run --user`
> It integrates better with:
> - cgroups
> - session tracking
> - cleanup
> - `systemctl --user`
> - desktop sessions such as Wayland/Hyprland/UWSM-managed environments

## 6.5. `wait` in the shell

The shell builtin `wait` is for **child processes of that shell**.

Examples:

```bash
sleep 30 &
pid=$!
wait "$pid"
```

```bash
cmd1 &
cmd2 &
wait
```

Important modern Bash features:

- `wait -n` → wait for the next child to finish
- `wait -p var -n` → also store which PID/jobspec finished

Example:

```bash
sleep 2 &
a=$!
sleep 5 &
b=$!

if wait -n -p done "$a" "$b"; then
  printf 'First finished child: %s\n' "$done"
fi
```

### Exit status when a child dies from a signal

In shell practice, a signaled child commonly yields status `128 + signal_number`.

Typical values seen in Bash:

| Status | Meaning |
|---|---|
| `130` | terminated by `SIGINT` |
| `137` | terminated by `SIGKILL` |
| `143` | terminated by `SIGTERM` |

> [!note] `wait` is not for arbitrary PIDs
> If the process is not your shell child, use `pidwait`, not `wait`.

---

## 7. Bash `trap`: writing scripts that clean up correctly

A good Bash process-management script must handle:

- `EXIT`
- `INT`
- `TERM`
- often `HUP`
- sometimes `ERR`

## 7.1. Core rules for robust traps

1. **Use functions**, not giant inline trap strings
2. **Make cleanup idempotent**
3. **Capture `$?` at the top** of cleanup
4. **Clear traps before re-exiting** if needed
5. **Prefer `exec`** if your wrapper only launches one long-lived child
6. **Use foreground/no-daemon mode** for daemons when supervised by Bash or systemd
7. **Do not expect traps to catch `SIGKILL` or `SIGSTOP`**

## 7.2. `EXIT`, `ERR`, and real signal traps

### `EXIT`

Runs when the shell exits normally through an orderly exit path.

Use it for:

- temp file cleanup
- lock cleanup
- final logging

### `ERR`

`ERR` is a Bash pseudo-signal, not a real kernel signal.

Use with:

```bash
set -E
```

or:

```bash
set -o errtrace
```

Caveats:

- `ERR` does **not** fire in every nonzero-status situation
- commands tested by `if`, `while`, `until`, `&&`, `||`, `!`, and some pipeline contexts behave differently
- `pipefail` changes pipeline failure behavior and is usually desirable

Recommended baseline:

```bash
set -Eeuo pipefail
```

## 7.3. Minimal cleanup pattern

This is the standard pattern for tempdir cleanup with signal-to-exit conversion.

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

tmpdir=$(mktemp -d)
cleanup_done=0

cleanup() {
  local st=$?
  (( cleanup_done )) && exit "$st"
  cleanup_done=1

  trap - EXIT INT TERM HUP
  rm -rf -- "$tmpdir"

  exit "$st"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Main script logic
printf 'Using tempdir: %s\n' "$tmpdir"
sleep 60
```

Why this works:

- signal traps convert asynchronous termination into an orderly shell `exit`
- the `EXIT` trap then performs cleanup
- cleanup is protected against running twice

## 7.4. Best wrapper pattern: `exec` the child

If your script’s only real purpose is to set up environment and launch one long-lived program, replace the shell with the child:

```bash
#!/usr/bin/env bash
set -euo pipefail

export MYAPP_CACHE_DIR="$HOME/.cache/myapp"
exec myapp --foreground --config "$HOME/.config/myapp/config.toml"
```

Benefits:

- no wrapper shell remains
- signals go directly to the real process
- no child-forwarding logic required
- process identity is cleaner in `ps`, `systemd`, and `/proc`

> [!tip] Prefer foreground mode
> If the program supports `--foreground`, `--no-daemon`, or similar, use it when supervised.
>
> Daemonizing breaks clean supervision because the parent exits and the real workload moves elsewhere.

## 7.5. Wrapper pattern for one child you must supervise

Use this when you need pre/post logic and cannot simply `exec`.

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

tmpdir=$(mktemp -d)
child=''

cleanup() {
  local st=$?
  trap - EXIT
  rm -rf -- "$tmpdir"
  exit "$st"
}

forward_signal() {
  local sig=$1
  local code=$2

  if [[ -n ${child:-} ]]; then
    kill -"${sig}" "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi

  exit "$code"
}

trap cleanup EXIT
trap 'forward_signal INT 130' INT
trap 'forward_signal TERM 143' TERM
trap 'forward_signal HUP 129' HUP

mydaemon --foreground --state-dir "$tmpdir" &
child=$!

wait "$child"
```

Important nuance:

- this reliably forwards to **one child PID**
- it does **not** automatically manage an entire arbitrary descendant tree
- for whole-workload control, prefer a process group, session, or `systemd` scope/unit

## 7.6. Multiple background workers with Bash 5.3 `wait -n` and `wait -p`

This pattern starts several workers and stops the rest if one fails.

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

declare -a pids=()
terminating=0

terminate_all() {
  local sig=${1:-TERM}
  local pid
  for pid in "${pids[@]}"; do
    kill -"${sig}" "$pid" 2>/dev/null || true
  done
}

reap_all() {
  local pid rc=0
  for pid in "${pids[@]}"; do
    if wait "$pid"; then
      :
    else
      rc=$?
    fi
  done
  return "$rc"
}

on_signal() {
  local sig=$1
  local code=$2

  (( terminating )) && return 0
  terminating=1

  terminate_all "$sig"
  reap_all || true
  exit "$code"
}

trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM
trap 'on_signal HUP 129' HUP

for cfg in a b c; do
  worker --config "$cfg" &
  pids+=("$!")
done

remaining=("${pids[@]}")

while ((${#remaining[@]})); do
  if wait -n -p finished "${remaining[@]}"; then
    :
  else
    rc=$?
    terminate_all TERM
    reap_all || true
    exit "$rc"
  fi

  next=()
  for pid in "${remaining[@]}"; do
    [[ $pid != "$finished" ]] && next+=("$pid")
  done
  remaining=("${next[@]}")
done
```

Why this pattern is good:

- it uses arrays, not brittle whitespace parsing
- `wait -n` reacts to the first completion
- `wait -p` tells you which worker finished
- failures can trigger controlled teardown of peers

## 7.7. Common Bash trap pitfalls

### Bad quoting at trap definition time

Bad:

```bash
trap "rm -f $tmpfile" EXIT
```

This expands `$tmpfile` **when the trap is set**, not when it runs.

Better:

```bash
trap 'rm -f -- "$tmpfile"' EXIT
```

### Relying on `ERR` to catch everything

`ERR` is useful, but not universal.  
Do not assume it replaces explicit checks or signal traps.

### Using `kill 0` in cleanup without a dedicated process group

This is dangerous:

```bash
trap 'kill 0' EXIT
```

It may signal processes you did not intend to touch if your script shares a process group with other things.

### Forgetting that pipelines and subshells change process layout

Examples that often surprise people:

- `cmd1 | while read -r x; do ...; done`
- background pipelines
- command substitutions
- subshell groupings

In Bash, state changes inside a subshell do not modify the parent shell.

Prefer process substitution when parent-shell state matters:

```bash
while read -r x; do
  printf '%s\n' "$x"
done < <(some_command)
```

### Expecting traps to run after `SIGKILL`

They will not.

`SIGKILL` and `SIGSTOP` are not trappable.

---

## 8. systemd-aware control on Arch Linux

On Arch, process management is incomplete if you ignore `systemd`.

## 8.1. `systemctl stop`, `restart`, `reload`, and `kill`

### Normal unit operations

```bash
systemctl stop foo.service
systemctl restart foo.service
systemctl reload foo.service
```

Use these when the process belongs to a system service.

### Explicit unit-scoped signaling

```bash
systemctl kill -s TERM foo.service
systemctl kill -s HUP foo.service
systemctl kill --kill-whom=all -s TERM foo.service
```

This is different from raw PID signaling:

- it is unit-aware
- it follows cgroup membership
- it can reach all relevant unit processes depending on `--kill-whom`

## 8.2. User services and scopes

For desktop and per-user workloads:

```bash
systemctl --user list-units --type=service,scope
systemctl --user status bar.service
systemctl --user restart bar.service
systemctl --user kill -s TERM bar.service
```

This is especially useful for:

- status bars
- notification daemons
- portals
- user agents
- ad-hoc scopes created under the user manager

## 8.3. Hyprland, Wayland, and UWSM-related practice

On modern Arch desktops using Wayland and systemd-based session orchestration:

- processes may be owned by **user services**
- graphical apps may live in **scopes**
- PPID alone may not explain lifecycle ownership
- `systemctl --user` is often the right control surface

Recommended workflow before using `pkill`:

```bash
systemctl --user list-units --type=service,scope
loginctl session-status "$XDG_SESSION_ID"
```

If a compositor or desktop component is responsive, prefer its documented control mechanism or its owning user unit.

Only fall back to raw signals when:

- the unit/control interface is absent
- the process is wedged
- you explicitly want ad-hoc termination

### Clean session termination

If you truly need to tear down the current session:

```bash
loginctl terminate-session "$XDG_SESSION_ID"
```

> [!warning] Avoid blind compositor `pkill`
> Blindly killing a compositor or session-critical process can leave the user session in a dirty or partially torn-down state.
>
> Prefer:
> - the compositor’s own exit command when responsive
> - the owning user unit if one exists
> - `loginctl terminate-session` for whole-session teardown

---

## 9. When a signal appears to do nothing

Use this checklist.

## 9.1. Wrong target

Re-verify the PID:

```bash
ps -q "$pid" -o pid,ppid,pgid,sid,stat,etime,cmd
readlink -f "/proc/$pid/exe"
tr '\0' ' ' < "/proc/$pid/cmdline"; echo
```

## 9.2. Signal ignored or blocked

```bash
/usr/bin/kill -d "$pid"
ps s "$pid"
```

## 9.3. Process is stopped

```bash
ps -o pid,stat,cmd -p "$pid"
kill -CONT "$pid"
kill -TERM "$pid"
```

## 9.4. Process is in `D` state

```bash
ps -o pid,stat,wchan:24,cmd -p "$pid"
```

If `STAT` contains `D`, the real problem is often underlying I/O, not signal choice.

## 9.5. Process is a zombie

```bash
ps -o pid,ppid,stat,cmd -p "$pid"
```

If `STAT` contains `Z`, investigate the parent.

## 9.6. Supervisor immediately restarted it

Check unit or supervisor ownership:

```bash
ps -p "$pid" -o pid,ppid,unit,uunit,slice,cgroup,cmd
systemctl status foo.service
systemctl --user status foo.service
```

## 9.7. You used the wrong tool

- use `wait` only for shell children
- use `pidwait` for arbitrary PIDs
- use `pkill -f` if the short process name is insufficient
- use `systemctl kill` for unit-managed workloads
- use `kill --timeout` for race-safe escalation

---

## 10. Operational recipes

### 10.1. Graceful stop with automatic forced fallback

```bash
pid=1234
/usr/bin/kill --timeout 5000 KILL --signal TERM "$pid"
```

### 10.2. Stop all exact-name matches for the current user

```bash
pkill -TERM -u "$USER" -x waybar
```

### 10.3. Stop by full command line

```bash
pkill -TERM -f 'python3 /srv/app/server\.py'
```

### 10.4. Kill a whole process group

```bash
pid=1234
pgid=$(ps -o pgid= -p "$pid" | tr -d '[:space:]')
kill -TERM -- "-$pgid"
```

### 10.5. Resume a stopped process, then terminate gracefully

```bash
pid=1234
kill -CONT "$pid"
kill -TERM "$pid"
```

### 10.6. Wait for an arbitrary PID to exit

```bash
pid=1234
printf '%s\n' "$pid" | pidwait -F -
```

### 10.7. Signal a shell job

```bash
sleep 1000 &
jobs -l
kill %1
```

### 10.8. Start a long-running user workload outside terminal fragility

```bash
systemd-run --user --scope long-job arg1 arg2
```

### 10.9. Stop a unit-managed service correctly

```bash
systemctl stop foo.service
```

Or send a unit-scoped signal:

```bash
systemctl kill -s TERM foo.service
```

### 10.10. Restart a user-managed desktop component

```bash
systemctl --user restart bar.service
```

### 10.11. Decode signal masks of a stubborn process

```bash
pid=1234
/usr/bin/kill -d "$pid"
ps s "$pid"
```

### 10.12. Produce a diagnostic core dump if appropriate

```bash
kill -QUIT "$pid"
coredumpctl info "$pid"
```

---

## 11. Quick reference

## 11.1. Best-tool summary

| Situation | Best command |
|---|---|
| known PID, polite stop | `kill -TERM <pid>` |
| known PID, polite then forced | `/usr/bin/kill --timeout ... KILL --signal TERM <pid>` |
| exact name match | `pkill -x name` |
| full cmdline match | `pkill -f 'regex'` |
| shell job | `kill %1` |
| whole process group | `kill -- -PGID` |
| arbitrary PID wait | `pidwait -F -` |
| shell child wait | `wait` |
| system service | `systemctl stop` / `systemctl kill` |
| user service/scope | `systemctl --user stop` / `systemctl --user kill` |
| whole login session | `loginctl terminate-session` |

## 11.2. Habits to prefer

- use **signal names**, not numbers
- prefer **`TERM` before `KILL`**
- prefer **`systemctl stop`** for unit-managed workloads
- use **`pkill`** instead of `ps | grep | awk | xargs kill`
- use **`pidwait`** instead of DIY polling for arbitrary PIDs
- use **`/usr/bin/kill --timeout`** instead of `kill; sleep; kill`
- use **`exec`** in wrappers when possible
- use **foreground/non-daemon mode** under supervision
- use **`systemd-run --user`** for durable ad-hoc user jobs on Arch desktops

## 11.3. Habits to avoid

- `kill -9` as the first response
- assuming `HUP` always means reload
- assuming PPID is ownership on a systemd system
- killing a child that is being restarted by a supervisor
- using `wait` for non-child PIDs
- relying on `ERR` traps alone
- backgrounding daemons that then daemonize again
- using `kill 0` in cleanup without controlling the process group
- using `killall` as portable cross-platform muscle memory

---

## 12. Part 2 summary

Part 2 establishes the control side of process management:

- what Linux signals actually mean
- why `TERM` is the normal first stop signal
- when `KILL` is justified and when it will not help
- how `kill`, `pkill`, `killall`, and `pidwait` differ
- how process groups, jobs, and sessions affect signal delivery
- why `systemd` units and cgroups are often the real operational target on Arch Linux
- how to write Bash 5.3+ scripts that clean up reliably with `trap`
- how to avoid PID reuse races with pidfd-based modern tools

> [!note] Part 1 + Part 2 together
> With Part 1 for inspection and Part 2 for control, you now have a complete Arch Linux reference for:
> - discovering processes
> - understanding their state
> - correlating them with units, sessions, and resources
> - terminating or supervising them safely and correctly
