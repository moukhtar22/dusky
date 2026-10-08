#!/usr/bin/env python3
# 211_systemd_oomd_zram.py
#d: Authoritative OOM-protection deployer for a Hyprland desktop.
#   Target: Arch Linux rolling (kernel 7.3+), systemd 262+, Python 3.14+.
#   Suite order: 205 (ZRAM swap) -> 211 (this) -> 212 (THP/mTHP).
#
# HARD INVARIANTS (do not "optimise" these away):
#
#   1. ZERO MEMORY RESERVATION for the compositor / session. No MemoryMin=,
#      no MemoryLow= is emitted by this script, anywhere. Compositor survival
#      is strictly behavioural: ManagedOOMPreference=avoid + OOMPolicy=continue
#      + OOMScoreAdjust=-100.
#
#   2. SwapUsageMax=80% is intentional. 205 pairs a prio-32767 ZRAM device with
#      a prio--1 disk swap. The kernel drains the highest priority device first,
#      so 80% of *total* swap is only reachable once ZRAM is saturated and the
#      disk tier is actively buffering. 80% is the anti-thrash ceiling.
#      Never raise it to 90/95%.
#
#   3. Every rule uses Action=kill-by-pgscan -- including the swap rule.
#      systemd.resource-control(5): for *swap* candidate selection, oomd honours
#      user.oomd_avoid / user.oomd_omit ONLY on root-owned cgroups. Every cgroup
#      here lives under user@$UID.service and is user-owned, so Action=kill-by-swap
#      would silently discard the entire avoid hierarchy. kill-by-pgscan routes
#      through the memory-pressure candidate path, where oomd honours the xattr
#      when the candidate and the monitored ancestor share an owner.
#
#   4. /etc/systemd/system/user@.service.d/*.conf (OOMScoreAdjust=-100) is a
#      PRECONDITION for every user-level OOMScoreAdjust=-100 drop-in. Lowering
#      oom_score_adj below the current value needs CAP_SYS_RESOURCE; the user
#      manager has none. systemd treats a failed adjust as fatal (EXIT_OOM_ADJUST,
#      status 207). The user manager must already sit at -100 so that children
#      re-writing -100 perform an equal-value write, which the kernel permits.
#
#   5. MGLRU needs no sysfs writes: Arch kernel 7.3+ ships CONFIG_LRU_GEN_ENABLED=y
#      => /sys/kernel/mm/lru_gen/enabled == 0x0007 at boot. min_ttl_ms is
#      deliberately NOT set: per Documentation/admin-guide/mm/multigen_lru.rst it
#      invokes the *kernel* OOM killer, which bypasses every oomd preference.

import argparse
import errno
import filecmp
import functools
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SELF_PATH: Final[Path] = Path(__file__).resolve()
PROG: Final[str] = "211_systemd_oomd_zram"

# Rich is optional presentation only. Offline installs use the plain fallback
# rather than trying to install a package before arguments are parsed.
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    HAVE_RICH = True
    console: Console | None = Console()
except ImportError:
    HAVE_RICH = False
    console = None


# --------------------------------------------------------------------------- #
# Output helpers (rich + exact-parity plain fallback)
# --------------------------------------------------------------------------- #

def say(msg: str, *, style: str = "", plain: str | None = None) -> None:
    if HAVE_RICH and console:
        console.print(f"[{style}]{msg}[/]" if style else msg)
    else:
        print(plain if plain is not None else _strip_markup(msg))


def _strip_markup(s: str) -> str:
    return re.sub(r"\[/?[a-z0-9 ._#]*\]", "", s)


def warn(msg: str) -> None:
    say(msg, style="yellow", plain=f"[WARN] {_strip_markup(msg)}")


def fail(msg: str, code: int = 1) -> None:
    if HAVE_RICH and console:
        console.print(f"[bold red]{msg}[/]")
    else:
        print(f"[ERROR] {_strip_markup(msg)}", file=sys.stderr)
    sys.exit(code)


def panel(title: str) -> None:
    if HAVE_RICH and console:
        console.print(Panel.fit(f"[bold cyan]{title}[/]", box=box.DOUBLE))
    else:
        print(f"=== {_strip_markup(title)} ===")


# --------------------------------------------------------------------------- #
# OOM pressure tiering -- independent of 212's continuous THP policy.
#   7 GiB  = 7340032 KiB      14 GiB = 14680064 KiB      28 GiB = 29360128 KiB
# Keep the --tier S|M|L|P contract for discrete OOM response thresholds.
# --------------------------------------------------------------------------- #

def read_mem_total_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1048576.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def get_ram_tier(gib: float) -> str:
    if gib <= 0.0:
        return "M"          # unreadable /proc/meminfo -> safest middle profile
    if gib < 7.0:
        return "S"
    if gib < 14.0:
        return "M"
    if gib < 28.0:
        return "L"
    return "P"


TIER_PROFILES: Final[dict[str, dict[str, str]]] = {
    "S": {"pressure_above": "30%", "pressure_lasting": "3s",
          "swap_max": "80%", "swap_pressure": "5%", "swap_lasting": "1s",
          "bg_pressure_above": "20%", "bg_pressure_lasting": "2s",
          "label": "< 7 GiB"},
    "M": {"pressure_above": "35%", "pressure_lasting": "5s",
          "swap_max": "80%", "swap_pressure": "5%", "swap_lasting": "1s",
          "bg_pressure_above": "20%", "bg_pressure_lasting": "3s",
          "label": "7 - <14 GiB"},
    "L": {"pressure_above": "40%", "pressure_lasting": "5s",
          "swap_max": "80%", "swap_pressure": "10%", "swap_lasting": "2s",
          "bg_pressure_above": "25%", "bg_pressure_lasting": "3s",
          "label": "14 - <28 GiB"},
    "P": {"pressure_above": "40%", "pressure_lasting": "10s",
          "swap_max": "80%", "swap_pressure": "15%", "swap_lasting": "3s",
          "bg_pressure_above": "25%", "bg_pressure_lasting": "5s",
          "label": ">= 28 GiB"},
}

HDR: Final[str] = "# Managed by 211_systemd_oomd_zram.py -- local edits are overwritten.\n"

# --------------------------------------------------------------------------- #
# Static unit / config payloads
# --------------------------------------------------------------------------- #

OOMD_TUNE: Final[str] = HDR + """#
# PrekillHookTimeoutSec= (oomd.conf(5), v260+) is pinned to 0s: the prekill
# varlink hook is synchronous and delays the kill. On an interactive desktop the
# kill must be immediate; pressure notification is handled the unprivileged,
# asynchronous way (MEMORY_PRESSURE / sd_event_add_memory_pressure).
#
# SwapUsedLimit= and DefaultMemoryPressure{Limit,DurationSec}= are deliberately
# NOT set: they only govern the legacy ManagedOOM*=kill path, which every unit
# in this deployment opts out of in favour of explicit OOMRules= rulesets.
[OOM]
PrekillHookTimeoutSec=0s
"""

APP_SLICE: Final[str] = HDR + """#
# app.slice is the monitored ancestor for interactive applications.
# ManagedOOM*=auto is an explicit *opt-out* of the legacy global-limit path
# (systemd.resource-control(5): "Defaults to auto. When set to kill, the unit
# becomes a candidate"). It is written explicitly so that a vendor drop-in
# setting =kill cannot re-enable double monitoring behind our back.
# OOMRules= is a list: the bare reset then the assignment guarantees exactly
# these two rulesets regardless of drop-in ordering.
[Slice]
ManagedOOMSwap=auto
ManagedOOMMemoryPressure=auto
ManagedOOMPreference=none
MemoryAccounting=yes
OOMRules=
OOMRules=30-dusky-pressure 30-dusky-swap 30-dusky-swap-ceiling
"""

BACKGROUND_SLICE: Final[str] = HDR + """[Slice]
ManagedOOMSwap=auto
ManagedOOMMemoryPressure=auto
ManagedOOMPreference=none
MemoryAccounting=yes
OOMRules=
OOMRules=30-dusky-background 30-dusky-swap 30-dusky-swap-ceiling
"""

SESSION_SLICE: Final[str] = HDR + """#
# Defence in depth. session.slice is not itself monitored by our rulesets, but
# if any ancestor (-.slice, user@.service) is ever monitored by a vendor drop-in
# this makes the whole session bucket a last-resort candidate.
# NO MemoryMin= / MemoryLow= -- protection here is strictly behavioural.
[Slice]
ManagedOOMPreference=avoid
MemoryAccounting=yes
"""

COMPOSITOR_SCOPE: Final[str] = HDR + """#
# Applies to session-N.scope via systemd.unit(5) dash-truncation drop-in lookup
# (session-7.scope also reads session-.scope.d/). This is the logind scope that
# hosts the Hyprland compositor.
#
# HARD CONSTRAINT: no MemoryMin=, no MemoryLow=. The compositor stays at the
# inherited 0/0 and is therefore *unprotected by memory reservation* on purpose.
# Survival is behavioural only:
#   OOMPolicy=continue          -> a kill inside the scope does not tear it down
#   ManagedOOMPreference=avoid  -> oomd picks it only if nothing else is viable
# Verify with: systemctl show session-$XDG_SESSION_ID.scope -p MemoryMin -p MemoryLow
[Scope]
OOMPolicy=continue
ManagedOOMPreference=avoid
MemoryAccounting=yes
"""

