# `grep` — GNU Text Search Reference

`grep` searches line-oriented text in files or from standard input and prints matching lines. On Arch Linux, this note targets **GNU `grep`** as shipped by the `grep` package in the core repository. Behavior on BSD/macOS or BusyBox systems can differ.

> [!tip]
> For **literal text**, prefer `-F`. It is faster and avoids regex surprises.
>
> For **recursive searches**, always provide a path operand, usually `.`:
>
> ```bash
> grep -RIn -- 'pattern' .
> ```

> [!warning]
> `grep -r 'pattern'` **without a path** does **not** mean “search the current directory recursively”.  
> If no file or directory operand is given, `grep` reads from **standard input**.

---

## Command Syntax

```bash
grep [OPTION ...] PATTERN [FILE ...]
grep [OPTION ...] -e PATTERN ... [FILE ...]
grep [OPTION ...] -f PATTERN_FILE ... [FILE ...]
```

### Key Rules

- If no `FILE` is provided, `grep` reads from **stdin**.
- By default, `grep` interprets `PATTERN` as a **Basic Regular Expression** (**BRE**).
- To search recursively, provide one or more directory operands:
  - `grep -r -- 'pattern' /etc`
  - `grep -R -- 'pattern' .`

---

## Matching Modes

| Mode | Option | Use When | Example |
|---|---|---|---|
| Basic regex | _default_ | Standard regex search | `grep 'erro[rR]' file` |
| Extended regex | `-E` | You want `|`, `+`, `?`, `{m,n}` without backslashes | `grep -E 'error|warning' file` |
| Fixed strings | `-F` | You want literal text, not regex | `grep -F 'a.b' file` |
| PCRE2 | `-P` | You need lookarounds or PCRE-specific syntax | `grep -Po '(?<=user=)\w+' file` |

> [!note]
> On Arch Linux, GNU `grep` includes **PCRE2** support, so `-P` is available.  
> However, `-P` is **less portable** than `-E` or `-F`; avoid it in scripts that may run on non-GNU systems.

---

## Option Reference

### Common Match Selection Options

| Option | Long Form | Meaning | Notes |
|---|---|---|---|
| `-i` | `--ignore-case` | Case-insensitive search | Locale-sensitive |
| `-v` | `--invert-match` | Select non-matching lines | Useful for exclusion filters |
| `-w` | `--word-regexp` | Match whole words only | Word chars are letters, digits, `_` |
| `-x` | `--line-regexp` | Match the entire line only | Often combined with `-F` |
| `-m NUM` | `--max-count=NUM` | Stop after `NUM` selected lines per file | Useful for “first hit” searches |

### Output Control

| Option | Long Form | Meaning | Notes |
|---|---|---|---|
| `-n` | `--line-number` | Show line numbers | Very useful in config/code search |
| `-H` | `--with-filename` | Always print file names | Helpful when searching one file but wanting explicit output |
| `-h` | `--no-filename` | Suppress file names | Useful when combining many files into one stream |
| `-o` | `--only-matching` | Print only the matching text | One output line per match |
| `-c` | `--count` | Print count of **matching lines** | Not total match occurrences |
| `-l` | `--files-with-matches` | Print only file names with matches | Suppresses matching lines |
| `-L` | `--files-without-match` | Print only file names without matches | Inverse of `-l` |
| `-q` | `--quiet` / `--silent` | Suppress normal output; use exit status only | Ideal in shell conditionals |
| `--color=auto` | — | Highlight matches | Common interactive default |

### Recursive and File Selection

| Option | Long Form | Meaning | Notes |
|---|---|---|---|
| `-r` | `--recursive` | Recurse into directories | Does **not** follow all symlinks encountered |
| `-R` | `--dereference-recursive` | Recurse and follow symlinks | Can search more than expected |
| `--include=GLOB` | — | Search only matching file names | Example: `--include='*.conf'` |
| `--exclude=GLOB` | — | Skip matching file names | Example: `--exclude='*.log'` |
| `--exclude-dir=GLOB` | — | Skip matching directories | Example: `--exclude-dir=.git` |
| `-s` | `--no-messages` | Suppress error diagnostics | Does not change exit status |

