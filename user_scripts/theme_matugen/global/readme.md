# Global Matugen theming

Dusky uses Matugen to generate a shared color palette from a wallpaper or a
chosen color. GTK, Qt, KDE and Kvantum consume different representations of that
palette. Cursor colors and Papirus folder colors also follow it.

The GTK overrides change colors while keeping the toolkit's widget layout.
Qt palette publication preserves the selected widget style. Kvantum is a
separate, optional widget style: its generated assets matter only when Kvantum
is selected. Applications can still supply their own styling or cache colors.

This document covers global toolkit and desktop configuration. Individual
application themes and application-specific preferences are outside its scope.

## Where configuration lives

Paths below use the normal XDG defaults:

| Purpose | Location |
| --- | --- |
| Template definitions and post-generation hooks | `~/.config/matugen/config.toml` |
| Editable color templates | `~/.config/matugen/templates/` |
| Matugen's generated output | `~/.config/matugen/generated/` |
| Validated Qt palette files | `~/.config/matugen/published/` |
| Global publishers and shared file helpers | `~/user_scripts/theme_matugen/global/` |
| Wallpaper/color controller | `~/user_scripts/theme_matugen/theme_ctl.sh` |

The Python publishers respect `XDG_CONFIG_HOME` and `XDG_DATA_HOME`; the shared
lock helper also respects runtime/cache locations. Several Matugen shell hooks
and the root setup helper explicitly use the default directories, so changing
XDG variables alone does not relocate the entire setup.

Edit templates to change the color mapping. Generated and published files are
runtime output and will be replaced on subsequent theme changes.

## How a theme change travels through the system

```text
Wallpaper or chosen color
    -> theme_ctl.sh invokes Matugen
    -> config.toml selects templates and output paths
    -> Matugen renders files into generated/
    -> post-hooks validate and publish toolkit colors
    -> toolkit notifications, reloads, or application restart
```

The publisher scripts are [gtk_colors.py](gtk_colors.py),
[qt_colors.py](qt_colors.py) and [kde_colors.py](kde_colors.py). Their shared
publication helpers live in [theme_files.py](theme_files.py).

Publication validates the relevant output before replacing the live file.
Replacement uses a temporary file on the destination filesystem and an atomic
rename, so readers see an old or new complete file rather than a truncated
write. GTK, KDE, Kvantum configuration and cursor templates contain completion
markers; Qt palettes are checked for valid color roles and SVGs are parsed.
An invalid input fails the affected hook and retains its previously published
output.

These protections operate per file and per toolkit. A whole theme change is
not one transaction: components may finish at different times, and Kvantum's
configuration and SVG are published separately.

## GTK3

| Item | Location |
| --- | --- |
| Template | `~/.config/matugen/templates/gtk3-colors.css` |
| Generated palette | `~/.config/matugen/generated/gtk-3.css` |
| Publisher | `gtk_colors.py 3` |
| Reloadable theme wrappers | `~/.local/share/themes/dusky-matugen-{a,b}/gtk-3.0/` |
| Cached base theme and assets | `~/.local/share/themes/dusky-matugen-base/<fingerprint>/` |
| Selected theme preference | `~/.config/gtk-3.0/settings.ini` and GNOME interface GSettings |

The publisher discovers the installed `adw-gtk3-dark` theme, copies its CSS and
assets into an immutable cache, and imports that base before the generated
palette. The base keeps its original layout and assets. The cached copy is
also readable inside Flatpak, where the host's `/usr/share/themes` is not the
runtime's `/usr/share/themes`.

File metadata determines the cache fingerprint. An unchanged base is reused;
a package update creates another complete cached version on the next GTK3
publication. Old versions remain available for existing imports.

GTK3 caches user CSS for the lifetime of an application. To reload colors,
both wrappers receive the same stylesheet and the publisher alternates their
names through `org.gnome.desktop.interface`'s `gtk-theme` setting. Applications
that observe theme changes can then reload without changing their layout.
The `[Settings]` theme name is updated too, while other settings are preserved.

`~/.config/gtk-3.0/gtk.css` contains only a comment pointing to the wrappers.
The palette deliberately lives in the reloadable theme provider. Applications
already running during migration from the old user-CSS arrangement need one
restart to discard their cached overrides.

## GTK4 and libadwaita

| Item | Location |
| --- | --- |
| Template | `~/.config/matugen/templates/gtk4-colors.css` |
| Generated palette | `~/.config/matugen/generated/gtk-4.css` |
| Publisher | `gtk_colors.py 4` |
| Published user stylesheet | `~/.config/gtk-4.0/gtk.css` |

