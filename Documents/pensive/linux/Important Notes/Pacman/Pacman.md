# pacman Reference

> [!info]
> Verified against `man pacman` from **pacman 7.1.0** (`2026-01-12`).  
> This note is accurate for **Arch Linux as of March 2026**.

## Core Model

`pacman` commands are built from:

1. **Exactly one operation**  
   Examples: `-S` (`--sync`), `-Q` (`--query`), `-R` (`--remove`)

2. **Zero or more options**  
   Examples: `-y` (`--refresh`), `-u` (`--sysupgrade`), `-i` (`--info`)

3. **Zero or more targets**  
   Examples: package names, repository-qualified package names (`repo/pkg`), file paths, URLs, or search strings

Examples:

```bash
sudo pacman -Syu
pacman -Qi openssh
sudo pacman -U ./package.pkg.tar.zst
```

> [!note]
> If stdin is **not** a terminal and a single hyphen (`-`) is passed as a target, `pacman` reads targets from stdin.

---

## Golden Rules

> [!warning] No partial upgrades
> On Arch Linux, do **not** refresh sync databases without upgrading the system.
>
> Unsafe pattern:
> ```bash
> sudo pacman -Sy
> sudo pacman -S some-package
> ```
>
> Safe pattern:
> ```bash
> sudo pacman -Syu some-package
> ```
>
> If you already fully upgraded and the system is still current, a later `sudo pacman -S some-package` is fine.

> [!warning] `--force` is obsolete and removed
> Modern `pacman` does **not** provide `-f/--force` for package installation transactions.  
> If you must intentionally replace conflicting files, use **targeted** `--overwrite <glob>` instead.

> [!important] Prefer `--needed` for idempotent installs
> `--needed` prevents reinstalling target packages that are already up to date.  
> This saves bandwidth, CPU time, disk I/O, and power.

> [!warning] Long-term `--ignore` use is risky
> Temporarily ignoring a package can be useful during a known breakage.  
> Leaving packages ignored indefinitely undermines Arch’s rolling-release assumptions and can create unsupported states.

---

## Quick Safe Workflows

### Full system upgrade

```bash
sudo pacman -Syu
```

### Full upgrade and install packages in the same transaction

```bash
sudo pacman -Syu --needed neovim ripgrep fd
```

### Search repositories

```bash
pacman -Ss neovim
```

### Inspect a package before installing

```bash
pacman -Si openssh
```

### Reinstall a package

```bash
sudo pacman -S openssh
```

### Remove a package thoroughly

```bash
sudo pacman -Rns openssh
```

### Check which installed package owns a local file

```bash
pacman -Qo /usr/bin/ssh
```

### Check which repository package ships a file you do not have installed

```bash
sudo pacman -Fy
pacman -F /usr/lib/systemd/system/sshd.service
```

### Clean cached packages that are no longer installed

```bash
sudo pacman -Sc
```

---

## Installed vs Repository vs Package-File Lookups

| Task | Installed package / local filesystem | Sync repositories | Local package file |
|---|---|---|---|
| Package info | `pacman -Qi pkg` | `pacman -Si pkg` | `pacman -Qip ./pkg.pkg.tar.zst` |
| More detailed info | `pacman -Qii pkg` | `pacman -Sii pkg` | — |
| List files | `pacman -Ql pkg` | `pacman -Fl pkg` | `pacman -Qlp ./pkg.pkg.tar.zst` |
| File owner lookup | `pacman -Qo /path/to/file` | `pacman -F /path/in/repo` | — |

> [!tip]
> `-Qo` answers **“what installed package owns this file on my system?”**  
> `-F` answers **“what package in my enabled repositories ships this file?”**

---

## Operations Overview