### Pattern Input

| Option | Long Form | Meaning | Notes |
|---|---|---|---|
| `-e PATTERN` | `--regexp=PATTERN` | Add a pattern explicitly | Required when pattern begins with `-` |
| `-f FILE` | `--file=FILE` | Read patterns from a file | One pattern per line |

### Binary / Special Cases

| Option | Long Form | Meaning | Notes |
|---|---|---|---|
| `-I` | — | Ignore binary files | Equivalent to `--binary-files=without-match` |
| `-a` | `--text` | Treat binary data as text | Can print garbage to terminal |
| `-Z` | `--null` | End file names with NUL | Useful with `xargs -0` |

---

## Regex and Quoting Rules

### Prefer Single Quotes for Patterns

Use single quotes around regex patterns so the shell does not interpret special characters.

```bash
grep -E 'error|warning' file.log
grep -F 'a.b' file.log
```

If you need shell expansion inside the pattern, use double quotes carefully:

```bash
pattern='NetworkManager'
grep -F -- "$pattern" /etc/NetworkManager/NetworkManager.conf
```

### BRE vs ERE

Default `grep` uses **BRE**. In BRE:

- `.` `*` `^` `$` are special
- `+` `?` `|` `{m,n}` are **not** special unless escaped

```bash
grep 'colou\?r' file      # BRE
grep -E 'colou?r' file    # ERE
```

### `-w` Is Not a General “Boundary” Operator

`-w` matches whole words only, where “word” means letters, digits, and underscore.

```bash
grep -w -- 'cat' file
```

This matches `cat`, but not `caterpillar`. It also does **not** behave like arbitrary token or punctuation boundaries.

### Search for Literal Text with `-F`

If your pattern contains `.` `[` `]` `*` `?` or other regex characters and you want literal matching, use `-F`.

```bash
grep -F -- 'exec-once = foot' ~/.config/hypr/hyprland.conf
```

---

## Recommended Command Patterns

### Literal text in a file

```bash
grep -nF -- 'monitor=' ~/.config/hypr/hyprland.conf
```

### Regex in a file

```bash
grep -nE -- '^[[:space:]]*exec-once[[:space:]]*=' ~/.config/hypr/hyprland.conf
```

### Recursive search in the current directory

```bash
grep -RIn -- 'your_word_here' .
```

### Exact full-line match

```bash
pacman -Qq | grep -Fx -- 'hyprland'
```

### Quiet test for scripts

```bash
grep -qF -- 'source = ~/.config/hypr/theme.conf' ~/.config/hypr/hyprland.conf
```

---

## Practical Examples

### Search a Single File

```bash
grep -nF -- 'Include' /etc/pacman.conf
```

### Case-Insensitive Recursive Search

```bash
grep -Ril --ignore-case --include='*.conf' -- 'networkmanager' /etc
```

- `-R` recursively searches and follows symlinks
- `-i` ignores case
- `-l` prints only file names

If you do **not** want to follow symlinks, use `-r` instead.

### Correct Recursive Search from the Current Directory

```bash
grep -RIn -- 'your_word_here' .
```

> [!warning]
> The path operand `.` is required for recursive directory search.  
> `grep -RIn -- 'your_word_here'` without `.` reads from stdin instead.

### Filter Command Output

```bash
pacman -Qq | grep -iF -- 'pipewire'
```

Using `pacman -Qq` is cleaner than `pacman -Q` because it outputs only package names.

### Search Arch Package Manager Logs

```bash
grep -nE -- 'installed|upgraded|removed' /var/log/pacman.log
```

This is often more useful on Arch than traditional syslog examples.

> [!note]
> Arch Linux uses **systemd-journald** by default.  
> `/var/log/syslog` usually does **not** exist unless you install and configure a syslog daemon such as `rsyslog`.