The template supplies CSS color variables and named colors for libadwaita,
plus explicit paint-property rules for plain GTK4 widgets. The built-in GTK4
theme compiles many colors into its stylesheet, so variables alone do not
recolor plain GTK4 applications such as pavucontrol. GTK validates the generated
CSS before publication.
The overrides do not replace the application's widget geometry.

GTK4/libadwaita user CSS is cached by running applications. Reopen an app after
a palette change to load the new stylesheet; atomic publication ensures the
next process reads complete CSS. This setup does not force `GTK_THEME` for
GTK4 applications.

## Qt5 and Qt6: qt5ct / qt6ct

| Item | Location |
| --- | --- |
| Shared template | `~/.config/matugen/templates/qtct-colors.conf` |
| Generated palettes | `~/.config/matugen/generated/qt{5,6}ct-colors.conf` |
| Publisher | `qt_colors.py qt5ct` or `qt_colors.py qt6ct` |
| Published palettes | `~/.config/matugen/published/qt{5,6}ct-colors.conf` |
| Stable palette links | `~/.config/qt{5,6}ct/colors/matugen.conf` |
| Toolkit preferences | `~/.config/qt{5,6}ct/qt{5,6}ct.conf` |

The publisher validates active, inactive and disabled palettes. Qt5 receives
21 roles; Qt6 receives 22, including its Accent role.

The stable links use relative targets:

```text
~/.config/qt5ct/colors/matugen.conf -> ../../matugen/published/qt5ct-colors.conf
~/.config/qt6ct/colors/matugen.conf -> ../../matugen/published/qt6ct-colors.conf
```

Git can track these links without tracking the current palette contents.
Their targets contain validated runtime output. Linking directly to Matugen's
in-place generated writes would bypass the publication protection.

After a changed palette is published, a temporary entry is created and removed
in the qtct configuration directory. This triggers qtct's directory watcher
and roughly three-second reload timer without rewriting the main preferences.
Unchanged output is skipped.

[165_qtct_config.sh](../../arch_setup_scripts/scripts/165_qtct_config.sh)
enables `custom_palette`, writes the current user's absolute palette path,
sets Papirus-Dark icons and the dialog preference, and preserves an existing
widget style. A fresh configuration defaults to Fusion. Fonts and other
existing preferences are retained; fresh configurations receive setup defaults.

The matching qtct platform-theme plugin must be active for a Qt application to
consume these preferences. The shipped Hyprland environment selects
`QT_QPA_PLATFORMTHEME=qt6ct`; writing a Qt5 palette alone does not prove a Qt5
process has loaded its matching plugin. Applications using another platform
theme or their own palette may behave differently.

## Kvantum

| Item | Location |
| --- | --- |
| Templates | `~/.config/matugen/templates/kvantum-colors.kvconfig` and `kvantum-colors.svg` |
| Generated assets | `~/.config/matugen/generated/kvantum-matugen.{kvconfig,svg}` |
| Publisher | `qt_colors.py kvantum_kvconfig` or `qt_colors.py kvantum_svg` |
| Published theme | `~/.config/Kvantum/matugen/matugen.{kvconfig,svg}` |
| Kvantum theme selection | `~/.config/Kvantum/kvantum.kvconfig`: `[General] theme=matugen` |

The publisher checks configuration sections, color values and the completion
marker, or parses the SVG, then publishes the corresponding asset. Both hooks
share the Qt publication lock.

Kvantum has two separate choices: the Qt widget style must be Kvantum, and
Kvantum must select the `matugen` theme. Merely generating these files does not
switch Qt from Fusion to Kvantum. The current qtct configurations select Fusion,
so they use the Qt palettes rather than these Kvantum widget assets. Selecting
Kvantum is a deliberate layout/style choice. Reopen applications if their
Kvantum assets remain cached after a change.

## KDE colors

| Item | Location |
| --- | --- |
| Template | `~/.config/matugen/templates/Matugen.colors` |
| Generated scheme | `~/.config/matugen/generated/kdeglobals` |
| Publisher | `kde_colors.py` |
| Published named scheme | `~/.local/share/color-schemes/Matugen.colors` |
| Global KDE preferences | `~/.config/kdeglobals` |

The publisher validates the scheme, publishes its named copy and merges the
managed entries into `kdeglobals`. Managed groups include Button, Complementary,
Header, Selection, Tooltip, View and Window colors, disabled/inactive effects,
WM colors and the Matugen scheme name. Unrelated preferences such as fonts,
icons and widget styles are preserved.

