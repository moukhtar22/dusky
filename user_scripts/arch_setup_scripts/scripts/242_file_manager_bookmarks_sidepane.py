#!/usr/bin/env python3
"""
File Manager Bookmark Configurator
----------------------------------
Automatically sets up bookmarks (side pane directories) for popular Linux file
managers such as Nautilus, Thunar, and Dolphin.

Includes beautiful output, atomic file writing to prevent corruption,
strict validation of directory targets, and pretty-printing of XML.
"""

import os
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# Attempt to load 'rich' for beautiful output, but degrade gracefully if missing
# This avoids breaking on modern Arch Linux where 'pip install' is blocked by PEP 668.
try:
    from rich.console import Console
    from rich.theme import Theme
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    custom_theme = Theme({
        "info": "cyan",
        "warning": "bold yellow",
        "danger": "bold red",
        "success": "bold green",
    })
    console = Console(theme=custom_theme)
    RICH_ENABLED = True
except ImportError:
    RICH_ENABLED = False
    print("The 'rich' library is missing. Attempting to auto-install via pacman (Arch Linux)...")
    import subprocess
    import sys
    try:
        # Calls pacman to install it properly on Arch Linux, bypassing PEP 668 pip errors.
        # It will prompt for your sudo password in the terminal.
        subprocess.check_call(["sudo", "pacman", "-S", "--noconfirm", "python-rich"])
        
        from rich.console import Console
        from rich.theme import Theme
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text

        custom_theme = Theme({
            "info": "cyan",
            "warning": "bold yellow",
            "danger": "bold red",
            "success": "bold green",
        })
        console = Console(theme=custom_theme)
        RICH_ENABLED = True
        print("Successfully installed 'python-rich'!")
    except Exception as e:
        print(f"Auto-install failed ({e}). Falling back to plain-text mode...\n")
        
        class DummyConsole:
            def print(self, *args, **kwargs):
                import re
                text = " ".join(str(a) for a in args)
                # Naive strip of rich markup tags
                text = re.sub(r'\[/?.*?\]', '', text)
                print(text)
                
            def status(self, msg, *args, **kwargs):
                import contextlib
                @contextlib.contextmanager
                def dummy_status():
                    self.print(msg)
                    yield
                return dummy_status()
                
        console = DummyConsole()
        
        class DummyTable:
            def __init__(self, *args, **kwargs):
                self.title = kwargs.get("title", "")
                self.columns = []
                self.rows = []
            def add_column(self, name, **kwargs):
                self.columns.append(name)
            def add_row(self, *args):
                self.rows.append(args)
            def __str__(self):
                out = f"--- {self.title} ---\n"
                out += " | ".join(self.columns) + "\n"
                for row in self.rows:
                    out += " | ".join(str(x) for x in row) + "\n"
                return out
                
        Table = DummyTable
        
        def Panel(text, **kwargs):
            return f"=== {text} ==="
            
        def Text(text, **kwargs):
            return text

@dataclass(kw_only=True)
class Bookmark:
    path: str
    name: str | None = None
    icon: str = "folder"

# =====================================================================
# CONFIGURATION
# Add or remove directories here. Paths will be expanded automatically.
# =====================================================================
BOOKMARKS: list[Bookmark] = [
    Bookmark(path="~/Documents", name="Documents", icon="folder-documents"),
    Bookmark(path="~/Downloads", name="Downloads", icon="folder-downloads"),
    Bookmark(path="~/Pictures", name="Pictures", icon="folder-pictures"),
    Bookmark(path="/mnt/zram1", name="ZRAM Disk", icon="drive-harddisk"),
    Bookmark(path="~/user_scripts", name="Dusky Scripts", icon="text-x-script"),
    Bookmark(path="/mnt", name="Mounts", icon="drive-harddisk"),
    Bookmark(path="~/.config", name="Config", icon="folder-system"),
]
# =====================================================================

def ensure_directory(path: Path) -> bool:
    """Ensures a directory exists safely without raising exceptions."""
    if path.exists():
        if not path.is_dir():
            console.print(f"[warning]⚠ Path {path} exists but is not a directory. Skipping.[/warning]")
            return False
        return True
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        console.print(f"[danger]✖ Failed to create directory {path}: {e}[/danger]")
        return False