USER_MANAGER_SCORE: Final[str] = HDR + """#
# PRECONDITION for every user-level OOMScoreAdjust=-100 drop-in.
# The per-user manager has no CAP_SYS_RESOURCE; the kernel refuses to lower
# oom_score_adj below the current value without it, and systemd treats the
# failure as fatal (EXIT_OOM_ADJUST / status 207). Pinning the manager at -100
# makes each child's -100 an equal-value write, which is always permitted.
[Service]
OOMScoreAdjust=-100
OOMPolicy=continue
"""

USER_CONF: Final[str] = HDR + """#
# Read by the per-user manager AT START -- requires a full re-login
# (or: loginctl terminate-user $USER) to take effect.
#
# DefaultMemoryAccounting=yes is required for kill-by-pgscan to have a sort key:
# oomd ranks candidates by the delta of memory.stat pgscan_* per interval.
[Manager]
DefaultOOMScoreAdjust=100
DefaultMemoryAccounting=yes
DefaultMemoryPressureWatch=yes
"""

OOM_SHIELD: Final[str] = HDR + """[Service]
Slice=session.slice
OOMScoreAdjust=-100
OOMPolicy=continue
ManagedOOMPreference=avoid
MemoryAccounting=yes
"""

OOMD_SERVICE_SHIELD: Final[str] = HDR + """#
# The arbiter must never be the victim. -1000 makes systemd-oomd immune to the
# kernel OOM killer that would otherwise fire during the same stall it is
# trying to resolve.
[Service]
OOMScoreAdjust=-1000
"""

# dbus.service is an *alias* on Arch (dbus-broker is the default implementation);
# alias drop-in resolution is fragile, so both names are shielded.
CRITICAL_USER: Final[tuple[str, ...]] = (
    "pipewire.service",
    "pipewire-pulse.service",
    "wireplumber.service",
    "dbus.service",
    "dbus-broker.service",
    "xdg-desktop-portal.service",
    "xdg-desktop-portal-hyprland.service",
    "xdg-desktop-portal-gtk.service",
    "xdg-document-portal.service",
    "xdg-permission-store.service",
    "gnome-keyring-daemon.service",
    "mako.service",
    "hypridle.service",
    "hyprpolkitagent.service",
)

# Superseded artefacts removed on every run (idempotent cleanup).
# The 10- prefix under /etc violates the drop-in precedence guidance in
# oomd.conf(5) ("10-40 for /usr, 60-90 for /etc") and could be shadowed.
LEGACY_PATHS: Final[tuple[Path, ...]] = (
    Path("/etc/systemd/oomd.conf.d/10-desktop-tune.conf"),
)

# --------------------------------------------------------------------------- #
# dusky-run  --  raw string: every backslash below is literal shell syntax
# --------------------------------------------------------------------------- #

DUSKY_RUN_WRAPPER: Final[str] = r"""#!/bin/bash
# dusky-run -- launch a command inside a transient, oomd-classified user scope.
# Managed by 211_systemd_oomd_zram.py -- local edits are overwritten.
#
# Why this exists: a process launched directly from a Hyprland exec keybind
# inherits the compositor's session-N.scope, which is a *system* scope owned by
# PID 1. It is therefore invisible to app.slice's OOMRules and cannot be
# protected by dusky-oom-shield. dusky-run reparents it into app.slice (or
# background.slice / session.slice) so the whole OOM policy applies to it.
#
# Env:
#   DUSKY_GRACE_SEC   seconds of ManagedOOMPreference=avoid after launch
#                     (default 0; 0 disables the grace period entirely)
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: dusky-run [--session|--background] [--] <cmd> [args...]

  (default)      app.slice        oom_score_adj=200  preference=none
  --session      session.slice    oom_score_adj=100  preference=avoid
  --background   background.slice oom_score_adj=300  preference=none

env: DUSKY_GRACE_SEC=<int>   avoid-window in seconds (default 0, 0 = off)
EOF
  exit 1
}

[[ $# -gt 0 ]] || usage

slice="app.slice"
score=200
preference="none"

case "${1-}" in
  --session)    slice="session.slice"; score=100; preference="avoid"; shift ;;
  --background) slice="background.slice"; score=300; preference="none"; shift ;;
  -h|--help)    usage ;;
esac
[[ "${1-}" == "--" ]] && shift
[[ $# -gt 0 ]] || usage

# If launched command is a desktop panel/daemon, place in session.slice
raw_name="${1##*/}"
case "$raw_name" in
  waybar|mako|swaync|hypridle|hyprpaper|awww-daemon|wpaperd|hyprpolkitagent)
    slice="session.slice"
    score=100
    preference="avoid"
    ;;
esac

grace_sec="${DUSKY_GRACE_SEC:-0}"
[[ "$grace_sec" =~ ^[0-9]+$ ]] || grace_sec=0
if [[ "$slice" == "app.slice" && "$grace_sec" -gt 0 ]]; then
  preference="avoid"
fi

if ! command -v -- "$1" >/dev/null 2>&1 && [[ ! -x "$1" ]]; then
  echo "dusky-run: command not found: $1" >&2
  exit 127
fi

# oom_score_adj is only ever RAISED here (user units default to 100 via
# DefaultOOMScoreAdjust), so this never needs CAP_SYS_RESOURCE. A failure is
# non-fatal: the cgroup-level oomd policy is the primary mechanism, the kernel
# score is only the backstop ordering.
if ! printf '%d\n' "$score" > /proc/self/oom_score_adj 2>/dev/null; then
  echo "dusky-run: warning: cannot set oom_score_adj to $score" >&2
fi

app_name="$(printf '%s' "$raw_name" | tr -cd 'a-zA-Z0-9_.-')"
app_name="${app_name:-app}"
app_name="${app_name:0:64}"
unit="app-${app_name}-$$-${RANDOM}${RANDOM}"

# Grace period: if explicitly requested via DUSKY_GRACE_SEC for an app.slice
# application, ensure user.oomd_avoid is removed from the cgroup upon expiry.
if [[ "$preference" == "avoid" && "$grace_sec" -gt 0 && "$slice" == "app.slice" ]]; then
  # shellcheck disable=SC2016 # Variables belong to the detached helper shell.
  setsid --fork bash -c '
    sleep "$1"
    unit="$2.scope"
    systemctl --user -q is-active "$unit" 2>/dev/null || exit 0
    systemctl --user set-property "$unit" ManagedOOMPreference=none >/dev/null 2>&1 || true
    cgroup="/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/app.slice/$unit"
    if [[ -d "$cgroup" ]]; then
      if ! getfattr -n user.dusky_shield "$cgroup" >/dev/null 2>&1; then
        setfattr -x user.oomd_avoid "$cgroup" 2>/dev/null || true
      fi
    fi
  ' _ "$grace_sec" "$unit" </dev/null >/dev/null 2>&1 &
  disown || true
fi

# --collect is mandatory: a scope whose only process was SIGKILLed by oomd would
# otherwise linger in "failed" state and its empty cgroup keeps showing up in
# the candidate ranking.
exec systemd-run --user --scope --slice="$slice" --unit="$unit" --collect --quiet --expand-environment=no \
  --property=OOMPolicy=continue \
  --property=ManagedOOMPreference="$preference" \
  --property=MemoryAccounting=yes \
  -- "$@"
"""

# --------------------------------------------------------------------------- #
# dusky-oom-shield.c  --  raw string: all backslashes are literal C
# --------------------------------------------------------------------------- #

