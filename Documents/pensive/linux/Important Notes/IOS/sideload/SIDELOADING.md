# iOS Sideloading on Arch

## Install

```sh
sudo pacman -S usbmuxd libimobiledevice --needed
sudo systemctl enable --now usbmuxd
```

## Steps

1. Unlock iPhone, plug in via USB (good cable, no hubs)
2. Tap **Trust This Computer** on iPhone if prompted
3. Verify detection:
   ```sh
   sudo idevice_id -l
   ```
4. Launch Impactor:
   ```sh
   /mnt/zram/Impactor-linux-x86_64.appimage
   ```

## Fixes

| Problem | Fix |
|---------|-----|
| No device found | `sudo systemctl restart usbmuxd && sudo idevicepair pair && sudo idevice_id -l` |
| Not trusted | `sudo idevicepair pair` then check iPhone for Trust prompt |
| Keeps disconnecting | Bad cable or dirty Lightning port |
