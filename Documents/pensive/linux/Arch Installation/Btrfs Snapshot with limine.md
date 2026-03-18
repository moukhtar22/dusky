# Complete BTRFS + Snapper + Limine Integration Guide

This guide configures Snapper with fully isolated subvolumes, `snap-pac` for automated pacman snapshots, and `limine-snapper-sync` with OverlayFS. This combination allows you to boot directly into read-only snapshots from the Limine bootloader menu, mounting them transparently as read-write via OverlayFS for seamless rollbacks.

> [!info] **Prerequisites**
> - You have already booted into your installed Arch system.
> - Your top-level BTRFS subvolumes (`@snapshots` and `@home_snapshots`) were already created during the base installation.
> - You are using `systemd` in your mkinitcpio hooks (modern Arch standard).

---

## Phase 1: Install Required Packages

We need the core snapshotting utilities, module preservation hooks, and the Limine integration tools.

**1a. Install Official Packages**
```bash
sudo pacman -S --needed snapper snap-pac btrfs-progs kernel-modules-hook

```

> [!note] `kernel-modules-hook` ensures that your current kernel modules are preserved during an update, preventing loss of functionality (like networking or USB) before you reboot.

**1b. Install AUR Dependencies (Java for build)**

> [!note] Building the Limine hooks from the AUR currently requires a Java 21+ environment as a build dependency.

```bash
sudo pacman -S --needed jdk21-openjdk

```

**1c. Install AUR Integration Packages**
Using your AUR helper, install the Limine sync daemon and mkinitcpio hooks:

```bash
paru -S --needed limine-snapper-sync limine-mkinitcpio-hook

```

---

## Phase 2: Initialize Snapper Configs & Isolate Subvolumes

> [!danger] **Critical Step:** When Snapper creates a config, it automatically generates a *nested* `.snapshots` subvolume (e.g., `/@/.snapshots`). We must delete these and mount our dedicated *top-level* subvolumes instead so that snapshot metadata survives root rollbacks.

**2a. Create the Snapper Configurations**

```bash
sudo snapper -c root create-config /
sudo snapper -c home create-config /home

```

**2b. Delete the Auto-Generated Nested Subvolumes**

```bash
sudo btrfs subvolume delete /.snapshots
sudo btrfs subvolume delete /home/.snapshots

```

**2c. Recreate Empty Mount Point Directories**

```bash
sudo mkdir -p /.snapshots
sudo mkdir -p /home/.snapshots

```

**2d. Mount the Dedicated Top-Level Subvolumes**

```bash
sudo mount -o rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvol=/@snapshots /dev/mapper/cryptroot /.snapshots

sudo mount -o rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvol=/@home_snapshots /dev/mapper/cryptroot /home/.snapshots

```

**2e. Set Strict Permissions**

```bash
sudo chmod 750 /.snapshots
sudo chmod 750 /home/.snapshots

```

**2f. Make Mounts Persistent in fstab**
Open `/etc/fstab` and append the mounts for your snapshot subvolumes so they survive reboots.

```bash
sudo nvim /etc/fstab

```

Add these lines (ensure you use your actual BTRFS UUID):

```text
UUID=<YOUR-BTRFS-UUID> /.snapshots      btrfs rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvol=/@snapshots 0 0
UUID=<YOUR-BTRFS-UUID> /home/.snapshots btrfs rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvol=/@home_snapshots 0 0

```

Run `sudo systemctl daemon-reload` afterward.

---

## Phase 3: Tune Snapper Limits & Disable Quotas

To maximize SSD performance and prevent I/O bottlenecks, we strictly enforce count-based snapshot limits and completely disable BTRFS quotas.

**3a. Tune Root and Home Configs**
Edit both `/etc/snapper/configs/root` and `/etc/snapper/configs/home`:

```bash
sudo nvim /etc/snapper/configs/root
# and
sudo nvim /etc/snapper/configs/home

```

Modify these specific keys in both files:

```ini
TIMELINE_CREATE="no"
NUMBER_LIMIT="10"
NUMBER_LIMIT_IMPORTANT="5"
SPACE_LIMIT="0"
FREE_LIMIT="0"

```

**3b. Allow Non-Root User Access (Optional but Recommended)**
To allow your user to list snapshots and view diffs without `sudo`:

```bash
sudo sed -i "s/^ALLOW_USERS=\"\"/ALLOW_USERS=\"$USER\"/" /etc/snapper/configs/root
sudo sed -i "s/^ALLOW_USERS=\"\"/ALLOW_USERS=\"$USER\"/" /etc/snapper/configs/home

```

