---
title: "Windows Guest — Passthrough Stack (VDD / LG / OpenSSH)"
tags:
  - kvm
  - windows
  - vfio
  - looking-glass
  - vdd
---

# Windows Guest — Passthrough Stack

> [!tip] Workflow
> Do this **inside the Windows VM** after you have a functional NAT or `br0` ([[Network Bridging for LAN access]]). SPICE viewer works until VDD/LG takes over.

## 1. Visual C++ Redistributable

Required for VDD + LG host.

- Download **VC++ latest supported** → Run installer → Reboot if prompted

<https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist>

## 2. Virtual Display Driver (VDD) — fake monitor for headless dGPU

Pass-through dGPU has no physical output → Windows needs an IDD sink or GPU goes idle / `Error 43`.

- **Repo:** <https://github.com/VirtualDrivers/Virtual-Display-Driver>

### A — GUI

Download latest `*.zip` → extract → `VirtualDriverControl.exe` → Install → confirm virtual monitor appears.

### B — CLI (scriptable)

Extract `.inf/.cer/.sys` to `C:\VirtualDisplayDriver` → Admin PowerShell:

```powershell
certutil -addstore "TrustedPublisher" C:\VirtualDisplayDriver\VirtualDisplayDriver.cer
certutil -addstore "Root"               C:\VirtualDisplayDriver\VirtualDisplayDriver.cer
# pick one:
devcon install C:\VirtualDisplayDriver\VirtualDisplayDriver.inf Root\MttVDD   # fresh node (recommended fresh VM)
pnputil /add-driver C:\VirtualDisplayDriver\VirtualDisplayDriver.inf /install
```

### Config (SDR lock — 10 MiB overhead rule)

`C:\VirtualDisplayDriver\vdd_settings.xml` → keep to **SDR single 1440p** (matches host 64 MiB):

```xml
<?xml version='1.0' encoding='utf-8'?>
<VirtualDisplaySettings>
   <Monitors>1</Monitors>
   <Resolution><Width>2560</Width><Height>1440</Height><RefreshRate>144</RefreshRate></Resolution>
</VirtualDisplaySettings>
```

> [!danger] One install only
> Re-running creates ghost monitors → cursor exits to invisible monitor. Keep **one** VDD node; disable/reenable to flush DWM cache.

## 3. Looking Glass Host (frame exporter)

Runs in Windows, matches **client** `looking-glass` version on host (`pacman -S looking-glass freerdp`).

- <https://looking-glass.io/downloads> → host binary → install → autostart (Scheduled Task or `host.ini`):
  Install to `C:\Program Files\Looking Glass (host)\` and set `looking-glass-host.exe` to run at logon. Version must match `looking-glass-client` on host (B7+).

## 4. NVIDIA Guest Driver

Standard GeForce driver for your card inside VM → install → reboot. Do **not** disable `NVIDIA Display` nor `VDD` in Device Manager.

## 5. Utilities

- **7-Zip** — <https://www.7-zip.org>
- **O&O ShutUp10++** — <https://www.oo-software.com/en/download/current/ooshutup10>
- **Windows Update MiniTool** — <https://www.majorgeeks.com/files/details/windows_update_minitool.html>
- **WinFsp** — <https://github.com/winfsp/winfsp> (if you enabled virtiofs sharing earlier)

## 6. OpenSSH Server (portable, not DISM)

`DISM /Add-Capability` often hangs on fresh VMs (WU lock). Use official Win32-OpenSSH portable:

```powershell
# 1. Download OpenSSH-Win64.zip from https://github.com/PowerShell/Win32-OpenSSH/releases
# 2. Expand to C:\Program Files\OpenSSH-Win64
powershell -ExecutionPolicy Bypass -File "C:\Program Files\OpenSSH-Win64\install-sshd.ps1"
Set-Service sshd -StartupType Automatic; Start-Service sshd
New-NetFirewallRule -Name "SSH" -DisplayName "OpenSSH SSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name "DefaultShell" -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
# for RDP rescue / WinFsp re-arm, this is your headless CLI bridge
```

## 7. Quirks

- **VirtIO-FS Service** appears only after `virtio-win-guest-tools` installed.
- **Cursor vanishing** after `vioinput` → Device Manager → uninstall mouse → reinstall while viewing via Looking Glass (or uninstall `vioinput` via x64 pkg then re-add).

Next: [[Looking Glass]] (host `$XDG` sharing `/dev/shm/looking-glass` wire-up).