| Operation | Long form | Purpose |
|---|---|---|
| `-S` | `--sync` | Install, upgrade, search, list, and query packages in sync repositories |
| `-Q` | `--query` | Query the local package database and installed packages |
| `-R` | `--remove` | Remove installed packages |
| `-U` | `--upgrade` | Install or upgrade a package from a local file or URL |
| `-F` | `--files` | Query repository file databases (`repo.files`) |
| `-D` | `--database` | Modify package install reasons or check database consistency |
| `-T` | `--deptest` | Test whether dependencies are satisfied |
| `-V` | `--version` | Show pacman version |
| `-h` | `--help` | Show help |

---

## `-S` / `--sync`: Repository Operations

Use `-S` to synchronize with configured repositories, install packages, upgrade the system, search, and inspect repository metadata.

### High-value `-S` options

| Option | Long form | Meaning |
|---|---|---|
| `-y` | `--refresh` | Refresh sync package databases |
| `-yy` | `--refresh --refresh` | Force refresh all sync databases even if they appear current |
| `-u` | `--sysupgrade` | Upgrade all out-of-date installed packages |
| `-uu` | `--sysupgrade --sysupgrade` | Allow downgrades when repo version does not match local version |
| `-s` | `--search` | Search repository package names/descriptions |
| `-i` | `--info` | Show repository package info |
| `-ii` | `--info --info` | Also show repository reverse dependencies |
| `-l` | `--list` | List packages in one or more repositories |
| `-g` | `--groups` | List package groups or their members |
| `-q` | `--quiet` | Script-friendlier output |
| `-c` | `--clean` | Clean cached packages and unused downloaded databases |
| `-cc` | `--clean --clean` | Remove all cached packages |
| `-w` | `--downloadonly` | Download packages only; do not install |

### Shared upgrade/install options commonly used with `-S`

| Option | Meaning |
|---|---|
| `--needed` | Skip reinstalling target packages that are already current |
| `--asdeps` | Mark installed targets as dependencies |
| `--asexplicit` | Mark installed targets as explicitly installed |
| `--ignore <pkg1,pkg2>` | Ignore upgrades for specific packages in this transaction |
| `--ignoregroup <grp1,grp2>` | Ignore upgrades for packages in specific groups |
| `--overwrite <glob>` | Overwrite matching conflicting files |
| `-d` / `--nodeps` | Skip dependency **version** checks; `-dd` skips **all** dependency checks |

### Common `-S` commands

```bash
# Standard full upgrade
sudo pacman -Syu

# Force-refresh repo databases, then upgrade
# Use only after mirror changes, stale metadata, or sync-db troubleshooting
sudo pacman -Syyu

# Upgrade and install packages in one transaction
sudo pacman -Syu --needed hyprland uwsm xdg-desktop-portal-hyprland

# Search repositories
pacman -Ss pipewire

# Show repository metadata for a package
pacman -Si openssh

# Show repository metadata plus reverse dependencies in repos
pacman -Sii openssl

# Install a package when the system is already current
sudo pacman -S --needed git

# Download package(s) to cache only
sudo pacman -Sw linux linux-headers

# List packages in a repository
pacman -Sl core

# List members of a package group
pacman -Sg base-devel
```

### Advanced `-S` behaviors

#### Install from a specific repository

```bash
sudo pacman -S core/pacman
```

#### Use a versioned target

Quote the target so the shell does not treat `>` as redirection:

```bash
sudo pacman -S "bash>=5.2"
```

#### Providers

If an exact package name is not found, `pacman` also checks packages that **provide** the target and prompts if multiple providers exist.

#### Groups

If the target is a package group, `pacman` prompts for member selection. The selector supports:

- space-separated package numbers
- comma-separated package numbers
- ranges like `1-5`
- exclusions using `^`

> [!warning] About `-Sy`
> `sudo pacman -Sy` by itself refreshes repo metadata without upgrading installed packages.  
> That is the classic path to a partial upgrade. Avoid it in normal administration.

---

## `-Q` / `--query`: Local Package Database

Use `-Q` to inspect what is already installed, check ownership, find orphans, audit packages, and query local package files.