### Search Journal Output with `grep`

```bash
journalctl -b --no-pager | grep -iE -- 'error|warning'
```

For a specific unit:

```bash
journalctl -b -u NetworkManager.service --no-pager | grep -iE -- 'error|warning'
```

> [!tip]
> `journalctl` also has native filtering features. When possible, prefer native filters first and use `grep` for additional text matching.

### List Only Files That Contain a Match

```bash
grep -RIlF -- 'TODO' "$HOME/projects"
```

- `-I` skips binary files
- `-l` prints only matching file names
- `-F` treats `TODO` literally

### Count Matching Lines

```bash
grep -cF -- 'sudo' ~/.bash_history
```

This counts **lines containing** `sudo`, not the total number of `sudo` occurrences.

### Count Total Non-Overlapping Matches

```bash
grep -oF -- 'sudo' ~/.bash_history | wc -l
```

### Whole-Word Search with Line Numbers

```bash
grep -wn -- 'user' /usr/local/bin/backup-script.sh
```

### Show Context Around Matches

```bash
grep -nC 2 -E -- '^[[:space:]]*(bind|exec-once)[[:space:]]*=' ~/.config/hypr/hyprland.conf
```

- `-C 2` shows 2 lines before and after each match

### Exclude Directories While Searching Recursively

```bash
grep -RIn \
  --exclude-dir=.git \
  --exclude-dir=node_modules \
  --exclude-dir=.cache \
  -- 'wlroots' .
```

> [!tip]
> Unlike `ripgrep`, `grep` does **not** respect `.gitignore` and does **not** skip hidden files by default.

### Search Wayland / Hyprland Config Trees

```bash
grep -RIn --include='*.conf' -F -- 'monitor=' ~/.config/hypr
```

### Search systemd User Unit Files

```bash
grep -RIn \
  --include='*.service' \
  --include='*.target' \
  -F -- 'WAYLAND_DISPLAY' \
  ~/.config/systemd/user /etc/systemd/user /usr/lib/systemd/user
```

### Follow a Live Stream Without Output Buffering

When `grep` is in a pipeline and stdout is not a terminal, buffering can delay output. Use `--line-buffered` for live monitoring:

```bash
journalctl -f -u NetworkManager.service --no-pager | grep --line-buffered -iE -- 'error|warn'
```

---

## Scripting with `grep`

### Exit Status

`grep` is extremely useful in shell scripts because of its exit codes:

- `0` — at least one selected line matched
- `1` — no selected lines matched
- `2` — an error occurred

### Correct Use in Conditionals

```bash
if grep -qF -- 'exec-once' ~/.config/hypr/hyprland.conf; then
  printf '%s\n' 'exec-once directive present'
fi
```

### `set -e` Consideration

In strict shell scripts, a bare `grep` that finds no match returns `1`, which is **not** an error semantically but can still trigger shell exit under `set -e`.

Use it in conditionals:

```bash
if grep -qF -- 'uwsm' "$file"; then
  do_something
fi
```

Instead of:

```bash
grep -qF -- 'uwsm' "$file"
do_something
```

### Safe Handling of File Lists with NUL Delimiters

For complex recursive searches, `find` plus NUL-delimited paths is safer than parsing file names with whitespace or newlines.

```bash
mapfile -d '' -t conf_files < <(
  find ~/.config/hypr -type f -name '*.conf' -print0
)

if ((${#conf_files[@]})); then
  grep -nH -F -- 'monitor=' "${conf_files[@]}"
fi
```

> [!tip]
> Guard against an empty array.  
> If `grep` receives no file operands, it reads from stdin, which may not be what you intended.

### Searching for Patterns That Begin with `-`

Use `-e`:

```bash
grep -n -e '--help' script.sh
```

### Searching Files Whose Names Begin with `-`

Use `--` before file names:

```bash
grep -nF -- 'pattern' -- ./-strange-filename
```

If both the pattern and file name begin with `-`:

```bash
grep -n -e '-option' -- ./-strange-filename
```

---

## Performance Notes

### Use `-F` for Fixed Strings

This is usually the fastest choice when regex is unnecessary.

```bash
grep -RInF -- 'seatd' /etc
```

### Locale Affects Speed and Matching

For byte-oriented matching and often better performance on large data, use `LC_ALL=C`:

```bash
LC_ALL=C grep -RInF -- 'wlroots' /usr/include
```

> [!warning]
> `LC_ALL=C` changes character handling and case-folding behavior.  
> Do **not** use it when you need locale-aware or Unicode-aware matching semantics.

### Avoid Useless `cat`

Prefer:

```bash
grep -F -- 'pipewire' /etc/mkinitcpio.conf
```

instead of:

```bash
cat /etc/mkinitcpio.conf | grep -F -- 'pipewire'
```

Pipelines are appropriate when input genuinely comes from another command.

---

## Common Pitfalls

- **Forgetting the path in recursive searches**
  - Correct: `grep -RIn -- 'pattern' .`
  - Incorrect: `grep -RIn -- 'pattern'`

- **Using regex accidentally**
  - `grep 'a.b' file` matches `aab`, `acb`, etc.
  - Use `grep -F 'a.b' file` for a literal dot

- **Assuming `-c` counts every occurrence**
  - It counts **matching lines**
  - Use `grep -o ... | wc -l` for total non-overlapping matches

- **Assuming `-w` means arbitrary token boundaries**
  - It only uses word boundaries based on letters, digits, and underscore

- **Using `-R` when you did not intend to follow symlinks**
  - This can expand the search scope unexpectedly

- **Searching binary data unintentionally**
  - Use `-I` to ignore binary files
  - Use `-a` only if you explicitly want to treat binary as text

- **Relying on `GREP_OPTIONS`**
  - This mechanism is obsolete and removed
  - Use aliases or shell functions for interactive defaults

---

## `grep` vs Related Tools

### `grep`

Best for:

- ubiquitous shell usage
- scripts
- stdin pipelines
- quick ad hoc searches on any Linux system

### `ripgrep` (`rg`)

Usually better for:

- large source trees
- respecting `.gitignore`
- speed
- sane defaults for code search

### `git grep`

Best for:

- searching only tracked files in a Git repository

### `zgrep`

Use for compressed `.gz` files:

```bash
zgrep -nE 'error|warning' /var/log/old.log.gz
```

---

## Quick Reference

| Task | Command |
|---|---|
| Literal search in one file | `grep -nF -- 'text' file` |
| Regex search in one file | `grep -nE -- 'error|warn' file` |
| Recursive search | `grep -RIn -- 'pattern' .` |
| Recursive search, only file names | `grep -RIl -- 'pattern' .` |
| Exact full-line match | `grep -xF -- 'line' file` |
| Whole-word match | `grep -w -- 'word' file` |
| Count matching lines | `grep -c -- 'pattern' file` |
| Count total matches | `grep -o -- 'pattern' file \| wc -l` |
| Quiet existence test | `grep -qF -- 'text' file` |
| Invert match | `grep -v -- 'pattern' file` |
| Show 3 lines of context | `grep -C 3 -- 'pattern' file` |
| Search only `*.conf` files | `grep -RIn --include='*.conf' -- 'pattern' .` |
| Exclude `.git` | `grep -RIn --exclude-dir=.git -- 'pattern' .` |

---

## Minimal Safe Defaults

If you need one dependable pattern for each common case, use these:

### Literal text in known files

```bash
grep -nF -- 'literal text' file
```

### Recursive search in a directory tree

```bash
grep -RIn -- 'pattern' /path
```

### Script conditional

```bash
grep -qF -- 'literal text' file
```

### Recursive literal search, ignoring binary files

```bash
grep -RInIF -- 'literal text' /path
```
