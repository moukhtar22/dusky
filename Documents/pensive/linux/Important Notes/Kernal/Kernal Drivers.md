# Kernel Modules on Arch Linux

> [!info] Scope
> This note is a permanent reference for **Linux kernel module management on Arch Linux** using the modern **kmod** toolchain (`lsmod`, `modprobe`, `modinfo`, `depmod`, `insmod`, `rmmod`).
>
> It covers:
> - inspecting loaded modules
> - loading and unloading modules safely
> - persistent configuration with `modprobe.d` and `modules-load.d`
> - Arch-specific early-boot and initramfs considerations
> - common failure modes and diagnostics

---

## What Kernel Modules Are

A **kernel module** is a piece of kernel code that can be loaded into the running kernel at runtime. Modules are commonly used for:

- hardware drivers
- filesystems
- network protocols
- virtualization features
- optional kernel subsystems

This modular design keeps the base kernel smaller and allows hardware support to be added only when needed.

> [!important] Built-in vs loadable
> Not every driver is a loadable module.
>
> Some features are compiled **directly into the kernel**. Built-in drivers:
> - do **not** appear in `lsmod`
> - cannot be unloaded
> - are not controlled by `modprobe -r`
>
> If a feature is built-in, configure it via the **kernel command line** rather than `modprobe.d`.

---

## Arch Linux Context

On Arch, module management is provided by the **`kmod`** package. Module files for a given kernel live under:

```bash
/usr/lib/modules/$(uname -r)/
```

Because Arch is `usr`-merged, you may also see paths under:

```bash
/lib/modules/$(uname -r)/
```

These refer to the same location via symlink compatibility.

Typical contents include:

- `kernel/` — actual module files (`.ko`, usually compressed as `.ko.zst`)
- `modules.dep(.bin)` — dependency map
- `modules.alias(.bin)` — alias map used for autoloading
- `modules.builtin*` — built-in module metadata

> [!note] Compression
> On Arch, module files are commonly stored as **`.ko.zst`**. Tools like `modprobe` and `modinfo` handle compressed modules transparently.

---

## Core Concepts

### Module names vs package names

The **module name** is not always the same as the **package name**.

Examples:

- package: `nvidia`  
  modules: `nvidia`, `nvidia_modeset`, `nvidia_uvm`, `nvidia_drm`
- package: `linux`  
  provides thousands of kernel modules in its module tree

Use `modinfo`, `lspci -k`, or `pacman -Qo "$(modinfo -n <module>)"` to map modules back to packages.

### Hyphens and underscores

For module tools, `-` and `_` are generally treated as equivalent.

Examples:

- `snd_hda_intel`
- `snd-hda-intel`

Both usually resolve to the same module.

### How automatic loading works

Most modules are not loaded manually. They are typically auto-loaded by one of these mechanisms:

- **udev** reacting to device events via **modalias**
- **systemd-modules-load** loading names from `modules-load.d`
- **initramfs** loading drivers needed early in boot
- explicit requests from tools or services using `modprobe`

---

## Important Files and Directories

| Path | Purpose |
|---|---|
| `/proc/modules` | Live list of currently loaded modules |
| `/sys/module/<name>/` | Runtime state, parameters, dependents |
| `/usr/lib/modules/<release>/` | Module files and dependency/alias maps |
| `/etc/modprobe.d/*.conf` | Local module options, blacklists, soft dependencies |
| `/usr/lib/modprobe.d/*.conf` | Package-provided defaults |
| `/etc/modules-load.d/*.conf` | Modules to load statically at boot |
| `/usr/lib/modules-load.d/*.conf` | Package-provided static boot loading |
| `/etc/mkinitcpio.conf` | Early-boot module inclusion for mkinitcpio-based systems |

> [!tip] Override policy
> Put local configuration in **`/etc`**, not `/usr/lib`. Files under `/usr/lib` are owned by packages and may be overwritten on upgrade.

---

## Inspecting Loaded Modules

### `lsmod`: list loaded modules

```bash
lsmod
```

To check for a specific module:

```bash
lsmod | grep -E '^iwlwifi\b'
lsmod | grep -E '^nvidia(_|$)'
```

### What `lsmod` shows

`lsmod` formats the contents of:

```bash
/proc/modules
```

The columns are:

1. **Module** — module name
2. **Size** — module object size reported by the kernel, **not total memory usage**
3. **Used by** — current reference count, optionally followed by dependent module names

