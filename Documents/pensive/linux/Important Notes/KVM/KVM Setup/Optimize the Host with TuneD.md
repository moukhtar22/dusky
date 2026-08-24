---
title: "Host Tuning — TuneD (Arch)"
tags:
  - kvm
  - tuning
  - arch
  - latency
---

# Host Tuning — TuneD

> [!info] Scope
> Optional. `tuned` optimizes kernel scheduling/I/O for KVM host (`virtual-host` profile). Mutually exclusive with **TLP** power manager — pick one. This note reflects current `tuned` on Arch rolling (Aug 2026).

> [!danger] TLP conflict
> `tuned` and `TLP` both rewrite `sysctl`/`cpufreq`/`usb` autosuspend. Running both = flapping governors, conflicting `udev` rules. **If you use TLP on a laptop → skip this note.**

## Install & enable

```bash
sudo pacman -S --needed tuned
sudo systemctl enable --now tuned
tuned-adm active      # → balanced (default)
tuned-adm list | grep -E 'virtual-host|throughput'
```

## Activate `virtual-host`

```bash
tuned-adm list   # full catalogue (see callout below)
sudo tuned-adm profile virtual-host
tuned-adm active # → Current active profile: virtual-host
sudo tuned-adm verify   # → Verification succeeded
```

> [!example]- Profile catalogue (reference — click to expand)
> ```
> - accelerator-performance       - Throughput performance based tuning with disabled higher latency STOP states
> - atomic-guest                  - Optimize virtual guests based on the Atomic variant
> - atomic-host                   - Optimize bare metal systems running the Atomic variant
> - aws                           - Optimize for aws ec2 instances
> - balanced                      - General non-specialized tuned profile
> - balanced-battery              - Balanced profile biased towards power savings changes for battery
> - cpu-partitioning              - Optimize for CPU partitioning
> - cpu-partitioning-powersave    - Optimize for CPU partitioning with additional powersave
> - default                       - Legacy default tuned profile
> - desktop                       - Optimize for the desktop use-case
> - desktop-powersave             - Optmize for the desktop use-case with power saving
> - enterprise-storage            - Legacy profile for RHEL6, for RHEL7, please use throughput-performance profile
> - hpc-compute                   - Optimize for HPC compute workloads
> - intel-sst                     - Configure for Intel Speed Select Base Frequency
> - laptop-ac-powersave           - Optimize for laptop with power savings
> - laptop-battery-powersave      - Optimize laptop profile with more aggressive power saving
> - latency-performance           - Optimize for deterministic performance at the cost of increased power consumption
> - mssql                         - Optimize for Microsoft SQL Server
> - network-latency               - Optimize for deterministic performance at the cost of increased power consumption, focused on low latency network performance
> - network-throughput            - Optimize for streaming network throughput, generally only necessary on older CPUs or 40G+ networks
> - openshift                     - Optimize systems running OpenShift (parent profile)
> - openshift-control-plane       - Optimize systems running OpenShift control plane
> - openshift-node                - Optimize systems running OpenShift nodes
> - optimize-serial-console       - Optimize for serial console use.
> - oracle                        - Optimize for Oracle RDBMS
> - postgresql                    - Optimize for PostgreSQL server
> - powersave                     - Optimize for low power consumption
> - realtime                      - Optimize for realtime workloads
> - realtime-virtual-guest        - Optimize for realtime workloads running within a KVM guest
> - realtime-virtual-host         - Optimize for KVM guests running realtime workloads
> - sap-hana                      - Optimize for SAP HANA
> - sap-hana-kvm-guest            - Optimize for running SAP HANA on KVM inside a virtual guest
> - sap-netweaver                 - Optimize for SAP NetWeaver
> - server-powersave              - Optimize for server power savings
> - spectrumscale-ece             - Optimized for Spectrum Scale Erasure Code Edition Servers
> - spindown-disk                 - Optimize for power saving by spinning-down rotational disks
> - throughput-performance        - Broadly applicable tuning that provides excellent performance across a variety of common server workloads
> - virtual-guest                 - Optimize for running inside a virtual guest
> - virtual-host                  - Optimize for running KVM guests
> ```
> For KVM host, `virtual-host` is tuned for I/O scheduling and dirty/writeback that benefits qcow2/`virtio`. Matches old exhaustive list; kept collapsible so main prose stays succinct.

## Verify & revert

```bash
sudo tuned-adm verify
systemctl status tuned
# revert
sudo tuned-adm profile balanced
# or on TLP laptops:
sudo pacman -Rns tuned; sudo systemctl enable --now tlp
```

Related: [[+ MOC KVM]], [[KVM Services]] — tuning complements modular idle savings.