### High-value `-Q` options

| Option | Long form | Meaning |
|---|---|---|
| `-q` | `--quiet` | Print less information; ideal for scripts |
| `-i` | `--info` | Show installed package info |
| `-ii` | `--info --info` | Also show backup files and their modification states |
| `-l` | `--list` | List files owned by an installed package |
| `-o <file>` | `--owns <file>` | Show which installed package owns a file |
| `-s <regexp>` | `--search <regexp>` | Search installed package names/descriptions |
| `-g` | `--groups` | Show installed packages belonging to a group |
| `-c` | `--changelog` | Show package changelog if present |
| `-k` | `--check` | Verify packaged files are present |
| `-kk` | `--check --check` | Perform more detailed mtree-based file checks |
| `-e` | `--explicit` | Only explicitly installed packages |
| `-d` | `--deps` | Only dependency-installed packages |
| `-t` | `--unrequired` | Only unrequired packages |
| `-tt` | `--unrequired --unrequired` | Also include packages only optionally required by others |
| `-m` | `--foreign` | Packages not found in current sync databases |
| `-n` | `--native` | Packages found in current sync databases |
| `-u` | `--upgrades` | Packages that are out of date according to sync DB versions |
| `-p` | `--file` | Treat target as a package file rather than installed package name |

### Common `-Q` commands

```bash
# List all installed packages
pacman -Q

# List installed package names only
pacman -Qq

# Show info for an installed package
pacman -Qi openssh

# Show info plus backup-file states
pacman -Qii openssh

# List all files owned by an installed package
pacman -Ql openssh

# Find who owns a local file
pacman -Qo /usr/bin/ssh

# Search installed packages
pacman -Qs systemd

# Show changelog if available
pacman -Qc linux

# List explicitly installed packages
pacman -Qe

# List packages installed as dependencies
pacman -Qd

# List orphaned packages
pacman -Qdt

# List foreign packages (often AUR or manually installed packages)
pacman -Qm

# Query a package file without installing it
pacman -Qip ./foo-1.0-1-x86_64.pkg.tar.zst

# List files inside a package file without installing it
pacman -Qlp ./foo-1.0-1-x86_64.pkg.tar.zst
```

### Important `-Q` details

> [!note]
> `pacman -Qkk` does **not** verify generic file checksums.  
> It performs more detailed file validation using package **mtree** metadata, including checks such as permissions, sizes, and modification times when that metadata is available.

> [!tip] Multiple search terms are ANDed
> For both `pacman -Qs` and `pacman -Ss`, when you provide multiple terms, only packages matching **all** terms are returned.

> [!tip] Foreign packages are not just “AUR packages”
> `pacman -Qm` means **not present in your currently enabled sync databases**.  
> That commonly includes AUR builds, manually installed local packages, or packages from repos you no longer have enabled.

### Checking for updates

`pacman -Qu` shows installed packages that are out of date **according to the current sync databases**.

```bash
pacman -Qu
```

> [!warning]
> `pacman -Qu` works best after refreshing sync databases, but doing `pacman -Sy` only to check updates is a partial-upgrade trap.  
> For “check updates without upgrading” workflows, use `checkupdates` from `pacman-contrib` instead.

---

## `-R` / `--remove`: Package Removal

Use `-R` to remove installed packages.

### `-R` options

| Option | Long form | Meaning |
|---|---|---|
| `-s` | `--recursive` | Remove target packages and dependencies that are no longer needed **and** were not explicitly installed |
| `-ss` | `--recursive --recursive` | Also remove unneeded dependencies even if they were explicitly installed |
| `-n` | `--nosave` | Do not preserve backup config files as `.pacsave` |
| `-c` | `--cascade` | Remove targets and packages that depend on them |
| `-d` | `--nodeps` | Skip dependency **version** checks |
| `-dd` | `--nodeps --nodeps` | Skip **all** dependency checks |
| `-u` | `--unneeded` | Remove targets only if they are not required by other packages |