DUSKY_OOM_SHIELD_C: Final[str] = r"""/*
 * dusky-oom-shield -- dynamic systemd-oomd protection for the focused and
 * pinned Hyprland windows.  Managed by 211_systemd_oomd_zram.py.
 *
 * Mechanism
 * ---------
 * systemd.resource-control(5) implements ManagedOOMPreference= as the cgroup
 * extended attributes user.oomd_avoid / user.oomd_omit, and states they are
 * NOT applied recursively.  systemd-oomd honours a user-owned xattr when the
 * candidate cgroup and the monitored ancestor cgroup share an owner (that is
 * the case here: everything lives under user@$UID.service), but ONLY on the
 * memory-pressure candidate path -- which is exactly why every ruleset in this
 * deployment uses Action=kill-by-pgscan.
 *
 * Candidate level
 * ---------------
 * oomd ranks the subgroups of the monitored cgroup, so the xattr must land on
 * the cgroup that is actually ranked.  For app.slice/app-foo.slice/app-foo-1.scope
 * that is app-foo.slice, not the leaf scope.  We therefore mark BOTH the direct
 * child of the monitored slice and the leaf, and never the monitored slice
 * itself (marking app.slice would make the monitored ancestor "avoid" and
 * neuter the rule).
 *
 * Ownership marker
 * ----------------
 * Alongside user.oomd_avoid we write user.dusky_shield=1.  We only ever remove
 * an avoid that carries our marker, so we cannot clobber an avoid that systemd
 * itself owns (e.g. a dusky-run grace window).  A startup nftw() sweep removes
 * markers stranded by SIGKILL, cgroup reuse or a compositor crash.
 *
 * Reconciler, not edge-trigger
 * ----------------------------
 * Every sync probes the xattr and re-applies if missing.  This is what closes
 * the race with dusky-run's grace demotion, which removes user.oomd_avoid from
 * a scope that may still be the focused window.
 *
 * Build: gcc $CFLAGS -march=native -Wall -Wextra -Werror shield.c -o shield $LDFLAGS
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <sys/xattr.h>
#include <time.h>
#include <unistd.h>

#define XATTR_AVOID    "user.oomd_avoid"
#define XATTR_MARKER   "user.dusky_shield"
#define CGROUP_ROOT    "/sys/fs/cgroup"
#define PATH_LEN       4096
#define FALLBACK_MS    2000
#define IPC_TIMEOUT_S  1
#define READ_CHUNK     65536
#define RESPONSE_CAP   (16u * 1024u * 1024u)
#define BACKOFF_MAX_S  16

static int g_verbose = 0;

static void logmsg(int level, const char *fmt, ...)
{
        if (level > g_verbose)
                return;
        va_list ap;
        va_start(ap, fmt);
        fputs("dusky-oom-shield: ", stderr);
        vfprintf(stderr, fmt, ap);
        fputc('\n', stderr);
        va_end(ap);
}

/* ----------------------------------------------------------------- vectors */

typedef struct {
        char   **v;
        size_t   n;
        size_t   cap;
} StrVec;

typedef struct {
        int     *v;
        size_t   n;
        size_t   cap;
} IntVec;

static bool sv_has(const StrVec *s, const char *str)
{
        for (size_t i = 0; i < s->n; i++)
                if (strcmp(s->v[i], str) == 0)
                        return true;
        return false;
}

static bool sv_push(StrVec *s, const char *str)
{
        if (sv_has(s, str))
                return true;
        if (s->n == s->cap) {
                size_t nc = s->cap ? s->cap * 2 : 16;
                char **nv = realloc(s->v, nc * sizeof(*nv));
                if (!nv)
                        return false;
                s->v = nv;
                s->cap = nc;
        }
        char *dup = strdup(str);
        if (!dup)
                return false;
        s->v[s->n++] = dup;
        return true;
}

static void sv_reset(StrVec *s)
{
        for (size_t i = 0; i < s->n; i++)
                free(s->v[i]);
        s->n = 0;
}

static void sv_free(StrVec *s)
{
        sv_reset(s);
        free(s->v);
        s->v = NULL;
        s->cap = 0;
}

static void sv_swap(StrVec *a, StrVec *b)
{
        StrVec t = *a;
        *a = *b;
        *b = t;
}

static bool iv_push(IntVec *s, int val)
{
        for (size_t i = 0; i < s->n; i++)
                if (s->v[i] == val)
                        return true;
        if (s->n == s->cap) {
                size_t nc = s->cap ? s->cap * 2 : 32;
                int *nv = realloc(s->v, nc * sizeof(*nv));
                if (!nv)
                        return false;
                s->v = nv;
                s->cap = nc;
        }
        s->v[s->n++] = val;
        return true;
}

static void iv_free(IntVec *s)
{
        free(s->v);
        s->v = NULL;
        s->n = s->cap = 0;
}

/* ------------------------------------------------------------ growable buf */

typedef struct {
        char   *p;
        size_t  len;
        size_t  cap;
} Buf;

static bool buf_reserve(Buf *b, size_t need)
{
        if (b->cap >= need)
                return true;
        size_t nc = b->cap ? b->cap : 8192;
        while (nc < need)
                nc *= 2;
        char *np = realloc(b->p, nc);
        if (!np)
                return false;
        b->p = np;
        b->cap = nc;
        return true;
}

static void buf_free(Buf *b)
{
        free(b->p);
        b->p = NULL;
        b->len = b->cap = 0;
}

/* ------------------------------------------------------------- xattr layer */

static bool xattr_present(const char *path, const char *name)
{
        return getxattr(path, name, NULL, 0) >= 0;
}

/*
 * Apply protection. Always write our marker so we can safely clean up later
 * when focus changes.
 */
static void shield_apply(const char *path)
{
        /* Do not take ownership of an avoid set by systemd or the user. */
        if (xattr_present(path, XATTR_AVOID) && !xattr_present(path, XATTR_MARKER))
                return;
        if (setxattr(path, XATTR_AVOID, "1", 1, 0) < 0) {
                if (errno != ENOENT)
                        logmsg(1, "setxattr(%s, %s): %s", path, XATTR_AVOID, strerror(errno));
                return;
        }
        if (setxattr(path, XATTR_MARKER, "1", 1, 0) < 0 && errno != ENOENT)
                logmsg(1, "setxattr(%s, %s): %s", path, XATTR_MARKER, strerror(errno));
        logmsg(1, "protect %s", path);
}

/* Release protection -- only ever our own. */
static void shield_clear(const char *path)
{
        if (!xattr_present(path, XATTR_MARKER))
                return;
        if (removexattr(path, XATTR_AVOID) < 0 &&
            errno != ENODATA && errno != ENOENT)
                logmsg(1, "removexattr(%s, %s): %s", path, XATTR_AVOID, strerror(errno));
        if (removexattr(path, XATTR_MARKER) < 0 &&
            errno != ENODATA && errno != ENOENT)
                logmsg(1, "removexattr(%s, %s): %s", path, XATTR_MARKER, strerror(errno));
        logmsg(1, "release %s", path);
}

static int sweep_cb(const char *path, const struct stat *sb, int flag, struct FTW *ftw)
{
        (void)sb;
        (void)ftw;
        if (flag == FTW_D || flag == FTW_DP) {
                shield_clear(path);
        }
        return 0;
}

/* Remove markers stranded by SIGKILL / cgroup reuse / compositor crash. */
static void sweep_stale(uid_t uid)
{
        char root[PATH_LEN];
        int n = snprintf(root, sizeof(root),
                         CGROUP_ROOT "/user.slice/user-%u.slice/user@%u.service",
                         (unsigned)uid, (unsigned)uid);
        if (n < 0 || (size_t)n >= sizeof(root))
                return;
        struct stat st;
        if (stat(root, &st) != 0 || !S_ISDIR(st.st_mode))
                return;
        if (nftw(root, sweep_cb, 24, FTW_PHYS) != 0)
                logmsg(1, "stale sweep incomplete under %s", root);
}

/* ------------------------------------------------------------- hyprland IPC */

static int hypr_query(const char *sock_path, const char *cmd, Buf *out)
{
        out->len = 0;

        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd < 0)
                return -1;

        struct timeval tv = { .tv_sec = IPC_TIMEOUT_S, .tv_usec = 0 };
        if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) < 0 ||
            setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) < 0) {
                close(fd);
                return -1;
        }

        struct sockaddr_un addr;
        memset(&addr, 0, sizeof(addr));
        addr.sun_family = AF_UNIX;
        size_t plen = strlen(sock_path);
        if (plen >= sizeof(addr.sun_path)) {
                close(fd);
                return -1;
        }
        memcpy(addr.sun_path, sock_path, plen + 1);

        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
                close(fd);
                return -1;
        }

        size_t off = 0, clen = strlen(cmd);
        while (off < clen) {
                ssize_t w = write(fd, cmd + off, clen - off);
                if (w < 0) {
                        if (errno == EINTR)
                                continue;
                        close(fd);
                        return -1;
                }
                off += (size_t)w;
        }

        for (;;) {
                if (!buf_reserve(out, out->len + READ_CHUNK + 1)) {
                        close(fd);
                        return -1;
                }
                ssize_t r = read(fd, out->p + out->len, READ_CHUNK);
                if (r < 0) {
                        if (errno == EINTR)
                                continue;
                        close(fd);
                        return -1;
                }
                if (r == 0)
                        break;
                out->len += (size_t)r;
                if (out->len >= RESPONSE_CAP) {
                        logmsg(1, "IPC response exceeded %u bytes, truncating", RESPONSE_CAP);
                        break;
                }
        }
        out->p[out->len] = '\0';
        close(fd);
        return 0;
}

/* --------------------------------------------------------- minimal JSON ops */

static const char *skip_to_value(const char *p, const char *end)
{
        while (p < end && (*p == ' ' || *p == '\t' || *p == ':'))
                p++;
        return p;
}

static long json_int(const char *s, size_t len, const char *key)
{
        const char *p = memmem(s, len, key, strlen(key));
        if (!p)
                return -1;
        const char *end = s + len;
        p = skip_to_value(p + strlen(key), end);
        if (p >= end)
                return -1;
        char *stop = NULL;
        long v = strtol(p, &stop, 10);
        if (stop == p)
                return -1;
        return v;
}

static bool json_true(const char *s, size_t len, const char *key)
{
        const char *p = memmem(s, len, key, strlen(key));
        if (!p)
                return false;
        const char *end = s + len;
        p = skip_to_value(p + strlen(key), end);
        return (size_t)(end - p) >= 4 && memcmp(p, "true", 4) == 0;
}

/*
 * Iterate the top-level objects of a j/clients array in place (no copies, no
 * per-object size cap) and collect the PIDs of pinned windows.
 */
static void collect_pinned(const Buf *b, IntVec *out)
{
        const char *s = b->p;
        size_t n = b->len;
        size_t i = 0;

        while (i < n) {
                if (s[i] != '{') {
                        i++;
                        continue;
                }
                size_t depth = 0, j = i;
                bool in_str = false, esc = false;
                for (; j < n; j++) {
                        char c = s[j];
                        if (esc) {
                                esc = false;
                                continue;
                        }
                        if (c == '\\') {
                                esc = true;
                                continue;
                        }
                        if (c == '"') {
                                in_str = !in_str;
                                continue;
                        }
                        if (in_str)
                                continue;
                        if (c == '{') {
                                depth++;
                        } else if (c == '}') {
                                depth--;
                                if (depth == 0)
                                        break;
                        }
                }
                if (j >= n)
                        break;                       /* truncated tail */
                size_t olen = j - i + 1;
                if (json_true(s + i, olen, "\"pinned\"")) {
                        long pid = json_int(s + i, olen, "\"pid\"");
                        if (pid > 0 && !iv_push(out, (int)pid))
                                logmsg(1, "out of memory collecting pinned pids");
                }
                i = j + 1;
        }
}

/* ------------------------------------------------------- cgroup resolution */

static void push_if_dir(StrVec *out, const char *path)
{
        struct stat st;
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode))
                sv_push(out, path);
}

/*
 * Resolve a PID to the cgroups that systemd-oomd will actually rank:
 *   - the direct child of the monitored slice (app.slice / background.slice)
 *   - the leaf cgroup itself, when it differs
 * A PID outside those slices (e.g. spawned straight from a Hyprland exec
 * keybind, hence inside the compositor's session-N.scope) is intentionally
 * skipped: it is not a candidate of any of our rulesets, and marking a system
 * scope would be both useless and rude.
 */
static void add_candidates(int pid, StrVec *out)
{
        static const char *const monitored[] = { "/app.slice/", "/background.slice/" };

        char procpath[64];
        int n = snprintf(procpath, sizeof(procpath), "/proc/%d/cgroup", pid);
        if (n < 0 || (size_t)n >= sizeof(procpath))
                return;

        FILE *f = fopen(procpath, "re");
        if (!f)
                return;

        char line[PATH_LEN];
        while (fgets(line, sizeof(line), f)) {
                if (strncmp(line, "0::", 3) != 0)
                        continue;
                char *rel = line + 3;
                rel[strcspn(rel, "\r\n")] = '\0';
                if (rel[0] != '/')
                        break;

                bool inside = false;
                for (size_t m = 0; m < sizeof(monitored) / sizeof(monitored[0]); m++) {
                        char *hit = strstr(rel, monitored[m]);
                        if (!hit)
                                continue;
                        inside = true;
                        char *seg = hit + strlen(monitored[m]);
                        char *slash = strchr(seg, '/');
                        size_t keep = slash ? (size_t)(slash - rel) : strlen(rel);
                        char cand[PATH_LEN];
                        int w = snprintf(cand, sizeof(cand), "%s%.*s",
                                         CGROUP_ROOT, (int)keep, rel);
                        if (w > 0 && (size_t)w < sizeof(cand))
                                push_if_dir(out, cand);
                }

                if (inside) {
                        char leaf[PATH_LEN];
                        int w = snprintf(leaf, sizeof(leaf), "%s%s", CGROUP_ROOT, rel);
                        if (w > 0 && (size_t)w < sizeof(leaf))
                                push_if_dir(out, leaf);
                } else {
                        logmsg(2, "pid %d is outside app/background.slice (%s)", pid, rel);
                }
                break;
        }
        fclose(f);
}

/*
 * Terminals (like Kitty) and IDEs move child processes into sub-scopes or child
 * scopes. Recursively inspect direct children of the process so that commands
 * running inside the focused window share the protection.
 */
static void add_descendant_cgroups(int pid, StrVec *out, int depth)
{
        if (depth <= 0)
                return;

        char path[64];
        snprintf(path, sizeof(path), "/proc/%d/task/%d/children", pid, pid);
        FILE *f = fopen(path, "re");
        if (!f)
                return;

        int child_pid;
        while (fscanf(f, "%d", &child_pid) == 1) {
                if (child_pid > 1) {
                        add_candidates(child_pid, out);
                        add_descendant_cgroups(child_pid, out, depth - 1);
                }
        }
        fclose(f);
}

/*
 * Kitty creates peer scopes under app.slice named kitty-<ppid>-<id>.scope.
 * Pattern match them directly under the user's app.slice.
 */
static void add_pattern_scopes(int pid, StrVec *out)
{
        char app_slice[PATH_LEN];
        int n = snprintf(app_slice, sizeof(app_slice),
                         "%s/user.slice/user-%u.slice/user@%u.service/app.slice",
                         CGROUP_ROOT, (unsigned)getuid(), (unsigned)getuid());
        if (n < 0 || (size_t)n >= sizeof(app_slice))
                return;

        DIR *d = opendir(app_slice);
        if (!d)
                return;

        char prefix[64];
        int plen = snprintf(prefix, sizeof(prefix), "kitty-%d-", pid);

        struct dirent *de;
        while ((de = readdir(d)) != NULL) {
                if (plen > 0 && strncmp(de->d_name, prefix, (size_t)plen) == 0 &&
                    strstr(de->d_name, ".scope") != NULL) {
                        char cand[PATH_LEN];
                        int w = snprintf(cand, sizeof(cand), "%s/%s", app_slice, de->d_name);
                        if (w > 0 && (size_t)w < sizeof(cand))
                                push_if_dir(out, cand);
                }
        }
        closedir(d);
}

static void add_cgroups_for_window(int pid, StrVec *out)
{
        add_candidates(pid, out);
        add_descendant_cgroups(pid, out, 3);
        add_pattern_scopes(pid, out);
}

/* ------------------------------------------------------ socket discovery */

static const char *runtime_dir(char *buf, size_t len)
{
        const char *xdg = getenv("XDG_RUNTIME_DIR");
        if (xdg && *xdg == '/')
                return xdg;
        int n = snprintf(buf, len, "/run/user/%u", (unsigned)getuid());
        if (n < 0 || (size_t)n >= len)
                return NULL;
        return buf;
}

static bool both_exist(const char *cmd, const char *evt)
{
        return access(cmd, F_OK) == 0 && access(evt, F_OK) == 0;
}

/*
 * Prefer $HYPRLAND_INSTANCE_SIGNATURE.  Otherwise pick the NEWEST instance
 * directory by mtime -- readdir order is arbitrary and would attach to a stale
 * instance after a compositor restart.
 */
static int find_sockets(char *cmd, size_t cmd_len, char *evt, size_t evt_len)
{
        char rtbuf[PATH_LEN];
        const char *rt = runtime_dir(rtbuf, sizeof(rtbuf));
        if (!rt)
                return -1;

        char base[PATH_LEN];
        int n = snprintf(base, sizeof(base), "%s/hypr", rt);
        if (n < 0 || (size_t)n >= sizeof(base))
                return -1;

        const char *sig = getenv("HYPRLAND_INSTANCE_SIGNATURE");
        if (sig && *sig) {
                int a = snprintf(cmd, cmd_len, "%s/%s/.socket.sock", base, sig);
                int b = snprintf(evt, evt_len, "%s/%s/.socket2.sock", base, sig);
                if (a > 0 && (size_t)a < cmd_len && b > 0 && (size_t)b < evt_len &&
                    both_exist(cmd, evt))
                        return 0;
        }

        DIR *d = opendir(base);
        if (!d)
                return -1;

        char best_cmd[PATH_LEN] = "";
        char best_evt[PATH_LEN] = "";
        time_t best_mtime = 0;
        struct dirent *e;

        while ((e = readdir(d)) != NULL) {
                if (e->d_name[0] == '.')
                        continue;
                char c[PATH_LEN], v[PATH_LEN];
                int a = snprintf(c, sizeof(c), "%s/%s/.socket.sock", base, e->d_name);
                int b = snprintf(v, sizeof(v), "%s/%s/.socket2.sock", base, e->d_name);
                if (a <= 0 || (size_t)a >= sizeof(c) || b <= 0 || (size_t)b >= sizeof(v))
                        continue;
                struct stat st;
                if (stat(v, &st) != 0 || access(c, F_OK) != 0)
                        continue;
                if (st.st_mtime >= best_mtime) {
                        best_mtime = st.st_mtime;
                        memcpy(best_cmd, c, sizeof(best_cmd));
                        memcpy(best_evt, v, sizeof(best_evt));
                }
        }
        closedir(d);

        if (best_cmd[0] == '\0')
                return -1;
        if (strlen(best_cmd) >= cmd_len || strlen(best_evt) >= evt_len)
                return -1;
        memcpy(cmd, best_cmd, strlen(best_cmd) + 1);
        memcpy(evt, best_evt, strlen(best_evt) + 1);
        return 0;
}

/* ---------------------------------------------------------- reconciliation */

static StrVec g_protected;

static void reconcile(const char *cmd_sock)
{
        Buf act = { 0 }, clients = { 0 };
        IntVec pids = { 0 };
        StrVec desired = { 0 };

        if (hypr_query(cmd_sock, "j/activewindow", &act) == 0 && act.len > 0) {
                long pid = json_int(act.p, act.len, "\"pid\"");
                if (pid > 0)
                        iv_push(&pids, (int)pid);
        }
        if (hypr_query(cmd_sock, "j/clients", &clients) == 0 && clients.len > 0)
                collect_pinned(&clients, &pids);

        for (size_t i = 0; i < pids.n; i++)
                add_cgroups_for_window(pids.v[i], &desired);

        /*
         * Idempotent apply.  This is a full reconcile, not an edge trigger:
         * dusky-run's grace demotion (ManagedOOMPreference=none) makes systemd
         * removexattr user.oomd_avoid behind our back, and an edge-triggered
         * design would never restore it while the window stayed focused.
         */
        for (size_t i = 0; i < desired.n; i++)
                shield_apply(desired.v[i]);

        for (size_t i = 0; i < g_protected.n; i++)
                if (!sv_has(&desired, g_protected.v[i]))
                        shield_clear(g_protected.v[i]);

        sv_swap(&g_protected, &desired);
        sv_free(&desired);
        iv_free(&pids);
        buf_free(&act);
        buf_free(&clients);
}

static void release_all(void)
{
        for (size_t i = 0; i < g_protected.n; i++)
                shield_clear(g_protected.v[i]);
        sv_reset(&g_protected);
}

/* ------------------------------------------------------------ event filter */

static bool event_is_interesting(const char *line)
{
        static const char *const wanted[] = {
                "activewindow", "activewindowv2", "pin", "openwindow", "closewindow",
                "movewindow", "movewindowv2", "fullscreen", "changefloatingmode",
                "focusedmon", "focusedmonv2", "workspace", "workspacev2",
                "destroyworkspace", "destroyworkspacev2", "urgent", "monitorremoved",
                "closelayer", NULL
        };
        const char *sep = strstr(line, ">>");
        size_t tlen = sep ? (size_t)(sep - line) : strlen(line);
        for (size_t i = 0; wanted[i]; i++)
                if (strlen(wanted[i]) == tlen && memcmp(line, wanted[i], tlen) == 0)
                        return true;
        return false;
}

/* --------------------------------------------------------------------- main */

int main(int argc, char **argv)
{
        for (int i = 1; i < argc; i++) {
                if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--verbose") == 0) {
                        g_verbose++;
                } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
                        printf("usage: %s [-v|--verbose]\n", argv[0]);
                        return 0;
                } else {
                        fprintf(stderr, "dusky-oom-shield: unknown argument: %s\n", argv[i]);
                        return 2;
                }
        }

        signal(SIGPIPE, SIG_IGN);

        sigset_t mask;
        sigemptyset(&mask);
        sigaddset(&mask, SIGTERM);
        sigaddset(&mask, SIGINT);
        sigaddset(&mask, SIGHUP);
        if (sigprocmask(SIG_BLOCK, &mask, NULL) < 0) {
                perror("dusky-oom-shield: sigprocmask");
                return 1;
        }
        int sig_fd = signalfd(-1, &mask, SFD_CLOEXEC | SFD_NONBLOCK);
        if (sig_fd < 0) {
                perror("dusky-oom-shield: signalfd");
                return 1;
        }

        sweep_stale(getuid());

        bool running = true;
        unsigned backoff = 1;

        while (running) {
                char cmd_sock[PATH_LEN], evt_sock[PATH_LEN];

                if (find_sockets(cmd_sock, sizeof(cmd_sock), evt_sock, sizeof(evt_sock)) != 0) {
                        struct pollfd wait_pfd = { .fd = sig_fd, .events = POLLIN, .revents = 0 };
                        if (poll(&wait_pfd, 1, (int)backoff * 1000) > 0)
                                running = false;              /* any signal -> drain below */
                        if (backoff < BACKOFF_MAX_S)
                                backoff *= 2;
                        if (!running)
                                break;
                        continue;
                }

                int evt_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
                if (evt_fd < 0)
                        break;

                struct sockaddr_un addr;
                memset(&addr, 0, sizeof(addr));
                addr.sun_family = AF_UNIX;
                size_t plen = strlen(evt_sock);
                if (plen >= sizeof(addr.sun_path)) {
                        close(evt_fd);
                        break;
                }
                memcpy(addr.sun_path, evt_sock, plen + 1);

                if (connect(evt_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
                        close(evt_fd);
                        struct pollfd wait_pfd = { .fd = sig_fd, .events = POLLIN, .revents = 0 };
                        if (poll(&wait_pfd, 1, (int)backoff * 1000) > 0)
                                break;
                        if (backoff < BACKOFF_MAX_S)
                                backoff *= 2;
                        continue;
                }

                backoff = 1;
                logmsg(1, "attached to %s", evt_sock);
                reconcile(cmd_sock);

                char line_buf[8192];
                size_t line_len = 0;
                bool reattach = false;

                struct pollfd pfds[2] = {
                        { .fd = evt_fd, .events = POLLIN, .revents = 0 },
                        { .fd = sig_fd, .events = POLLIN, .revents = 0 },
                };

                while (running && !reattach) {
                        int ret = poll(pfds, 2, FALLBACK_MS);
                        if (ret < 0) {
                                if (errno == EINTR)
                                        continue;
                                reattach = true;
                                break;
                        }

                        /* Signals first: exit latency must not depend on IPC. */
                        if (pfds[1].revents & POLLIN) {
                                struct signalfd_siginfo si;
                                bool resync = false;
                                while (read(sig_fd, &si, sizeof(si)) == (ssize_t)sizeof(si)) {
                                        if (si.ssi_signo == SIGHUP)
                                                resync = true;
                                        else
                                                running = false;
                                }
                                if (!running)
                                        break;
                                if (resync)
                                        reconcile(cmd_sock);
                        }

                        /* Drain readable data BEFORE acting on HUP/ERR. */
                        if (pfds[0].revents & POLLIN) {
                                if (line_len + 1 >= sizeof(line_buf))
                                        line_len = 0;         /* pathological line, resync */
                                ssize_t r = read(evt_fd, line_buf + line_len,
                                                 sizeof(line_buf) - line_len - 1);
                                if (r < 0) {
                                        if (errno != EINTR)
                                                reattach = true;
                                } else if (r == 0) {
                                        reattach = true;
                                } else {
                                        line_len += (size_t)r;
                                        line_buf[line_len] = '\0';

                                        bool sync_needed = false;
                                        char *start = line_buf, *nl;
                                        while ((nl = strchr(start, '\n')) != NULL) {
                                                *nl = '\0';
                                                if (event_is_interesting(start))
                                                        sync_needed = true;
                                                start = nl + 1;
                                        }
                                        size_t consumed = (size_t)(start - line_buf);
                                        if (consumed < line_len) {
                                                memmove(line_buf, start, line_len - consumed);
                                                line_len -= consumed;
                                        } else {
                                                line_len = 0;
                                        }
                                        if (sync_needed)
                                                reconcile(cmd_sock);
                                }
                        } else if (ret == 0) {
                                /* Genuine timeout: the 2 s safety-net reconcile. */
                                reconcile(cmd_sock);
                        }

                        if (pfds[0].revents & (POLLHUP | POLLERR | POLLNVAL))
                                reattach = true;
                }

                close(evt_fd);
                release_all();
        }

        release_all();
        sweep_stale(getuid());
        sv_free(&g_protected);
        close(sig_fd);
        return 0;
}
"""

