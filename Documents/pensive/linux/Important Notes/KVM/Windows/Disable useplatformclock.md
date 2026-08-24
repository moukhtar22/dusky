---
title: "Disable useplatformclock (Fix Clock Stutter)"
tags:
  - kvm
  - windows
  - clock
  - hyperv
---

# Disable `useplatformclock`

> [!warning] When to do this
> When Hyper-V enlightenments (`hypervclock.present=yes` + `hyperv.synic/stimer`) are active, forcing `useplatformclock` + HPET creates lag/stutter. The fix is to **disable** it.

## Inside Windows VM (Admin)

Open **Command Prompt** or **PowerShell as Administrator**:

```powershell
bcdedit /set useplatformclock No
# verify
bcdedit /enum | findstr useplatformclock   # should show No or absent
Restart-Computer
```

> [!info] Pair with XML
> Virt-manager XML should be:
> ```xml
> <clock offset='localtime'>
>   <timer name='rtc' tickpolicy='catchup'/><timer name='pit' tickpolicy='delay'/>
>   <timer name='hpet' present='no'/><timer name='hypervclock' present='yes'/>
> </clock>
> ```
> (`hpet=no` + `hypervclock=yes` — matches `30_kvm_vm_deploy.py:build_command --clock`)

If stutter persists, verify `35_cpu_pinning_generator.py` pinning and that host `tuned` isn’t in `balanced` vs `virtual-host`.

See: [[Enable Hyper-V Enlightenments]], [[Optimize Windows Performance]].