> [!important] The `Used by` column is a refcount
> The third column is **not only “how many modules depend on this module.”**
>
> It is a **reference count**. That count can increase because of:
> - other modules
> - active userspace access through device nodes
> - internal kernel references
>
> The list shown after the count includes dependent modules, but it does **not** explain every reference.

### Check whether a module is built-in

If `lsmod` shows nothing, the driver may be built-in rather than missing.

Try:

```bash
modinfo -k "$(uname -r)" ext4
```

A built-in module may report a filename like:

```text
filename:       (builtin)
```

If needed, inspect the built-in metadata directly:

```bash
grep -F 'ext4' "/usr/lib/modules/$(uname -r)/modules.builtin" \
  "/usr/lib/modules/$(uname -r)/modules.builtin.modinfo" 2>/dev/null
```

---

## Discovering Which Driver a Device Uses

For PCI devices, `lspci -k` is often the fastest way to identify the active kernel driver:

```bash
lspci -k
```

Example: show only network and GPU sections with driver info:

```bash
lspci -k | grep -EA3 'VGA|3D|Display|Network|Ethernet|Wireless'
```

Useful fields:

- **Kernel driver in use** — currently bound driver
- **Kernel modules** — candidate modules that can support the device

For a specific loaded module, inspect runtime data in sysfs:

```bash
ls /sys/module/iwlwifi/
ls /sys/module/i915/holders/
```

To see which module an alias resolves to:

```bash
modprobe -R 'pci:v00008086d000051F0sv*sd*bc*sc*i*'
```

This is useful when troubleshooting autoloading for a device modalias.

---

## Inspecting Module Metadata with `modinfo`

`modinfo` prints metadata embedded in a module.

```bash
modinfo -k "$(uname -r)" iwlwifi
modinfo -k "$(uname -r)" nvidia
```

Commonly useful fields:

- `filename`
- `license`
- `description`
- `author`
- `depends`
- `alias`
- `firmware`
- `parm`

### Query individual fields

```bash
modinfo -F filename iwlwifi
modinfo -F depends nvidia_drm
modinfo -F firmware amdgpu
modinfo -F parm iwlwifi
```

Equivalent shorthand for parameters:

```bash
modinfo -p iwlwifi
```

> [!note] Firmware dependencies
> Many drivers require external firmware blobs. If a module loads but the device still fails, check:
>
> ```bash
> modinfo -F firmware <module>
> journalctl -k -b | grep -i firmware
> ```
>
> On Arch, firmware is package-managed like any other dependency.

### Find which package installed a module

On Arch, this is often useful:

```bash
pacman -Qo "$(modinfo -n iwlwifi)"
```

If the module is built-in, there is no module file to own.

---

## Loading Modules Safely with `modprobe`

`modprobe` is the **recommended high-level tool** for loading modules.

It understands:

- dependency maps
- aliases
- `modprobe.d` configuration
- install/remove rules
- blacklists

### Load a module

```bash
sudo modprobe iwlwifi
sudo modprobe nvidia
```

### Dry-run a load first

Prefer a dry run before changing a live system:

```bash
sudo modprobe -n -v nvidia
```

This shows what `modprobe` **would** do, including config-driven behavior, without actually loading anything.

### Show dependencies that would be loaded

```bash
modprobe --show-depends nvidia_drm
```

### Load with temporary parameters

```bash
sudo modprobe iwlwifi power_save=0
```

This applies only for the current load instance. It is not persistent across unload/reload or reboot unless configured in `modprobe.d`.

> [!important] If the module is already loaded
> Loading it again with new parameters does **not** usually reapply options.
>
> You must:
> 1. unload the module stack if safe,
> 2. reload it with new parameters,
> 3. or reboot after making the change persistent.

---

## Unloading Modules Safely

### Preferred method: `modprobe -r`

```bash
sudo modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia
```

Or for a simple case:

```bash
sudo modprobe -r iwlwifi
```

`modprobe -r` is preferred because it can remove the requested module set in dependency-aware order.

### Why unload fails

A module cannot be removed if it is still referenced.

Common reasons:

- a userspace process has the device open
- another module depends on it
- a filesystem using that module is mounted
- the current graphics stack is using the GPU driver
- the module or feature is built-in

### Check dependent modules

```bash
ls /sys/module/nvidia/holders/
ls /sys/module/iwlwifi/holders/
```

