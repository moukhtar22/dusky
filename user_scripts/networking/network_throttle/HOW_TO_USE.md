---
title: Dusky Network Limiter - How to Use
tags:
  - networking
  - linux
  - ebpf
  - guide
---

# Dusky Network Limiter

> [!info] Overview
> **Dusky Network Limiter** is an eBPF-powered, kernel-level traffic shaping and firewall daemon. It intercepts network traffic at the socket level to provide pinpoint-accurate bandwidth limits and application blocking, complete with a graphical terminal interface.

---

## 🚀 Getting Started

The primary control script is the `netctl` wrapper located in the project root. Because the background daemon is managed by systemd (`netctl.service`) and runs as root, **you do not need to use `sudo`** to execute these commands.

```bash
# Navigate to the project directory
cd ~/user_scripts/networking/network_throttle

# View all available commands
./netctl --help
```

---

## 📊 Monitoring & Dashboards

> [!tip] Recommended
> Use the **TUI** for the best visual experience when monitoring your network.

### Launch the TUI Dashboard
The interactive Textual UI provides live graphs, connection tables, and firewall statuses.
```bash
./netctl tui
```

### View Live Statistics (CLI)
Instantly print a table of all active network processes and their bandwidth consumption.
```bash
./netctl stats
```

### Historical Data
- **Top Talkers:** See which apps used the most data over the system's lifetime.
  ```bash
  ./netctl top
  ```
- **Historical Report:** View a timeline chart of bandwidth usage.
  ```bash
  ./netctl report day   # Options: day, week, month, year
  ```

---

## 🚦 Traffic Shaping & Limits

> [!warning] Bandwidth Syntax
> Rates can be specified in `kbit`, `mbit`, `gbit` (e.g., `20mbit`, `500kbit`). If no unit is provided, it defaults to megabits.

### Global Interface Limits
Apply a system-wide cap to all incoming and outgoing network traffic.
```bash
# Symmetric limit (20mbit Up & Down)
./netctl limit 20mbit

# Asymmetric limits
./netctl limit --down 50mbit --up 10mbit
```

### Per-Application Limits
Throttle a specific application without affecting the rest of the system.
```bash
./netctl limit firefox 5mbit
```

---

## 🧱 Firewall Management

> [!caution] Application Blocking
> Blocking applications instantly routes their traffic into a restricted kernel cgroup, dropping packets at the eBPF layer.

### Block an Application
```bash
./netctl block telegram-desktop
```

### Unblock / Remove Limits
Use the `allow` command to lift restrictions or blocks previously applied to an application.
```bash
./netctl allow telegram-desktop
```

---

## 📅 Data Quotas

You can configure bandwidth consumption quotas to automatically kill network access when a data cap is reached.

### Configure a Quota
```bash
# Set a 50GB monthly cap, with desktop notifications at 80% and 90% usage
./netctl quota 50GB --period monthly --warning 0.8
```

### Check Quota Status
```bash
./netctl quota-status
```

### Reset Exhausted Quotas
If you have been blocked because a quota was exceeded, run this to reset the status and restore connectivity.
```bash
./netctl reset-quotas
```

---

## ⚙️ Architecture Notes

> [!note] How it Works
> - **eBPF Probes:** Telemetry is gathered silently via `kprobe__tcp_sendmsg` and `udp_sendmsg`.
> - **Traffic Control (tc):** Speed limits are enforced natively using Linux HTB (Hierarchical Token Bucket) classing and direct ingress policing.
> - **nftables:** Firewalls are managed dynamically via cgroup v2 socket matching.
> - **Wayland:** Notifications seamlessly pass through the root daemon back to your Hyprland session via DBus.