def update_gtk_bookmarks(bookmarks: list[Bookmark]) -> int:
    """Updates GTK 3/4 bookmarks using safe atomic writes."""
    gtk_bookmarks_file = Path.home() / ".config" / "gtk-3.0" / "bookmarks"
    gtk_bookmarks_file.parent.mkdir(parents=True, exist_ok=True)
    
    existing_lines: list[str] = []
    if gtk_bookmarks_file.exists():
        existing_lines = gtk_bookmarks_file.read_text(encoding="utf-8").splitlines()
        
    existing_uris = {line.split(" ")[0] for line in existing_lines if line.strip()}
    
    new_lines = list(existing_lines)
    added = 0
    for bm in bookmarks:
        real_path = Path(bm.path).expanduser().resolve()
        if not ensure_directory(real_path):
            continue
            
        uri = real_path.as_uri()
        if uri not in existing_uris:
            if bm.name:
                new_lines.append(f"{uri} {bm.name}")
            else:
                new_lines.append(uri)
            added += 1
                
    if new_lines != existing_lines:
        # Atomic write: write to .tmp first, then replace.
        temp_file = gtk_bookmarks_file.with_suffix('.tmp')
        temp_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        temp_file.replace(gtk_bookmarks_file)
        
    return added

def update_kde_bookmarks(bookmarks: list[Bookmark]) -> int:
    """Updates KDE bookmarks with atomic writes and pretty-printed XML."""
    kde_bookmarks_file = Path.home() / ".local" / "share" / "user-places.xbel"
    kde_bookmarks_file.parent.mkdir(parents=True, exist_ok=True)
    
    ET.register_namespace("bookmark", "http://www.freedesktop.org/standards/desktop-bookmarks")
    ET.register_namespace("mime", "http://www.freedesktop.org/standards/shared-mime-info")
    
    if not kde_bookmarks_file.exists():
        initial_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE xbel>\n'
            '<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" '
            'xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">\n'
            '</xbel>\n'
        )
        kde_bookmarks_file.write_text(initial_xml, encoding="utf-8")
        
    try:
        tree = ET.parse(kde_bookmarks_file)
        root = tree.getroot()
    except ET.ParseError:
        root = ET.Element("xbel")
        tree = ET.ElementTree(root)
        
    root.set("xmlns:bookmark", "http://www.freedesktop.org/standards/desktop-bookmarks")
    root.set("xmlns:mime", "http://www.freedesktop.org/standards/shared-mime-info")

    existing_hrefs = {elem.get("href") for elem in root.findall(".//bookmark")}
    
    added = 0
    for bm in bookmarks:
        real_path = Path(bm.path).expanduser().resolve()
        if not ensure_directory(real_path):
            continue
            
        uri = real_path.as_uri()
        if uri not in existing_hrefs:
            bookmark_elem = ET.SubElement(root, "bookmark", href=uri)
            
            title_elem = ET.SubElement(bookmark_elem, "title")
            title_elem.text = bm.name or real_path.name
            
            info_elem = ET.SubElement(bookmark_elem, "info")
            
            fd_metadata_elem = ET.SubElement(info_elem, "metadata", owner="http://freedesktop.org")
            ET.SubElement(fd_metadata_elem, "bookmark:icon", name=bm.icon)
            
            kde_metadata_elem = ET.SubElement(info_elem, "metadata", owner="http://www.kde.org")
            ET.SubElement(kde_metadata_elem, "isSystemItem").text = "false"
            
            added += 1
            
    if added > 0:
        # Pretty-print the XML natively
        if hasattr(ET, "indent"):
            ET.indent(tree, space="  ", level=0)
            
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        final_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n' + xml_str
        
        # Atomic write
        temp_file = kde_bookmarks_file.with_suffix('.tmp')
        temp_file.write_text(final_xml, encoding="utf-8")
        temp_file.replace(kde_bookmarks_file)
        
    return added

def main() -> None:
    console.print(Panel(Text("Linux File Manager Bookmarks", justify="center", style="bold magenta")))
    
    table = Table(expand=True, title="[info]Loaded Configuration[/info]")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Directory Path", style="magenta")
    table.add_column("Icon", style="green")
    
    for bm in BOOKMARKS:
        table.add_row(str(bm.name), str(bm.path), str(bm.icon))
        
    console.print(table)
    console.print()
    
    with console.status("[bold cyan]Applying configuration...[/bold cyan]") as status:
        try:
            added_gtk = update_gtk_bookmarks(BOOKMARKS)
            msg = "Configured successfully" if added_gtk == 0 else f"Added {added_gtk} new entries"
            console.print(f"[success]✔ GTK (Nautilus/Thunar)[/success]: {msg}")
        except Exception as e:
            console.print(f"[danger]✖ GTK Error:[/danger] {e}")
            
        try:
            added_kde = update_kde_bookmarks(BOOKMARKS)
            msg = "Configured successfully" if added_kde == 0 else f"Added {added_kde} new entries"
            console.print(f"[success]✔ KDE (Dolphin)[/success]: {msg}")
        except Exception as e:
            console.print(f"[danger]✖ KDE Error:[/danger] {e}")

    console.print("\n[bold green]🎉 Side panes are fully configured![/bold green]")

if __name__ == "__main__":
    main()