### Check kernel log messages

```bash
journalctl -k -b | grep -Ei 'nvidia|iwlwifi|module'
```

> [!warning] GPU modules
> Unloading GPU drivers on a live graphical session is disruptive and often impossible.
>
> For `amdgpu`, `i915`, or `nvidia*`:
> - switch to a TTY
> - stop the display manager or graphical session
> - then remove the module stack if required
>
> On Wayland systems, the compositor itself is usually the reason the module remains in use.

> [!warning] Remote systems
> Never unload network, storage, or GPU modules on a remote or production system unless you are certain of the consequences.

---

## Viewing Current Runtime Parameters

Declared parameters:

```bash
modinfo -p iwlwifi
```

Current runtime values, if exported through sysfs:

```bash
grep . /sys/module/iwlwifi/parameters/* 2>/dev/null
grep . /sys/module/nvidia_drm/parameters/* 2>/dev/null
```

For a single parameter:

```bash
cat /sys/module/iwlwifi/parameters/power_save
```

Some parameters can be changed at runtime; many cannot. Writable parameters can sometimes be changed with `tee`:

```bash
echo 0 | sudo tee /sys/module/iwlwifi/parameters/power_save
```

This change is usually **temporary** and may not exist for every parameter.

---

## Persistent Configuration

Persistent module behavior is configured primarily through:

- `modprobe.d` — options, blacklist, soft dependencies
- `modules-load.d` — force loading specific module names at boot

---

## `modprobe.d`: options, blacklist, soft dependencies

Create local configuration snippets in:

```bash
/etc/modprobe.d/
```

File names should end in `.conf`.

### Set persistent options

Example:

```conf
# /etc/modprobe.d/iwlwifi.conf
options iwlwifi power_save=0
```

Apply by reloading the module if safe, or rebooting.

### Blacklist a module

Example:

```conf
# /etc/modprobe.d/blacklist-nouveau.conf
blacklist nouveau
```

> [!important] What `blacklist` actually does
> A `blacklist` entry mainly prevents **automatic loading through aliases**.
>
> It does **not** reliably stop:
> - an explicit `modprobe nouveau`
> - every possible indirect load path
> - a built-in driver

### Hard-block a module from normal `modprobe` loading

If you need a stronger policy:

```conf
# /etc/modprobe.d/disable-nouveau.conf
blacklist nouveau
install nouveau /usr/bin/false
```

This prevents normal `modprobe nouveau` from succeeding unless `--ignore-install` is used.

> [!warning] Use `install` sparingly
> `install` rules are powerful but can make behavior less transparent. Prefer plain `options` and `blacklist` unless you specifically need a hard block.

### Define soft dependencies

Example:

```conf
# /etc/modprobe.d/example-softdep.conf
softdep snd_hda_intel pre: snd_hda_codec_hdmi
```

This tells `modprobe` to load the `pre:` modules before the target module when possible.

### Inspect the effective config

```bash
modprobe -c | less
modprobe -c | grep -E '^(options|blacklist|softdep|install)\s+(iwlwifi|nouveau|nvidia)'
```

---

## `modules-load.d`: load modules at boot

Use this when a module should always be loaded during boot, regardless of hardware-triggered autoloading.

Create a file in:

```bash
/etc/modules-load.d/
```

Example:

```conf
# /etc/modules-load.d/virtiofs.conf
virtiofs
```

Each line is a module name.

This is processed by:

```text
systemd-modules-load.service
```

> [!note]
> Use `modules-load.d` for **static boot-time loading**.
>
> Use `modprobe.d` for:
> - options
> - blacklists
> - soft dependencies
> - install/remove rules

---

## Early Boot and Initramfs Considerations

This is the most common source of confusion on Arch.

If a module is:

- included in the **initramfs**
- loaded very early in boot
- required for graphics, storage, keyboard, root filesystem, or KMS

then changing `modprobe.d` alone may not be enough.

You may also need to **rebuild the initramfs**.

### When to rebuild the initramfs

Rebuild after changing module options or blacklist policy if the affected module is loaded in early boot.

Typical examples:

- GPU drivers used for early KMS
- storage and filesystem drivers needed before root is mounted
- keyboard/input drivers needed in initramfs

### Rebuild commands on Arch

If using the default **mkinitcpio**:

```bash
sudo mkinitcpio -P
```

If using **dracut**:

```bash
sudo dracut --regenerate-all --force
```