### Common `-R` commands

```bash
# Remove only the package
sudo pacman -R package_name

# Remove package and its unneeded, non-explicit dependencies
sudo pacman -Rs package_name

# Remove package, unneeded deps, and backup config files
sudo pacman -Rns package_name
```

> [!info]
> `-Rns` is often the cleanest uninstall:
>
> - `R` → remove package
> - `s` → remove unneeded dependencies
> - `n` → do not preserve backup files as `.pacsave`

> [!note]
> `pacman` removes files tracked by the package database. It does **not** remove arbitrary per-user configuration in home directories.

> [!warning] Dangerous flags
> Use these only when you fully understand the dependency graph:
>
> - `-Rc` / `--cascade` can remove large dependency trees
> - `-Rd` or especially `-Rdd` can leave broken dependents installed

---

## `-U` / `--upgrade`: Install From File or URL

Use `-U` to install or upgrade a package from:

- a **local package file**
- a **remote URL**

This is commonly used for:

- locally built packages
- manually built AUR package files
- downgrading from `/var/cache/pacman/pkg/`
- installing a package artifact outside normal repo sync flow

### Common `-U` commands

```bash
# Install from a local package file
sudo pacman -U ./package-name-1.2.3-1-x86_64.pkg.tar.zst

# Downgrade or reinstall from pacman cache
sudo pacman -U /var/cache/pacman/pkg/package-name-1.2.2-1-x86_64.pkg.tar.zst
```

### Notes for `-U`

- `pacman -U` still resolves required dependencies from configured sync repositories.
- Shared upgrade options such as `--needed`, `--asdeps`, `--asexplicit`, `--overwrite`, and `-d/--nodeps` also apply.
- Installing an AUR package with `pacman` requires that you already have a built package file.

> [!note] Pacman and the AUR
> `pacman` does **not** fetch PKGBUILDs, resolve AUR metadata, or build AUR packages.  
> It only installs a finished package file with `-U`.

> [!warning]
> Downgrading individual packages is a recovery tactic, not a normal steady state.  
> Always consider ABI compatibility and dependent packages when downgrading.

---

## `-F` / `--files`: Repository File Database

Use `-F` to query **repository file databases** (`repo.files`) rather than your installed filesystem.

This is ideal when you need to know:

- which package ships a binary or library
- which package provides a systemd unit
- whether a file exists in any enabled repository before installing it

### `-F` options

| Option | Long form | Meaning |
|---|---|---|
| `-y` | `--refresh` | Refresh repository file databases |
| `-yy` | `--refresh --refresh` | Force refresh file databases |
| `-l` | `--list` | List files owned by a repository package |
| `-x` | `--regex` | Interpret query as a regular expression |
| `-q` | `--quiet` | Less verbose output |
| `--machinereadable` | — | NUL-delimited output for scripts |

### Common `-F` commands

```bash
# Refresh file databases
sudo pacman -Fy

# Find which repository package provides a file
pacman -F /usr/bin/ssh

# Find which repository package ships a systemd unit
pacman -F /usr/lib/systemd/system/sshd.service

# List files shipped by a repository package
pacman -Fl openssh
```

> [!tip]
> `pacman -F ...` is read-only once the files databases exist locally.  
> The refresh step `pacman -Fy` requires root because it writes to pacman’s database area.

> [!note]
> `-F` only searches packages in your **enabled sync repositories**.  
> It does not know about local package files or AUR packages unless those packages are also present in an enabled repository.

### Machine-readable output

For scripting, `--machinereadable` emits:

- `repository`
- `pkgname`
- `pkgver`
- `path`

separated as:

```text
repository\0pkgname\0pkgver\0path\n
```

---

## `-D` / `--database`: Install Reason and Database Consistency

Use `-D` to modify package metadata in the local pacman database or to validate database consistency.

### `-D` options

