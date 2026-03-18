# GNOME Boxes, virt-manager, and SPICE clipboard on Arch Linux with Hyprland/UWSM

> [!summary]
> - **GNOME Boxes uses the per-user libvirt session**: `qemu:///session`
> - **Boxes VMs will not appear in the default root/system libvirt connection**
> - To inspect or edit Boxes VMs with virt-manager, use:
>   ```bash
>   virt-manager --connect qemu:///session
>   ```
> - For an **Arch guest running Hyprland**, install and enable **`spice-vdagent`** inside the guest, keep **Xwayland enabled**, and start the **user agent** in the guest session.
> - `wl-clipboard` + `xclip` are **optional guest-side workarounds** for Wayland clipboard edge cases; they are **not** host-side requirements for GNOME Boxes.
> - Do **not** enable `libvirtd.service` or add yourself to `libvirt`/**`kvm`** just to use GNOME Boxes in its default user-session mode.
> - Do **not** symlink `~/.local/share/gnome-boxes/images` to zram/tmpfs unless you intentionally want **ephemeral, RAM-backed VM disks**.

---

## Scope

This reference assumes:

- **Host OS**: Arch Linux
- **Host virtualization UI**: `gnome-boxes` from Arch repos, optionally `virt-manager`
- **Display stack**: Wayland
- **Guest OS example**: Arch Linux with **Hyprland**
- **Session management**: Hyprland launched via **UWSM**, or a comparable systemd-aware session

This note is specifically about:

- accessing **GNOME Boxes VMs from virt-manager**
- getting **SPICE clipboard integration** working reliably
- avoiding incorrect host/guest separation and unsafe storage hacks

> [!note]
> This note assumes the **native Arch package** for GNOME Boxes, **not the Flatpak**. Flatpak Boxes uses different sandboxing, filesystem visibility, and sometimes different troubleshooting paths.

---

## Core architecture

### libvirt scope used by GNOME Boxes

GNOME Boxes creates and manages VMs in the **user/session libvirt instance**:

```text
qemu:///session
```

That means:

- VM definitions are tied to your **user account**
- VMs are **not** visible in the default system connection (`qemu:///system`)
- you do **not** need root-managed libvirt services for normal Boxes usage
- user/group steps commonly used for system libvirt are usually **irrelevant here**

### Why virt-manager often looks “empty”

If you open virt-manager normally, it usually lands on the **system** connection. Boxes VMs will be missing there.

Use the same libvirt scope as Boxes:

```bash
virt-manager --connect qemu:///session
```

Equivalent shorthand:

```bash
virt-manager -c qemu:///session
```

You can verify the same VM inventory with:

```bash
virsh --connect qemu:///session list --all
```

---

## Package layout

### Host packages

Minimal host install:

```bash
sudo pacman -Syu --needed gnome-boxes virt-manager
```

Useful optional host packages:

```bash
sudo pacman -Syu --needed edk2-ovmf swtpm
```

- `edk2-ovmf`: UEFI firmware for guests
- `swtpm`: TPM emulation, useful for modern Windows guests and some secure-boot workflows

> [!note]
> You do **not** need to explicitly install `spice`, `spice-gtk`, `spice-protocol`, `gvfs-dnssd`, `wl-clipboard`, or `xclip` on the **host** just to make GNOME Boxes clipboard sharing work. Boxes already pulls its required runtime stack from package dependencies.

### Guest packages

Inside an **Arch guest** running Hyprland:

```bash
sudo pacman -Syu --needed spice-vdagent xorg-xwayland
```

Optional guest-side workaround tools:

```bash
sudo pacman -Syu --needed wl-clipboard xclip
```

Package roles:

| Package | Install where | Purpose |
|---|---|---|
| `gnome-boxes` | host | VM frontend / SPICE client |
| `virt-manager` | host | advanced VM editing against the same libvirt session |
| `spice-vdagent` | guest | SPICE agent for clipboard, cursor, and related guest integration |
| `xorg-xwayland` | guest | required on Hyprland because `spice-vdagent` still depends on X11/Xwayland-facing clipboard paths |
| `wl-clipboard` | guest | optional Wayland clipboard bridge helper |
| `xclip` | guest | optional X11 clipboard bridge helper |

---

## Host setup

### 1. Launch Boxes once

Run GNOME Boxes at least once so it creates its user-level state and storage paths.

After that, you can inspect/edit the same VMs from virt-manager using the **session** connection:

```bash
virt-manager --connect qemu:///session
```

### 2. Confirm you are looking at the correct connection

From the host:

```bash
virsh --connect qemu:///session list --all
```

If the VM appears there, it is in the same libvirt scope used by Boxes.

> [!warning]
> `qemu:///system` and `qemu:///session` are different inventories.  
> If you edit the wrong connection, you are editing the wrong VM set.

---

## SPICE requirements for clipboard integration

Clipboard sharing requires all of the following:

1. the VM console must use **SPICE**, not VNC
2. the VM must have a **SPICE agent channel**
3. the guest must run **`spice-vdagentd`**
4. the guest user session must run **`spice-vdagent`**
5. on Hyprland, **Xwayland must be enabled**
6. a SPICE client must be actively attached to the guest console (Boxes or virt-manager viewer)

### Check the SPICE agent channel

Boxes normally creates this automatically. If you edited the VM in virt-manager, verify the channel still exists.

The relevant libvirt XML looks like:

```xml
<channel type='spicevmc'>
  <target type='virtio' name='com.redhat.spice.0'/>
</channel>
```

Check it from the host:

```bash
virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A3 "com.redhat.spice.0"
```

If you are building the VM manually in virt-manager, add:

- **Add Hardware**
- **Channel**
- device type corresponding to the **SPICE agent**

### Check the graphics backend

Clipboard integration depends on SPICE graphics. Confirm the XML includes something like:

```xml
<graphics type='spice' ... />
```

Example check:

```bash
virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A2 "<graphics type='spice'"
```

> [!note]
> If you switch the guest display to **VNC**, the SPICE clipboard agent will not work.

---

## Guest setup: Arch + Hyprland

### 1. Enable the SPICE daemon inside the guest

Install the package, then enable the system daemon:

```bash
sudo systemctl enable --now spice-vdagentd.service
```

This is the **system** component. It is not enough by itself.

### 2. Start the user agent in the guest session

The user-session process is:

```bash
spice-vdagent
```

It must run as the **logged-in guest user**, not as root.

On full desktop environments, this is often handled by XDG autostart. On minimal Hyprland setups, do **not** assume that happens automatically.

---

## Preferred autostart method on Hyprland with UWSM

When Hyprland is started through UWSM, the cleanest long-running-session approach is a **systemd user service**.

Create:

```ini
# ~/.config/systemd/user/spice-vdagent-user.service
[Unit]
Description=SPICE user agent
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=/usr/bin/spice-vdagent
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now spice-vdagent-user.service
```

> [!tip]
> With UWSM, session environment propagation into `systemd --user` is normally handled correctly.  
> If you are **not** using UWSM and the service starts without a valid `DISPLAY`, use the `exec-once` method below instead, or import the session environment into `systemd --user`.

---

## Simpler alternative: start from Hyprland config

If you prefer to keep this in Hyprland config, add the following **inside the guest**.

### Raw Hyprland `exec-once`

```ini
exec-once = /usr/bin/spice-vdagent
```

### UWSM-aware variant

```ini
exec-once = uwsm app -- /usr/bin/spice-vdagent
```

Use the UWSM form if you want the process tracked as a session-managed app scope.

> [!warning]
> `spice-vdagent` belongs in the **guest session**, not on the host.

---

## Hyprland/Wayland clipboard behavior

### Important detail

Under Hyprland, `spice-vdagent` does **not** talk to a Hyprland-native SPICE clipboard API. In practice, it still depends on the **X11/Xwayland clipboard path**.

That is why the guest needs:

- `xorg-xwayland`
- Xwayland enabled in Hyprland
- a running `spice-vdagent` user process

### What usually works on current Hyprland

On current Hyprland builds, Xwayland clipboard integration is often sufficient for normal SPICE clipboard sync once `spice-vdagent` is running.

### When the optional bridge is useful

If you see this behavior:

- **host → guest works**
- but **guest native Wayland app → host does not**

then an explicit Wayland→X11 clipboard bridge can help.

This is a **guest-side workaround**, not a host-side requirement.

---

## Optional guest-side Wayland → X11 clipboard bridge

Install the optional tools inside the **guest**:

```bash
sudo pacman -Syu --needed wl-clipboard xclip
```

### Preferred systemd user service

Create:

```ini
# ~/.config/systemd/user/spice-wayland-clipboard-bridge.service
[Unit]
Description=Bridge Wayland clipboard to X11 clipboard for SPICE
PartOf=graphical-session.target
After=graphical-session.target spice-vdagent-user.service

[Service]
ExecStart=/usr/bin/wl-paste --type text --watch /usr/bin/xclip -selection clipboard
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now spice-wayland-clipboard-bridge.service
```

### Hyprland config alternative

```ini
exec-once = /usr/bin/wl-paste --type text --watch /usr/bin/xclip -selection clipboard
```

UWSM-aware variant:

```ini
exec-once = uwsm app -- /usr/bin/wl-paste --type text --watch /usr/bin/xclip -selection clipboard
```

### What this bridge actually does

This command:

```bash
wl-paste --type text --watch xclip -selection clipboard
```

takes **Wayland clipboard text** and mirrors it into the **X11 clipboard** inside the **guest**.

That helps `spice-vdagent` see text copied from native Wayland apps.

> [!note]
> This bridge is **text-only** as written. It is not a general binary/image clipboard synchronizer.

> [!warning]
> Run this **inside the guest**, not on the host.  
> A host-side `wl-paste | xclip ...` pipeline only manipulates the host clipboard; it does **not** push data into the guest VM.

---

## One-shot and temporary debugging commands

All commands in this section are **guest-side**.

### One-shot Wayland → X11 clipboard copy

```bash
wl-paste --type text | xclip -selection clipboard
```

### Temporary persistent bridge for the current shell

```bash
wl-paste --type text --watch xclip -selection clipboard
```

### Reset the guest-side SPICE user process

```bash
pkill -x spice-vdagent 2>/dev/null || true
spice-vdagent &
```

### Reset the bridge process

```bash
pkill -x wl-paste 2>/dev/null || true
wl-paste --type text --watch xclip -selection clipboard &
```

### Restart the guest daemon

```bash
sudo systemctl restart spice-vdagentd.service
```

---

## Verifying the guest-side plumbing

### Confirm the virtio SPICE channel exists in the guest

Inside the guest:

```bash
ls -l /dev/virtio-ports/com.redhat.spice.0
```

If that path is missing, first confirm the VM XML still contains the SPICE channel. If needed, also verify the kernel module is present:

```bash
sudo modprobe virtio_console
```

Then check again:

```bash
ls -l /dev/virtio-ports/com.redhat.spice.0
```

> [!note]
> On a normal Arch guest kernel, `virtio_console` is usually available and auto-loaded.  
> If the device file is still missing after loading the module, the VM is likely missing the SPICE agent channel in its libvirt config.

### Confirm the daemon and user agent are running

System daemon:

```bash
systemctl status spice-vdagentd.service
```

User agent:

```bash
pgrep -af spice-vdagent
```

### Confirm Xwayland exists

Inside the guest:

```bash
pgrep -af Xwayland
printf 'DISPLAY=%s\nWAYLAND_DISPLAY=%s\n' "${DISPLAY-}" "${WAYLAND_DISPLAY-}"
```

If Xwayland is disabled, `spice-vdagent` cannot provide useful clipboard integration under Hyprland.

---

## What **not** to do for Boxes storage

> [!warning]
> `~/.local/share/gnome-boxes/images` contains the **actual VM disk images**, not disposable installer scratch data.  
> Symlinking this directory to zram/tmpfs makes those VM disks **RAM-backed and non-persistent** unless that is explicitly your goal.

The following pattern is **not** a safe “temporary installer acceleration” trick:

```bash
mkdir -p /mnt/zram1/boxes_vm/
rm -rf ~/.local/share/gnome-boxes/images
ln -nfs /mnt/zram1/boxes_vm "$HOME/.local/share/gnome-boxes/images"
```

That redirects the VM image directory itself.

### Safer alternatives

If you only want transient temp files on a RAM-backed filesystem, use a temporary directory for the application process rather than replacing the VM image store.

Example using the user runtime dir:

```bash
mkdir -p "$XDG_RUNTIME_DIR/gnome-boxes-tmp"
TMPDIR="$XDG_RUNTIME_DIR/gnome-boxes-tmp" gnome-boxes
```

Or use your own mounted RAM-backed path:

```bash
mkdir -p /mnt/zram1/gnome-boxes-tmp
TMPDIR=/mnt/zram1/gnome-boxes-tmp gnome-boxes
```

> [!note]
> `TMPDIR` affects transient temp-file usage only. It does **not** relocate existing VM disks.  
> If you want persistent VM disks on a different filesystem, move the images there deliberately and update the VM disk path via virt-manager or `virsh`.

---

## System libvirt: only if you intentionally use `qemu:///system`

For **GNOME Boxes default usage**, skip this entire section.

If you intentionally manage VMs in the **system** libvirt scope instead:

- VMs live under `qemu:///system`
- non-root management typically requires membership in the `libvirt` group
- service/socket activation is a separate libvirt setup task
- this is **not** the same inventory used by Boxes

> [!note]
> The old pattern of enabling `libvirtd.service` globally is not the default recommendation for Boxes-based user-session workflows.  
> For modern libvirt deployments, follow the current Arch libvirt/socket-activation guidance appropriate to the scope you actually intend to use.

### Group membership caveat

- `libvirt` group: relevant for **system libvirt management**
- `kvm` group: **not usually required** for local desktop use with logind ACLs; only add it if your rootless session lacks access to `/dev/kvm`

Do **not** add yourself to `libvirt,kvm` as a reflex just because Boxes exists.

---

## Logging and troubleshooting

### Host-side checks

List Boxes/session VMs:

```bash
virsh --connect qemu:///session list --all
```

Search the user journal for Boxes or virt-manager messages:

```bash
journalctl --user -b --grep='gnome-boxes|org.gnome.Boxes|virt-manager'
```

Per-VM libvirt session logs are typically under:

```bash
ls ~/.cache/libvirt/qemu/log/
```

### Guest-side checks

SPICE daemon logs:

```bash
journalctl -b -u spice-vdagentd.service
```

User-session logs:

```bash
journalctl --user -b --grep='spice-vdagent|wl-paste|xclip'
```

### High-value checklist

1. **Boxes VM visible in the correct libvirt scope**
   ```bash
   virsh --connect qemu:///session list --all
   ```

2. **Graphics type is SPICE**
   ```bash
   virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A2 "<graphics type='spice'"
   ```

3. **SPICE agent channel exists**
   ```bash
   virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A3 "com.redhat.spice.0"
   ```

4. **Guest daemon is running**
   ```bash
   systemctl status spice-vdagentd.service
   ```

5. **Guest user agent is running**
   ```bash
   pgrep -af spice-vdagent
   ```

6. **Xwayland is present**
   ```bash
   pgrep -af Xwayland
   ```

7. **SPICE client is actually attached**
   - keep Boxes or the virt-manager viewer open
   - clipboard sync does not exist for a headless guest with no SPICE client attached

8. **If guest native Wayland copies do not reach host**
   - add the guest-side `wl-paste --watch xclip` bridge
   - verify `wl-clipboard` and `xclip` are installed inside the guest

> [!tip]
> If clipboard behavior is inconsistent, temporarily stop third-party clipboard managers inside the guest and retest. They can interfere with ownership timing on both X11 and Wayland clipboards.

---

## Quick reference

### Host

Install:

```bash
sudo pacman -Syu --needed gnome-boxes virt-manager
```

Open the same VM inventory Boxes uses:

```bash
virt-manager --connect qemu:///session
```

### Guest

Install:

```bash
sudo pacman -Syu --needed spice-vdagent xorg-xwayland
```

Enable daemon:

```bash
sudo systemctl enable --now spice-vdagentd.service
```

Start user agent:

```bash
spice-vdagent
```

Optional bridge for Hyprland clipboard edge cases:

```bash
sudo pacman -Syu --needed wl-clipboard xclip
wl-paste --type text --watch xclip -selection clipboard
```

---

## Related but separate feature: shared folders

> [!note]
> **Clipboard sharing** and **shared folders** are separate SPICE features.  
> If you want guest-accessible shared folders, you typically need **`spice-webdavd`** inside the guest plus the corresponding SPICE WebDAV channel. `spice-vdagent` alone does not provide that.