> [!important] UKI users
> If you boot a **Unified Kernel Image**, regenerate the image using the tooling your preset or build pipeline uses. On Arch, this is commonly handled by the same preset-driven initramfs rebuild process.

### Force early loading with mkinitcpio

For modules that must be present in the initramfs, add them to `MODULES=()`:

```bash
sudoedit /etc/mkinitcpio.conf
```

Example:

```bash
MODULES=(amdgpu)
```

Then rebuild:

```bash
sudo mkinitcpio -P
```

### Built-in drivers and kernel command line parameters

If the driver is built into the kernel, use a kernel command line parameter:

```text
<module>.<parameter>=<value>
```

Example:

```text
nvidia_drm.modeset=1
```

This is also the most reliable approach for some early-boot parameters.

### Temporary blacklist from the kernel command line

`modprobe` also honors blacklist directives on the kernel command line:

```text
modprobe.blacklist=nouveau
```

This is useful for testing before committing to a permanent `modprobe.d` policy.

---

## `depmod`: Rebuild the Module Dependency and Alias Maps

`depmod` scans a kernel’s module tree and regenerates metadata used by `modprobe`.

For the running kernel:

```bash
sudo depmod -a "$(uname -r)"
```

For a different installed kernel version:

```bash
sudo depmod -a 6.14.2-arch1-1
```

This updates files such as:

- `modules.dep`
- `modules.dep.bin`
- `modules.alias`
- `modules.alias.bin`

### When you need `depmod`

Usually **not manually**.

It is typically run automatically after:

- kernel package installation or upgrade
- module package installation
- DKMS builds
- pacman hook execution

You usually need it only when:

- manually copying a `.ko` file into a module tree
- testing custom out-of-tree modules outside normal packaging
- repairing a damaged or incomplete module index

> [!note]
> If you add a custom module file under `/usr/lib/modules/<release>/`, run `depmod` for that exact `<release>` before expecting `modprobe` to find it by name or alias.

---

## Low-Level Tools: `insmod` and `rmmod`

These are valid tools, but they are **lower-level** than `modprobe`.

### `insmod`

```bash
sudo insmod /usr/lib/modules/"$(uname -r)"/kernel/drivers/.../foo.ko.zst
```

Characteristics:

- loads a module **by exact path**
- does **not** resolve dependencies
- does **not** use alias matching
- does **not** apply normal `modprobe.d` policy the way `modprobe` does

This makes it useful mostly for:

- debugging
- testing custom modules before indexing
- very controlled recovery situations

### `rmmod`

```bash
sudo rmmod foo
```

Characteristics:

- removes a module by name
- does **not** resolve or remove unused dependency stacks for you

> [!important] Kernel safety still exists
> `rmmod` does **not** bypass the kernel’s reference counting.
>
> If a module is still in use, removal should fail rather than silently corrupt the system.
>
> The main reason to prefer `modprobe -r` is not that `rmmod` is inherently reckless, but that `modprobe -r` understands the broader module stack and normal policy.

### Force removal

Avoid force-unload options unless you are debugging a disposable system.

Examples:

```bash
sudo modprobe -r --force foo
sudo rmmod -f foo
```

These only work if the kernel supports forced removal, and they can destabilize or crash the system.

---

## Recommended Workflow

Use this order for safe troubleshooting:

### 1. Confirm the running kernel release

```bash
uname -r
```

### 2. Identify the device and active driver

```bash
lspci -k
```

### 3. Inspect the module metadata

```bash
modinfo -k "$(uname -r)" <module>
```

### 4. Dry-run the load

```bash
sudo modprobe -n -v <module>
```

### 5. Load or reload the module if safe

```bash
sudo modprobe <module>
```

### 6. Verify the result

```bash
lsmod | grep -E '^<module>\b'
journalctl -k -b | tail -n 100
```

### 7. Make the change persistent if needed

- `modprobe.d` for options/blacklists
- `modules-load.d` for static boot loading
- rebuild initramfs if early boot is involved

---

## Troubleshooting

## Module not found

Symptoms:

- `modprobe: FATAL: Module <name> not found in directory /usr/lib/modules/<release>`

Check:

```bash
uname -r
modinfo -k "$(uname -r)" <module>
```

Common causes:

- wrong module name
- module built for a different kernel release
- package missing
- DKMS build failed
- feature is built into the kernel instead of modular

For DKMS-backed modules:

```bash
dkms status
journalctl -b | grep -i dkms
```

---

## Module loads but device still does not work

Check:

```bash
journalctl -k -b | grep -Ei '<module>|firmware|pci|usb'
modinfo -F firmware <module>
```

Common causes:

- missing firmware
- incompatible module parameter
- wrong driver bound
- hardware/BIOS issue
- module tainted or denied under Secure Boot / lockdown policy

### Secure Boot / lockdown edge case

On systems enforcing Secure Boot-related kernel lockdown or module signature policy, unsigned out-of-tree modules may fail to load.

Look for messages such as:

- `Lockdown: ...`
- `module verification failed`
- `Key was rejected by service`

Check:

```bash
journalctl -k -b | grep -Ei 'lockdown|verification|signature|key was rejected|taint'
```

---

## Blacklist appears to do nothing

Likely reasons:

- the module is loaded from the **initramfs**
- the driver is **built-in**
- the module is being loaded **explicitly by name**
- another configuration snippet overrides your expectations

Check effective configuration:

```bash
modprobe -c | grep -E '^(blacklist|install|options)\s+<module>'
```

If early boot is involved:

- rebuild the initramfs
- for testing, use `modprobe.blacklist=<module>` on the kernel command line

If the driver is built-in, `blacklist` will not disable it.

---

## Cannot unload a module

Check:

```bash
ls /sys/module/<module>/holders/
journalctl -k -b | grep -i '<module>'
```

Common blockers:

- active display/compositor for GPU modules
- mounted filesystem
- live network device
- userspace process with a device node open
- module stack still referenced

---

## New parameter does not apply

Possible reasons:

- module was already loaded
- parameter is read-only after load
- module loads in initramfs before your root filesystem config is visible
- driver is built-in and needs a kernel command line parameter

Typical fix:

1. put the option in `/etc/modprobe.d/*.conf`
2. rebuild initramfs if necessary
3. reboot

---

## Quick Reference

| Task | Command |
|---|---|
| List loaded modules | `lsmod` |
| Check whether one module is loaded | `lsmod \| grep -E '^<module>\b'` |
| Inspect module metadata | `modinfo -k "$(uname -r)" <module>` |
| Show only parameters | `modinfo -p <module>` |
| Show dependency chain | `modprobe --show-depends <module>` |
| Dry-run a load | `sudo modprobe -n -v <module>` |
| Load a module | `sudo modprobe <module>` |
| Load with temporary parameters | `sudo modprobe <module> param=value` |
| Unload a module stack | `sudo modprobe -r <module>` |
| Rebuild module dependency maps | `sudo depmod -a "$(uname -r)"` |
| Show runtime parameter values | `grep . /sys/module/<module>/parameters/*` |
| Show effective modprobe config | `modprobe -c` |
| Force static load at boot | add name to `/etc/modules-load.d/*.conf` |
| Set persistent options / blacklist | edit `/etc/modprobe.d/*.conf` |
| Rebuild mkinitcpio images | `sudo mkinitcpio -P` |
| Rebuild dracut images | `sudo dracut --regenerate-all --force` |

---

## Minimal Examples

### Load a module now

```bash
sudo modprobe v4l2loopback
```

### Load with a temporary option

```bash
sudo modprobe iwlwifi power_save=0
```

### Persist that option

```conf
# /etc/modprobe.d/iwlwifi.conf
options iwlwifi power_save=0
```

### Load a module at every boot

```conf
# /etc/modules-load.d/v4l2loopback.conf
v4l2loopback
```

### Blacklist a module

```conf
# /etc/modprobe.d/blacklist-example.conf
blacklist pcspkr
```

### Hard-block a module from normal `modprobe` loading

```conf
# /etc/modprobe.d/disable-example.conf
blacklist pcspkr
install pcspkr /usr/bin/false
```

---

## Best Practices

- Prefer **`modprobe`** over `insmod` and `rmmod`
- Use **`modprobe -n -v`** before changing a live system
- Treat `lsmod`’s **`Used by`** value as a refcount, not just a dependency count
- Put local policy in **`/etc/modprobe.d/`** and **`/etc/modules-load.d/`**
- Rebuild the **initramfs** after changing options for early-loaded modules
- Use the **kernel command line** for built-in drivers or very early parameters
- Verify with **`journalctl -k -b`** after every meaningful change

---

## Related

- [[Nvidia]]