DUSKY_OOM_SHIELD_SERVICE: Final[str] = HDR + """[Unit]
Description=Dusky OOM Shield (Hyprland active + pinned window oomd protection)
Documentation=man:systemd.resource-control(5) man:oomd.conf(5)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=exec
ExecStart=/usr/local/bin/dusky-oom-shield
Restart=always
RestartSec=2s
Slice=session.slice
OOMScoreAdjust=-100
OOMPolicy=continue
ManagedOOMPreference=avoid
MemoryAccounting=yes
# Cheap by construction: <=0.5 Hz steady-state syscall traffic.
MemoryHigh=48M
# Sandboxing limited to namespace-free restrictions. ProtectKernelTunables= and
# ProtectSystem= are deliberately NOT used: they would remount /sys read-only
# and break the setxattr() calls that are this daemon's entire purpose.
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM

[Install]
WantedBy=graphical-session.target
"""


# --------------------------------------------------------------------------- #
# makepkg build-flag resolution (no hardcoded usernames anywhere)
# --------------------------------------------------------------------------- #

def _target_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    try:
        login = os.getlogin()
        if login and login != "root":
            return Path(pwd.getpwnam(login).pw_dir)
    except (OSError, KeyError):
        pass
    # /run/user/<uid> is the strongest live-session signal available to root.
    run_user = Path("/run/user")
    if run_user.is_dir():
        for p in sorted(run_user.iterdir(), key=lambda q: q.name):
            if p.name.isdigit() and int(p.name) >= 1000:
                try:
                    return Path(pwd.getpwuid(int(p.name)).pw_dir)
                except KeyError:
                    continue
    for u in pwd.getpwall():
        if u.pw_uid >= 1000 and u.pw_shell not in ("/usr/bin/nologin", "/sbin/nologin") \
                and Path(u.pw_dir).is_dir():
            return Path(u.pw_dir)
    return Path.home()


