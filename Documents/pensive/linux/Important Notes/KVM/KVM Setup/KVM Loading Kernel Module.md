# KVM Kernel Modules Setup

> [!abstract] What are we doing?
> 
> Before installing tools like QEMU or libvirt, we must ensure your Arch Linux kernel is legally allowed to act as a Hypervisor. We do this by verifying the **[[KVM]] (Kernel-based Virtual Machine)** modules are actively bridging your hardware's virtualization extensions to the OS.

## 1. Verify Existing Modules (Modern Autoload)

In modern Arch Linux (Kernel 7.1+), the `systemd-udevd` service automatically detects your CPU capabilities during boot and loads the correct KVM modules.

Let's verify that this automatic process succeeded. Open your terminal and check the loaded modules:

```
lsmod | grep -iE kvm
```

### Understanding the Output

> [!success] Scenario A: Modules are Loaded
> 
> If the system successfully detected your hardware, you will see output similar to this:
> 
> ```
> kvm_intel             401408  0
> kvm                  1204224  1 kvm_intel
> irqbypass             16384   1 kvm
> ```
> 
> _(Note: AMD users will see `kvm_amd` instead of `kvm_intel`)_.
> 
> **Action:** If you see this output, you are done! The modern kernel handled it. You can skip the rest of this note.

> [!failure] Scenario B: No Output
> 
> If the command returns nothing (a blank line), the modules are not loaded. This usually means **Virtualization is disabled in your BIOS/UEFI**, or you are running a custom kernel without KVM support. Double-check your firmware settings. If firmware is correct, proceed to Step 2 to force-load them.

## 2. Manual Loading (Fallback)

If `udev` failed to load the modules but your BIOS is configured correctly, we can load them manually into the current session.

Run the command corresponding to your CPU manufacturer:

**For Intel Processors:**

```
sudo modprobe kvm_intel
```

**For AMD Processors:**

```
sudo modprobe kvm_amd
```

Verify again with `lsmod | grep kvm`. If it worked, proceed to Step 3 to make it permanent.

## 3. Configure Auto-load on Boot (Persistent)

Using `modprobe` only injects the module until your next reboot. To enforce KVM loading every time your machine turns on, we must create a configuration file in `/etc/modules-load.d/`.

Run the command for your specific CPU architecture:

**For Intel:**

```
echo "kvm_intel" | sudo tee /etc/modules-load.d/kvm.conf
```

**For AMD:**

```
echo "kvm_amd" | sudo tee /etc/modules-load.d/kvm.conf
```

> [!info] What did this command do?
> 
> It created a persistent text file named `kvm.conf`. During the boot sequence, `systemd` reads this directory and guarantees the hypervisor modules are injected into the kernel before your graphical desktop even starts.

## 4. Apply Changes

To ensure the persistent configuration hooks in properly, reboot your system.

```
systemctl reboot
```