| Option | Meaning |
|---|---|
| `--asdeps <pkg>` | Mark package as installed as a dependency |
| `--asexplicit <pkg>` | Mark package as explicitly installed |
| `-k` / `--check` | Check local package database consistency |
| `-kk` | Also check sync-database dependency availability |
| `-q` / `--quiet` | Suppress success messages |

### Common `-D` commands

```bash
# Mark a package as explicitly installed
sudo pacman -D --asexplicit hyprland

# Mark a package as dependency-installed
sudo pacman -D --asdeps wlroots

# Check local package database consistency
pacman -Dk

# More thorough database check
pacman -Dkk
```

### When `-D` is useful

- Preventing wanted packages from being removed as orphans
- Fixing incorrect install reasons after using build tools or helper scripts
- Auditing local package database health

> [!tip]
> If `pacman -Qdt` shows a package you intentionally want to keep, mark it explicit instead of leaving it orphaned:
>
> ```bash
> sudo pacman -D --asexplicit package_name
> ```

---

## `-T` / `--deptest`: Dependency Testing

Use `-T` to check whether dependencies are already satisfied.

This is especially useful in scripts and packaging workflows.

### Example

```bash
pacman -T git "python>=3.12" cmake
```

The command prints dependencies that are **not** satisfied on the system.

> [!note]
> `-T` accepts **no other options**.

---

## Global and Advanced Options

### Common global options

| Option | Meaning |
|---|---|
| `-h`, `--help` | Show help for pacman or the selected operation |
| `-V`, `--version` | Show pacman version |
| `-v`, `--verbose` | Show paths such as root, config, DB path, cache dirs |
| `--color <always\|never\|auto>` | Control colored output |
| `--config <file>` | Use an alternate `pacman.conf` |
| `--arch <arch>` | Use an alternate architecture |
| `--debug` | Emit debug messages |

### Path / environment override options

| Option | Meaning |
|---|---|
| `-r`, `--root <path>` | Alternate installation root |
| `--sysroot <dir>` | Alternate system root; correct choice for mounted guest systems |
| `-b`, `--dbpath <path>` | Alternate pacman database path |
| `--cachedir <dir>` | Alternate package cache directory; may be specified multiple times |
| `--logfile <file>` | Alternate log file |
| `--gpgdir <dir>` | Alternate GnuPG keyring directory |
| `--hookdir <dir>` | Alternate hook directory; may be specified multiple times |

> [!warning]
> `--root` is **not** the right tool for operating on a mounted guest system.  
> Use `--sysroot` for that use case.

> [!note]
> `--dbpath`, `--cachedir`, `--gpgdir`, `--hookdir`, and `--logfile` use **absolute paths**.  
> They are **not** automatically prefixed by `--root`.

### Advanced transaction options

These apply to transactions (`-S`, `-R`, `-U`) and are mainly for recovery, packaging, or automation.

| Option | Meaning |
|---|---|
| `--noconfirm` | Bypass confirmation prompts |
| `--confirm` | Cancel a previous `--noconfirm` |
| `--noprogressbar` | Disable download progress bar |
| `-p`, `--print` | Print targets instead of performing the transaction |
| `--print-format <fmt>` | Format `--print` output using pacman placeholders |
| `--assume-installed <pkg=ver>` | Satisfy a dependency virtually for this transaction |
| `--dbonly` | Modify database entries only; leave files untouched |
| `--noscriptlet` | Do not execute package scriptlets |
| `-d`, `--nodeps` | Skip dependency version checks; `-dd` skips all dependency checks |

> [!warning]
> `--dbonly`, `--noscriptlet`, `--assume-installed`, and `-dd` are advanced tools.  
> They are not normal day-to-day package management options.

### Download troubleshooting options