**3c. Disable Global BTRFS Quotas (Performance Optimization)**

```bash
sudo btrfs quota disable /

```

**3d. Configure `snap-pac**`
`snap-pac` creates pre/post snapshot pairs whenever you use Pacman.

```bash
sudo nvim /etc/snap-pac.ini

```

Ensure the file targets both root and home:

```ini
[root]
snapshot = yes

[home]
snapshot = yes

```

---

## Phase 4: Configure Limine & Kernel Parameters

The `limine-update` hook requires explicit configuration to know your ESP path and kernel parameters.

**4a. Define the Kernel Command Line**
Create `/etc/kernel/cmdline` to explicitly define your boot flags for the integration tools.

```bash
sudo mkdir -p /etc/kernel
sudo nvim /etc/kernel/cmdline

```

Insert your exact parameters (adjust LUKS UUID accordingly):

```text
rd.luks.name=<YOUR-LUKS-UUID>=cryptroot root=/dev/mapper/cryptroot rootflags=subvol=/@ rw quiet rootfstype=btrfs

```

**4b. Define the ESP Path for Limine**
Configure `/etc/default/limine` so `limine-update` knows where to deploy entries.

```bash
sudo nvim /etc/default/limine

```

Add:

```ini
ESP_PATH="/boot"

```

---

## Phase 5: Configure Limine OverlayFS Boot Integration

This step injects an OverlayFS hook into your initramfs. When booting a read-only snapshot, this hook mounts an ephemeral read-write layer over it in RAM.

**5a. Inject the mkinitcpio Hook**
Because you use the modern `systemd` hook in mkinitcpio, we must use `sd-btrfs-overlayfs` instead of the busybox equivalent.

```bash
sudo mkdir -p /etc/mkinitcpio.conf.d
sudo nvim /etc/mkinitcpio.conf.d/zz-limine-overlayfs.conf

```

Add the following snippet:

```bash
# Managed by limine + snapper integration setup
if [[ " ${HOOKS[*]} " != *" sd-btrfs-overlayfs "* ]]; then
    _new_hooks=()
    for _h in "${HOOKS[@]}"; do
        _new_hooks+=("$_h")
        if [[ "$_h" == "filesystems" ]]; then
            _new_hooks+=("sd-btrfs-overlayfs")
        fi
    done
    HOOKS=("${_new_hooks[@]}")
    unset _new_hooks _h
fi

```

**5b. Rebuild Initramfs & Update Limine**

```bash
sudo limine-update

```

---

## Phase 6: Configure `limine-snapper-sync`

This daemon automatically reads your Snapper snapshots and translates them into Limine boot menu entries.

**6a. Define Subvolume Paths**

```bash
sudo nvim /etc/limine-snapper-sync.conf

```

Update these two variables to point exactly to your BTRFS topology:

```ini
ROOT_SUBVOLUME_PATH="/@"
ROOT_SNAPSHOTS_PATH="/@snapshots"

```

---

## Phase 7: Finalization, Baseline & Services

**7a. Create a Baseline Snapshot**
Create a known-good starting point now that integration is fully configured.

```bash
sudo snapper -c root create -t single -c important -d "Baseline after Limine + Snapper integration"
sudo snapper -c home create -t single -c important -d "Baseline after Limine + Snapper integration"

```

**7b. Enable Background Services**
Enable Snapper's cleanup timer (to enforce the `10` snapshot limit) and the Limine sync daemon (to populate your boot menu).

```bash
sudo systemctl enable --now snapper-cleanup.timer
sudo systemctl enable --now limine-snapper-sync.service

```

> [!tip] **Verification**
> Check your Limine boot menu upon your next reboot. You should now see an "Arch Linux Snapshots" submenu containing your newly created baseline snapshot. Selecting it will boot your system statelessly using OverlayFS!

---

## Phase 8: Day-to-Day Operations

### Manual Snapshots

```bash
# Create a snapshot before making a risky change
sudo snapper -c root create -c number -d "Before risky config change"

```

### Viewing Diff and Status

```bash
# See files added/modified/deleted between snapshot 1 and current (0)
snapper -c root status 1..0

# See exact code/file differences
sudo snapper -c root diff 1..0

```

### Undoing Changes & Restoring Single Files

```bash
# Revert all changes made between snapshot 1 and 2
sudo snapper -c root undochange 1..2

# Restore a single file from a snapshot WITHOUT rolling back the whole system
sudo cp /.snapshots/3/snapshot/etc/pacman.conf /etc/pacman.conf

```

### Manual Cleanup