@functools.cache
def get_makepkg_build_flags() -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """Resolve CFLAGS/LDFLAGS through makepkg's real sourcing hierarchy.

    /etc/makepkg.conf -> /etc/makepkg.conf.d/*.conf -> ~/.config/pacman/makepkg.conf
    (or ~/.makepkg.conf). -march=native is injected when absent so the binary
    targets the host CPU. Memoised: two independent bash evaluations could
    otherwise disagree between the dry-run preview and the real build.
    """
    home = _target_home()
    user_conf: Path | None = next(
        (c for c in (home / ".config/pacman/makepkg.conf", home / ".makepkg.conf") if c.is_file()),
        None,
    )

    script = r"""
    [[ -f /etc/makepkg.conf ]] && source /etc/makepkg.conf
    if compgen -G '/etc/makepkg.conf.d/*.conf' > /dev/null; then
        for f in /etc/makepkg.conf.d/*.conf; do
            [[ -f "$f" ]] && source "$f"
        done
    fi
    if [[ -n "${USER_MAKEPKG:-}" && -f "$USER_MAKEPKG" ]]; then
        source "$USER_MAKEPKG"
    fi
    printf '%s\n' "${CFLAGS:-}"
    printf '%s\n' "${LDFLAGS:-}"
    """
    env = {**os.environ, "USER_MAKEPKG": str(user_conf) if user_conf else ""}
    res = subprocess.run(["bash", "-c", script], env=env,
                         capture_output=True, text=True, check=False)
    lines = res.stdout.splitlines()
    cflags = shlex.split(lines[0]) if len(lines) > 0 else []
    ldflags = shlex.split(lines[1]) if len(lines) > 1 else []

    if not cflags:
        cflags = ["-march=native", "-O2", "-pipe", "-fno-plt"]
    elif not any(f.startswith("-march=") or f.startswith("-mcpu=") for f in cflags):
        cflags.insert(0, "-march=native")

    src = str(user_conf) if user_conf else "/etc/makepkg.conf"
    return tuple(cflags), tuple(ldflags), src