| Option | Meaning |
|---|---|
| `--disable-download-timeout` | Disable pacman’s default low-speed limit and timeout |
| `--disable-sandbox` | Disable both filesystem and syscall download sandboxing |
| `--disable-sandbox-filesystem` | Disable only the download filesystem restrictions |
| `--disable-sandbox-syscalls` | Disable only the download syscall filter |

> [!warning]
> Sandbox-disabling flags are for troubleshooting kernel/container/network incompatibilities, not routine use.

---

## Cache Management

Pacman caches downloaded package files in:

```text
/var/cache/pacman/pkg/
```

This enables:

- reinstalls without re-downloading
- local downgrades
- offline reuse of cached packages

### Cache cleanup commands

```bash
# Remove cached packages that are no longer installed
sudo pacman -Sc

# Remove all cached packages
sudo pacman -Scc
```

### What these do

- `-Sc` removes cached package files for packages that are no longer installed and cleans unused downloaded sync databases.
- `-Scc` aggressively removes all cached package files.

> [!warning]
> `sudo pacman -Scc` removes the easiest downgrade path.  
> If you clear the entire cache, recovery may require fetching packages from the Arch Linux Archive or another trusted source.

### Prefer retention-based cleanup for routine maintenance

For regular pruning, `paccache` from `pacman-contrib` is usually better than deleting everything:

```bash
sudo paccache -rk3
```

That keeps the three most recent cached versions of each package.

---

## Configuration File Handling: `.pacnew` and `.pacsave`

Pacman tracks certain package files as **backup files**.

### `.pacnew`

During upgrade, if:

- the currently installed config file was modified locally, **and**
- the new package version ships a different version of that file

then pacman preserves your file and writes the new packaged file as:

```text
filename.pacnew
```

### `.pacsave`

During removal, pacman normally preserves backup files as:

```text
filename.pacsave
```

unless you remove with:

```bash
sudo pacman -Rn package_name
```

or:

```bash
sudo pacman -Rns package_name
```

### Inspect backup-file state

```bash
pacman -Qii package_name
```

### Recommended workflow

- Review `.pacnew` files after upgrades
- Merge them promptly
- Use `pacdiff` from `pacman-contrib` to help compare and merge

> [!important]
> Ignoring `.pacnew` files for too long can leave services running with stale configs after package upgrades.

---

## Common Recipes

### Safe orphan removal with modern Bash

Avoid raw command substitution like `$(pacman -Qdtq)` for destructive removals. Use an array and handle the empty case cleanly:

```bash
mapfile -t orphans < <(pacman -Qdtq)

if ((${#orphans[@]})); then
  sudo pacman -Rns -- "${orphans[@]}"
fi
```

If you also want packages that are only *optionally* required by others, use `pacman -Qdttq` instead.

### Mark a wanted orphan as explicit

```bash
sudo pacman -D --asexplicit package_name
```

### Back up package selection for rebuild/reinstall

```bash
pacman -Qqen > pkglist-native.txt
pacman -Qqem > pkglist-foreign.txt
```

- `pkglist-native.txt`: explicitly installed packages found in sync repos
- `pkglist-foreign.txt`: explicitly installed packages not found in sync repos

### Install from stdin

```bash
printf '%s\n' git neovim ripgrep | sudo pacman -S --needed -
```

This works because `-` tells pacman to read targets from stdin when stdin is not a TTY.

### Find which package ships a systemd unit

If the package is already installed:

```bash
pacman -Qo /usr/lib/systemd/system/sshd.service
```

If the package is not installed:

```bash
sudo pacman -Fy
pacman -F /usr/lib/systemd/system/sshd.service
```

### Reinstall a package’s files

```bash
sudo pacman -S package_name
```

Use `--needed` if you want idempotent behavior and no reinstall when already current:

```bash
sudo pacman -S --needed package_name
```

---

## Further Reading

- `man pacman`
- `man pacman.conf`
- `man libalpm`
- `man alpm-hooks`
- `man pacdiff`  *(from `pacman-contrib`)*
- `man paccache` *(from `pacman-contrib`)*
