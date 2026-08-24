#!/usr/bin/env python3
"""
Dusky Sites Setup Script (Arch Linux / Python 3.12+ / Firefox 153+)
======================================================================
Provisions XDG configuration directories, installs native messaging host
to ~/.local/share/dusky-sites/dusky_sites_host.py, parses profiles.ini,
registers native messaging host manifests, and configures userChrome CSS.
"""

from __future__ import annotations

import sys
import os
import re
import json
import shutil
from pathlib import Path

# Terminal Styling
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_BLUE = "\033[0;34m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[0;31m"
C_RESET = "\033[0m"

HOST_INSTALL_NAME = "dusky_sites_host.py"
MANIFEST_NAME = "dusky_sites.json"
EXTENSION_ID = "dusky_sites@dusky.com"

def print_step(msg: str) -> None: print(f"{C_BLUE}==>{C_RESET} {msg}")
def print_success(msg: str) -> None: print(f"{C_GREEN}✓{C_RESET} {msg}")
def print_warn(msg: str) -> None: print(f"{C_YELLOW}[!] {msg}")
def print_error(msg: str) -> None: print(f"{C_RED}[!] Error:{C_RESET} {msg}"); sys.exit(1)

PREFS_TO_SET = [
    ("toolkit.legacyUserProfileCustomizations.stylesheets", "true"),
    ("extensions.autoDisableScopes", "0"),
    ("extensions.enabledScopes", "15"),
]