SAFE_CFLAGS: Final[tuple[str, ...]] = ("-march=native", "-O2", "-pipe", "-fno-plt",
                                       "-fstack-protector-strong")


# --------------------------------------------------------------------------- #
# File specs + atomic installation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True, kw_only=True)
class FileSpec:
    dest: Path
    content: str
    desc: str
    mode: int = 0o644


def specs(tier: str) -> list[FileSpec]:
    p = TIER_PROFILES[tier]

    rule_hdr = (
        "# Managed by 211_systemd_oomd_zram.py -- local edits are overwritten.\n"
        "#\n"
        "# Action=kill-by-pgscan is MANDATORY here, including for the swap rule.\n"
        "# systemd.resource-control(5): swap-candidate selection honours\n"
        "# user.oomd_avoid / user.oomd_omit ONLY on root-owned cgroups, whereas\n"
        "# memory-pressure candidate selection honours them when the candidate and\n"
        "# the monitored ancestor share an owner. Every cgroup here is user-owned\n"
        "# (under user@$UID.service), so Action=kill-by-swap would silently discard\n"
        "# the compositor/pipewire/focused-window avoid hierarchy.\n"
        "#\n"
        "# MemoryPressureAbove= is PSI 'full avg10' -- a 10 s decaying window. Values\n"
        f"# of LastingSec= below 10s therefore mean 'avg10 stayed above the threshold',\n"
        f"# not 'N independent samples'. Tier {tier} ({p['label']}).\n"
    )

    pressure_rule = rule_hdr + f"""[Rule]
MemoryPressureAbove={p['pressure_above']}
LastingSec={p['pressure_lasting']}
Action=kill-by-pgscan
"""

    swap_rule = rule_hdr + f"""#
# SwapUsageMax + MemoryPressureAbove in one ruleset are combined with AND
# (oomd.conf(5)): fire only when swap is deep AND tasks are genuinely stalling.
# 80% is the anti-thrash ceiling for the 205 topology (ZRAM prio 32767 +
# disk prio -1): the kernel drains ZRAM first, so 80% of total swap is only
# reachable once ZRAM is saturated and disk swap is buffering. DO NOT RAISE.
[Rule]
SwapUsageMax={p['swap_max']}
MemoryPressureAbove={p['swap_pressure']}
LastingSec={p['swap_lasting']}
Action=kill-by-pgscan
"""

    swap_ceiling_rule = rule_hdr + f"""#
# Emergency swap exhaustion threshold (>95%), without a PSI condition.
# kill-by-pgscan still requires recent reclaim and an eligible descendant;
# it cannot guarantee a kill when every descendant is quiescent.
[Rule]
SwapUsageMax=95%
LastingSec=0
Action=kill-by-pgscan
"""

    bg_rule = rule_hdr + f"""[Rule]
MemoryPressureAbove={p['bg_pressure_above']}
LastingSec={p['bg_pressure_lasting']}
Action=kill-by-pgscan
"""

    out: list[FileSpec] = [
        # user@.service FIRST: it is the capability precondition for every
        # user-level OOMScoreAdjust=-100 below (see header invariant 4).
        FileSpec(dest=Path("/etc/systemd/system/user@.service.d/90-desktop-oom-score.conf"),
                 content=USER_MANAGER_SCORE,
                 desc="user@.service score (-100, continue) [precondition]"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-dusky-pressure.oomrule"),
                 content=pressure_rule,
                 desc=f"app pressure rule ({p['pressure_above']} / {p['pressure_lasting']})"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-dusky-swap.oomrule"),
                 content=swap_rule,
                 desc=f"swap rule ({p['swap_max']} AND {p['swap_pressure']} / {p['swap_lasting']})"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-dusky-swap-ceiling.oomrule"),
                 content=swap_ceiling_rule,
                 desc="emergency swap ceiling rule (95% / 0s)"),
        FileSpec(dest=Path("/etc/systemd/oomd/rules.d/30-dusky-background.oomrule"),
                 content=bg_rule,
                 desc=f"background rule ({p['bg_pressure_above']} / {p['bg_pressure_lasting']})"),
        FileSpec(dest=Path("/etc/systemd/oomd.conf.d/90-dusky-oomd.conf"),
                 content=OOMD_TUNE, desc="oomd global tuning (prekill hook 0s)"),
        FileSpec(dest=Path("/etc/systemd/user/app.slice.d/90-desktop-oomd.conf"),
                 content=APP_SLICE, desc="app.slice -> 30-dusky-pressure 30-dusky-swap 30-dusky-swap-ceiling"),
        FileSpec(dest=Path("/etc/systemd/user/background.slice.d/90-desktop-oomd.conf"),
                 content=BACKGROUND_SLICE, desc="background.slice -> 30-dusky-background 30-dusky-swap 30-dusky-swap-ceiling"),
        FileSpec(dest=Path("/etc/systemd/user/session.slice.d/90-desktop-oomd.conf"),
                 content=SESSION_SLICE, desc="session.slice preference=avoid (no MemoryMin/Low)"),
        FileSpec(dest=Path("/etc/systemd/system/session-.scope.d/90-desktop-oomd.conf"),
                 content=COMPOSITOR_SCOPE, desc="compositor scope (continue + avoid, zero reservation)"),
        FileSpec(dest=Path("/etc/systemd/user.conf.d/90-desktop-oom.conf"),
                 content=USER_CONF, desc="user manager defaults (score 100, accounting, pressure watch)"),
        FileSpec(dest=Path("/etc/systemd/system/systemd-oomd.service.d/90-desktop-oomd.conf"),
                 content=OOMD_SERVICE_SHIELD, desc="systemd-oomd arbiter shield (-1000)"),
        FileSpec(dest=Path("/usr/local/bin/dusky-run"), content=DUSKY_RUN_WRAPPER,
                 mode=0o755, desc="dusky-run transient-scope launcher"),
        FileSpec(dest=Path("/usr/local/src/dusky-oom-shield.c"), content=DUSKY_OOM_SHIELD_C,
                 desc="dusky-oom-shield C source"),
        FileSpec(dest=Path("/etc/systemd/user/dusky_oom_shield.service"),
                 content=DUSKY_OOM_SHIELD_SERVICE, desc="dusky_oom_shield user service"),
    ]
    for svc in CRITICAL_USER:
        out.append(FileSpec(dest=Path(f"/etc/systemd/user/{svc}.d/90-desktop-oom.conf"),
                            content=OOM_SHIELD, desc=f"shield {svc}"))
    return out