`kdeglobals` is a regular merged configuration file, not a symlink to transient
generated output. When content changes and a session bus is available, the
publisher sends native KConfig and KDE palette notifications. Without a bus,
it still publishes the files for the next session.

Applications that cache a pinned KDE color scheme may need reopening despite
the notifications. Global KDE colors do not force every Qt application to use
KDE's platform theme. Setup also has a separate KDE application-preference
helper; its individual application settings are outside this document's scope.

## Flatpak

[332_flatpack_apps_themeing.py](../../arch_setup_scripts/scripts/332_flatpack_apps_themeing.py)
runs as the desktop user during setup. It adds global user overrides for:

```text
xdg-config/gtk-3.0:ro
xdg-config/gtk-4.0:ro
xdg-data/themes:ro
xdg-data/icons:ro
```

The XDG mappings expose the host's GTK configuration, user themes and cursor
icons at the locations sandboxed applications use. Sharing theme directories
also makes the cached GTK3 base and its assets available. Sharing directories
rather than individual generated files keeps atomic replacements visible.

These overrides apply to existing and future apps, including system-installed
Flatpaks. No downloads are needed. Existing explicit writable access and
unrelated overrides are preserved; a repeat run makes no changes when all four
mappings are present. If Flatpak is absent, the helper skips configuration and
can be run manually after installation.

This is a setup operation, not a wallpaper hook. Reopen running Flatpak apps
after changing permissions, and reopen GTK4 apps after palette updates.
Application-specific permission overrides remain in force. This helper shares
GTK themes and icons; it does **not** configure Qt/KDE platform plugins inside
runtimes or recolor Electron, Flutter, games and other custom interfaces.

## Cursor theme

The `dusky_cursor` Matugen entry renders
`~/.config/matugen/templates/dusky-cursor.env` into
`~/.config/matugen/generated/dusky-cursor.env`. Its hook runs
[dusky_cursor.py](../../cursor/color/dusky_cursor.py) with `--apply --quiet`.

The script builds `~/.local/share/icons/Dusky/` from the installed
`Bibata-Modern-Classic` cursor theme. It uses the generated primary/background
colors, derives the darker fill, and preserves cursor geometry, transparency
and animation. A fingerprint allows an unchanged build to be reused; a new
build is prepared before replacing the installed theme.

Applying the theme updates Hyprland's cursor, GNOME interface settings, the session
activation environment, user Hyprland environment Lua, the default icon-theme
index and GTK3/4 cursor settings. GTK settings updates share the GTK publication
lock so color and cursor hooks do not overwrite each other's settings.

User theme, size and color overrides live in
`~/.config/dusky/settings/cursor.conf`. The separate size helper is
[cursor_size.py](../../cursor/size/cursor_size.py). Cursor color and size changes
share a runtime cursor lock. Hook diagnostics are recorded in
`~/.cache/dusky-cursor/hook.log`.

## Icons and shared editor color schemes

The global icon-theme hook selects `Papirus-Dark`. Matugen compares the primary
color with the configured Papirus folder palette and selects the closest
available folder color. The hook runs `papirus-folders` through noninteractive
sudo and, after recoloring and cache updates finish successfully, toggles the
icon-theme setting to encourage reloads. This chooses a
predefined folder color rather than recoloring every icon. The utility and its
existing sudo permission must be available for that hook to succeed.

Two shared editor scheme families are also generated:

| Scheme | Published location |
| --- | --- |
| GtkSourceView | `~/.local/share/gtksourceview-{3.0,4,5}/styles/matugen.xml` |
| KDE syntax highlighting | `~/.local/share/org.kde.syntax-highlighting/themes/Matugen.theme` |

These are symlinks to their generated XML/theme files. Installing a shared
scheme makes it available to consumers; selection and reload behavior depend
on the editor. These shell-link hooks do not use the validated atomic publishers.

## Desktop components

The same Matugen configuration also supplies these global desktop consumers:

| Component | Generated file | Configured refresh behavior |
| --- | --- | --- |
| Hyprland | `hyprland-colors.lua` | `hyprctl reload config-only` |
| Hyprlock | `hyprlock-colors.conf` | No dedicated reload hook; consumed by lock configuration |
| Waybar | `waybar-colors.css` | `USR2` signal |
| Mako | `mako-colors.ini` | `makoctl reload` |
| Rofi | `rofi-colors.rasi` | No dedicated reload hook; consumed when launched |
| Wlogout | `wlogout-colors.css` | No dedicated reload hook; requires the selected style to include it |

