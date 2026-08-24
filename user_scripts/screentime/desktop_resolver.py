#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: DESKTOP ENTRY RESOLVER (Python 3.14 Bleeding-Edge)
===============================================================================
Scans and parses system and user `.desktop` entries line-by-line without any
subprocesses or regex bottlenecks, matching the exact behavior of Rofi
(`rofi/dusky_launcher.sh`) to provide clean application names, icons, and
categories from raw Hyprland window classes.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KNOWN_TERMINALS: set[str] = {
    "kitty",
    "alacritty",
    "wezterm",
    "foot",
    "ghostty",
    "konsole",
    "gnome-terminal",
    "urxvt",
    "st",
    "rio",
    "termite",
    "xterm",
}


@dataclass(slots=True, frozen=True)
class AppInfo:
    name: str
    category: str
    icon: str
    window_class: str


class DesktopResolver:
    """
    High-performance caching resolver for XDG application desktop entries.
    """

    def __init__(self) -> None:
        # Lookup tables mapped by lowercase key to AppInfo
        self._by_wmclass: dict[str, AppInfo] = {}
        self._by_stem: dict[str, AppInfo] = {}
        self._by_name: dict[str, AppInfo] = {}
        self._by_exec: dict[str, AppInfo] = {}

        # Cache for previously resolved window_classes during runtime
        self._resolved_cache: dict[str, AppInfo] = {}
        self.reload()

    def reload(self) -> None:
        """
        Scan all XDG application directories and build lookup indexes.
        """
        self._by_wmclass.clear()
        self._by_stem.clear()
        self._by_name.clear()
        self._by_exec.clear()
        self._resolved_cache.clear()

        search_dirs: list[Path] = [
            Path("~/.local/share/applications").expanduser(),
        ]

        xdg_dirs = os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share/:/usr/share/"
        )
        for d in xdg_dirs.split(":"):
            if d.strip():
                p = Path(d.strip()) / "applications"
                if p not in search_dirs:
                    search_dirs.append(p)

        extra_dirs = [
            Path("/var/lib/flatpak/exports/share/applications"),
            Path("~/.local/share/flatpak/exports/share/applications").expanduser(),
        ]
        for p in extra_dirs:
            if p not in search_dirs:
                search_dirs.append(p)

        for d in search_dirs:
            if not d.exists() or not d.is_dir():
                continue
            for filepath in d.glob("*.desktop"):
                self._parse_file(filepath)

    def _parse_file(self, filepath: Path) -> None:
        name = ""
        generic_name = ""
        icon = ""
        wm_class = ""
        exec_cmd = ""
        categories = ""
        no_display = False
        in_desktop_entry = False

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("["):
                        in_desktop_entry = (line == "[Desktop Entry]")
                        continue

                    if not in_desktop_entry:
                        continue

                    # Exact key prefix matching (prevent locale keys like Name[de]= from overriding Name=)
                    if line.startswith("Name=") and not name:
                        name = line[5:].strip()
                    elif line.startswith("GenericName=") and not generic_name:
                        generic_name = line[12:].strip()
                    elif line.startswith("Icon=") and not icon:
                        icon = line[5:].strip()
                    elif line.startswith("StartupWMClass=") and not wm_class:
                        wm_class = line[15:].strip()
                    elif line.startswith("Exec=") and not exec_cmd:
                        exec_cmd = line[5:].strip()
                    elif line.startswith("Categories=") and not categories:
                        categories = line[11:].strip()
                    elif line.lower().startswith("nodisplay=true") or line.lower().startswith("hidden=true"):
                        no_display = True
        except Exception:
            return

        if not name or no_display:
            return

        # Clean up XML/Pango entities if present
        name = name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        generic_name = (
            generic_name.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        )

        # Determine best category description
        category_desc = generic_name
        if not category_desc and categories:
            cats = [c.strip() for c in categories.split(";") if c.strip()]
            for c in cats:
                match c:
                    case "Application" | "X-GNOME-Utilities" | "GTK" | "Qt" | "KDE" | "GNOME":
                        continue
                    case "AudioVideo":
                        category_desc = "Audio & Video"
                    case "Network":
                        category_desc = "Internet"
                    case "Development":
                        category_desc = "Development"
                    case "Utility":
                        category_desc = "Utilities"
                    case "System":
                        category_desc = "System"
                    case "Game":
                        category_desc = "Gaming"
                    case "Graphics":
                        category_desc = "Graphics"
                    case "Office":
                        category_desc = "Office"
                    case "TerminalEmulator":
                        category_desc = "Terminal & Shell"
                    case _:
                        category_desc = c
                break
            if not category_desc and cats:
                category_desc = cats[0]

        if not category_desc:
            category_desc = "Application"

        stem = filepath.stem
        info = AppInfo(
            name=name,
            category=category_desc,
            icon=icon or "application-x-executable",
            window_class=wm_class or stem,
        )

        # Index by StartupWMClass
        if wm_class:
            self._by_wmclass[wm_class.lower()] = info

        # Index by stem (e.g. firefox from firefox.desktop)
        self._by_stem[stem.lower()] = info

        # If stem has dots (e.g. org.kde.kdenlive), also index the last segment
        if "." in stem:
            last_seg = stem.split(".")[-1].lower()
            if last_seg not in self._by_stem:
                self._by_stem[last_seg] = info

        # Index by exact Name
        self._by_name[name.lower()] = info

        # Index by Exec command (handle quotes and path basenames cleanly)
        if exec_cmd:
            raw_exec = exec_cmd.strip('"\'').split()[0].strip('"\'')
            clean_exec = raw_exec.split("/")[-1].lower()
            if clean_exec and clean_exec not in self._by_exec:
                self._by_exec[clean_exec] = info

    def resolve(self, window_class: str, window_title: str = "") -> AppInfo:
        """
        Given a raw Hyprland window class and optional title, resolve to a
        clean AppInfo object with human-readable Name, Category, and Icon.
        """
        if not window_class:
            return AppInfo(
                name="Desktop / Idle",
                category="System",
                icon="user-desktop",
                window_class="desktop",
            )

        cache_key = f"{window_class.lower()}::{window_title.lower()}"
        if cache_key in self._resolved_cache:
            return self._resolved_cache[cache_key]

        wc_lower = window_class.lower()

        # Terminal heuristic prioritization
        if wc_lower in KNOWN_TERMINALS:
            title_clean = (
                window_title.split(" - ")[-1] if " - " in window_title else window_title
            ).strip()
            term_name = window_class.replace("-", " ").replace("_", " ").title()
            res = AppInfo(
                name=f"{term_name} ({title_clean})"
                if title_clean and title_clean.lower() != wc_lower
                else f"{term_name} Terminal",
                category="Terminal & Shell",
                icon="utilities-terminal",
                window_class=window_class,
            )
            self._resolved_cache[cache_key] = res
            return res

        # 1. Check StartupWMClass exact match
        if wc_lower in self._by_wmclass:
            res = self._by_wmclass[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 2. Check filename stem exact match
        if wc_lower in self._by_stem:
            res = self._by_stem[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 3. Check if window_class has dots or hyphens (e.g. codium-url-handler -> codium / vscodium)
        if "." in wc_lower:
            last_seg = wc_lower.split(".")[-1]
            if last_seg in self._by_stem:
                res = self._by_stem[last_seg]
                self._resolved_cache[cache_key] = res
                return res
            if last_seg in self._by_wmclass:
                res = self._by_wmclass[last_seg]
                self._resolved_cache[cache_key] = res
                return res

        if "-" in wc_lower:
            first_seg = wc_lower.split("-")[0]
            if first_seg in self._by_stem:
                res = self._by_stem[first_seg]
                self._resolved_cache[cache_key] = res
                return res
            if first_seg in self._by_wmclass:
                res = self._by_wmclass[first_seg]
                self._resolved_cache[cache_key] = res
                return res

        # 4. Check Name exact match
        if wc_lower in self._by_name:
            res = self._by_name[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 5. Check Exec command exact match
        if wc_lower in self._by_exec:
            res = self._by_exec[wc_lower]
            self._resolved_cache[cache_key] = res
            return res

        # 6. Fallback heuristics for un-indexed window classes (including reverse-DNS)
        clean_name = window_class
        if "." in window_class:
            parts = [p for p in window_class.split(".") if p]
            if len(parts) > 1:
                clean_name = parts[-1]
                if clean_name.lower() in ("desktop", "client", "app", "ui") and len(parts) > 2:
                    clean_name = f"{parts[-2]} {parts[-1]}"

        clean_name = (
            clean_name.replace("-", " ")
            .replace("_", " ")
            .strip()
            .title()
        )

        res = AppInfo(
            name=clean_name or window_class,
            category="Application",
            icon="application-x-executable",
            window_class=window_class,
        )
        self._resolved_cache[cache_key] = res
        return res


if __name__ == "__main__":
    resolver = DesktopResolver()
    test_classes = (
        sys.argv[1:]
        if len(sys.argv) > 1
        else [
            ("firefox", ""),
            ("code", ""),
            ("steam", ""),
            ("kitty", "nvim desktop_resolver.py"),
            ("org.kde.kdenlive", ""),
            ("codium-url-handler", ""),
        ]
    )
    print("\033[1;34m::\033[0m \033[1mDusky Screentime Desktop Resolver Test\033[0m\n")
    for item in test_classes:
        cls, title = item if isinstance(item, tuple) else (item, "")
        info = resolver.resolve(cls, title)
        print(
            f"Class: \033[96m{cls:<22}\033[0m Title: \033[90m{title:<25}\033[0m => Name: \033[92m{info.name:<25}\033[0m | Category: \033[93m{info.category:<18}\033[0m | Icon: {info.icon}"
        )