def atomic_install(spec: FileSpec) -> str:
    """Idempotent, crash-atomic install. Returns 'updated' or 'up-to-date'."""
    dest = spec.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = spec.content if spec.content.endswith("\n") else spec.content + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.tmp.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, spec.mode)

        exists = dest.exists()
        same_content = exists and filecmp.cmp(tmp_name, str(dest), shallow=False)
        same_mode = exists and (dest.stat().st_mode & 0o7777) == spec.mode

        if same_content and same_mode:
            return "up-to-date"
        if same_content:
            dest.chmod(spec.mode)
            return "updated"

        os.replace(tmp_name, str(dest))
        # Durability: rename atomicity alone does not survive a power cut.
        dir_fd = os.open(str(dest.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return "updated"
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as e:
            if e.errno != errno.ENOENT:
                warn(f"leftover temp file {tmp}: {e}")


def prune_legacy() -> int:
    removed = 0
    for p in LEGACY_PATHS:
        try:
            if p.exists():
                p.unlink()
                removed += 1
                say(f"[magenta]{'REMOVED':11}[/] {p} [dim](superseded)[/]",
                    plain=f"{'REMOVED':11} {p} (superseded)")
        except OSError as e:
            warn(f"could not remove legacy {p}: {e}")
    return removed


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def _pacman_install(pkg: str) -> bool:
    base = ["pacman", "-S", "--needed", "--noconfirm", pkg]
    cmd = base if os.geteuid() == 0 else ["sudo", *base]
    return subprocess.run(cmd, check=False).returncode == 0


def build_shield(src: Path, binary: Path) -> bool:
    if not shutil.which("gcc") and not _pacman_install("gcc"):
        fail("gcc is required to build dusky-oom-shield and could not be installed")

    cflags, ldflags, flag_src = get_makepkg_build_flags()
    cflags, ldflags = list(cflags), list(ldflags)

    if any("mold" in f for f in (*cflags, *ldflags)) and not shutil.which("mold"):
        if not _pacman_install("mold"):
            warn("mold requested by makepkg flags but unavailable; stripping mold flags")
            cflags = [f for f in cflags if "mold" not in f]
            ldflags = [f for f in ldflags if "mold" not in f]

    stamp = binary.with_name(binary.name + ".buildid")
    fingerprint = " ".join([*cflags, "--", *ldflags, "--", str(src.stat().st_mtime_ns)])
    if binary.exists() and stamp.exists() and stamp.read_text(encoding="utf-8") == fingerprint:
        say(f"[dim]{'UNCHANGED':11}[/] {binary} [dim](flags + source unchanged)[/]",
            plain=f"{'UNCHANGED':11} {binary} (flags + source unchanged)")
        return True

    warnflags = ["-Wall", "-Wextra", "-Werror", "-std=gnu23"]
    attempts = [
        ("makepkg", cflags, ldflags),
        ("sanitised", list(SAFE_CFLAGS), []),
    ]
    for label, cf, lf in attempts:
        cmd = ["gcc", *cf, *warnflags, str(src), "-o", str(binary), *lf]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            binary.chmod(0o755)
            stamp.write_text(fingerprint, encoding="utf-8")
            stamp.chmod(0o644)
            say(f"[green]{'COMPILED':11}[/] {binary} [dim]({label} flags via {flag_src})[/]",
                plain=f"{'COMPILED':11} {binary} ({label} flags via {flag_src})")
            return True
        warn(f"build with {label} flags failed (rc={res.returncode})")
        if res.stderr.strip():
            say(f"[dim]{res.stderr.strip()[-2000:]}[/]", plain=res.stderr.strip()[-2000:])
    return False


# --------------------------------------------------------------------------- #
# systemd plumbing
# --------------------------------------------------------------------------- #

def run_sysctl(args: list[str], *, quiet_ok: bool = True) -> bool:
    res = subprocess.run(args, capture_output=True, text=True, check=False)
    if res.returncode != 0 and not quiet_ok:
        warn(f"{' '.join(args)} -> rc={res.returncode}: {res.stderr.strip()}")
    return res.returncode == 0


def active_sessions() -> list[tuple[int, str]]:
    """Every uid >= 1000 with a live per-user manager, SUDO_USER first."""
    found: list[tuple[int, str]] = []
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            pw = pwd.getpwnam(sudo_user)
            found.append((pw.pw_uid, pw.pw_name))
        except KeyError:
            pass
    run_user = Path("/run/user")
    if run_user.is_dir():
        for p in sorted(run_user.iterdir(), key=lambda q: q.name):
            if not (p.is_dir() and p.name.isdigit()):
                continue
            uid = int(p.name)
            if uid < 1000:
                continue
            try:
                name = pwd.getpwuid(uid).pw_name
            except KeyError:
                continue
            if (uid, name) not in found:
                found.append((uid, name))
    # A per-user manager only exists if its private bus socket does.
    return [(u, n) for (u, n) in found if Path(f"/run/user/{u}/systemd/private").exists()]


def reload_user_managers() -> None:
    # --global is the authoritative, user-agnostic enablement: it creates
    # /etc/systemd/user/graphical-session.target.wants/dusky_oom_shield.service
    # for every present and future user, and is idempotent.
    run_sysctl(["systemctl", "--global", "enable", "dusky_oom_shield.service"], quiet_ok=False)

    sessions = active_sessions()
    if not sessions:
        warn("no live per-user systemd manager found; the shield starts at next login")
        return

    for uid, name in sessions:
        runtime = f"/run/user/{uid}"
        env = {
            **os.environ,
            "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        }
        pre = ["runuser", "-u", name, "-w", "XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS", "--",
               "systemctl", "--user"]

        def user_ctl(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([*pre, *args], env=env,
                                  capture_output=True, text=True, check=False)

        r = user_ctl("daemon-reload")
        if r.returncode != 0:
            warn(f"{name}: user daemon-reload failed: {r.stderr.strip()}")
            continue

        graphical = user_ctl("is-active", "--quiet", "graphical-session.target").returncode == 0
        if graphical:
            r = user_ctl("restart", "dusky_oom_shield.service")
            verb = "restarted"
        else:
            r = user_ctl("start", "dusky_oom_shield.service")
            verb = "started"
        if r.returncode == 0:
            say(f"[green]{'SHIELD':11}[/] {verb} for {name} (uid {uid})",
                plain=f"{'SHIELD':11} {verb} for {name} (uid {uid})")
        else:
            warn(f"{name}: shield {verb[:-1]} failed: {r.stderr.strip() or 'see journalctl --user -u dusky_oom_shield'}")


# --------------------------------------------------------------------------- #
# Read-only coherence preflight (205 / 211 / 212 drift detector)
# --------------------------------------------------------------------------- #

def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def coherence_report(tier: str, gib: float) -> list[tuple[str, str, str]]:
    """Returns (subsystem, observed, verdict). Never writes anything."""
    rows: list[tuple[str, str, str]] = []

    # --- 205: ZRAM / swap topology -----------------------------------------
    swaps = _read("/proc/swaps").splitlines()[1:]
    if not swaps:
        rows.append(("205 swap", "no active swap device",
                     "FAIL: run 205 first -- SwapUsageMax rules can never fire"))
    else:
        zram = [s for s in swaps if "/dev/zram" in s]
        disk = [s for s in swaps if "/dev/zram" not in s]
        total_kb = sum(int(s.split()[2]) for s in swaps if len(s.split()) > 2)
        detail = f"{len(zram)} zram + {len(disk)} disk, total {total_kb / 1048576:.1f} GiB"
        if zram and disk:
            verdict = "OK: tiered (ZRAM prio-first, disk buffering) -- 80% ceiling is meaningful"
        elif zram:
            verdict = "NOTE: ZRAM only -- 80% of swap == 80% of ZRAM, no disk buffer tier"
        else:
            verdict = "WARN: no ZRAM device; 205 did not apply"
        rows.append(("205 swap", detail, verdict))

    algo = _read("/sys/block/zram0/comp_algorithm")
    if algo:
        sel = re.search(r"\[([^\]]+)\]", algo)
        selected = sel.group(1) if sel else algo
        rows.append(("205 zram algo", selected,
                     "OK (expect zstd from 205)" if selected == "zstd" else "NOTE: compressor override"))

    # --- 212: THP / mTHP ----------------------------------------------------
    for label, path, expect in (
        ("212 thp enabled", "/sys/kernel/mm/transparent_hugepage/enabled", "madvise"),
        ("212 thp defrag", "/sys/kernel/mm/transparent_hugepage/defrag", "defer"),
        ("212 max_ptes_swap", "/sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_swap", "0"),
        ("212 shrink_underused", "/sys/kernel/mm/transparent_hugepage/shrink_underused", "1"),
    ):
        val = _read(path)
        if not val:
            rows.append((label, "absent", "SKIP: sysfs path not present"))
            continue
        sel = re.search(r"\[([^\]]+)\]", val)
        cur = sel.group(1) if sel else val
        rows.append((label, cur, "OK" if cur == expect else f"DRIFT: expected {expect} from 212"))

    # --- MGLRU: read-only ruling -------------------------------------------
    lru = _read("/sys/kernel/mm/lru_gen/enabled")
    if not lru:
        rows.append(("MGLRU", "absent", "WARN: CONFIG_LRU_GEN not enabled in this kernel"))
    else:
        try:
            ok = int(lru, 0) == 0x7
        except ValueError:
            ok = False
        rows.append(("MGLRU", lru,
                     "OK: 0x0007 default, no sysfs write needed (min_ttl_ms deliberately unset)"
                     if ok else "WARN: expected 0x0007"))

    # --- tier alignment -----------------------------------------------------
    t212 = "DYNAMIC_EFFICIENCY" if gib <= 17 else "PROGRESSIVE_PERFORMANCE"
    rows.append(("tier alignment", f"{gib:.1f} GiB -> 211:{tier} / 212:{t212}",
                 "OOM thresholds are discrete; THP stays conservative through 16GB class then scales linearly"))
    return rows


def print_coherence(tier: str, gib: float) -> None:
    rows = coherence_report(tier, gib)
    if HAVE_RICH and console:
        t = Table(box=box.SIMPLE, title="205 / 211 / 212 coherence preflight (read-only)")
        t.add_column("Subsystem", style="cyan")
        t.add_column("Observed")
        t.add_column("Verdict")
        for a, b, c in rows:
            style = "red" if c.startswith("FAIL") else "yellow" if c.startswith(("WARN", "DRIFT")) else "green"
            t.add_row(a, b, f"[{style}]{c}[/]")
        console.print(t)
    else:
        print("--- coherence preflight (read-only) ---")
        for a, b, c in rows:
            print(f"  {a:22} {b:36} {c}")


# --------------------------------------------------------------------------- #
# Verify mode
# --------------------------------------------------------------------------- #

def verify() -> int:
    problems = 0
    oomd_active = subprocess.run(["systemctl", "is-active", "--quiet", "systemd-oomd"], check=False).returncode == 0
    if oomd_active:
        say(f"[green]{'OOMD OK':11}[/] systemd-oomd active", plain=f"{'OOMD OK':11} systemd-oomd active")
    else:
        warn("systemd-oomd is inactive or failed")
        problems += 1

    for rule in ("30-dusky-pressure", "30-dusky-swap", "30-dusky-swap-ceiling", "30-dusky-background"):
        rp = Path(f"/etc/systemd/oomd/rules.d/{rule}.oomrule")
        if rp.is_file():
            say(f"[green]{'RULE OK':11}[/] {rule} installed", plain=f"{'RULE OK':11} {rule} installed")
        else:
            warn(f"{rule} missing at {rp}")
            problems += 1

    binary = Path("/usr/local/bin/dusky-oom-shield")
    if binary.is_file() and os.access(binary, os.X_OK):
        say(f"[green]{'BIN OK':11}[/] {binary}", plain=f"{'BIN OK':11} {binary}")
    else:
        warn(f"{binary} missing or not executable")
        problems += 1

    sessions = active_sessions()
    if not sessions:
        uid = os.getuid()
        name = pwd.getpwuid(uid).pw_name
        sessions = [(uid, name)]

    for uid, name in sessions:
        if os.geteuid() == 0:
            env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}",
                   "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
            r = subprocess.run(["runuser", "-u", name, "-w",
                                "XDG_RUNTIME_DIR,DBUS_SESSION_BUS_ADDRESS", "--",
                                "systemctl", "--user", "is-active", "--quiet",
                                "dusky_oom_shield.service"],
                               env=env, capture_output=True, text=True, check=False)
        else:
            r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "dusky_oom_shield.service"],
                               capture_output=True, text=True, check=False)

        if r.returncode == 0:
            say(f"[green]{'SHIELD OK':11}[/] running for {name}",
                plain=f"{'SHIELD OK':11} running for {name}")
        else:
            warn(f"shield not active for {name}")
            problems += 1
    return problems


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Deploy Hyprland/desktop OOM protection (Arch, systemd 262+, kernel 7.3+)")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would change")
    ap.add_argument("--tier", choices=["S", "M", "L", "P"], default=None,
                    help="override detected RAM tier")
    ap.add_argument("--verify", action="store_true",
                    help="read-only post-install verification, then exit")
    args = ap.parse_args()

    gib = read_mem_total_gib()
    tier = args.tier or get_ram_tier(gib)
    prof = TIER_PROFILES[tier]

    if args.verify:
        panel("Verification: systemd-oomd / dusky stack")
        print_coherence(tier, gib)
        sys.exit(1 if verify() else 0)

    if not args.dry_run and os.geteuid() != 0:
        if not shutil.which("sudo"):
            fail("root privileges required and sudo is unavailable")
        say("[blue]Re-executing via sudo...[/]", plain="[INFO] Re-executing via sudo...")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(SELF_PATH), *sys.argv[1:]])

    controllers = Path("/sys/fs/cgroup/cgroup.controllers")
    if not controllers.exists():
        if Path("/sys/fs/cgroup/memory/memory.stat").exists():
            fail("cgroup v1/hybrid hierarchy detected. systemd-oomd requires the unified "
                 "hierarchy. Boot with systemd.unified_cgroup_hierarchy=1 (Arch default since "
                 "systemd 248) and re-run.")
        fail("/sys/fs/cgroup/cgroup.controllers is absent: no cgroup v2 hierarchy is mounted.")
    if "memory" not in controllers.read_text(encoding="utf-8").split():
        fail("the 'memory' controller is not available in the unified hierarchy; "
             "systemd-oomd cannot compute pgscan or PSI candidates.")

    all_specs = specs(tier)
    _, _, flag_src = get_makepkg_build_flags()

    if args.dry_run:
        panel(f"DRY RUN: systemd 262+ OOM configuration (tier {tier}, {gib:.1f} GiB, {prof['label']})")
        if HAVE_RICH and console:
            t = Table(box=box.SIMPLE_HEAVY)
            t.add_column("Action"); t.add_column("Destination"); t.add_column("Description"); t.add_column("Mode")
            for sp in all_specs:
                t.add_row("install", str(sp.dest), sp.desc, oct(sp.mode))
            for lp in LEGACY_PATHS:
                t.add_row("remove", str(lp), "superseded artefact", "-")
            t.add_row("compile", "/usr/local/bin/dusky-oom-shield",
                      f"native C, -march=native via {flag_src}", "0o755")
            console.print(t)
        else:
            for sp in all_specs:
                print(f"INSTALL: {sp.dest} ({sp.desc}) [mode {oct(sp.mode)}]")
            for lp in LEGACY_PATHS:
                print(f"REMOVE : {lp} (superseded artefact)")
            print(f"COMPILE: /usr/local/bin/dusky-oom-shield (-march=native via {flag_src}) [mode 0o755]")
        print_coherence(tier, gib)
        return

    panel(f"Deploying systemd 262+ OOM configuration (tier {tier}, {gib:.1f} GiB)")
    print_coherence(tier, gib)

    updated = 0
    for sp in all_specs:
        status = atomic_install(sp)
        if status == "updated":
            updated += 1
        colour = "green" if status == "updated" else "dim"
        say(f"[{colour}]{status.upper():11}[/] {sp.dest} [dim]({sp.desc})[/]",
            plain=f"{status.upper():11} {sp.dest} ({sp.desc})")
    prune_legacy()

    src = Path("/usr/local/src/dusky-oom-shield.c")
    if not build_shield(src, Path("/usr/local/bin/dusky-oom-shield")):
        fail("dusky-oom-shield failed to build with both makepkg and sanitised flags")

    run_sysctl(["systemctl", "daemon-reload"], quiet_ok=False)
    run_sysctl(["systemctl", "enable", "--now", "systemd-oomd.socket", "systemd-oomd.service"], quiet_ok=False)
    run_sysctl(["systemctl", "restart", "systemd-oomd.socket", "systemd-oomd.service"], quiet_ok=False)
    reload_user_managers()

    oomd_ok = subprocess.run(["systemctl", "is-active", "--quiet", "systemd-oomd.service"],
                             check=False).returncode == 0
    status_txt = "active" if oomd_ok else "INACTIVE"

    msg = (
        f"{updated} updated, {len(all_specs) - updated} up-to-date\n"
        f"tier {tier} ({prof['label']}, {gib:.1f} GiB)  "
        f"app {prof['pressure_above']}/{prof['pressure_lasting']}  "
        f"swap {prof['swap_max']}+{prof['swap_pressure']}/{prof['swap_lasting']}  "
        f"bg {prof['bg_pressure_above']}/{prof['bg_pressure_lasting']}\n"
        f"systemd-oomd: {status_txt}\n"
        f"verify: sudo {SELF_PATH.name} --verify   |   oomctl dump\n"
        f"re-login required for DefaultOOMScoreAdjust / DefaultMemoryAccounting / "
        f"session-.scope.d to apply to existing sessions"
    )
    if HAVE_RICH and console:
        console.print(Panel.fit(f"[bold green]{msg}[/]", box=box.ROUNDED))
    else:
        print(msg)


if __name__ == "__main__":
    main()