def atomic_write_text(path: Path, text: str) -> None:
    """Write text via tempfile + replace (same-directory atomic rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy file via tempfile + replace (same-directory atomic rename)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)

def ensure_css_import(path: Path, import_line: str) -> None:
    """Ensure import_line exists at the top of path, respecting @charset if present."""
    if not path.is_file():
        atomic_write_text(path, f"{import_line}\n")
        return
    content = path.read_text(encoding="utf-8")
    if import_line in content:
        return
    lines = content.splitlines(keepends=True)
    if lines and lines[0].startswith('@charset'):
        lines.insert(1, f"{import_line}\n")
    else:
        lines.insert(0, f"{import_line}\n")
    atomic_write_text(path, "".join(lines))

def ensure_symlink(link: Path, target: Path) -> None:
    """Create or replace a symlink link -> target (absolute). No-op if already correct."""
    target_abs = target.expanduser()
    try:
        target_abs = target_abs.resolve() if target_abs.exists() else target_abs.absolute()
    except OSError:
        target_abs = target_abs.absolute()
    try:
        if link.is_symlink():
            try:
                if link.resolve() == target_abs or os.readlink(link) == str(target_abs):
                    return
            except OSError:
                pass
            link.unlink()
        elif link.is_file():
            link.unlink()
        elif link.exists():
            return  # do not clobber directories
        link.symlink_to(target_abs)
    except OSError as e:
        print_warn(f"Could not link {link} -> {target_abs}: {e}")

def remove_css_import(path: Path, import_line: str) -> None:
    """Remove import_line from path if present."""
    if not path.is_file():
        return
    content = path.read_text(encoding="utf-8")
    if import_line not in content:
        return
    new_content = content.replace(f"{import_line}\n", "").replace(import_line, "")
    atomic_write_text(path, new_content)

def ensure_firefox_prefs(user_js: Path) -> bool:
    try:
        content = user_js.read_text(encoding="utf-8") if user_js.is_file() else ""
        for pref_name, pref_val in PREFS_TO_SET:
            pref_re = re.compile(rf'user_pref\(\s*"{re.escape(pref_name)}"\s*,\s*[^)]+\s*\)\s*;')
            pref_line = f'user_pref("{pref_name}", {pref_val});'
            if pref_re.search(content):
                content = pref_re.sub(pref_line, content)
            else:
                content = content.rstrip() + f"\n{pref_line}\n"
        if not content.endswith("\n"):
            content += "\n"
        atomic_write_text(user_js, content)
        return True
    except OSError as e:
        print_warn(f"Could not write {user_js}: {e}")
        return False

def iter_firefox_profiles(base_dir: Path):
    """Yield profile directories from profiles.ini; fallback to prefs.js heuristic."""
    ini = base_dir / "profiles.ini"
    if ini.is_file():
        current: dict[str, str] = {}
        try:
            text = ini.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""

        profiles: list[Path] = []
        def consider(cur: dict[str, str]) -> None:
            rel = cur.get("path")
            if not rel:
                return
            p = Path(rel)
            is_relative = cur.get("isrelative", "1") != "0"
            if is_relative:
                profile = base_dir / p
            else:
                profile = p if p.is_absolute() else (base_dir / p)
            try:
                if profile.is_dir():
                    profiles.append(profile.resolve())
            except OSError:
                if profile.is_dir():
                    profiles.append(profile)

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                consider(current)
                current = {}
            elif "=" in line:
                k, v = line.split("=", 1)
                current[k.strip().lower()] = v.strip()
        consider(current)

        seen: set[Path] = set()
        for prof in profiles:
            if prof not in seen:
                seen.add(prof)
                yield prof
        return

    try:
        for profile in base_dir.iterdir():
            if profile.is_dir() and (profile / "prefs.js").is_file():
                yield profile
    except OSError:
        return

MENU_CSS_CONTENT = """/* Auto-generated by Dusky Sites — Native Context Menu & Chrome UI */
:root {
    --zen-primary-color: var(--lwt-accent-color, #1c1b22) !important;
    --zen-accent-primary: var(--lwt-accent-color, #1c1b22) !important;
    --zen-background: var(--lwt-accent-color, #1c1b22) !important;
    --zen-text: var(--lwt-text-color, #fbfbfe) !important;

    --panel-item-hover-bgcolor: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.1)) !important;
    --panel-separator-color: var(--chrome-content-separator-color, rgba(255, 255, 255, 0.15)) !important;

    --message-bar-background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    --message-bar-text-color: var(--lwt-text-color, #fbfbfe) !important;
    --message-bar-icon-color: var(--lwt-text-color, #fbfbfe) !important;
}

#navigator-toolbox,
#zen-appcontent-wrapper {
    background: var(--lwt-accent-color, #1c1b22) !important;
}

#sidebar-box,
#sidebar-header,
sidebarheader {
    background-color: var(--sidebar-background-color, var(--lwt-accent-color, #1c1b22)) !important;
    color: var(--sidebar-text-color, var(--lwt-text-color, #fbfbfe)) !important;
}

menupopup,
panel:not(#autoscroller) {
    appearance: none !important;
    -moz-default-appearance: none !important;
    background-color: transparent !important;
    background: transparent !important;
    --panel-background-color: var(--lwt-accent-color, #1c1b22) !important;
    --panel-background: var(--lwt-accent-color, #1c1b22) !important;
    --panel-text-color: var(--lwt-text-color, #fbfbfe) !important;
    --panel-color: var(--lwt-text-color, #fbfbfe) !important;
    --panel-border-color: var(--toolbar-field-background-color-focus, #42414d) !important;
    --menu-background-color: var(--lwt-accent-color, #1c1b22) !important;
    --menu-color: var(--lwt-text-color, #fbfbfe) !important;
    --menu-border-color: var(--toolbar-field-background-color-focus, #42414d) !important;
    --panel-menuitem-border-radius: 6px !important;
}

menupopup::part(content),
panel::part(content),
.popup-notification-body {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 8px !important;
}

panelview,
panelmultiview,
#unified-extensions-panel,
#unified-extensions-view,
#unified-extensions-area,
.unified-extensions-list,
.panel-subview-body,
.panel-subview-footer {
    background-color: transparent !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
}

/* Middle-Click Auto-Scroll Floating Disc (autoscroll.css) */
#autoscroller,
panel#autoscroller,
.autoscroller {
    appearance: none !important;
    -moz-default-appearance: none !important;
    --panel-background-color: var(--lwt-accent-color, #1c1b22) !important;
    --panel-border-color: var(--toolbar-field-background-color-focus, #42414d) !important;
    background-color: transparent !important;
    background: transparent !important;
    border: none !important;
    border-radius: 0 !important;
    background-image: none !important;
    position: relative !important;
    box-shadow: none !important;
}

.autoscroller::after,
#autoscroller::after {
    content: "" !important;
    display: block !important;
    position: absolute !important;
    inset: 0 !important;
    background-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color, #fbfbfe)) !important;
    mask-image: var(--autoscroll-background-image) !important;
    mask-repeat: no-repeat !important;
    mask-position: center !important;
    mask-size: auto !important;
}

/* Native Scrollbars, Thumb, Corner & Window Resizer (scrollbars.css) */
scrollbar,
thumb,
scrollbarbutton,
scrollcorner,
resizer {
    appearance: none !important;
    -moz-default-appearance: none !important;
}

scrollbar {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
    border: none !important;
}

scrollbar[vertical] {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
}

thumb {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.2)) !important;
    border-radius: 6px !important;
    border: 2px solid var(--lwt-accent-color, #1c1b22) !important;
}

thumb:hover,
thumb[active] {
    background-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color, #fbfbfe)) !important;
}

scrollbarbutton {
    display: none !important;
}

scrollcorner {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
}

resizer {
    background-color: transparent !important;
    -moz-context-properties: fill, stroke !important;
    fill: var(--toolbarbutton-icon-fill, var(--lwt-text-color, #fbfbfe)) !important;
}

menu,
menuitem {
    appearance: none !important;
    -moz-default-appearance: none !important;
    border-radius: 6px !important;
    padding: 6px 12px !important;
    background-color: transparent !important;
    color: var(--lwt-text-color) !important;
    transition: background-color 120ms ease, color 120ms ease;
}

menu:is(:hover, [_moz-menuactive="true"]):not([disabled]),
menuitem:is(:hover, [_moz-menuactive="true"]):not([disabled]) {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255,255,255,0.1)) !important;
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

/* Subview Hover (Hamburger menu text color) */
panelview .toolbarbutton-1:not([disabled]):is(:hover, :active),
.subviewbutton:not([disabled]):not(#appMenu-zoomReduce-button2, #appMenu-zoomReset-button2, #appMenu-zoomEnlarge-button2, #appMenu-fullscreen-button2):is(:hover, :active) {
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

/* Menubar */
menubar > menu[open] {
    border-bottom: 2px solid var(--tab-loading-fill, #00ddff) !important;
}

/* Separator */
menuseparator::before {
    border-top: 1px solid var(--chrome-content-separator-color, #42414d) !important;
}

/* Checkbox & Radio Items in Menus */
menuitem[type="checkbox"] > .menu-icon,
menuitem[type="radio"] > .menu-icon {
    appearance: none !important;
    -moz-default-appearance: none !important;
    width: 14px !important;
    height: 14px !important;
    display: inline-block !important;
    box-sizing: border-box !important;
    content: "" !important;
    list-style-image: none !important;
    border: 1px solid transparent !important;
    outline: none !important;
    box-shadow: none !important;
    background-color: var(--toolbar-field-background-color, rgba(255,255,255,0.1)) !important;
    border-radius: 50%;
    transition: all 120ms ease;
}

menuitem[type="checkbox"] > .menu-icon {
    border-radius: 4px;
}

menuitem[type="checkbox"][checked] > .menu-icon,
menuitem[type="radio"][checked] > .menu-icon {
    background-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

menuitem:is(:hover, [_moz-menuactive="true"]) > .menu-icon {
    border-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

findbar {
    background-color: var(--lwt-accent-color) !important;
    color: var(--lwt-text-color) !important;
    border-top: 1px solid var(--chrome-content-separator-color, #42414d) !important;
}

.findbar-textbox {
    background-color: var(--toolbar-field-background-color, rgba(255,255,255,0.1)) !important;
    color: var(--lwt-text-color) !important;
}

/* Popup Notifications, Fullscreen & Pointerlock Warnings */
moz-message-bar,
notification-message,
notification,
notification-bar,
message-bar,
notification-message-bar,
.notificationbox-stack,
.container.infobar,
infobar,
.notification-bar,
.notification-box,
[value="popup-blocked"],
.notificationbox-stack message-bar,
#fullscreen-warning,
#pointerlock-warning {
    --message-bar-background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    --message-bar-text-color: var(--lwt-text-color, #fbfbfe) !important;
    --message-bar-icon-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color, #fbfbfe)) !important;
    background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
}

moz-message-bar .container,
notification-message .container {
    background-color: transparent !important;
}

moz-message-bar .icon,
notification-message .icon {
    fill: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

notification-bar button,
notification button,
message-bar button,
infobar button,
.notification-button,
.notification-box button,
moz-message-bar moz-button.close,
notification-message moz-button.close {
    background-color: transparent !important;
    color: var(--lwt-text-color) !important;
    fill: var(--lwt-text-color) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 4px !important;
}

notification-bar button:hover,
notification button:hover,
message-bar button:hover,
infobar button:hover,
.notification-button:hover,
.notification-box button:hover,
moz-message-bar moz-button.close:hover,
notification-message moz-button.close:hover {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.1)) !important;
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
    fill: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

/* Exit Full Screen & Pointerlock Buttons */
#fullscreen-exit-button,
#pointerlock-exit-button {
    appearance: none !important;
    -moz-default-appearance: none !important;
    background-color: transparent !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
    padding: 6px 12px !important;
    margin-left: 12px !important;
    transition: all 120ms ease;
}

#fullscreen-exit-button:hover,
#pointerlock-exit-button:hover {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.1)) !important;
    border-color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
}

tooltip {
    appearance: none !important;
    background-color: var(--lwt-accent-color) !important;
    color: var(--lwt-text-color) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
}

/* AI Sidebar Container (aiWindowSidebar.css) */
#sidebar-box[sidebarcommand*="ai"],
.ai-sidebar-container,
#ai-window-sidebar {
    background-color: var(--sidebar-background-color, var(--lwt-accent-color, #1c1b22)) !important;
    color: var(--sidebar-text-color, var(--lwt-text-color, #fbfbfe)) !important;
}

/* WebRTC Active Camera/Mic Sharing Indicators (webRTC-indicator.css) */
#webRTC-sharing-icon,
#webRTC-sharing-container,
.webrtc-indicator,
.webrtc-indicator-icon {
    background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    color: var(--toolbarbutton-icon-fill, var(--lwt-text-color)) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
}

/* Federated Identity Credential Notifications (identity-credential-notification.css) */
identity-credential-notification,
.identity-credential-panel {
    background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
}

/* Clear Browsing Data Dialog (sanitizeDialog_v2.css) */
#sanitizeDialog,
.sanitize-dialog-container {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
}

/* Vertical Tab Groups & Tab Tree (tab-list-tree.css & smartwindowGroupTabs.css) */
.tab-group-header,
.tab-group-container,
.tab-list-tree-item,
tab-item[selected],
tab-item:hover {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.1)) !important;
    color: var(--lwt-text-color) !important;
}

/* Address Bar & Autocomplete Popups (urlbar-searchbar.css & autocomplete.css) */
#urlbar-results,
.urlbarView,
.autocomplete-history-popup {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
}

.urlbarView-row:is([selected], :hover) {
    background-color: var(--toolbarbutton-background-color-hover, rgba(255, 255, 255, 0.1)) !important;
    color: var(--lwt-text-color) !important;
}

/* Form Autofill & Credit Card Popups (formautofill-notification.css) */
formautofill-creditcard-popup,
.formautofill-popup {
    background-color: var(--toolbar-field-background-color, #2b2a33) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
    border: 1px solid var(--toolbar-field-background-color-focus, #42414d) !important;
    border-radius: 6px !important;
}

/* Page Info Window (pageInfo.css) */
#pageInfoWindow,
.page-info-container {
    background-color: var(--lwt-accent-color, #1c1b22) !important;
    color: var(--lwt-text-color, #fbfbfe) !important;
}
"""

def patch_extensions_json(profile: Path) -> None:
    """Ensure extensions.json marks dusky_sites@dusky.com as active/enabled if present and invalidate addon cache."""
    ext_json_path = profile / "extensions.json"
    if ext_json_path.is_file():
        try:
            data = json.loads(ext_json_path.read_text(encoding="utf-8"))
            addons = data.get("addons", [])
            found = False
            for addon in addons:
                if addon.get("id") == EXTENSION_ID:
                    addon["active"] = True
                    addon["userDisabled"] = False
                    addon["appDisabled"] = False
                    addon["softDisabled"] = False
                    addon["seen"] = True
                    user_perms = addon.setdefault("userPermissions", {})
                    if "origins" not in user_perms or "<all_urls>" not in (user_perms.get("origins") or []):
                        user_perms["origins"] = ["<all_urls>"]
                    addon["userPermissions"] = user_perms
                    found = True
                    break
            if found:
                atomic_write_text(ext_json_path, json.dumps(data, indent=2) + "\n")
        except Exception as e:
            print_warn(f"Could not patch extensions.json in {profile}: {e}")

    startup_cache = profile / "addonStartup.json.lz4"
    if startup_cache.is_file():
        try:
            startup_cache.unlink()
        except OSError as e:
            print_warn(f"Could not reset addonStartup cache in {profile}: {e}")

def setup_user_chrome(home: Path, source_xpi: Path | None = None) -> None:
    browser_dirs = [
        home / ".mozilla" / "firefox",
        home / ".config" / "mozilla" / "firefox",
        home / ".librewolf",
        home / ".config" / "librewolf",
        home / ".zen",
        home / ".config" / "zen",
        home / ".waterfox",
        home / ".floorp",
        home / ".firedragon",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf",
    ]

    installed_profiles = 0
    failed_prefs = 0
    installed_xpis = 0

    for base_dir in browser_dirs:
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            if not ensure_firefox_prefs(profile / "user.js"):
                failed_prefs += 1

            if source_xpi and source_xpi.is_file():
                ext_dir = profile / "extensions"
                try:
                    ext_dir.mkdir(parents=True, exist_ok=True)
                    target_xpi = ext_dir / f"{EXTENSION_ID}.xpi"
                    atomic_copy_file(source_xpi, target_xpi)
                    patch_extensions_json(profile)
                    installed_xpis += 1
                except OSError as e:
                    print_warn(f"Could not copy XPI into {profile}: {e}")

            chrome_dir = profile / "chrome"
            try:
                chrome_dir.mkdir(parents=True, exist_ok=True)
                # Browser chrome (menus, toolbars, panels) -> userChrome.css
                menu_css = chrome_dir / "dusky_menu.css"
                atomic_write_text(menu_css, MENU_CSS_CONTENT)
                ensure_css_import(chrome_dir / "userChrome.css", '@import url("dusky_menu.css");')

                # about: documents -> userContent.css
                # Palette must stay LIVE (symlink), not a setup-time snapshot.
                # Chrome UI gets colors via browser.theme.update(); about: pages
                # only re-read userContent on startup, so a symlink to matugen's
                # generated file is enough — restart picks up new colors without
                # re-running this setup script. (Same idea as chrome/colors.css.)
                about_css = chrome_dir / "dusky_about.css"
                matugen_gen_css = home / ".config" / "matugen" / "generated" / "dusky_sites.css"
                ensure_symlink(chrome_dir / "dusky_palette.css", matugen_gen_css)

                template_about = home / ".config" / "dusky_sites" / "about.css"
                base_about = template_about.read_text(encoding="utf-8") if template_about.is_file() else ""

                about_content = (
                    "/* Live Matugen palette via dusky_palette.css symlink.\n"
                    " * userContent is loaded at browser start — no setup re-run needed. */\n"
                    '@import url("dusky_palette.css");\n\n'
                    f"{base_about}"
                )
                atomic_write_text(about_css, about_content)

                user_content = chrome_dir / "userContent.css"
                ensure_css_import(user_content, '@import url("dusky_about.css");')

                installed_profiles += 1
            except OSError as e:
                print_warn(f"Could not write chrome CSS in {profile}: {e}")

    if installed_profiles > 0:
        print_success(f"Context menu styling injected into {installed_profiles} profile(s).")
    else:
        print_warn("No profile directories found for context menu styling.")
    if installed_xpis > 0:
        print_success(f"Signed XPI installed into {installed_xpis} browser profile(s).")
    if failed_prefs:
        print_warn(f"user.js pref write failed for {failed_prefs} profile(s); userChrome may be inert until fixed.")

def resolve_source_host(script_dir: Path) -> Path:
    candidates = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "dusky_sites_host.py",
        script_dir / HOST_INSTALL_NAME,
        script_dir / "dusky_sites_host.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]

def resolve_source_xpi(script_dir: Path) -> Path | None:
    """Return a signed XPI only if its manifest identifies EXTENSION_ID."""
    def _xpi_has_expected_id(xpi: Path) -> bool:
        import json
        import zipfile
        try:
            with zipfile.ZipFile(xpi) as zf:
                with zf.open("manifest.json") as fh:
                    data = json.load(fh)
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
            return False
        if not isinstance(data, dict):
            return False
        for key in ("browser_specific_settings", "applications"):
            root = data.get(key)
            if isinstance(root, dict):
                gecko = root.get("gecko")
                if isinstance(gecko, dict) and gecko.get("id") == EXTENSION_ID:
                    return True
        return False

    candidates = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "xpi" / f"{EXTENSION_ID}.xpi",
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / f"{EXTENSION_ID}.xpi",
        script_dir / "xpi" / f"{EXTENSION_ID}.xpi",
        script_dir / f"{EXTENSION_ID}.xpi",
    ]
    for c in candidates:
        if c.is_file() and _xpi_has_expected_id(c):
            return c

    search_dirs = [
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites" / "xpi",
        Path.home() / ".config" / "firefox_extentions" / "dusky_sites",
        script_dir / "xpi",
        script_dir,
    ]
    for d in search_dirs:
        if d.is_dir():
            for xpi in sorted(d.glob("*.xpi")):
                if xpi.is_file() and _xpi_has_expected_id(xpi):
                    return xpi
    return None

# ─────────────────────────────────────────────────────────────
# Uninstall
# ─────────────────────────────────────────────────────────────
def _browser_data_dirs(home: Path) -> list[Path]:
    """Firefox-family base directories that receive NMH manifests / global XPIs."""
    return [
        home / ".mozilla",
        home / ".config" / "mozilla",
        home / ".librewolf",
        home / ".config" / "librewolf",
        home / ".zen",
        home / ".config" / "zen",
        home / ".waterfox",
        home / ".floorp",
        home / ".firedragon",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla",
        home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf",
    ]

def _profile_base_dirs(home: Path) -> list[Path]:
    """Directories whose children are profiles (mirrors setup_user_chrome)."""
    return [
        home / ".mozilla" / "firefox",
        home / ".config" / "mozilla" / "firefox",
        home / ".librewolf",
        home / ".config" / "librewolf",
        home / ".zen",
        home / ".config" / "zen",
        home / ".waterfox",
        home / ".floorp",
        home / ".firedragon",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf",
    ]

def _browser_processes_running() -> list[str]:
    """Return names of detected running Firefox-family browsers."""
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-x", "-a", "firefox|librewolf|zen|waterfox|floorp|firedragon"],
                             capture_output=True, text=True).stdout
    except OSError:
        return []
    names: set[str] = set()
    for line in out.splitlines():
        for b in ("firefox", "librewolf", "zen", "waterfox", "floorp", "firedragon"):
            # pgrep -x matches full process name; token at start or after whitespace
            if re.search(rf"(^|\s){re.escape(b)}(\s|$)", line):
                names.add(b)
    return sorted(names)

def _remove_file(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
    except OSError as e:
        print_warn(f"Could not remove {path}: {e}")
    return False

def _remove_tree(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
    except OSError as e:
        print_warn(f"Could not remove directory {path}: {e}")
    return False

def unpatch_extensions_json(profile: Path) -> None:
    """Remove dusky_sites@dusky.com entries from extensions.json and invalidate addon cache."""
    ext_json_path = profile / "extensions.json"
    if ext_json_path.is_file():
        try:
            data = json.loads(ext_json_path.read_text(encoding="utf-8"))
            addons = data.get("addons", [])
            kept = [a for a in addons if a.get("id") != EXTENSION_ID]
            if len(kept) != len(addons):
                data["addons"] = kept
                atomic_write_text(ext_json_path, json.dumps(data, indent=2) + "\n")
                print_success(f"Removed {EXTENSION_ID} from {ext_json_path}")
        except Exception as e:
            print_warn(f"Could not patch extensions.json in {profile}: {e}")

    startup_cache = profile / "addonStartup.json.lz4"
    if startup_cache.is_file():
        try:
            startup_cache.unlink()
        except OSError as e:
            print_warn(f"Could not reset addonStartup cache in {profile}: {e}")

def restore_profile_prefs(profile: Path) -> int:
    """Remove the prefs the installer wrote, only if they still match our values."""
    user_js = profile / "user.js"
    if not user_js.is_file():
        return 0
    try:
        content = user_js.read_text(encoding="utf-8")
    except OSError as e:
        print_warn(f"Could not read {user_js}: {e}")
        return 0
    removed = 0
    for pref_name, pref_val in PREFS_TO_SET:
        exact = f'user_pref("{pref_name}", {pref_val});'
        # remove only exact full-line matches we wrote (never a user-modified value)
        new_content_lines = []
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == exact:
                removed += 1
                continue
            new_content_lines.append(line)
        content = "".join(new_content_lines)
    if removed:
        try:
            atomic_write_text(user_js, content)
            print_success(f"Removed {removed} pref line(s) from {user_js}")
        except OSError as e:
            print_warn(f"Could not write {user_js}: {e}")
    return removed

def restore_user_chrome(profile: Path) -> None:
    """Remove dusky_menu.css and its @import from userChrome.css."""
    chrome_dir = profile / "chrome"
    if not chrome_dir.is_dir():
        return
    menu_css = chrome_dir / "dusky_menu.css"
    if _remove_file(menu_css):
        print_success(f"Removed {menu_css}")
    remove_css_import(chrome_dir / "userChrome.css", '@import url("dusky_menu.css");')

def restore_user_content(profile: Path) -> None:
    """Remove dusky_about.css, palette symlink, and @import from userContent.css."""
    chrome_dir = profile / "chrome"
    if not chrome_dir.is_dir():
        return
    about_css = chrome_dir / "dusky_about.css"
    if _remove_file(about_css):
        print_success(f"Removed {about_css}")
    palette_link = chrome_dir / "dusky_palette.css"
    if _remove_file(palette_link):
        print_success(f"Removed {palette_link}")
    remove_css_import(chrome_dir / "userContent.css", '@import url("dusky_about.css");')

def uninstall_manifests(home: Path) -> int:
    """Remove native messaging manifests that belong to this extension."""
    removed = 0
    for base_dir in _browser_data_dirs(home):
        nmh_dir = base_dir / "native-messaging-hosts"
        if not nmh_dir.is_dir():
            continue
        manifest_file = nmh_dir / MANIFEST_NAME
        if not manifest_file.is_file():
            continue
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("name") == "dusky_sites" and EXTENSION_ID in data.get("allowed_extensions", []):
            if _remove_file(manifest_file):
                print_success(f"Removed manifest {manifest_file}")
                removed += 1
    return removed

def uninstall_global_xpis(home: Path) -> int:
    """Remove the XPI from global extension paths."""
    global_ext_dirs = [
        home / ".mozilla" / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}",
        home / ".config" / "mozilla" / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}",
        home / ".librewolf" / "extensions",
        home / ".zen" / "extensions",
        home / ".waterfox" / "extensions",
        home / ".floorp" / "extensions",
    ]
    removed = 0
    for g_dir in global_ext_dirs:
        xpi = g_dir / f"{EXTENSION_ID}.xpi"
        if _remove_file(xpi):
            print_success(f"Removed {xpi}")
            removed += 1
    return removed

def uninstall_profile_artifacts(home: Path) -> int:
    """Per profile: remove XPI, extensions.json entry, storage data, chrome CSS, and prefs."""
    profiles = 0
    for base_dir in _profile_base_dirs(home):
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            profiles += 1
            xpi = profile / "extensions" / f"{EXTENSION_ID}.xpi"
            if _remove_file(xpi):
                print_success(f"Removed {xpi}")
            data_dir = profile / "browser-extension-data" / EXTENSION_ID
            if _remove_tree(data_dir):
                print_success(f"Removed {data_dir}")
            unpatch_extensions_json(profile)
            restore_user_chrome(profile)
            restore_user_content(profile)
            restore_profile_prefs(profile)
    return profiles

def uninstall_host(data_home: Path) -> bool:
    installed_host = data_home / "dusky-sites" / HOST_INSTALL_NAME
    removed = _remove_file(installed_host)
    if removed:
        print_success(f"Removed host {installed_host}")
    # drop the container dir if now empty
    host_dir = data_home / "dusky-sites"
    try:
        if host_dir.is_dir() and not any(host_dir.iterdir()):
            host_dir.rmdir()
            print_success(f"Removed empty directory {host_dir}")
    except OSError as e:
        print_warn(f"Could not remove {host_dir}: {e}")
    return removed

def run_uninstall(home: Path) -> None:
    print(f"\n{C_CYAN}[-] Dusky Sites Uninstaller{C_RESET}\n")

    running = _browser_processes_running()
    if running:
        print_warn(f"Detected running browser(s): {', '.join(running)}")
        print_warn("It is strongly recommended to close them before continuing.")

    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home_raw:
        data_home = Path(xdg_data_home_raw).expanduser()
        if not data_home.is_absolute():
            data_home = home / ".local" / "share"
    else:
        data_home = home / ".local" / "share"

    print_step("Removing native messaging manifests...")
    manifests = uninstall_manifests(home)
    print_success(f"{manifests} manifest(s) removed.") if manifests else print_warn("No manifests found to remove.")

    print_step("Removing global extension XPI copies...")
    global_xpis = uninstall_global_xpis(home)
    print_success(f"{global_xpis} global XPI(s) removed.") if global_xpis else print_warn("No global XPI copies found.")

    print_step("Removing per-profile extension, chrome CSS and prefs...")
    profiles = uninstall_profile_artifacts(home)
    if profiles:
        print_success(f"Cleaned {profiles} browser profile(s).")
    else:
        print_warn("No browser profiles found.")

    print_step("Removing native host...")
    if uninstall_host(data_home):
        print_success("Host removed.")
    else:
        print_warn("No host file found.")

    print_step("Purging user configuration...")
    purged = 0
    config_dir = home / ".config" / "dusky" / "settings" / "dusky_sites"
    if _remove_tree(config_dir):
        print_success(f"Removed {config_dir}")
        purged += 1
    gen_css = home / ".config" / "matugen" / "generated" / "dusky_sites.css"
    if _remove_file(gen_css):
        print_success(f"Removed {gen_css}")
        purged += 1
    if not purged:
        print_warn("No configuration files found to purge.")

    print(f"\n{C_GREEN}[+] Uninstall complete.{C_RESET}")
    print("------------------------------------------------------------------")
    print(f"Removed: {manifests} manifest(s), {global_xpis} global XPI(s), host, {profiles} profile(s) cleaned.")
    print("Purging: User configuration (config.json) and generated CSS removed.")
    print("Site templates under ~/.config/dusky_sites and dev files under ~/.config/firefox_extentions/dusky_sites were left intact.")
    print("------------------------------------------------------------------\n")

def main() -> None:
    args = [a for a in sys.argv[1:]]
    if "--uninstall" in args or "--purge" in args or "--help" in args or "-h" in args:
        if "--help" in args or "-h" in args:
            print(__doc__)
            print("Options:")
            print("  --uninstall   Completely remove installed extension, host, manifests, chrome CSS & config.json.")
            print("  --yes         Skip the confirmation prompt.")
            return

        auto_yes = "--yes" in args
        label = "Dusky Sites (extension, host, manifests & user config.json)"
        if not auto_yes:
            resp = input(f"Are you sure you want to uninstall {label}? [y/N] ").strip().lower()
            if resp not in ("y", "yes"):
                print("Aborted.")
                return
        home = Path.home()
        run_uninstall(home)
        return

    print(f"\n{C_CYAN}Dusky Sites Setup Script (Arch Linux / Python 3.12+){C_RESET}\n")

    script_dir = Path(__file__).parent.resolve()
    source_host = resolve_source_host(script_dir)
    source_xpi = resolve_source_xpi(script_dir)

    print_step("Performing pre-flight checks...")
    if not source_host.is_file():
        print_error(f"Host script not found. Place {HOST_INSTALL_NAME} next to this setup script.\n  looked for: {source_host}")
    print_success(f"Found host source at {source_host}")

    if source_xpi and source_xpi.is_file():
        print_success(f"Found signed WebExtension package at {source_xpi}")
    else:
        print_warn("Signed XPI package not found; fallback to manual add-on load.")

    home = Path.home()
    xdg_data_home_raw = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data_home_raw:
        data_home = Path(xdg_data_home_raw).expanduser()
        if not data_home.is_absolute():
            data_home = home / ".local" / "share"
    else:
        data_home = home / ".local" / "share"
    install_dir = data_home / "dusky-sites"
    install_dir.mkdir(parents=True, exist_ok=True)
    installed_host = install_dir / HOST_INSTALL_NAME

    print_step("Installing host to stable XDG path...")
    try:
        if installed_host.is_symlink():
            installed_host.unlink()
        atomic_copy_file(source_host, installed_host)
        installed_host.chmod(0o700)
        print_success(f"Host installed at {installed_host}")
    except OSError as e:
        print_error(f"Failed to install host: {e}")

    print_step("Provisioning configuration directories...")
    config_dir = home / ".config" / "dusky" / "settings" / "dusky_sites"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.json"
    if not config_file.is_file():
        config_data = {
            "colorsPath": "~/.config/matugen/generated/dusky_sites.css",
            "websitesDir": "~/.config/dusky_sites",
            "webThemeEnabled": False,
            "forceUnthemedWebsites": False,
            "disabledSites": [],
        }
        try:
            atomic_write_text(config_file, json.dumps(config_data, indent=2) + "\n")
            print_success(f"Created primary config file at {config_file}")
        except OSError as e:
            print_error(f"Failed to create config: {e}")
    else:
        print_success(f"Config file exists at {config_file}")

    dusky_sites_dir = home / ".config" / "dusky_sites"
    dusky_sites_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured templates directory exists at {dusky_sites_dir}")

    matugen_gen_dir = home / ".config" / "matugen" / "generated"
    matugen_gen_dir.mkdir(parents=True, exist_ok=True)
    print_success(f"Ensured Matugen output directory exists at {matugen_gen_dir}")

    print_step("Detecting supported Firefox-based browsers...")
    targets: list[tuple[str, Path]] = []
    candidates = [
        ("Firefox", home / ".mozilla"),
        ("Firefox (XDG)", home / ".config" / "mozilla"),
        ("LibreWolf", home / ".librewolf"),
        ("LibreWolf (XDG)", home / ".config" / "librewolf"),
        ("Zen", home / ".zen"),
        ("Zen (XDG)", home / ".config" / "zen"),
        ("Waterfox", home / ".waterfox"),
        ("Floorp", home / ".floorp"),
        ("FireDragon", home / ".firedragon"),
        ("Firefox (Flatpak)", home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla"),
        ("LibreWolf (Flatpak)", home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf"),
    ]

    for name, path in candidates:
        nmh_dir = path / "native-messaging-hosts"
        if path.is_dir() or nmh_dir.is_dir():
            targets.append((name, nmh_dir))

    if not targets:
        targets.append(("Firefox (Default)", home / ".mozilla" / "native-messaging-hosts"))

    print_step("Installing native messaging manifests...")
    manifest_payload = {
        "name": "dusky_sites",
        "description": "Dusky Sites Native Messaging Host",
        "path": str(installed_host),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    manifest_text = json.dumps(manifest_payload, indent=2) + "\n"

    installed_count = 0
    for name, target_dir in targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = target_dir / MANIFEST_NAME
            atomic_write_text(manifest_file, manifest_text)
            print_success(f"Manifest installed for {name} → {manifest_file}")
            installed_count += 1
        except OSError as e:
            print_warn(f"Failed to install manifest in {target_dir}: {e}")

    if installed_count == 0:
        print_warn("No native messaging manifests were installed.")

    if source_xpi and source_xpi.is_file():
        print_step("Installing signed WebExtension into global extension paths...")
        FIREFOX_APP_ID = home / ".mozilla" / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
        global_ext_dirs = [
            FIREFOX_APP_ID,
            home / ".config" / "mozilla" / "extensions" / "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}",
            home / ".librewolf" / "extensions",
            home / ".zen" / "extensions",
            home / ".waterfox" / "extensions",
            home / ".floorp" / "extensions",
        ]
        g_count = 0
        for g_dir in global_ext_dirs:
            try:
                g_dir.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(source_xpi, g_dir / f"{EXTENSION_ID}.xpi")
                g_count += 1
            except OSError as e:
                print_warn(f"Could not copy XPI to {g_dir}: {e}")
        if g_count > 0:
            print_success(f"Signed XPI installed into {g_count} global extension path(s).")

    print_step("Provisioning native context menu, userChrome & profile XPI extensions...")
    setup_user_chrome(home, source_xpi)

    print(f"\n{C_GREEN}[+] Setup Complete! Dusky Sites host and signed WebExtension provisioned cleanly.{C_RESET}")
    print("------------------------------------------------------------------")
    print(f"{C_CYAN}Host path:{C_RESET} {installed_host}")
    print(f"{C_CYAN}Manifest name:{C_RESET} {MANIFEST_NAME} (native app name: dusky_sites)")
    if source_xpi:
        print(f"{C_CYAN}Signed XPI:{C_RESET} {source_xpi}")
    print("------------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
