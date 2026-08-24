---
title: "Disable Unnecessary Scheduled Tasks"
tags:
  - kvm
  - windows
  - performance
---

# Disable Unnecessary Scheduled Tasks

> [!abstract] Goal
> Stop Windows background defrag/telemetry bursts that steal VM I/O/CPU.

## List tasks (Admin PowerShell)

```powershell
Get-ScheduledTask
Get-ScheduledTask -TaskName '*schedule*'
```

Example output:

```
TaskPath                                TaskName                     State
\Microsoft\Windows\Defrag\               ScheduledDefrag              Ready
\Microsoft\Windows\Diagnosis\            Scheduled                    Ready
\Microsoft\Windows\UpdateOrchestrator\   Schedule Scan                Ready
```

## Disable chosen tasks (example: defrag — host storage `discard=unmap` handles TRIM)

```powershell
Disable-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName ScheduledDefrag
# verify
Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName ScheduledDefrag | Select TaskPath,TaskName,State
# → State: Disabled
```

Re-enable if ever needed:

```powershell
Enable-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -TaskName ScheduledDefrag
```

> [!warning] Don’t blindly disable all
> Keep `Time Synchronization` / `UpdateOrchestrator` if you rely on Windows Update control via `Windows Update MiniTool` / `O&O ShutUp10`.

See: [[Optimize Windows Performance]].