Files in this table live under `~/.config/matugen/generated/`. A generated file
only affects a component when its active configuration imports it. The current
Hyprland, Waybar, Mako and Rofi configurations reference their generated colors.
Lock/logout theme selection determines whether those consumers use theirs.

## Installation and runtime responsibilities

| Stage | Helper | Responsibility |
| --- | --- | --- |
| Setup | `165_qtct_config.sh` | Configure global Qt palette preferences |
| Setup, privileged | `330_gtk_root_symlink.sh` | Link root's relevant theme/configuration paths to the installing user's paths |
| Setup | `332_flatpack_apps_themeing.py` | Configure global per-user Flatpak theme access |
| Setup | `375_cursor_theme_bibata_classic_modern.sh` | Install the source cursor theme |
| Initial generation | `376_generate_colorfiles_for_current_wallpaer.sh` | Generate from the shipped default wallpaper |
| Palette changes | Matugen post-hooks | Publish GTK, Qt, KDE, Kvantum, cursors and desktop colors |

The three installation profiles include these setup steps. The updater includes
the Qt and Flatpak setup helpers as `once` tasks; root linkage is commented out
there. Run user setup and publication commands as the desktop user. The root
helper is specifically invoked through sudo and replaces its target entries
with links; it is not a wallpaper hook.

## Locks and concurrency

GTK, Qt and KDE publishers use separate blocking locks under
`$XDG_RUNTIME_DIR/dusky-theme/{gtk,qt,kde}.lock`. If runtime storage is absent,
the helper uses `$XDG_CACHE_HOME`, or `~/.cache`, instead. Cursor operations use
`$XDG_RUNTIME_DIR/dusky-cursor.lock` with a cache fallback.

These files coordinate processes and contain no palette data. They belong in
runtime/cache storage, not `~/.config/matugen/`. Do not remove active lock files:
replacing a lock's inode can let two processes enter the same critical section.
Runtime storage is normally cleared with the session's runtime directory.

Publication locks do not serialize Matugen's writes into `generated/`.
`theme_ctl.sh` also explicitly does not serialize independent invocations.
Avoid overlapping palette-generation or mode-change commands. Rapid
wallpaper-only cycling can use `next --no-regen`; a later `refresh` generates
the palette for the current wallpaper.

## Manual refresh and inspection

Regenerate colors through the normal controller:

```bash
bash "$HOME/user_scripts/theme_matugen/theme_ctl.sh" refresh
```

`refresh` uses the current wallpaper; if its source is missing or the wallpaper
daemon reports only a solid color, it may select a random wallpaper. To use an
explicit existing image without changing the displayed wallpaper:

```bash
bash "$HOME/user_scripts/theme_matugen/theme_ctl.sh" set /path/to/source.png --no-wall
```

To republish existing generated toolkit output without running Matugen:

```bash
python3 "$HOME/user_scripts/theme_matugen/global/gtk_colors.py" 3
python3 "$HOME/user_scripts/theme_matugen/global/gtk_colors.py" 4
python3 "$HOME/user_scripts/theme_matugen/global/qt_colors.py" qt5ct
python3 "$HOME/user_scripts/theme_matugen/global/qt_colors.py" qt6ct
python3 "$HOME/user_scripts/theme_matugen/global/qt_colors.py" kvantum_kvconfig
python3 "$HOME/user_scripts/theme_matugen/global/qt_colors.py" kvantum_svg
python3 "$HOME/user_scripts/theme_matugen/global/kde_colors.py"
```

These commands require the corresponding generated files to exist. GTK3
publication also selects the other reloadable theme name.

Inspect global configuration without changing it:

```bash
gsettings get org.gnome.desktop.interface gtk-theme
flatpak override --user --show
python3 "$HOME/user_scripts/arch_setup_scripts/scripts/332_flatpack_apps_themeing.py" --dry-run
python3 "$HOME/user_scripts/cursor/color/dusky_cursor.py" --status
python3 "$HOME/user_scripts/cursor/color/dusky_cursor.py" --check
```

When a window retains old colors, first check the affected hook's errors and
published file, then reopen it if its toolkit caches the palette. For Flatpak,
check global and any application-specific permissions. For Qt, check the loaded
platform-theme plugin and selected widget style. Successful generation alone
does not demonstrate that a particular application consumed the colors.