```bash
# Force cleanup algorithm to run immediately
sudo snapper -c root cleanup number
sudo snapper -c root delete 1 2 3

```

---

## Phase 9: Rollback Procedures

### Method A: Permanent Rollback via Limine OverlayFS (Recommended)

> [!info] **Scenario:** You broke the system, rebooted, selected a snapshot from the Limine menu, and are currently running in the ephemeral RAM overlay environment.

**1. Identify Current Snapshot:**

```bash
cat /proc/cmdline | grep -o 'rootflags=subvol=[^ ]*'

```

*(Assume it outputs `rootflags=subvol=/@snapshots/42/snapshot`)*

**2. Mount Top-Level & Isolate:**

```bash
sudo mkdir -p /mnt/btrfs-top
sudo mount -o subvolid=5 /dev/mapper/cryptroot /mnt/btrfs-top

sudo mv /mnt/btrfs-top/@ /mnt/btrfs-top/@.broken

```

**3. Promote Snapshot to Root:**

```bash
sudo btrfs subvolume snapshot /mnt/btrfs-top/@snapshots/42/snapshot /mnt/btrfs-top/@

```

**4. Cleanup & Reboot:**

```bash
sudo umount /mnt/btrfs-top
sudo reboot

```

*(Select the normal "Arch Linux" entry on reboot. Once confirmed working, delete the `@.broken` subvolume).*

### Method B: The "Nuclear" Live USB Rollback

> [!info] **Scenario:** The bootloader itself is destroyed or mkinitcpio completely failed. You cannot even reach the Limine Snapshot menu.

1. Boot from an Arch Linux Live USB.
2. Open LUKS and mount the top-level subvolume:
```bash
cryptsetup open /dev/nvme0n1p2 cryptroot
mount -o subvolid=5 /dev/mapper/cryptroot /mnt

```


3. Move the broken root and promote a snapshot:
```bash
mv /mnt/@ /mnt/@.broken
btrfs subvolume snapshot /mnt/@snapshots/42/snapshot /mnt/@

```


4. Unmount, close LUKS, and reboot:
```bash
umount /mnt
cryptsetup close cryptroot
reboot

```



---

## Phase 10: Maintenance & Arch Deep Dive

### BTRFS Maintenance

```bash
# Scrub: Periodic data integrity check
sudo btrfs scrub start /
sudo btrfs scrub status /

# Enable the systemd timer for automatic monthly scrubs:
sudo systemctl enable --now btrfs-scrub@-.timer

```

> [!warning] **Never Defragment Snapshots**
> Running `btrfs filesystem defragment` breaks the reflinks (shared data pointers) between your BTRFS snapshots. This will cause your disk usage to explode exponentially as each snapshot becomes a full, independent copy of the data.

### Troubleshooting

**Snapper says `.snapshots is not a subvolume**`
The auto-created subvolume conflicts with your dedicated one. Re-isolate it:

```bash
sudo umount /.snapshots
sudo btrfs subvolume delete /.snapshots
sudo mkdir /.snapshots
sudo mount -a
sudo chmod 750 /.snapshots

```

**`limine-update` produces no boot entries**

1. Check `/etc/default/limine` exists and has a valid `ESP_PATH`.
2. Check `/etc/kernel/cmdline` has valid boot flags.
3. Ensure the pacman hooks exist in `/usr/share/libalpm/hooks/`.

---

## Appendix: mkinitcpio Hook Architecture Deep Dive

As a systems architect, it is useful to understand how early userspace is constructed.

**File Structure:**

```text
/usr/lib/initcpio/
├── install/           ← Build hooks (defines what to pack into initramfs)
│   ├── base
│   ├── udev
│   ├── encrypt        ← LUKS decryption build hook
│   └── btrfs-overlayfs ← Snapshot overlay build hook
│
└── hooks/             ← Runtime hooks (the shell code that runs during boot)
    ├── encrypt         ← LUKS decryption runtime script
    └── btrfs-overlayfs ← Snapshot overlay runtime script

```

**How `btrfs-overlayfs` actually works:**

1. **Detection:** The runtime hook runs during early userspace and detects if you are booting from a snapshot (via kernel cmdline parameter injected by Limine).
2. **Read-Only Mount:** It mounts the BTRFS snapshot purely read-only (Lowerdir).
3. **RAM Layer:** It creates a `tmpfs` in RAM for the writable upper layer (Upperdir/Workdir).
4. **Overlay:** It creates an `overlayfs` combining the two.
5. **Pivot:** It pivots root to the overlayfs. The OS thinks it is writable, but all changes disappear on reboot.

